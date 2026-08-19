"""Event bus with configurable backend: in-memory (local/test) or Google Cloud Pub/Sub (deployed).

Backend selected by EVENT_BUS_BACKEND env var:
  - "memory" (default): in-memory bus for local dev and tests
  - "pubsub": real Google Cloud Pub/Sub for deployed/production use

Both backends expose the same EventBus interface. The in-memory bus also
serves as a local history cache even when Pub/Sub is the primary transport.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine

from src.models.events import Event, EventType

logger = logging.getLogger(__name__)

EventCallback = Callable[[Event], Coroutine[Any, Any, None]] | Callable[[Event], None]


class EventBus:
    """In-memory event bus with pub/sub semantics. Always available as the local cache."""

    _instance: EventBus | None = None

    def __init__(self) -> None:
        self._subscribers: dict[str, list[EventCallback]] = defaultdict(list)
        self._history: list[Event] = []
        self._max_history = 1000
        self._pubsub: PubSubTransport | None = None

        # Auto-configure Pub/Sub if env says so
        backend = os.environ.get("EVENT_BUS_BACKEND", "memory")
        if backend == "pubsub":
            try:
                self._pubsub = PubSubTransport()
                logger.info("Event bus: Pub/Sub transport enabled")
            except Exception as e:
                logger.warning(f"Event bus: Pub/Sub init failed, falling back to memory: {e}")

    @property
    def backend(self) -> str:
        return "pubsub" if self._pubsub else "memory"

    @classmethod
    def get(cls) -> EventBus:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        cls._instance = None

    def subscribe(self, event_type: str | EventType, callback: EventCallback) -> None:
        key = str(event_type)
        self._subscribers[key].append(callback)

    def subscribe_all(self, callback: EventCallback) -> None:
        self._subscribers["*"].append(callback)

    async def publish(self, event: Event) -> str:
        if not event.id:
            event.id = str(uuid.uuid4())

        # Always store in local history
        self._history.append(event)
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history:]

        # Publish to real Pub/Sub if configured
        if self._pubsub:
            try:
                self._pubsub.publish(event)
            except Exception as e:
                logger.error(f"Pub/Sub publish failed: {e}")

        # Notify local subscribers
        for callback in self._subscribers.get(str(event.type), []):
            try:
                result = callback(event)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                logger.error(f"Event handler error for {event.type}: {e}")

        for callback in self._subscribers.get("*", []):
            try:
                result = callback(event)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                logger.error(f"Wildcard event handler error: {e}")

        return event.id

    def get_history(
        self,
        incident_id: str = "",
        event_type: str = "",
        limit: int = 50,
    ) -> list[Event]:
        results = list(self._history)
        if incident_id:
            results = [e for e in results if e.incident_id == incident_id]
        if event_type:
            results = [e for e in results if str(e.type) == event_type]
        return results[-limit:]

    def clear_history(self) -> None:
        self._history.clear()


class PubSubTransport:
    """Real Google Cloud Pub/Sub transport. Publishes events to a topic."""

    def __init__(self) -> None:
        from google.cloud import pubsub_v1

        self.project = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
        self.topic_id = os.environ.get("PUBSUB_TOPIC_EVENTS", "crisismesh-events")
        self.publisher = pubsub_v1.PublisherClient()
        self._topic_path = self.publisher.topic_path(self.project, self.topic_id)

    def publish(self, event: Event) -> str:
        data = json.dumps(event.model_dump(mode="json"), default=str).encode("utf-8")
        future = self.publisher.publish(
            self._topic_path,
            data,
            event_type=str(event.type),
            incident_id=event.incident_id,
            agent_id=event.agent_id,
        )
        return future.result()


def create_event(
    event_type: EventType,
    incident_id: str,
    agent_id: str = "",
    data: dict[str, Any] | None = None,
) -> Event:
    return Event(
        id=str(uuid.uuid4()),
        type=event_type,
        incident_id=incident_id,
        agent_id=agent_id,
        timestamp=datetime.now(timezone.utc),
        data=data or {},
    )

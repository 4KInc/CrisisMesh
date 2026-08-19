"""Local event bus — in-memory alternative to Pub/Sub for local development.

Supports publish/subscribe with typed events, async callbacks, and event history.
In production, delegates to Google Cloud Pub/Sub.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine

from src.models.events import Event, EventType

logger = logging.getLogger(__name__)

EventCallback = Callable[[Event], Coroutine[Any, Any, None]] | Callable[[Event], None]


class EventBus:
    """In-memory event bus with pub/sub semantics."""

    _instance: EventBus | None = None

    def __init__(self) -> None:
        self._subscribers: dict[str, list[EventCallback]] = defaultdict(list)
        self._history: list[Event] = []
        self._max_history = 1000

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

        self._history.append(event)
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history:]

        # Notify specific subscribers
        for callback in self._subscribers.get(str(event.type), []):
            try:
                result = callback(event)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                logger.error(f"Event handler error for {event.type}: {e}")

        # Notify wildcard subscribers
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

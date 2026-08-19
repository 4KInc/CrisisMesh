"""Pub/Sub event bus for async agent-to-agent communication."""

from __future__ import annotations

import json
import os
from typing import Any, Callable, Coroutine

from google.cloud import pubsub_v1
from google.cloud.pubsub_v1.types import PubsubMessage

from src.models.events import Event


class PubSubBus:
    """Publishes and subscribes to CrisisMesh events via Google Cloud Pub/Sub."""

    def __init__(self, project: str | None = None) -> None:
        self.project = project or os.environ.get("GOOGLE_CLOUD_PROJECT", "")
        self.publisher = pubsub_v1.PublisherClient()
        self.subscriber = pubsub_v1.SubscriberClient()

    def _topic_path(self, topic: str) -> str:
        return self.publisher.topic_path(self.project, topic)

    def _subscription_path(self, subscription: str) -> str:
        return self.subscriber.subscription_path(self.project, subscription)

    def publish(self, topic: str, event: Event) -> str:
        """Publish an event to a Pub/Sub topic. Returns message ID."""
        topic_path = self._topic_path(topic)
        data = json.dumps(event.model_dump(mode="json")).encode("utf-8")
        future = self.publisher.publish(
            topic_path,
            data,
            event_type=event.type,
            incident_id=event.incident_id,
            agent_id=event.agent_id,
        )
        return future.result()

    def subscribe(
        self,
        subscription: str,
        callback: Callable[[Event], Coroutine[Any, Any, None]] | Callable[[Event], None],
    ) -> None:
        """Subscribe to events. Callback receives deserialized Event objects."""
        subscription_path = self._subscription_path(subscription)

        def _handle(message: PubsubMessage) -> None:
            try:
                data = json.loads(message.data.decode("utf-8"))
                event = Event(**data)
                result = callback(event)
                # If callback is async, we can't await here in sync context
                # In production, use async pull or Cloud Run push
                if result is not None and hasattr(result, "__await__"):
                    import asyncio
                    asyncio.get_event_loop().run_until_complete(result)
                message.ack()
            except Exception:
                message.nack()

        self.subscriber.subscribe(subscription_path, callback=_handle)

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class EventType(StrEnum):
    INCIDENT_DECLARED = "incident.declared"
    INCIDENT_UPDATED = "incident.updated"
    INCIDENT_RESOLVED = "incident.resolved"
    CHECKIN_RECEIVED = "checkin.received"
    CHECKIN_MISSED = "checkin.missed"
    TASK_CREATED = "task.created"
    TASK_COMPLETED = "task.completed"
    TASK_FAILED = "task.failed"
    TASK_TIMEOUT = "task.timeout"
    AGENT_DELEGATED = "agent.delegated"
    AGENT_RESPONDED = "agent.responded"
    AGENT_ERROR = "agent.error"
    APPROVAL_REQUESTED = "approval.requested"
    APPROVAL_GRANTED = "approval.granted"
    APPROVAL_DENIED = "approval.denied"
    SITREP_GENERATED = "sitrep.generated"
    LESSON_RECORDED = "lesson.recorded"
    POLICY_VIOLATION = "policy.violation"


class Event(BaseModel):
    id: str = Field(default_factory=lambda: "")
    type: EventType
    incident_id: str
    agent_id: str = ""
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    data: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

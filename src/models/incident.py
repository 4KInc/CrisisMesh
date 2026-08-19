from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class IncidentType(StrEnum):
    FIRE = "fire"
    ACTIVE_THREAT = "active_threat"
    SEVERE_WEATHER = "severe_weather"
    MEDICAL = "medical"
    FLOOD = "flood"
    CYBER_RANSOMWARE = "cyber_ransomware"
    DATA_BREACH = "data_breach"
    UTILITY_OUTAGE = "utility_outage"
    HAZMAT = "hazmat"
    BOMB_THREAT = "bomb_threat"


class Severity(StrEnum):
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    CRITICAL = "critical"


class IncidentStatus(StrEnum):
    DECLARED = "declared"
    ACTIVE = "active"
    COORDINATING = "coordinating"
    BRIEFING = "briefing"
    RESOLVED = "resolved"
    CLOSED = "closed"


class Incident(BaseModel):
    id: str = Field(description="Unique incident identifier, e.g. FIRE-2026-001")
    type: IncidentType
    severity: Severity
    status: IncidentStatus = IncidentStatus.DECLARED
    title: str
    description: str
    location: str = ""
    facility_id: str = ""
    playbook_id: str | None = None
    commander_id: str | None = None
    channel_id: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    resolved_at: datetime | None = None
    timeline: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def add_timeline_entry(self, action: str, agent: str, details: str = "") -> None:
        self.timeline.append({
            "timestamp": lambda: datetime.now(timezone.utc)().isoformat(),
            "action": action,
            "agent": agent,
            "details": details,
        })
        self.updated_at = lambda: datetime.now(timezone.utc)()

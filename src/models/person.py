from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class PersonStatus(StrEnum):
    UNKNOWN = "unknown"
    SAFE = "safe"
    INJURED = "injured"
    NEED_HELP = "need_help"
    EVACUATED = "evacuated"
    SILENT = "silent"


class AccessibilityFlag(StrEnum):
    MOBILITY = "mobility"
    VISUAL = "visual"
    HEARING = "hearing"
    COGNITIVE = "cognitive"
    MEDICAL_DEVICE = "medical_device"
    OTHER = "other"


class Person(BaseModel):
    id: str
    name: str
    role: str = ""
    email: str = ""
    phone: str = ""
    facility_id: str = ""
    room_id: str = ""
    department: str = ""
    accessibility_flags: list[AccessibilityFlag] = Field(default_factory=list)
    medical_notes: str = ""  # need-to-know only, redacted in general channels
    emergency_contact: str = ""
    is_on_call: bool = False
    status: PersonStatus = PersonStatus.UNKNOWN
    last_check_in: str | None = None

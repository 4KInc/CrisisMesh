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


class Person(BaseModel):
    id: str
    name: str
    slack_user_id: str = ""
    role: str = ""
    department: str = ""
    default_location: str = ""  # zone_id or room_id
    floor: int = 1
    phone: str = ""
    emergency_contact_name: str = ""
    emergency_contact_phone: str = ""
    medical_notes: str = ""  # need-to-know only, redacted in general channels
    mobility_limitations: bool = False
    trained_first_aid: bool = False
    trained_cpr: bool = False
    is_floor_warden: bool = False
    evacuation_role: str = ""  # Incident Commander, Floor Warden, Medical Lead, etc.
    status: PersonStatus = PersonStatus.UNKNOWN
    last_check_in: str | None = None

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class ResourceType(StrEnum):
    AED = "aed"
    TRAUMA_KIT = "trauma_kit"
    FIRE_EXTINGUISHER = "fire_extinguisher"
    UTILITY_SHUTOFF = "utility_shutoff"
    HAZMAT_STORAGE = "hazmat_storage"
    ASSEMBLY_POINT = "assembly_point"
    EMERGENCY_EXIT = "emergency_exit"
    SHELTER_AREA = "shelter_area"
    COMMUNICATION_DEVICE = "communication_device"


class Room(BaseModel):
    id: str
    name: str
    facility_id: str
    floor: int = 1
    building: str = ""
    capacity: int = 0
    room_type: str = ""  # classroom, lab, office, gym, cafeteria, etc.


class Route(BaseModel):
    id: str
    facility_id: str
    name: str
    from_zone: str
    to_zone: str
    route_type: str = "evacuation"  # evacuation, access, emergency
    is_accessible: bool = True
    status: str = "open"  # open, blocked, restricted
    notes: str = ""


class Resource(BaseModel):
    id: str
    facility_id: str
    type: ResourceType
    name: str
    location: str
    room_id: str = ""
    floor: int = 1
    notes: str = ""


class Facility(BaseModel):
    id: str
    name: str
    address: str = ""
    floors: int = 1
    buildings: list[str] = Field(default_factory=list)
    rooms: list[Room] = Field(default_factory=list)
    routes: list[Route] = Field(default_factory=list)
    resources: list[Resource] = Field(default_factory=list)

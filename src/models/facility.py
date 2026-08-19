from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class ResourceType(StrEnum):
    AED = "aed"
    FIRST_AID_KIT = "first_aid_kit"
    TRAUMA_KIT = "trauma_kit"
    FIRE_EXTINGUISHER = "fire_extinguisher"
    EMERGENCY_PHONE = "emergency_phone"


class Facility(BaseModel):
    id: str
    name: str
    address: str = ""
    floors: int = 1
    capacity: int = 0


class Zone(BaseModel):
    id: str
    facility_id: str
    name: str
    floor: int = 1
    zone_type: str = ""  # classrooms, administrative, common
    primary_exit: str = ""
    alternate_exit: str = ""
    shelter_location: str = ""
    capacity: int = 0
    notes: str = ""


class Room(BaseModel):
    id: str
    facility_id: str
    name: str
    floor: int = 1
    zone_id: str = ""
    room_type: str = ""  # classroom, laboratory, art_room, music_room
    capacity: int = 0
    notes: str = ""


class EvacuationRoute(BaseModel):
    facility_id: str
    name: str
    from_zone: str
    to_exit: str
    route_description: str = ""
    accessibility: str = "standard"  # standard, wheelchair_accessible
    blocked_by_zones: str = ""  # zones that if affected block this route


class EmergencyResource(BaseModel):
    facility_id: str
    resource_type: ResourceType
    location_description: str
    floor: int = 1
    zone_id: str = ""
    notes: str = ""


class AssemblyPoint(BaseModel):
    id: str
    facility_id: str
    name: str
    location_description: str = ""
    capacity: int = 0
    is_primary: bool = False
    accessibility: str = "standard"
    notes: str = ""


class NearbyService(BaseModel):
    service_type: str  # hospital, trauma_center, police_station, fire_station, urgent_care
    name: str
    address: str = ""
    phone: str = ""
    distance_miles: float = 0.0
    eta_minutes: int = 0
    trauma_level: str = ""
    helipad: bool = False

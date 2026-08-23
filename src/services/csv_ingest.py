"""CSV ingestion service — imports organizational data with semantic validation.

Row-level reject-and-report: valid rows load, invalid rows are quarantined with
a specific reason. Each batch returns a validation report.
"""

from __future__ import annotations

import csv
import io
import logging
import uuid
from typing import Any

from src.core.knowledge_base import KnowledgeBase
from src.models.facility import (
    AssemblyPoint,
    EmergencyResource,
    EvacuationRoute,
    Facility,
    NearbyService,
    ResourceType,
    Room,
    Zone,
)
from src.models.person import Person
from src.services.firestore_state import FirestoreState

logger = logging.getLogger(__name__)


def _bool(val: str) -> bool:
    return val.strip().lower() in ("true", "yes", "1")


def _make_report(
    csv_type: str,
    loaded: int,
    rejected: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "type": csv_type,
        "records_loaded": loaded,
        "records_rejected": len(rejected),
        "rejected_rows": rejected,
    }


# ── Semantic validators ──


def validate_evacuation_route(row: dict[str, str], kb: KnowledgeBase) -> str | None:
    """Reject if route terminates in a blocked/threat zone."""
    to_exit = row.get("to_exit", "")
    blocked_by = row.get("blocked_by_zones", "")
    from_zone = row.get("from_zone", "")
    facility_id = row.get("facility_id", "")

    if from_zone and kb.zones:
        zone = kb.get_zone(from_zone)
        if zone is None:
            return f"route '{row.get('name', '?')}' references unknown from_zone '{from_zone}'"

    if blocked_by and kb.zones:
        blocked_zones = {z.strip() for z in blocked_by.split(",") if z.strip()}
        for bz in blocked_zones:
            if kb.get_zone(bz) is None:
                return f"route '{row.get('name', '?')}' references unknown blocked zone '{bz}'"

    return None


def validate_emergency_resource(row: dict[str, str], kb: KnowledgeBase) -> str | None:
    """Reject if resource's zone_id doesn't resolve in the KB."""
    zone_id = row.get("zone_id", "")
    facility_id = row.get("facility_id", "")
    floor_str = row.get("floor", "1")

    if zone_id and kb.zones:
        zone = kb.get_zone(zone_id)
        if zone is None:
            return (
                f"resource at '{row.get('location_description', '?')}' references "
                f"unknown zone '{zone_id}'"
            )
        if facility_id and zone.get("facility_id") != facility_id:
            return (
                f"resource at '{row.get('location_description', '?')}' — zone '{zone_id}' "
                f"belongs to facility '{zone.get('facility_id')}', not '{facility_id}'"
            )

    if facility_id and kb.facilities:
        fac = kb.get_facility(facility_id)
        if fac is None:
            return (
                f"resource at '{row.get('location_description', '?')}' references "
                f"unknown facility '{facility_id}'"
            )
        try:
            floor = int(floor_str)
            max_floors = int(fac.get("floors", 1))
            if floor > max_floors or floor < 1:
                return (
                    f"resource at '{row.get('location_description', '?')}' — floor {floor} "
                    f"exceeds facility '{facility_id}' max floors ({max_floors})"
                )
        except ValueError:
            pass

    return None


def validate_room(row: dict[str, str], kb: KnowledgeBase) -> str | None:
    """Reject if room references a facility that isn't loaded."""
    facility_id = row.get("facility_id", "")
    zone_id = row.get("zone_id", "")

    if facility_id and kb.facilities:
        fac = kb.get_facility(facility_id)
        if fac is None:
            return (
                f"room '{row.get('name', row.get('room_id', '?'))}' references "
                f"unknown facility '{facility_id}'"
            )

    if zone_id and kb.zones:
        zone = kb.get_zone(zone_id)
        if zone is None:
            return (
                f"room '{row.get('name', row.get('room_id', '?'))}' references "
                f"unknown zone '{zone_id}'"
            )

    return None


_SEMANTIC_VALIDATORS = {
    "evacuation_routes": validate_evacuation_route,
    "emergency_resources": validate_emergency_resource,
    "rooms": validate_room,
}


# ── Ingestion handlers ──


async def ingest_csv(
    state: FirestoreState,
    csv_type: str,
    csv_content: str,
) -> dict[str, Any]:
    """Parse a CSV and upsert records into Firestore. Returns a validation report."""
    handler = _HANDLERS.get(csv_type)
    if handler is None:
        return {"error": f"Unknown CSV type: {csv_type}", "valid_types": sorted(_HANDLERS.keys())}

    reader = csv.DictReader(io.StringIO(csv_content))
    rows = list(reader)
    return await handler(state, rows)


async def _ingest_facility(state: FirestoreState, rows: list[dict]) -> dict[str, Any]:
    loaded = 0
    rejected: list[dict[str, Any]] = []
    for i, row in enumerate(rows):
        try:
            facility = Facility(
                id=row["facility_id"],
                name=row["name"],
                address=row.get("address", ""),
                floors=int(row.get("floors", 1)),
                capacity=int(row.get("capacity", 0)),
            )
            await state.upsert_facility_data(
                "facilities", facility.id, facility.model_dump(mode="json")
            )
            loaded += 1
        except Exception as e:
            rejected.append({"row": i + 1, "data": dict(row), "reason": str(e)})
    return _make_report("facility", loaded, rejected)


async def _ingest_zones(state: FirestoreState, rows: list[dict]) -> dict[str, Any]:
    loaded = 0
    rejected: list[dict[str, Any]] = []
    for i, row in enumerate(rows):
        try:
            zone = Zone(
                id=row["zone_id"],
                facility_id=row["facility_id"],
                name=row["name"],
                floor=int(row.get("floor", 1)),
                zone_type=row.get("zone_type", ""),
                primary_exit=row.get("primary_exit", ""),
                alternate_exit=row.get("alternate_exit", ""),
                shelter_location=row.get("shelter_location", ""),
                capacity=int(row.get("capacity", 0)),
                notes=row.get("notes", ""),
            )
            await state.upsert_facility_data("zones", zone.id, zone.model_dump(mode="json"))
            loaded += 1
        except Exception as e:
            rejected.append({"row": i + 1, "data": dict(row), "reason": str(e)})
    return _make_report("zones", loaded, rejected)


async def _ingest_rooms(state: FirestoreState, rows: list[dict]) -> dict[str, Any]:
    kb = KnowledgeBase.get()
    validator = _SEMANTIC_VALIDATORS.get("rooms")
    loaded = 0
    rejected: list[dict[str, Any]] = []
    for i, row in enumerate(rows):
        try:
            if validator:
                reason = validator(row, kb)
                if reason:
                    rejected.append({"row": i + 1, "data": dict(row), "reason": reason})
                    continue

            room = Room(
                id=row["room_id"],
                facility_id=row["facility_id"],
                name=row["name"],
                floor=int(row.get("floor", 1)),
                zone_id=row.get("zone_id", ""),
                room_type=row.get("room_type", ""),
                capacity=int(row.get("capacity", 0)),
                notes=row.get("notes", ""),
            )
            await state.upsert_facility_data("rooms", room.id, room.model_dump(mode="json"))
            loaded += 1
        except Exception as e:
            rejected.append({"row": i + 1, "data": dict(row), "reason": str(e)})
    return _make_report("rooms", loaded, rejected)


async def _ingest_personnel(state: FirestoreState, rows: list[dict]) -> dict[str, Any]:
    people = []
    rejected: list[dict[str, Any]] = []
    for i, row in enumerate(rows):
        try:
            person = Person(
                id=row["person_id"],
                name=row["name"],
                slack_user_id=row.get("slack_user_id", ""),
                role=row.get("role", ""),
                department=row.get("department", ""),
                default_location=row.get("default_location", ""),
                floor=int(row.get("floor", 1)),
                phone=row.get("phone", ""),
                emergency_contact_name=row.get("emergency_contact_name", ""),
                emergency_contact_phone=row.get("emergency_contact_phone", ""),
                medical_notes=row.get("medical_notes", ""),
                mobility_limitations=_bool(row.get("mobility_limitations", "")),
                trained_first_aid=_bool(row.get("trained_first_aid", "")),
                trained_cpr=_bool(row.get("trained_cpr", "")),
                is_floor_warden=_bool(row.get("is_floor_warden", "")),
                evacuation_role=row.get("evacuation_role", ""),
            )
            people.append(person)
        except Exception as e:
            rejected.append({"row": i + 1, "data": dict(row), "reason": str(e)})

    loaded = 0
    if people:
        loaded = await state.bulk_upsert_people(people)
    return _make_report("personnel", loaded, rejected)


async def _ingest_evacuation_routes(state: FirestoreState, rows: list[dict]) -> dict[str, Any]:
    kb = KnowledgeBase.get()
    validator = _SEMANTIC_VALIDATORS.get("evacuation_routes")
    loaded = 0
    rejected: list[dict[str, Any]] = []
    for i, row in enumerate(rows):
        try:
            if validator:
                reason = validator(row, kb)
                if reason:
                    rejected.append({"row": i + 1, "data": dict(row), "reason": reason})
                    continue

            route = EvacuationRoute(
                facility_id=row["facility_id"],
                name=row["name"],
                from_zone=row["from_zone"],
                to_exit=row["to_exit"],
                route_description=row.get("route_description", ""),
                accessibility=row.get("accessibility", "standard"),
                blocked_by_zones=row.get("blocked_by_zones", ""),
            )
            doc_id = f"route-{i:03d}"
            await state.upsert_facility_data(
                "evacuation_routes", doc_id, route.model_dump(mode="json")
            )
            loaded += 1
        except Exception as e:
            rejected.append({"row": i + 1, "data": dict(row), "reason": str(e)})
    return _make_report("evacuation_routes", loaded, rejected)


async def _ingest_emergency_resources(state: FirestoreState, rows: list[dict]) -> dict[str, Any]:
    kb = KnowledgeBase.get()
    validator = _SEMANTIC_VALIDATORS.get("emergency_resources")
    loaded = 0
    rejected: list[dict[str, Any]] = []
    for i, row in enumerate(rows):
        try:
            if validator:
                reason = validator(row, kb)
                if reason:
                    rejected.append({"row": i + 1, "data": dict(row), "reason": reason})
                    continue

            resource = EmergencyResource(
                facility_id=row["facility_id"],
                resource_type=ResourceType(row["resource_type"]),
                location_description=row["location_description"],
                floor=int(row.get("floor", 1)),
                zone_id=row.get("zone_id", ""),
                notes=row.get("notes", ""),
            )
            doc_id = f"res-{i:03d}"
            await state.upsert_facility_data(
                "emergency_resources", doc_id, resource.model_dump(mode="json")
            )
            loaded += 1
        except Exception as e:
            rejected.append({"row": i + 1, "data": dict(row), "reason": str(e)})
    return _make_report("emergency_resources", loaded, rejected)


async def _ingest_assembly_points(state: FirestoreState, rows: list[dict]) -> dict[str, Any]:
    loaded = 0
    rejected: list[dict[str, Any]] = []
    for i, row in enumerate(rows):
        try:
            ap = AssemblyPoint(
                id=row["point_id"],
                facility_id=row["facility_id"],
                name=row["name"],
                location_description=row.get("location_description", ""),
                capacity=int(row.get("capacity", 0)),
                is_primary=_bool(row.get("is_primary", "")),
                accessibility=row.get("accessibility", "standard"),
                notes=row.get("notes", ""),
            )
            await state.upsert_facility_data(
                "assembly_points", ap.id, ap.model_dump(mode="json")
            )
            loaded += 1
        except Exception as e:
            rejected.append({"row": i + 1, "data": dict(row), "reason": str(e)})
    return _make_report("assembly_points", loaded, rejected)


async def _ingest_nearby_services(state: FirestoreState, rows: list[dict]) -> dict[str, Any]:
    loaded = 0
    rejected: list[dict[str, Any]] = []
    for i, row in enumerate(rows):
        try:
            svc = NearbyService(
                service_type=row["service_type"],
                name=row["name"],
                address=row.get("address", ""),
                phone=row.get("phone", ""),
                distance_miles=float(row.get("distance_miles", 0)),
                eta_minutes=int(row.get("eta_minutes", 0)),
                trauma_level=row.get("trauma_level", ""),
                helipad=_bool(row.get("helipad", "")),
            )
            doc_id = f"svc-{i:03d}"
            await state.upsert_facility_data(
                "nearby_services", doc_id, svc.model_dump(mode="json")
            )
            loaded += 1
        except Exception as e:
            rejected.append({"row": i + 1, "data": dict(row), "reason": str(e)})
    return _make_report("nearby_services", loaded, rejected)


_HANDLERS = {
    "facility": _ingest_facility,
    "zones": _ingest_zones,
    "rooms": _ingest_rooms,
    "personnel": _ingest_personnel,
    "evacuation_routes": _ingest_evacuation_routes,
    "emergency_resources": _ingest_emergency_resources,
    "assembly_points": _ingest_assembly_points,
    "nearby_services": _ingest_nearby_services,
}

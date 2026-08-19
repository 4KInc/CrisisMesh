"""CSV ingestion service — imports organizational data into Firestore knowledge base."""

from __future__ import annotations

import csv
import io
import uuid
from typing import Any

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


def _bool(val: str) -> bool:
    return val.strip().lower() in ("true", "yes", "1")


async def ingest_csv(
    state: FirestoreState,
    csv_type: str,
    csv_content: str,
) -> dict[str, Any]:
    """Parse a CSV and upsert records into Firestore. Returns a summary."""
    handler = _HANDLERS.get(csv_type)
    if handler is None:
        return {"error": f"Unknown CSV type: {csv_type}", "valid_types": sorted(_HANDLERS.keys())}

    reader = csv.DictReader(io.StringIO(csv_content))
    rows = list(reader)
    return await handler(state, rows)


async def _ingest_facility(state: FirestoreState, rows: list[dict]) -> dict[str, Any]:
    for row in rows:
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
    return {"type": "facility", "records_imported": len(rows)}


async def _ingest_zones(state: FirestoreState, rows: list[dict]) -> dict[str, Any]:
    for row in rows:
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
    return {"type": "zones", "records_imported": len(rows)}


async def _ingest_rooms(state: FirestoreState, rows: list[dict]) -> dict[str, Any]:
    for row in rows:
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
    return {"type": "rooms", "records_imported": len(rows)}


async def _ingest_personnel(state: FirestoreState, rows: list[dict]) -> dict[str, Any]:
    people = []
    for row in rows:
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

    count = await state.bulk_upsert_people(people)
    return {"type": "personnel", "records_imported": count}


async def _ingest_evacuation_routes(state: FirestoreState, rows: list[dict]) -> dict[str, Any]:
    for i, row in enumerate(rows):
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
    return {"type": "evacuation_routes", "records_imported": len(rows)}


async def _ingest_emergency_resources(state: FirestoreState, rows: list[dict]) -> dict[str, Any]:
    for i, row in enumerate(rows):
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
    return {"type": "emergency_resources", "records_imported": len(rows)}


async def _ingest_assembly_points(state: FirestoreState, rows: list[dict]) -> dict[str, Any]:
    for row in rows:
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
    return {"type": "assembly_points", "records_imported": len(rows)}


async def _ingest_nearby_services(state: FirestoreState, rows: list[dict]) -> dict[str, Any]:
    for i, row in enumerate(rows):
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
    return {"type": "nearby_services", "records_imported": len(rows)}


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

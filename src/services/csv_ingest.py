"""CSV ingestion service — imports organizational data into Firestore knowledge base."""

from __future__ import annotations

import csv
import io
import uuid
from typing import Any

from src.models.facility import Facility, Resource, ResourceType, Room, Route
from src.models.person import AccessibilityFlag, Person
from src.services.firestore_state import FirestoreState


CSV_TYPES = {
    "facilities",
    "rooms",
    "people",
    "roles",
    "resources",
    "routes",
    "utilities",
    "hazmat",
    "assembly_points",
    "emergency_exits",
    "runbooks",
    "vendors",
    "on_call",
    "medical_flags",
    "accessibility",
    "incident_history",
    "playbooks",
}


async def ingest_csv(
    state: FirestoreState,
    csv_type: str,
    csv_content: str,
    facility_id: str = "",
) -> dict[str, Any]:
    """Parse a CSV and upsert records into Firestore. Returns a summary."""
    if csv_type not in CSV_TYPES:
        return {"error": f"Unknown CSV type: {csv_type}", "valid_types": sorted(CSV_TYPES)}

    reader = csv.DictReader(io.StringIO(csv_content))
    rows = list(reader)

    handler = _HANDLERS.get(csv_type)
    if handler is None:
        # Generic: store raw rows in the collection
        for row in rows:
            doc_id = row.get("id", str(uuid.uuid4()))
            row["facility_id"] = facility_id or row.get("facility_id", "")
            await state.upsert_facility_data(csv_type, doc_id, row)
        return {"type": csv_type, "records_imported": len(rows)}

    return await handler(state, rows, facility_id)


async def _ingest_people(
    state: FirestoreState, rows: list[dict], facility_id: str
) -> dict[str, Any]:
    people = []
    for row in rows:
        flags = []
        if row.get("accessibility_flags"):
            for f in row["accessibility_flags"].split(";"):
                f = f.strip().lower()
                if f and f in AccessibilityFlag.__members__.values():
                    flags.append(AccessibilityFlag(f))

        person = Person(
            id=row.get("id", str(uuid.uuid4())),
            name=row.get("name", ""),
            role=row.get("role", ""),
            email=row.get("email", ""),
            phone=row.get("phone", ""),
            facility_id=facility_id or row.get("facility_id", ""),
            room_id=row.get("room_id", ""),
            department=row.get("department", ""),
            accessibility_flags=flags,
            medical_notes=row.get("medical_notes", ""),
            emergency_contact=row.get("emergency_contact", ""),
            is_on_call=row.get("is_on_call", "").lower() in ("true", "yes", "1"),
        )
        people.append(person)

    count = await state.bulk_upsert_people(people)
    return {"type": "people", "records_imported": count}


async def _ingest_rooms(
    state: FirestoreState, rows: list[dict], facility_id: str
) -> dict[str, Any]:
    for row in rows:
        room = Room(
            id=row.get("id", str(uuid.uuid4())),
            name=row.get("name", ""),
            facility_id=facility_id or row.get("facility_id", ""),
            floor=int(row.get("floor", 1)),
            building=row.get("building", ""),
            capacity=int(row.get("capacity", 0)),
            room_type=row.get("room_type", ""),
        )
        await state.upsert_facility_data("rooms", room.id, room.model_dump(mode="json"))
    return {"type": "rooms", "records_imported": len(rows)}


async def _ingest_resources(
    state: FirestoreState, rows: list[dict], facility_id: str
) -> dict[str, Any]:
    for row in rows:
        resource = Resource(
            id=row.get("id", str(uuid.uuid4())),
            facility_id=facility_id or row.get("facility_id", ""),
            type=ResourceType(row.get("type", "aed")),
            name=row.get("name", ""),
            location=row.get("location", ""),
            room_id=row.get("room_id", ""),
            floor=int(row.get("floor", 1)),
            notes=row.get("notes", ""),
        )
        await state.upsert_facility_data(
            "resources", resource.id, resource.model_dump(mode="json")
        )
    return {"type": "resources", "records_imported": len(rows)}


async def _ingest_routes(
    state: FirestoreState, rows: list[dict], facility_id: str
) -> dict[str, Any]:
    for row in rows:
        route = Route(
            id=row.get("id", str(uuid.uuid4())),
            facility_id=facility_id or row.get("facility_id", ""),
            name=row.get("name", ""),
            from_zone=row.get("from_zone", ""),
            to_zone=row.get("to_zone", ""),
            route_type=row.get("route_type", "evacuation"),
            is_accessible=row.get("is_accessible", "true").lower() in ("true", "yes", "1"),
            status=row.get("status", "open"),
            notes=row.get("notes", ""),
        )
        await state.upsert_facility_data("routes", route.id, route.model_dump(mode="json"))
    return {"type": "routes", "records_imported": len(rows)}


_HANDLERS = {
    "people": _ingest_people,
    "rooms": _ingest_rooms,
    "resources": _ingest_resources,
    "routes": _ingest_routes,
}

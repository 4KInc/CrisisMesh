"""Firestore-backed state management for incidents, people, facilities, and audit logs."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from google.cloud import firestore

from src.models.events import Event, EventType
from src.models.incident import Incident, IncidentStatus
from src.models.person import Person, PersonStatus


class FirestoreState:
    """Manages all CrisisMesh state in Firestore."""

    def __init__(self, project: str | None = None, database: str = "(default)") -> None:
        self.db = firestore.AsyncClient(project=project, database=database)

    # ── Incidents ──

    async def create_incident(self, incident: Incident) -> Incident:
        doc_ref = self.db.collection("incidents").document(incident.id)
        await doc_ref.set(incident.model_dump(mode="json"))
        await self._append_audit_log(
            incident_id=incident.id,
            event_type=EventType.INCIDENT_DECLARED,
            agent_id="coordinator",
            data={"type": incident.type, "severity": incident.severity},
        )
        return incident

    async def get_incident(self, incident_id: str) -> Incident | None:
        doc = await self.db.collection("incidents").document(incident_id).get()
        if doc.exists:
            return Incident(**doc.to_dict())
        return None

    async def update_incident(
        self, incident_id: str, updates: dict[str, Any]
    ) -> Incident | None:
        updates["updated_at"] = datetime.now(timezone.utc).isoformat()
        doc_ref = self.db.collection("incidents").document(incident_id)
        await doc_ref.update(updates)
        await self._append_audit_log(
            incident_id=incident_id,
            event_type=EventType.INCIDENT_UPDATED,
            agent_id="coordinator",
            data=updates,
        )
        return await self.get_incident(incident_id)

    async def list_active_incidents(self) -> list[Incident]:
        query = (
            self.db.collection("incidents")
            .where(filter=firestore.FieldFilter("status", "in", [
                IncidentStatus.DECLARED,
                IncidentStatus.ACTIVE,
                IncidentStatus.COORDINATING,
            ]))
            .order_by("created_at", direction=firestore.Query.DESCENDING)
        )
        docs = query.stream()
        return [Incident(**doc.to_dict()) async for doc in docs]

    # ── People / Accountability ──

    async def upsert_person(self, person: Person) -> None:
        doc_ref = self.db.collection("people").document(person.id)
        await doc_ref.set(person.model_dump(mode="json"), merge=True)

    async def bulk_upsert_people(self, people: list[Person]) -> int:
        batch = self.db.batch()
        for person in people:
            ref = self.db.collection("people").document(person.id)
            batch.set(ref, person.model_dump(mode="json"), merge=True)
        await batch.commit()
        return len(people)

    async def get_person(self, person_id: str) -> Person | None:
        doc = await self.db.collection("people").document(person_id).get()
        if doc.exists:
            return Person(**doc.to_dict())
        return None

    async def get_people_by_facility(self, facility_id: str) -> list[Person]:
        query = self.db.collection("people").where(
            filter=firestore.FieldFilter("facility_id", "==", facility_id)
        )
        docs = query.stream()
        return [Person(**doc.to_dict()) async for doc in docs]

    async def update_person_status(
        self,
        person_id: str,
        incident_id: str,
        status: PersonStatus,
    ) -> None:
        # Store per-incident status in a subcollection
        await self.db.collection("incidents").document(incident_id).collection(
            "accountability"
        ).document(person_id).set(
            {
                "person_id": person_id,
                "status": status,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
            merge=True,
        )

    async def get_accountability_summary(
        self, incident_id: str
    ) -> dict[str, list[str]]:
        """Returns {status: [person_ids]} for an incident."""
        result: dict[str, list[str]] = {s: [] for s in PersonStatus}
        docs = (
            self.db.collection("incidents")
            .document(incident_id)
            .collection("accountability")
            .stream()
        )
        async for doc in docs:
            data = doc.to_dict()
            status = data.get("status", PersonStatus.UNKNOWN)
            result.setdefault(status, []).append(data["person_id"])
        return result

    # ── Knowledge Base (facilities, rooms, resources, routes) ──

    async def upsert_facility_data(self, collection: str, doc_id: str, data: dict) -> None:
        await self.db.collection(collection).document(doc_id).set(data, merge=True)

    async def query_knowledge_base(
        self, collection: str, filters: dict[str, Any] | None = None
    ) -> list[dict]:
        query = self.db.collection(collection)
        if filters:
            for field, value in filters.items():
                query = query.where(filter=firestore.FieldFilter(field, "==", value))
        docs = query.stream()
        return [doc.to_dict() async for doc in docs]

    # ── Audit Log (append-only) ──

    async def _append_audit_log(
        self,
        incident_id: str,
        event_type: EventType,
        agent_id: str,
        data: dict[str, Any] | None = None,
    ) -> str:
        event_id = str(uuid.uuid4())
        event = Event(
            id=event_id,
            type=event_type,
            incident_id=incident_id,
            agent_id=agent_id,
            data=data or {},
        )
        await (
            self.db.collection("audit_log")
            .document(event_id)
            .set(event.model_dump(mode="json"))
        )
        return event_id

    async def get_audit_trail(self, incident_id: str) -> list[Event]:
        query = (
            self.db.collection("audit_log")
            .where(filter=firestore.FieldFilter("incident_id", "==", incident_id))
            .order_by("timestamp")
        )
        docs = query.stream()
        return [Event(**doc.to_dict()) async for doc in docs]

    # ── Memory Bank (cross-session organizational memory) ──

    async def store_lesson(self, incident_id: str, lesson: dict[str, Any]) -> str:
        lesson_id = str(uuid.uuid4())
        lesson["id"] = lesson_id
        lesson["incident_id"] = incident_id
        lesson["created_at"] = datetime.now(timezone.utc).isoformat()
        await self.db.collection("lessons").document(lesson_id).set(lesson)
        return lesson_id

    async def find_similar_lessons(
        self, incident_type: str, limit: int = 5
    ) -> list[dict]:
        query = (
            self.db.collection("lessons")
            .where(filter=firestore.FieldFilter("incident_type", "==", incident_type))
            .order_by("created_at", direction=firestore.Query.DESCENDING)
            .limit(limit)
        )
        docs = query.stream()
        return [doc.to_dict() async for doc in docs]

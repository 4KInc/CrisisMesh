"""Memory Bank — persistent cross-session organizational memory.

Stores lessons learned, incident outcomes, and approved playbook notes.
In production, persists to Firestore. Locally, uses an in-memory store
with pre-seeded lessons for demo.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any


class MemoryBank:
    """Singleton cross-session memory store."""

    _instance: MemoryBank | None = None

    def __init__(self) -> None:
        self.lessons: list[dict[str, Any]] = []
        self.playbook_changes: list[dict[str, Any]] = []
        self.incident_outcomes: list[dict[str, Any]] = []

    @classmethod
    def get(cls) -> MemoryBank:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        cls._instance = None

    def store_lesson(
        self,
        incident_id: str,
        incident_type: str,
        facility_id: str,
        title: str,
        body: str,
        category: str = "general",
        tags: list[str] | None = None,
    ) -> str:
        lesson_id = str(uuid.uuid4())
        lesson = {
            "id": lesson_id,
            "incident_id": incident_id,
            "incident_type": incident_type,
            "facility_id": facility_id,
            "title": title,
            "body": body,
            "category": category,
            "tags": tags or [],
            "stored_at": datetime.now(timezone.utc).isoformat(),
            "approved": True,
        }
        self.lessons.append(lesson)
        return lesson_id

    def find_lessons(
        self,
        incident_type: str = "",
        facility_id: str = "",
        category: str = "",
        tags: list[str] | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        results = list(self.lessons)
        if incident_type:
            results = [l for l in results if l["incident_type"] == incident_type]
        if facility_id:
            facility_results = [l for l in results if l["facility_id"] == facility_id]
            if facility_results:
                results = facility_results
        if category:
            results = [l for l in results if l["category"] == category]
        if tags:
            tag_set = set(tags)
            results = [l for l in results if tag_set & set(l.get("tags", []))]

        results.sort(key=lambda x: x.get("stored_at", ""), reverse=True)
        return results[:limit]

    def store_incident_outcome(
        self,
        incident_id: str,
        incident_type: str,
        facility_id: str,
        total_personnel: int,
        accounted: int,
        response_time_seconds: int,
        resolved: bool,
        summary: str,
    ) -> str:
        outcome_id = str(uuid.uuid4())
        outcome = {
            "id": outcome_id,
            "incident_id": incident_id,
            "incident_type": incident_type,
            "facility_id": facility_id,
            "total_personnel": total_personnel,
            "accounted": accounted,
            "response_time_seconds": response_time_seconds,
            "resolved": resolved,
            "summary": summary,
            "stored_at": datetime.now(timezone.utc).isoformat(),
        }
        self.incident_outcomes.append(outcome)
        return outcome_id

    def get_outcome_stats(self, incident_type: str = "") -> dict[str, Any]:
        outcomes = self.incident_outcomes
        if incident_type:
            outcomes = [o for o in outcomes if o["incident_type"] == incident_type]
        if not outcomes:
            return {"total_incidents": 0}

        avg_response = sum(o["response_time_seconds"] for o in outcomes) / len(outcomes)
        total_personnel = sum(o["total_personnel"] for o in outcomes)
        total_accounted = sum(o["accounted"] for o in outcomes)

        return {
            "total_incidents": len(outcomes),
            "avg_response_time_seconds": round(avg_response),
            "total_personnel_tracked": total_personnel,
            "total_accounted": total_accounted,
            "accountability_rate": round(total_accounted / total_personnel * 100, 1) if total_personnel else 0,
        }

    def save_to_file(self, filepath: str) -> None:
        data = {
            "lessons": self.lessons,
            "playbook_changes": self.playbook_changes,
            "incident_outcomes": self.incident_outcomes,
        }
        with open(filepath, "w") as f:
            json.dump(data, f, indent=2)

    def load_from_file(self, filepath: str) -> None:
        if not os.path.exists(filepath):
            return
        with open(filepath) as f:
            data = json.load(f)
        self.lessons = data.get("lessons", [])
        self.playbook_changes = data.get("playbook_changes", [])
        self.incident_outcomes = data.get("incident_outcomes", [])


# ── Pre-seeded lessons for demo ──

_SEED_LESSONS = [
    {
        "incident_id": "FIRE-2025-DRILL-001",
        "incident_type": "fire",
        "facility_id": "jefferson",
        "title": "Floor 2 west stairwell bottleneck during fire drill",
        "body": (
            "During the October 2025 fire drill, Floor 2 West Wing evacuation took 4:30 "
            "to fully clear. The west stairwell created a bottleneck when Room 215 (Science Lab) "
            "and Room 210 evacuated simultaneously. Recommend staggering Room 215 evacuation 30 seconds "
            "before Room 210 to avoid congestion. Also noted: Mrs. Thompson (knee replacement) "
            "needed elevator evacuation — the key was not immediately accessible from Floor 2."
        ),
        "category": "evacuation",
        "tags": ["fire", "floor2", "stairwell", "bottleneck", "science_lab", "accessibility"],
    },
    {
        "incident_id": "FIRE-2025-DRILL-001",
        "incident_type": "fire",
        "facility_id": "jefferson",
        "title": "Elevator key should be pre-staged on Floor 2 for mobility evacuations",
        "body": (
            "During the October drill, it took 2 minutes to retrieve the elevator key from "
            "the main office (Floor 1) for Mrs. Thompson's evacuation from Floor 2. "
            "Approved change: a duplicate elevator key is now stored in Room 201 (Floor Warden "
            "Mrs. Nguyen's classroom). Verified with Principal Johnson."
        ),
        "category": "accessibility",
        "tags": ["fire", "elevator", "accessibility", "mobility", "key"],
    },
    {
        "incident_id": "FIRE-2025-DRILL-002",
        "incident_type": "fire",
        "facility_id": "jefferson",
        "title": "Science lab gas shutoff must be verified before evacuation clearance",
        "body": (
            "During the January 2026 drill, the science lab gas shutoff valve (Room 215 east wall) "
            "was not verified as closed before the all-clear. Dr. Franklin confirmed the gas line was "
            "off, but no formal check was in the drill procedure. Added to the fire playbook: "
            "Floor Warden must confirm science lab gas shutoff before reporting Floor 2 West clear."
        ),
        "category": "playbook",
        "tags": ["fire", "gas_shutoff", "science_lab", "floor2", "hazmat"],
    },
    {
        "incident_id": "WEATHER-2025-001",
        "incident_type": "severe_weather",
        "facility_id": "jefferson",
        "title": "Shelter-in-place locations should be marked with signage",
        "body": (
            "During the March 2026 tornado warning, staff in the East Wing Floor 2 were unsure "
            "which interior hallway was the designated shelter. The zone data specifies 'Interior hallway C' "
            "but there's no physical signage. Recommend adding shelter location signs at hallway entrances."
        ),
        "category": "facilities",
        "tags": ["severe_weather", "shelter", "signage", "floor2"],
    },
    {
        "incident_id": "MEDICAL-2025-001",
        "incident_type": "medical",
        "facility_id": "jefferson",
        "title": "AED response time from west wing was over 3 minutes",
        "body": (
            "A student fainted in Room 112 (West Wing Floor 1). The nearest AED is in Hallway B "
            "outside Room 112, which was correct and accessible. However, the responding teacher "
            "didn't know the AED location. Recommend: AED location posters in every classroom "
            "and quarterly AED awareness refreshers for all staff."
        ),
        "category": "resources",
        "tags": ["medical", "aed", "west_wing", "training"],
    },
]


def init_memory_bank() -> MemoryBank:
    """Initialize the Memory Bank singleton with pre-seeded lessons."""
    mb = MemoryBank.get()
    if not mb.lessons:
        for seed in _SEED_LESSONS:
            mb.store_lesson(**seed)
        # Store a demo outcome
        mb.store_incident_outcome(
            incident_id="FIRE-2025-DRILL-001",
            incident_type="fire",
            facility_id="jefferson",
            total_personnel=34,
            accounted=34,
            response_time_seconds=270,
            resolved=True,
            summary="Fire drill completed. All 34 personnel accounted for in 4:30. "
                    "West stairwell bottleneck identified as improvement area.",
        )
        mb.store_incident_outcome(
            incident_id="FIRE-2025-DRILL-002",
            incident_type="fire",
            facility_id="jefferson",
            total_personnel=34,
            accounted=34,
            response_time_seconds=240,
            resolved=True,
            summary="Fire drill completed. All 34 personnel accounted for in 4:00. "
                    "Gas shutoff verification gap identified.",
        )
    return mb

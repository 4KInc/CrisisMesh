"""Memory Bank — persistent cross-session organizational memory.

Stores lessons learned, incident outcomes, and approved playbook notes.

Backend selected by MEMORY_BACKEND, same facade shape as ContentScanner:

  * `vertex`  — managed Vertex AI Memory Bank (Agent Engine). Lessons live in a
                Google-managed store and are retrieved by semantic similarity
                search, so recall crosses processes and instances because the
                store is outside all of them.
  * `local`   — in-process store with seeded lessons (default). Offline, no GCP.

A managed backend that fails to initialise falls back to local rather than
losing the feature, and a retrieval that raises falls back rather than returning
an empty list — "no prior lessons" is a claim, and a backend that is down has
not established it.

The two backends do not compute the same number. The local store ranks by
Jaccard tag overlap; the managed store returns a vector distance from similarity
search. Both surface as "confidence", so every result carries the basis that
produced it. Presenting one as the other would be the same class of claim this
system spends its effort refusing to make.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

BASIS_JACCARD = "jaccard_tag_overlap"
BASIS_VECTOR = "vector_similarity"

# Partitions CrisisMesh memories inside a shared Agent Engine.
#
# Fixed, and deliberately one key. Scope matching is exact on the whole map, not
# a subset — a memory stored with {app, incident_id, facility_id} is invisible
# to a query for {app}. Putting metadata in scope would therefore make every
# lesson retrievable only by someone who already knew its incident id, which is
# the opposite of recall.
MEMORY_SCOPE = {"app": "crisismesh"}

# Vertex Memory Bank persists `fact` and `scope` and nothing else — display_name
# and description come back empty. The structured record rides at the end of the
# fact behind this marker, after the sentence, so the text the embedding is built
# from still leads with what the lesson actually says.
_RECORD_MARKER = "\n\u27eacrisismesh\u27eb"


class LocalMemoryBank:
    """In-process lesson store. The offline backend and the fallback."""

    def __init__(self) -> None:
        self.lessons: list[dict[str, Any]] = []
        self.playbook_changes: list[dict[str, Any]] = []
        self.incident_outcomes: list[dict[str, Any]] = []

    def store_lesson(
        self,
        incident_id: str,
        incident_type: str,
        facility_id: str,
        title: str,
        body: str,
        category: str = "general",
        tags: list[str] | None = None,
        seeded: bool = False,
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
            # Fixture rather than something the system learned. Rendered
            # identically to a real recall, a seed claims experience this
            # deployment has not had.
            "seeded": seeded,
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

class VertexMemoryBank:
    """Managed Vertex AI Memory Bank (Agent Engine `reasoningEngines/*/memories`).

    A lesson becomes one Memory: `fact` carries the sentence a responder reads,
    `description` carries the structured record as JSON so the citation survives
    the round trip, and `scope` partitions CrisisMesh's memories inside the
    engine. Retrieval is a similarity search, not a scan — which is the point of
    moving off tag matching, and also why the confidence it returns is a
    distance rather than an overlap.

    Outcomes and playbook changes stay local: they are counters and proposals,
    not things anyone searches semantically.
    """

    def __init__(self) -> None:
        self.engine = os.environ.get("VERTEX_MEMORY_ENGINE", "").strip()
        if not self.engine:
            # Guessing an engine would write lessons into a resource nobody
            # named, and read them back from one that may not be the same.
            raise ValueError("VERTEX_MEMORY_ENGINE is not set")
        self.location = os.environ.get("GOOGLE_CLOUD_REGION", "us-central1")
        self._client = self._build_client()
        self._types = self._build_types()
        # Not searched semantically, so they stay beside the managed store.
        self.playbook_changes: list[dict[str, Any]] = []
        self.incident_outcomes: list[dict[str, Any]] = []

    def _build_client(self) -> Any:
        from google.cloud import aiplatform_v1beta1 as v1beta1

        return v1beta1.MemoryBankServiceClient(
            client_options={"api_endpoint": f"{self.location}-aiplatform.googleapis.com"})

    def _build_types(self) -> Any:
        from google.cloud import aiplatform_v1beta1 as v1beta1

        return v1beta1

    def store_lesson(
        self,
        incident_id: str,
        incident_type: str,
        facility_id: str,
        title: str,
        body: str,
        category: str = "general",
        tags: list[str] | None = None,
        seeded: bool = False,
    ) -> str:
        lesson_id = str(uuid.uuid4())
        record = {
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
            # Fixture rather than something the system learned. Rendered
            # identically to a real recall, a seed claims experience this
            # deployment has not had.
            "seeded": seeded,
        }
        # Sentence first, record last: this whole string is what gets embedded,
        # so the semantics have to lead.
        sentence = f"[{incident_type}] {title}. {body} (tags: {', '.join(tags or [])})"
        memory = self._types.Memory(
            fact=sentence + _RECORD_MARKER + json.dumps(record),
            scope=dict(MEMORY_SCOPE),
        )
        self._client.create_memory(parent=self.engine, memory=memory).result(timeout=120)
        return lesson_id

    def find_lessons(
        self,
        incident_type: str = "",
        facility_id: str = "",
        category: str = "",
        tags: list[str] | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        query = " ".join(filter(None, [
            incident_type, facility_id, category, " ".join(tags or [])])) or "incident lesson"
        request = self._types.RetrieveMemoriesRequest(
            parent=self.engine,
            scope=dict(MEMORY_SCOPE),
            similarity_search_params=self._types.RetrieveMemoriesRequest
                .SimilaritySearchParams(search_query=query, top_k=max(1, limit)),
        )
        response = self._client.retrieve_memories(request=request)

        lessons: list[dict[str, Any]] = []
        for retrieved in response.retrieved_memories:
            fact = retrieved.memory.fact or ""
            if _RECORD_MARKER not in fact:
                # Not written by CrisisMesh, or written before this format.
                # Skipping one row is not a reason to lose the rest.
                logger.info("Skipping a managed memory with no CrisisMesh record")
                continue
            try:
                record = json.loads(fact.split(_RECORD_MARKER, 1)[1])
            except Exception:  # noqa: BLE001
                logger.warning("Skipping a managed memory with unreadable metadata")
                continue
            distance = getattr(retrieved, "distance", None)
            record["retrieval_distance"] = (
                float(distance) if distance is not None else None)
            record["retrieval_confidence"] = _confidence_from_distance(distance)
            record["retrieval_basis"] = BASIS_VECTOR
            lessons.append(record)

        # The managed store ranks; filters that it cannot express are applied
        # here without reordering what it returned.
        if category:
            lessons = [x for x in lessons if x.get("category") == category]
        return lessons[:limit]

    def store_incident_outcome(self, *args: Any, **kwargs: Any) -> str:
        return LocalMemoryBank.store_incident_outcome(self, *args, **kwargs)

    def get_outcome_stats(self, incident_type: str = "") -> dict[str, Any]:
        return LocalMemoryBank.get_outcome_stats(self, incident_type)


def _confidence_from_distance(distance: Any) -> float:
    """A similarity distance rendered as a 0-1 number, ordering-only.

    Measured against the live API: the closest match to a well-aimed query came
    back at 0.8345 and an unrelated lesson at 1.0489, so this renders a correct
    top hit as 0.166 and a miss as 0. The ordering is real; the magnitude is not
    comparable to a Jaccard overlap, and the raw distance is kept alongside it so
    a reader can see what it was derived from.
    """
    try:
        value = float(distance)
    except (TypeError, ValueError):
        return 0.0
    return round(max(0.0, min(1.0, 1.0 - value)), 3)


class MemoryBank:
    """Facade routing to the active backend, chosen by MEMORY_BACKEND."""

    _instance: MemoryBank | None = None

    def __init__(self) -> None:
        requested = (os.environ.get("MEMORY_BACKEND") or "local").strip().lower()
        self._backend = "local"
        self._store: Any = LocalMemoryBank()
        if requested == "vertex":
            try:
                self._store = VertexMemoryBank()
                self._backend = "vertex"
                logger.info("Memory Bank: managed Vertex AI Memory Bank enabled")
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    f"Memory Bank: managed init failed, falling back to local: {exc}")

    @property
    def backend(self) -> str:
        return self._backend

    @classmethod
    def get(cls) -> MemoryBank:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        cls._instance = None

    # ── Attributes callers still read directly ──
    @property
    def lessons(self) -> list[dict[str, Any]]:
        return getattr(self._store, "lessons", [])

    @property
    def incident_outcomes(self) -> list[dict[str, Any]]:
        return self._store.incident_outcomes

    @property
    def playbook_changes(self) -> list[dict[str, Any]]:
        return self._store.playbook_changes

    def store_lesson(self, *args: Any, **kwargs: Any) -> str:
        try:
            return self._store.store_lesson(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Managed lesson write failed, keeping it locally: {exc}")
            return self._fallback().store_lesson(*args, **kwargs)

    def find_lessons(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        try:
            return self._store.find_lessons(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            # An empty list reads as "no prior lessons", which is a claim. A
            # backend that is down has not established it.
            logger.error(f"Managed lesson read failed, falling back to local: {exc}")
            return self._fallback().find_lessons(*args, **kwargs)

    def store_incident_outcome(self, *args: Any, **kwargs: Any) -> str:
        return self._store.store_incident_outcome(*args, **kwargs)

    def get_outcome_stats(self, incident_type: str = "") -> dict[str, Any]:
        return self._store.get_outcome_stats(incident_type)

    def save_to_file(self, filepath: str) -> None:
        self._fallback().save_to_file(filepath)

    def load_from_file(self, filepath: str) -> None:
        self._fallback().load_from_file(filepath)

    _fallback_store: LocalMemoryBank | None = None

    def _fallback(self) -> LocalMemoryBank:
        """The local store, seeded, kept ready for whenever managed cannot answer."""
        if isinstance(self._store, LocalMemoryBank):
            return self._store
        if self._fallback_store is None:
            self._fallback_store = LocalMemoryBank()
            _seed(self._fallback_store)
        return self._fallback_store


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


def _seed(store: LocalMemoryBank) -> None:
    """Load the demo lessons into a local store."""
    if store.lessons:
        return
    for seed in _SEED_LESSONS:
        store.store_lesson(**seed, seeded=True)


def _already_seeded(mb: MemoryBank) -> bool:
    """Whether the active store already holds lessons.

    The old check read `mb.lessons`, which is the local store's list. The
    managed adapter has no such list, so the check read empty on every cold
    start and seeded again: eleven restarts put fifty-five memories in the
    Agent Engine, eleven copies of each seed, and similarity search then
    returned five hits that were all the same lesson.

    An unreadable store is treated as seeded. Failing to seed is a demo without
    prior lessons; seeding on a failed read writes duplicates that then need
    finding and deleting.
    """
    if getattr(mb, "lessons", None):
        return True
    try:
        return bool(mb.find_lessons(limit=1))
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Could not check whether the Memory Bank is seeded ({exc}); "
                       "not seeding")
        return True


def init_memory_bank() -> MemoryBank:
    """Initialize the Memory Bank singleton with pre-seeded lessons."""
    mb = MemoryBank.get()
    if not _already_seeded(mb):
        for seed in _SEED_LESSONS:
            mb.store_lesson(**seed, seeded=True)
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

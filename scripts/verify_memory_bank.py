"""Prove the managed Vertex AI Memory Bank round-trips, against the real API.

Not a test: it talks to Google. Run it after granting the Reasoning Engine
service agent permission to call the embedding model (see docs/PILLARS.md), to
show that a lesson written in one process is retrieved by semantic similarity
in another — which is the claim the Memory Bank pillar rests on.

    MEMORY_BACKEND=vertex \
    VERTEX_MEMORY_ENGINE=projects/…/locations/…/reasoningEngines/… \
    python scripts/verify_memory_bank.py
"""

from __future__ import annotations

import os
import sys

from src.core.memory_bank import MemoryBank

LESSON = {
    "incident_id": "FIRE-2025-011",
    "incident_type": "fire",
    "facility_id": "jefferson",
    "title": "Pre-stage the Floor 2 elevator key",
    "body": ("During a fire evacuation the Floor 2 elevator key was in the main "
             "office. Retrieving it cost four minutes while a staff member who "
             "could not use the stairs waited."),
    "category": "accessibility",
    "tags": ["mobility", "elevator", "evacuation"],
}

# Deliberately shares no tag vocabulary with the lesson: a tag-overlap store
# scores this zero, so a hit here is the managed semantic search working.
QUERY = "someone who cannot use stairs is stuck on an upper floor during a fire"


def main() -> int:
    if os.environ.get("MEMORY_BACKEND", "").lower() != "vertex":
        print("Set MEMORY_BACKEND=vertex to verify the managed path.")
        return 2

    MemoryBank.reset()
    writer = MemoryBank.get()
    if writer.backend != "vertex":
        print("FAIL: the managed backend did not initialise; the facade fell back "
              "to local. Check VERTEX_MEMORY_ENGINE and credentials.")
        return 1

    lesson_id = writer.store_lesson(**LESSON)
    print(f"  wrote lesson {lesson_id} to the managed store")

    # A separate process would build its own singleton. This is the same thing:
    # nothing about the read reuses the writer's state.
    MemoryBank.reset()
    reader = MemoryBank.get()
    found = reader.find_lessons(incident_type="fire", tags=QUERY.split(), limit=5)

    if not found:
        print("FAIL: nothing came back from the managed store")
        return 1

    top = found[0]
    print(f"  recalled  : {top['title']!r}")
    print(f"  incident  : {top['incident_id']}")
    print(f"  confidence: {top.get('retrieval_confidence')} "
          f"(basis: {top.get('retrieval_basis')})")

    if top.get("retrieval_basis") != "vector_similarity":
        print("FAIL: the result did not come from similarity search")
        return 1
    print("\n  Cross-session recall through the managed path: verified.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

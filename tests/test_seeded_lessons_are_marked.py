"""A seeded lesson must not look like one the system learned.

The console panel is headed PRIOR LESSONS and shows an incident id beside each
one. The five that ship with the repo are fixtures with invented ids, and
rendered identically to a real recall they claim the system has learned
something from experience that it has not.

The roster is fiction too, but nobody mistakes "Principal Johnson" for a real
person. "FIRE-2025-DRILL-001, elevator key should be pre-staged" reads exactly
like a real after-action finding, which is the point of it and also the problem.
"""

import pytest

from src.core.memory_bank import MemoryBank, _SEED_LESSONS, init_memory_bank


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    monkeypatch.delenv("MEMORY_BACKEND", raising=False)
    MemoryBank.reset()
    yield
    MemoryBank.reset()


class TestSeededLessonsSayTheyAreSeeded:
    def test_every_seed_carries_the_flag(self):
        bank = init_memory_bank()
        seeded = [l for l in bank.find_lessons(limit=50) if l.get("seeded")]
        assert len(seeded) == len(_SEED_LESSONS)

    def test_a_lesson_stored_from_a_real_incident_is_not_flagged(self):
        bank = init_memory_bank()
        bank.store_lesson("ACTIVE_THREAT-2026-1", "active_threat", "jefferson",
                          "Something learned in an actual run", "Body", tags=["x"])
        found = [l for l in bank.find_lessons(limit=50)
                 if l["incident_id"] == "ACTIVE_THREAT-2026-1"]
        assert found and not found[0].get("seeded")

    def test_the_tool_passes_the_flag_through(self):
        """The console renders what find_similar_incidents returns."""
        from src.agents.learning.tools import find_similar_incidents

        init_memory_bank()
        lessons = find_similar_incidents("fire", "jefferson")["lessons"]
        assert lessons
        assert any(l.get("seeded") for l in lessons)

    def test_the_console_shows_the_marker(self):
        import pathlib

        html = (pathlib.Path(__file__).resolve().parent.parent
                / "static" / "index.html").read_text()
        assert "seeded" in html, "the panel does not distinguish seeded lessons"

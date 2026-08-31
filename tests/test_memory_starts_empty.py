"""A deployment that has learned nothing must recall nothing.

The Memory Bank used to ship with five drill lessons and two outcomes invented
for a Jefferson Elementary demo. The console panel is headed PRIOR LESSONS and
prints an incident id beside each one, so "FIRE-2025-DRILL-001 — elevator key
should be pre-staged" read exactly like an after-action finding from a real
response. It was a fixture. Nobody mistakes "Principal Johnson" for a real
person; an invented lesson with an invented incident id is a different thing,
because the claim it makes is that this system has experience.

Seeding also caused the duplication bug: the old cold-start check read the
local store's list, the managed adapter has no such list, so every restart
seeded again. Eleven restarts put fifty-five memories in the Agent Engine and
similarity search returned five hits that were all the same lesson. Nothing
seeds now, so nothing can re-seed.
"""

import pytest

from src.core.memory_bank import MemoryBank, init_memory_bank


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    monkeypatch.delenv("MEMORY_BACKEND", raising=False)
    MemoryBank.reset()
    yield
    MemoryBank.reset()


class TestNothingIsFabricated:
    def test_init_stores_no_lessons(self):
        bank = init_memory_bank()
        assert bank.find_lessons(limit=50) == []

    def test_init_stores_no_outcomes(self):
        init_memory_bank()
        assert MemoryBank.get().incident_outcomes == []

    def test_init_writes_nothing_to_the_managed_store(self, monkeypatch):
        """The managed path is the one the deployment runs, and the one the
        suite was not covering when the duplicates accumulated."""
        from unittest.mock import patch
        from tests.test_managed_memory_bank import _fake_client

        monkeypatch.setenv("MEMORY_BACKEND", "vertex")
        monkeypatch.setenv("VERTEX_MEMORY_ENGINE",
                           "projects/p/locations/l/reasoningEngines/1")
        MemoryBank.reset()
        client = _fake_client()
        with patch("src.core.memory_bank.VertexMemoryBank._build_client",
                   return_value=client):
            init_memory_bank()
        assert client._stored == [], "init wrote fixtures into the managed store"

    def test_a_lesson_from_a_real_run_is_still_recalled(self):
        """Empty by default, not broken."""
        bank = init_memory_bank()
        bank.store_lesson("ACTIVE_THREAT-2026-1", "active_threat", "jefferson",
                          "Learned in an actual run", "Body", tags=["threat"])
        assert len(bank.find_lessons(limit=50)) == 1


class TestTheConsoleSaysSoPlainly:
    def test_the_panel_has_an_empty_state(self):
        html = open("static/index.html").read()
        assert "No prior lessons" in html

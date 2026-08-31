"""The Memory Bank on managed Vertex AI, with the local store as the fallback.

Same facade shape as ContentScanner: a managed backend chosen by an env switch,
an offline implementation behind it, and a failure in the managed path falling
back rather than losing the feature.

The one thing that must not blur: the two backends do not compute the same
number. The local store ranks by Jaccard tag overlap; the managed store returns
a vector distance from semantic similarity search. Both are called "confidence"
in the output, so every lesson has to say which one produced it. Presenting a
vector distance as a tag-overlap score would be the same class of claim this
system spends its effort refusing to make.
"""

import os
from unittest.mock import MagicMock, patch

import pytest

from src.core.memory_bank import MemoryBank


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    monkeypatch.delenv("MEMORY_BACKEND", raising=False)
    monkeypatch.delenv("VERTEX_MEMORY_ENGINE", raising=False)
    MemoryBank.reset()
    yield
    MemoryBank.reset()


def _fake_client(distance=0.21):
    """A stand-in for MemoryBankServiceClient shaped like the real one."""
    stored: list[dict] = []

    class _Mem:
        """Shaped like a real one: Vertex persists `fact` and `scope` and drops
        display_name and description, which is why the record rides in the fact.
        Discovered by storing one and reading it back, not from the docs."""

        def __init__(self, fact, scope):
            self.fact, self.scope = fact, scope
            self.display_name = ""
            self.description = ""
            self.name = f"…/memories/{len(stored)}"

    class _Retrieved:
        def __init__(self, memory, distance):
            self.memory, self.distance = memory, distance

    client = MagicMock()

    def create_memory(parent, memory):
        # Round-trip through the same lossy shape the service has.
        stored.append(_Mem(fact=memory.fact, scope=dict(memory.scope)))
        op = MagicMock()
        op.result.return_value = memory
        return op

    def retrieve_memories(request):
        resp = MagicMock()
        resp.retrieved_memories = [_Retrieved(m, distance) for m in stored]
        return resp

    client.create_memory.side_effect = create_memory
    client.retrieve_memories.side_effect = retrieve_memories
    client._stored = stored
    client._Mem = _Mem
    return client


class TestBackendSelection:
    def test_local_is_the_default(self):
        assert MemoryBank.get().backend == "local"

    def test_vertex_is_selected_by_env(self, monkeypatch):
        monkeypatch.setenv("MEMORY_BACKEND", "vertex")
        monkeypatch.setenv("VERTEX_MEMORY_ENGINE", "projects/p/locations/l/reasoningEngines/1")
        with patch("src.core.memory_bank.VertexMemoryBank._build_client",
                   return_value=_fake_client()):
            assert MemoryBank.get().backend == "vertex"

    def test_no_engine_configured_falls_back(self, monkeypatch):
        """Guessing an engine would write lessons into a resource nobody named."""
        monkeypatch.setenv("MEMORY_BACKEND", "vertex")
        assert MemoryBank.get().backend == "local"

    def test_a_managed_init_failure_falls_back(self, monkeypatch):
        monkeypatch.setenv("MEMORY_BACKEND", "vertex")
        monkeypatch.setenv("VERTEX_MEMORY_ENGINE", "projects/p/locations/l/reasoningEngines/1")
        with patch("src.core.memory_bank.VertexMemoryBank._build_client",
                   side_effect=RuntimeError("no credentials")):
            bank = MemoryBank.get()
        assert bank.backend == "local"
        # The feature survives the backend being unavailable.
        bank.store_lesson("I-1", "fire", "jefferson", "T", "B", tags=["fire"])
        assert bank.find_lessons(incident_type="fire")


class TestCrossSessionRecallThroughTheManagedPath:
    """The claim the pillar rests on: a lesson stored in one process is
    retrievable in another, because the store is outside both."""

    def _bank(self, monkeypatch, client):
        monkeypatch.setenv("MEMORY_BACKEND", "vertex")
        monkeypatch.setenv("VERTEX_MEMORY_ENGINE", "projects/p/locations/l/reasoningEngines/1")
        MemoryBank.reset()
        with patch("src.core.memory_bank.VertexMemoryBank._build_client", return_value=client):
            return MemoryBank.get()

    def test_a_lesson_written_in_one_session_is_read_in_the_next(self, monkeypatch):
        client = _fake_client()

        writer = self._bank(monkeypatch, client)
        writer.store_lesson(
            "FIRE-2025-011", "fire", "jefferson",
            "Pre-stage the Floor 2 elevator key",
            "The key was in the main office and cost four minutes.",
            tags=["mobility", "elevator"])

        # A different process. Same managed store.
        MemoryBank.reset()
        reader = self._bank(monkeypatch, client)
        found = reader.find_lessons(incident_type="fire", facility_id="jefferson")

        assert found, "the lesson did not survive the process boundary"
        assert found[0]["title"] == "Pre-stage the Floor 2 elevator key"
        assert found[0]["incident_id"] == "FIRE-2025-011"
        assert found[0]["tags"] == ["mobility", "elevator"]

    def test_the_lesson_is_written_to_the_managed_store(self, monkeypatch):
        client = _fake_client()
        bank = self._bank(monkeypatch, client)
        bank.store_lesson("I-1", "fire", "jefferson", "T", "Body text", tags=["a"])
        assert client.create_memory.called
        parent = client.create_memory.call_args.kwargs["parent"]
        assert parent.endswith("reasoningEngines/1")

    def test_retrieval_is_a_similarity_search_not_a_scan(self, monkeypatch):
        client = _fake_client()
        bank = self._bank(monkeypatch, client)
        bank.store_lesson("I-1", "fire", "jefferson", "T", "B", tags=["mobility"])
        bank.find_lessons(incident_type="fire", tags=["mobility"])
        request = client.retrieve_memories.call_args.kwargs["request"]
        assert request.similarity_search_params.search_query
        assert "fire" in request.similarity_search_params.search_query


class TestConfidenceSaysWhereItCameFrom:
    def _vertex_bank(self, monkeypatch, client):
        monkeypatch.setenv("MEMORY_BACKEND", "vertex")
        monkeypatch.setenv("VERTEX_MEMORY_ENGINE", "projects/p/locations/l/reasoningEngines/1")
        MemoryBank.reset()
        with patch("src.core.memory_bank.VertexMemoryBank._build_client", return_value=client):
            return MemoryBank.get()

    def test_managed_lessons_carry_a_vector_basis(self, monkeypatch):
        bank = self._vertex_bank(monkeypatch, _fake_client(distance=0.21))
        bank.store_lesson("I-1", "fire", "jefferson", "T", "B", tags=["fire"])
        lesson = bank.find_lessons(incident_type="fire")[0]
        assert lesson["retrieval_basis"] == "vector_similarity"
        assert 0.0 <= lesson["retrieval_confidence"] <= 1.0

    def test_local_lessons_do_not_claim_a_vector_basis(self):
        bank = MemoryBank.get()
        bank.store_lesson("I-1", "fire", "jefferson", "T", "B", tags=["fire"])
        assert "retrieval_basis" not in bank.find_lessons(incident_type="fire")[0]

    def test_the_tool_reports_which_metric_ranked_the_results(self, monkeypatch):
        """find_similar_incidents must not relabel a vector distance as tag
        overlap, and must not re-rank semantic results by Jaccard."""
        from src.agents.learning.tools import find_similar_incidents

        bank = self._vertex_bank(monkeypatch, _fake_client(distance=0.10))
        bank.store_lesson("FIRE-2025-011", "fire", "jefferson",
                          "Pre-stage the elevator key", "Body",
                          tags=["mobility", "elevator"])
        result = find_similar_incidents("fire", "jefferson", tags="mobility")
        assert result["confidence_basis"] == "vector_similarity"
        assert result["lessons"][0]["confidence"] > 0

    def test_the_local_path_still_says_jaccard(self):
        from src.agents.learning.tools import find_similar_incidents

        bank = MemoryBank.get()
        bank.store_lesson("FIRE-2025-011", "fire", "jefferson", "T", "B",
                          tags=["mobility", "elevator"])
        result = find_similar_incidents("fire", "jefferson", tags="mobility")
        assert result["confidence_basis"] == "jaccard_tag_overlap"


class TestSourceCitationSurvivesTheManagedRoundTrip:
    def test_incident_id_and_lesson_id_come_back(self, monkeypatch):
        from src.agents.learning.tools import find_similar_incidents

        monkeypatch.setenv("MEMORY_BACKEND", "vertex")
        monkeypatch.setenv("VERTEX_MEMORY_ENGINE", "projects/p/locations/l/reasoningEngines/1")
        MemoryBank.reset()
        client = _fake_client()
        with patch("src.core.memory_bank.VertexMemoryBank._build_client", return_value=client):
            bank = MemoryBank.get()
            bank.store_lesson("FIRE-2025-011", "fire", "jefferson", "T", "B", tags=["x"])
            bank.store_incident_outcome(
                incident_id="FIRE-2025-011", incident_type="fire",
                facility_id="jefferson", total_personnel=34, accounted=34,
                response_time_seconds=240, resolved=True,
                summary="Evacuated in 4 minutes")
            result = find_similar_incidents("fire", "jefferson")

        source = result["lessons"][0]["source"]
        assert source["incident_id"] == "FIRE-2025-011"
        assert source["lesson_id"]
        assert source["outcome_summary"] == "Evacuated in 4 minutes"

    def test_a_retrieval_failure_says_so_rather_than_returning_nothing(self, monkeypatch):
        """An empty list reads as "no prior lessons", which is a claim. A
        backend that is down has not established that.

        The seeded fixtures used to mask this: the local fallback always held
        five lessons, so an outage still returned something. Nothing is seeded
        now, so the outage has to be reported rather than absorbed."""
        from src.agents.learning.tools import find_similar_incidents

        monkeypatch.setenv("MEMORY_BACKEND", "vertex")
        monkeypatch.setenv("VERTEX_MEMORY_ENGINE", "projects/p/locations/l/reasoningEngines/1")
        MemoryBank.reset()
        client = _fake_client()
        with patch("src.core.memory_bank.VertexMemoryBank._build_client", return_value=client):
            bank = MemoryBank.get()
            bank.store_lesson("I-1", "fire", "jefferson", "T", "B", tags=["fire"])
            client.retrieve_memories.side_effect = RuntimeError("backend down")
            result = find_similar_incidents("fire", "jefferson")

        assert result["lessons_found"] == 0
        assert result["recall_degraded"] is True, (
            "a backend outage silently became 'no lessons exist'")

    def test_a_healthy_empty_store_is_not_reported_as_degraded(self, monkeypatch):
        """Empty because nothing has been learned is a different statement
        from empty because the store could not be read."""
        from src.agents.learning.tools import find_similar_incidents

        monkeypatch.setenv("MEMORY_BACKEND", "vertex")
        monkeypatch.setenv("VERTEX_MEMORY_ENGINE", "projects/p/locations/l/reasoningEngines/1")
        MemoryBank.reset()
        client = _fake_client()
        with patch("src.core.memory_bank.VertexMemoryBank._build_client", return_value=client):
            MemoryBank.get()
            result = find_similar_incidents("fire", "jefferson")

        assert result["lessons_found"] == 0
        assert result["recall_degraded"] is False


class TestTheVectorNumberIsNotDressedUp:
    """Measured against the live API: the closest match to a well-aimed query
    came back at distance 0.8345, an unrelated lesson at 1.0489. So `1 -
    distance` renders a correct top hit as 0.166 and a miss as negative.

    The ordering is real and the magnitude is not. Reporting 0.166 beside a
    Jaccard 0.75 invites a reader to conclude the managed store is less sure,
    when the two numbers are not on the same scale at all."""

    def _bank(self, monkeypatch, distance):
        monkeypatch.setenv("MEMORY_BACKEND", "vertex")
        monkeypatch.setenv("VERTEX_MEMORY_ENGINE", "projects/p/locations/l/reasoningEngines/1")
        MemoryBank.reset()
        with patch("src.core.memory_bank.VertexMemoryBank._build_client",
                   return_value=_fake_client(distance=distance)):
            return MemoryBank.get()

    def test_the_raw_distance_is_kept(self, monkeypatch):
        bank = self._bank(monkeypatch, 0.8345)
        bank.store_lesson("I-1", "fire", "jefferson", "T", "B", tags=["fire"])
        lesson = bank.find_lessons(incident_type="fire")[0]
        assert lesson["retrieval_distance"] == pytest.approx(0.8345)

    def test_a_miss_does_not_go_negative(self, monkeypatch):
        bank = self._bank(monkeypatch, 1.0489)
        bank.store_lesson("I-1", "fire", "jefferson", "T", "B", tags=["fire"])
        assert bank.find_lessons(incident_type="fire")[0]["retrieval_confidence"] == 0.0

    def test_the_tool_warns_the_scales_differ(self, monkeypatch):
        from src.agents.learning.tools import find_similar_incidents

        bank = self._bank(monkeypatch, 0.8345)
        bank.store_lesson("I-1", "fire", "jefferson", "T", "B", tags=["fire"])
        result = find_similar_incidents("fire", "jefferson")
        note = result["confidence_note"].lower()
        assert "order" in note or "rank" in note
        assert "not" in note

    def test_the_local_path_says_nothing_misleading(self):
        from src.agents.learning.tools import find_similar_incidents

        MemoryBank.get().store_lesson("I-1", "fire", "jefferson", "T", "B", tags=["fire"])
        result = find_similar_incidents("fire", "jefferson")
        assert "jaccard" in result["confidence_note"].lower()


class TestHealthNamesTheBackend:
    """The facade falls back quietly by design, which is right for a crisis and
    wrong for an operator: without this, a service that silently dropped to the
    local store looks identical to one running managed."""

    def test_health_reports_the_active_memory_backend(self):
        from tests.test_server import MockHandler

        payload = MockHandler("GET", "/health").get_response()
        assert payload["memory_backend"] in {"local", "vertex"}

    def test_it_says_local_when_managed_is_unavailable(self, monkeypatch):
        from tests.test_server import MockHandler

        monkeypatch.setenv("MEMORY_BACKEND", "vertex")
        monkeypatch.delenv("VERTEX_MEMORY_ENGINE", raising=False)
        MemoryBank.reset()
        assert MockHandler("GET", "/health").get_response()["memory_backend"] == "local"


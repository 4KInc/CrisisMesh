"""Durability of the authoritative store, and the round-trip the proof pivots on."""

import pytest

from src.core import incident_state, incident_store


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setenv("CRISISMESH_INCIDENT_STORE", "memory")
    incident_store.reset_backend()
    incident_store.reset()
    incident_state.reset()
    yield
    incident_store.reset_backend()
    incident_store.reset()
    incident_state.reset()


class TestActivenessIsExplicit:
    """The proof reads one thing post-redeploy: is an incident live? Derived
    from "is incident_id truthy" that reads ambiguously through a store that
    coerces empty values, so activeness is its own boolean."""

    def test_an_active_incident_serialises_as_active(self):
        incident_state.declare("T-1", {"incident_id": "T-1"}, source="slack")
        assert incident_state.as_document()["active"] is True

    def test_nothing_active_serialises_as_inactive(self):
        assert incident_state.as_document()["active"] is False

    def test_a_resolved_incident_does_not_come_back(self):
        incident_state.declare("T-1", {"incident_id": "T-1"}, source="slack")
        incident_state.clear()
        incident_state.from_document(incident_store.load())
        assert incident_state.is_active() is False

    def test_an_empty_document_leaves_nothing_active(self):
        incident_state.from_document({})
        assert incident_state.is_active() is False

    def test_active_false_is_not_resurrected(self):
        incident_state.from_document({"active": False, "incident_id": "T-1"})
        assert incident_state.is_active() is False


class TestRoundTrip:
    def test_identity_and_origin_survive(self):
        incident_state.declare(
            "T-1", {"incident_id": "T-1", "classification": {"incident_type": "fire"}},
            source="sms")
        incident_state.attach_origin(declared_by="U_PRINCIPAL", origin_channel="C1")
        doc = incident_store.load()

        incident_state.reset()
        incident_state.from_document(doc)

        assert incident_state.get_active_incident_id() == "T-1"
        assert incident_state.get_origin()["declared_by"] == "U_PRINCIPAL"
        assert incident_state.get_latest_incident()["classification"]["incident_type"] == "fire"

    def test_the_clock_survives_so_duration_is_not_reset(self):
        incident_state.declare("T-1", {"incident_id": "T-1"}, source="sms")
        started = incident_state.get_origin()["started_at"]
        doc = incident_store.load()
        incident_state.reset()
        incident_state.from_document(doc)
        assert incident_state.get_origin()["started_at"] == pytest.approx(started)


class TestRehydrate:
    def test_a_restart_restores_the_incident(self):
        incident_state.declare("T-1", {"incident_id": "T-1"}, source="slack")
        incident_state.reset()          # the process forgets
        assert incident_state.rehydrate() is True
        assert incident_state.get_active_incident_id() == "T-1"

    def test_a_restart_with_nothing_active_restores_nothing(self):
        assert incident_state.rehydrate() is False

    def test_a_store_failure_does_not_raise_into_startup(self, monkeypatch):
        def _boom():
            raise RuntimeError("store unavailable")

        monkeypatch.setattr(incident_store, "load", _boom)
        assert incident_state.rehydrate() is False

    def test_a_persistence_failure_does_not_lose_the_live_incident(self, monkeypatch):
        """Coordination continues even if durability does not."""
        def _boom(doc):
            raise RuntimeError("write failed")

        monkeypatch.setattr(incident_store, "save", _boom)
        incident_state.declare("T-1", {"incident_id": "T-1"}, source="slack")
        assert incident_state.is_active() is True


class TestReconciliationIsLazilyRebuilt:
    """Incident authoritative, reconciliation lazy — so a restart between
    declare and first tick is correct without special handling rather than a
    partial-failure window needing repair."""

    def test_a_tick_after_a_restart_with_no_reconciliation_state_works(self):
        from src.core import reconciliation, reconciliation_loop
        from src.core.knowledge_base import KnowledgeBase, init_knowledge_base
        import os

        KnowledgeBase.reset()
        init_knowledge_base(os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "seed"))
        incident_state.declare(
            "T-1", {"incident_id": "T-1",
                    "classification": {"incident_type": "active_threat", "severity": "critical"}},
            source="slack")

        reconciliation.reset()          # the restart: no reconciliation state at all
        reconciliation_loop.reset()
        incident_state.reset()
        incident_state.rehydrate()

        result = reconciliation_loop.run_tick("T-1")
        assert result["skipped_reason"] == ""
        assert result["intents"], "the rebuilt tick found nobody to chase"
        KnowledgeBase.reset()


class TestAnIdentitylessResultCannotWipeAnIncident:
    """The agentic background run finishes ~40s after the deterministic one and
    hands back a dict with no incident_id. Declaring that wiped the live
    incident — silently in memory, and durably as `active: False` over a
    running emergency once the store was persisted."""

    def test_the_incident_survives_an_identityless_result(self):
        incident_state.declare("T-1", {"incident_id": "T-1"}, source="web")
        incident_state.set_latest_incident(
            {"pipeline": "agentic", "final_response": "..."}, source="web")
        assert incident_state.get_active_incident_id() == "T-1"
        assert incident_state.is_active() is True

    def test_the_durable_record_is_not_deactivated(self):
        incident_state.declare("T-1", {"incident_id": "T-1"}, source="web")
        incident_state.set_latest_incident({"pipeline": "agentic"}, source="web")
        assert incident_store.load()["active"] is True

    def test_an_identityless_result_declares_nothing_when_idle(self):
        incident_state.set_latest_incident({"pipeline": "agentic"}, source="web")
        assert incident_state.is_active() is False

    def test_a_matching_result_enriches_without_restarting_the_clock(self):
        incident_state.declare("T-1", {"incident_id": "T-1"}, source="web")
        started = incident_state.get_origin()["started_at"]
        incident_state.set_latest_incident(
            {"incident_id": "T-1", "pipeline": "agentic", "final_response": "SITREP"},
            source="web")
        assert incident_state.get_origin()["started_at"] == pytest.approx(started)
        assert incident_state.get_latest_incident()["final_response"] == "SITREP"

    def test_a_genuinely_new_incident_still_replaces(self):
        incident_state.declare("T-1", {"incident_id": "T-1"}, source="web")
        incident_state.set_latest_incident({"incident_id": "T-2"}, source="web")
        assert incident_state.get_active_incident_id() == "T-2"

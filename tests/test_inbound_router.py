"""An active incident must never be replaced by an inbound message."""

import os

import pytest

from src.core import inbound_router, incident_digest, incident_state, observations
from src.core.knowledge_base import KnowledgeBase, init_knowledge_base
from src.agents.accountability.tools import _checkin_store, send_checkin_request

SEED_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "seed",
)


@pytest.fixture(autouse=True)
def fresh(tmp_path, monkeypatch):
    monkeypatch.setenv("CRISISMESH_CONSENT_LOG", str(tmp_path / "consent.jsonl"))
    monkeypatch.setenv("CRISISMESH_WHATSAPP_MODE", "off")
    monkeypatch.setenv("CRISISMESH_SMS_MODE", "off")
    KnowledgeBase.reset()
    init_knowledge_base(SEED_DIR)
    incident_state.reset()
    observations.reset()
    _checkin_store.clear()
    from src.services.sms_transport import _phone_to_person
    from src.services.whatsapp_transport import _phone_to_person as wa
    _phone_to_person.clear()
    wa.clear()
    yield
    incident_state.reset()
    observations.reset()
    _checkin_store.clear()
    KnowledgeBase.reset()


def _lockdown():
    incident_state.declare(
        "ACTIVE_THREAT-1",
        {"incident_id": "ACTIVE_THREAT-1", "report": "Armed intruder west hallway",
         "classification": {"incident_type": "active_threat", "severity": "critical"},
         "location": {"zone_name": "West Hallway"}},
        source="slack",
    )


class TestRouting:
    def test_declares_when_nothing_is_running(self):
        assert inbound_router.route("Smoke in the gym")[0] == inbound_router.ACTION_DECLARE

    def test_becomes_an_observation_during_an_incident(self):
        _lockdown()
        action, payload = inbound_router.route("he's moving toward the gym")
        assert action == inbound_router.ACTION_OBSERVATION
        assert payload == "he's moving toward the gym"

    @pytest.mark.parametrize("text", ["status", "STATUS", "sitrep", "what's happening?", "update"])
    def test_status_requests_are_recognised(self, text):
        _lockdown()
        assert inbound_router.route(text)[0] == inbound_router.ACTION_STATUS

    def test_new_prefix_declares_and_is_stripped(self):
        _lockdown()
        action, payload = inbound_router.route("NEW: fire alarm pulled in B wing")
        assert action == inbound_router.ACTION_NEW_INCIDENT
        assert payload == "fire alarm pulled in B wing"

    def test_new_prefix_works_when_nothing_is_running(self):
        assert inbound_router.route("new: smoke in the gym")[0] == inbound_router.ACTION_NEW_INCIDENT


class TestActiveIncidentSurvives:
    """The regression: a teacher's question wiped out a lockdown."""

    def test_whatsapp_question_does_not_replace_the_incident(self):
        from src.services.whatsapp_transport import handle_inbound_message
        _lockdown()
        send_checkin_request("ACTIVE_THREAT-1", facility_id="jefferson")
        before = len(_checkin_store["ACTIVE_THREAT-1"])

        result = handle_inbound_message("+15551110000", "what is happening? where is he?")

        # Free text, so it is logged as an observation — and the reply still
        # answers the question, because an observation reply carries the status.
        assert result["action"] == "observation"
        assert "CrisisMesh status" in result["reply"]
        assert incident_state.get_active_incident_id() == "ACTIVE_THREAT-1"
        assert len(_checkin_store["ACTIVE_THREAT-1"]) == before

    def test_bare_status_word_gets_status_only(self):
        from src.services.whatsapp_transport import handle_inbound_message
        _lockdown()
        result = handle_inbound_message("+15551110000", "status")
        assert result["action"] == "status"
        assert incident_state.get_active_incident_id() == "ACTIVE_THREAT-1"

    def test_whatsapp_observation_is_attached_not_declared(self):
        from src.services.whatsapp_transport import handle_inbound_message
        _lockdown()
        result = handle_inbound_message("+16155550101", "he's moving toward the gym")

        assert result["action"] == "observation"
        assert incident_state.get_active_incident_id() == "ACTIVE_THREAT-1"
        entries = observations.get("ACTIVE_THREAT-1")
        assert len(entries) == 1
        assert entries[0]["text"] == "he's moving toward the gym"
        assert entries[0]["person_name"] == "Principal Johnson"

    def test_sms_observation_is_attached_not_declared(self):
        from src.services.sms_transport import handle_inbound_sms
        _lockdown()
        result = handle_inbound_sms("+16155550101", "two people hurt near the library")
        assert result["action"] == "observation"
        assert incident_state.get_active_incident_id() == "ACTIVE_THREAT-1"
        assert observations.count("ACTIVE_THREAT-1") == 1

    def test_new_prefix_is_the_only_way_to_replace(self):
        from src.services.whatsapp_transport import handle_inbound_message
        _lockdown()
        result = handle_inbound_message("+15551110000", "NEW: fire alarm pulled in B wing")
        assert result["action"] == "incident"
        assert incident_state.get_active_incident_id() != "ACTIVE_THREAT-1"

    def test_first_message_still_declares(self):
        from src.services.whatsapp_transport import handle_inbound_message
        result = handle_inbound_message("+15551110000", "Armed intruder in the west hallway")
        assert result["action"] == "incident"
        assert incident_state.is_active() is True


class TestStatusDigest:
    def test_says_so_when_nothing_is_running(self):
        line = incident_digest.status_line()
        assert "no active incident" in line
        assert "911" in line

    def test_summarises_the_running_incident(self):
        _lockdown()
        send_checkin_request("ACTIVE_THREAT-1", facility_id="jefferson")
        line = incident_digest.status_line()
        assert "ACTIVE THREAT" in line
        assert "critical" in line
        assert "West Hallway" in line
        assert "ACTIVE_THREAT-1" in line

    def test_surfaces_the_last_reported_threat_position(self):
        _lockdown()
        observations.record("ACTIVE_THREAT-1", "shooter is in the cafeteria", source="sms")
        line = incident_digest.status_line()
        if observations.latest_threat_location("ACTIVE_THREAT-1"):
            assert "Last reported threat position" in line


class TestObservationStore:
    def test_append_only(self):
        observations.record("INC-1", "first", source="sms")
        observations.record("INC-1", "second", source="whatsapp")
        entries = observations.get("INC-1")
        assert [e["text"] for e in entries] == ["first", "second"]

    def test_get_returns_copies(self):
        observations.record("INC-1", "first", source="sms")
        observations.get("INC-1")[0]["text"] = "tampered"
        assert observations.get("INC-1")[0]["text"] == "first"

    def test_scoped_by_incident(self):
        observations.record("INC-1", "a", source="sms")
        observations.record("INC-2", "b", source="sms")
        assert observations.count("INC-1") == 1
        assert observations.count("INC-2") == 1

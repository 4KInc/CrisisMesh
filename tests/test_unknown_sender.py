"""A stranger texting the number is a judge, a parent, or a wrong number.

The check-in refusal is right: attributing a check-in to somebody who is not on
the roster would put a name in the accounted column that nobody can vouch for.
But "You are not registered in CrisisMesh" is a dead end, and the first thing
anyone tries is SAFE. It should refuse the check-in and say what the number can
actually do, which is most of it.
"""

import os

import pytest

from src.core import incident_state
from src.core.knowledge_base import KnowledgeBase, init_knowledge_base
from src.services.whatsapp_transport import handle_inbound_message

SEED = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "seed")
STRANGER = "+14155550199"


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setenv("CRISISMESH_DELIVERY", "off")
    monkeypatch.setenv("CRISISMESH_AUTO_TICK", "off")
    monkeypatch.delenv("CRISISMESH_DEMO_PHONE", raising=False)
    KnowledgeBase.reset()
    init_knowledge_base(SEED)
    incident_state.reset()
    from src.services import whatsapp_transport
    whatsapp_transport._phone_to_person.clear()
    yield
    incident_state.reset()


class TestTheRefusalStillRefuses:
    def test_a_stranger_cannot_check_in(self):
        incident_state.declare("T-1", {"incident_id": "T-1",
            "classification": {"incident_type": "fire", "severity": "high"}}, source="slack")
        result = handle_inbound_message(STRANGER, "SAFE")
        assert result["action"] == "unknown_person"

    def test_no_name_enters_the_accounted_column(self):
        """The reason for the refusal: a check-in nobody can attribute is a name
        in the safe column that nobody can vouch for."""
        from src.agents.accountability.tools import compute_accountability_summary

        incident_state.declare("T-1", {"incident_id": "T-1",
            "classification": {"incident_type": "fire", "severity": "high"}}, source="slack")
        handle_inbound_message(STRANGER, "SAFE")
        assert compute_accountability_summary("T-1")["accounted"] == 0


class TestTheRefusalPointsSomewhere:
    def test_it_says_what_the_number_can_do(self):
        incident_state.declare("T-1", {"incident_id": "T-1",
            "classification": {"incident_type": "fire", "severity": "high"}}, source="slack")
        reply = handle_inbound_message(STRANGER, "SAFE")["reply"].lower()
        assert "unaccounted" in reply or "board" in reply, reply

    def test_it_still_carries_the_911_line(self):
        incident_state.declare("T-1", {"incident_id": "T-1",
            "classification": {"incident_type": "fire", "severity": "high"}}, source="slack")
        assert "911" in handle_inbound_message(STRANGER, "SAFE")["reply"]

    def test_it_does_not_imply_they_were_recorded(self):
        incident_state.declare("T-1", {"incident_id": "T-1",
            "classification": {"incident_type": "fire", "severity": "high"}}, source="slack")
        reply = handle_inbound_message(STRANGER, "SAFE")["reply"].lower()
        assert "recorded" not in reply and "thank" not in reply


class TestEverythingElseWorksForAStranger:
    """The number is the product. A judge who cannot use it has not seen it."""

    def test_a_stranger_can_declare(self):
        assert handle_inbound_message(
            STRANGER, "/incident smoke in the science lab, floor 2")["action"] == "incident"

    @pytest.mark.parametrize("question", [
        "who is still unaccounted",
        "show the classroom board",
        "where is the shooter now",
        "room 104: 23 students are safe, 1 unaccounted",
    ])
    def test_a_stranger_can_ask(self, question):
        incident_state.declare("T-1", {"incident_id": "T-1",
            "report": "active shooter in the east wing",
            "classification": {"incident_type": "active_threat", "severity": "critical"},
            "location": {"zone_id": "east-wing-f1", "zone_name": "East Wing Floor 1"}},
            source="slack")
        result = handle_inbound_message(STRANGER, question)
        assert result["action"] == "query"
        assert result["reply"]

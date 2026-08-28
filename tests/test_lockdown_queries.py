"""What the loop and the query desk are allowed to do during a lockdown.

One live run produced all three of these:

  * The same three escalations arrived on the warden's phone every 25 seconds.
    The terminal-state guard existed, was tested, and sat in a function the
    running loop does not call.
  * "what's the fastest route out of east wing" during an active shooter
    returned corridor directions. The movement critic runs inside the fan-out
    and query answers do not go through it.
  * "where is the shooter now" was filed as an observation and answered with
    the incident status. The threat track was already being kept.
"""

import os
from unittest.mock import patch

import pytest

from src.core import incident_state, incident_queries, observations, reconciliation
from src.core.knowledge_base import KnowledgeBase, init_knowledge_base

SEED = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "seed")


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setenv("CRISISMESH_DELIVERY", "off")
    monkeypatch.setenv("CRISISMESH_AUTO_TICK", "off")
    monkeypatch.setenv("CRISISMESH_RECONCILIATION_STORE", "memory")
    KnowledgeBase.reset()
    init_knowledge_base(SEED)
    incident_state.reset()
    observations.reset()
    reconciliation.reset()
    yield
    incident_state.reset()
    KnowledgeBase.reset()


def _lockdown():
    rec = {"incident_id": "T-1", "source": "whatsapp",
           "report": "active shooter reported in the east wing, gunshots heard",
           "classification": {"incident_type": "active_threat", "severity": "critical"},
           "location": {"zone_id": "east-wing-f1", "zone_name": "East Wing Floor 1"}}
    incident_state.declare("T-1", rec, source="whatsapp")
    return rec


class TestAnEscalatedPersonIsFinished:
    def test_the_store_the_loop_actually_calls_agrees(self):
        """rec.should_act had the guard; the loop calls store.safe_should_act."""
        from src.core import reconciliation_store as store

        reconciliation.transition("T-1", "p002", reconciliation.SILENT, tick=1)
        reconciliation.transition("T-1", "p002", reconciliation.REPINGED, tick=2)
        reconciliation.transition("T-1", "p002", reconciliation.ESCALATED, tick=3)

        assert reconciliation.should_act("T-1", "p002", tick=9) is False
        assert store.safe_should_act("T-1", "p002", tick=9) is False, (
            "the loop would page this person's warden again on the next tick"
        )

    def test_an_accounted_person_is_also_finished(self):
        from src.core import reconciliation_store as store

        reconciliation.transition("T-1", "p004", reconciliation.SILENT, tick=1)
        reconciliation.transition("T-1", "p004", reconciliation.ACCOUNTED, tick=2)
        assert store.safe_should_act("T-1", "p004", tick=9) is False

    def test_a_silent_person_is_still_acted_on(self):
        from src.core import reconciliation_store as store

        reconciliation.transition("T-1", "p005", reconciliation.SILENT, tick=1)
        assert store.safe_should_act("T-1", "p005", tick=9) is True

    def test_a_reopened_person_comes_back(self):
        """Escalated is terminal for the loop, not permanent."""
        from src.core import reconciliation_store as store

        reconciliation.transition("T-1", "p002", reconciliation.SILENT, tick=1)
        reconciliation.transition("T-1", "p002", reconciliation.ESCALATED, tick=3)
        reconciliation.reopen("T-1", "p002", reason="warden could not find them", tick=4)
        assert store.safe_should_act("T-1", "p002", tick=9) is True


class TestNoRouteDirectionsDuringALockdown:
    def test_a_route_question_is_refused(self):
        _lockdown()
        reply = incident_queries.answer("what's the fastest route out of east wing")
        assert reply
        assert "door 7" not in reply.lower()
        assert "gym corridor" not in reply.lower()

    def test_the_refusal_says_why(self):
        _lockdown()
        reply = incident_queries.answer("what's the fastest route out of east wing")
        assert "lockdown" in reply.lower() or "shelter" in reply.lower()
        assert "911" in reply

    def test_a_fire_still_gets_its_route(self):
        """The rule is the incident type, not the word "route"."""
        incident_state.declare("F-1", {
            "incident_id": "F-1",
            "classification": {"incident_type": "fire", "severity": "high"},
            "location": {"zone_id": "east-wing-f1", "zone_name": "East Wing Floor 1"},
        }, source="whatsapp")
        reply = incident_queries.answer("what's the fastest route out of east wing")
        assert "door" in reply.lower()


class TestWhereIsTheShooter:
    def test_it_answers_from_the_declaration_when_nobody_has_reported(self):
        _lockdown()
        reply = incident_queries.answer("where is the shooter now")
        assert reply
        assert "east wing" in reply.lower()
        assert "log" not in reply.lower(), "it was filed as an observation instead"

    def test_it_prefers_the_latest_sighting(self):
        _lockdown()
        observations.record("T-1", "shooter last seen heading toward the gym",
                            source="whatsapp", person_name="Mrs. Rodriguez")
        reply = incident_queries.answer("where is the shooter now")
        assert "gym" in reply.lower()

    def test_it_marks_the_position_unconfirmed(self):
        """A reported sighting is not a confirmed position, and someone may act
        on this answer."""
        _lockdown()
        reply = incident_queries.answer("where is the shooter now")
        assert "unconfirmed" in reply.lower() or "unverified" in reply.lower()

    def test_it_gives_the_trail_when_there_is_one(self):
        """Two positions say which way it is moving."""
        _lockdown()
        observations.record("T-1", "shooter last seen near the cafeteria",
                            source="whatsapp", person_name="Mrs. Rodriguez")
        reply = incident_queries.answer("where is the shooter now")
        assert "east wing" in reply.lower() and "cafeteria" in reply.lower()

    def test_it_does_not_invent_a_position(self):
        incident_state.declare("F-1", {
            "incident_id": "F-1",
            "classification": {"incident_type": "fire", "severity": "high"},
            "report": "smoke in the cafeteria",
        }, source="whatsapp")
        reply = incident_queries.answer("where is the shooter now")
        assert reply
        assert "no reported" in reply.lower() or "nobody has reported" in reply.lower()

    @pytest.mark.parametrize("phrasing", [
        "where is the shooter now",
        "where is the shooter",
        "where was the gunman last seen",
        "last known location of the threat",
        "where is the attacker",
    ])
    def test_the_phrasings_people_use(self, phrasing):
        _lockdown()
        assert incident_queries.classify(phrasing) == incident_queries.KIND_THREAT_LOCATION

    def test_a_sighting_report_is_still_a_sighting_report(self):
        """"he is headed toward the gym" is a witness statement, not a question."""
        _lockdown()
        assert incident_queries.classify("he is headed toward the gym") != \
            incident_queries.KIND_THREAT_LOCATION

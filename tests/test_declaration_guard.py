"""A message has to be about an emergency here before it opens an incident."""

import os

import pytest

from src.core import declaration_guard, incident_state, observations
from src.core.knowledge_base import KnowledgeBase, init_knowledge_base

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
    from src.services.sms_transport import _phone_to_person
    from src.services.whatsapp_transport import _phone_to_person as wa
    _phone_to_person.clear()
    wa.clear()
    yield
    incident_state.reset()
    observations.reset()
    KnowledgeBase.reset()


class TestRefused:
    @pytest.mark.parametrize("text", [
        "What is promises in javascript",
        "explain kubernetes to me",
        "What is the weather today",
        "how do I write a python function",
        "translate this to spanish",
        "tell me about the world cup",
    ])
    def test_general_questions_do_not_declare(self, text):
        allowed, reason = declaration_guard.is_plausible_report(text)
        assert allowed is False
        assert reason

    @pytest.mark.parametrize("text", ["hi", "hello", "thanks", "test", "ok", "asdf", ""])
    def test_noise_does_not_declare(self, text):
        assert declaration_guard.is_plausible_report(text)[0] is False


class TestAllowed:
    """Declining a real report is far worse than allowing a junk one, so the
    bar is deliberately low."""

    @pytest.mark.parametrize("text", [
        "Armed intruder in the west wing",
        "smoke in the science lab",
        "someone is hurt near the library",
        "power is out in b wing",
        "he is moving toward the gym",
        "water everywhere in the cafeteria",
        "kids still in the building",
    ])
    def test_reports_are_allowed(self, text):
        assert declaration_guard.is_plausible_report(text)[0] is True

    def test_a_question_containing_an_emergency_is_allowed(self):
        """Question in form, report in substance."""
        assert declaration_guard.is_plausible_report("Is there a fire in the gym?")[0] is True

    def test_panic_phrased_as_a_question_is_allowed(self):
        assert declaration_guard.is_plausible_report("What should we do? kids are trapped")[0] is True

    def test_emergency_signal_overrides_off_topic_shape(self):
        assert declaration_guard.is_plausible_report("what is the evacuation route")[0] is True


class TestRefusalMessage:
    def test_says_nothing_was_declared(self):
        msg = declaration_guard.refusal_message("reads as a general question")
        assert "did not open an incident" in msg

    def test_shows_how_to_report_properly(self):
        msg = declaration_guard.refusal_message("too short")
        assert "describe what you see and where" in msg
        assert "911" in msg


class TestTransports:
    def test_whatsapp_junk_creates_no_incident(self):
        from src.services.whatsapp_transport import handle_inbound_message
        result = handle_inbound_message("+16155550101", "What is promises in javascript")
        assert result["action"] == "not_an_incident"
        assert incident_state.is_active() is False

    def test_sms_junk_creates_no_incident(self):
        from src.services.sms_transport import handle_inbound_sms
        result = handle_inbound_sms("+16155550101", "explain kubernetes to me")
        assert result["action"] == "not_an_incident"
        assert incident_state.is_active() is False

    def test_real_report_still_declares(self):
        from src.services.whatsapp_transport import handle_inbound_message
        result = handle_inbound_message("+16155550101", "Armed intruder in the west wing")
        assert result["action"] == "incident"
        assert incident_state.is_active() is True

    def test_junk_during_an_incident_is_not_logged_as_an_observation(self):
        """The observation log is what an after-action review is built from."""
        from src.services.whatsapp_transport import handle_inbound_message
        incident_state.declare("T-1", {"incident_id": "T-1",
                                       "classification": {"incident_type": "active_threat",
                                                          "severity": "critical"}},
                               source="slack")
        result = handle_inbound_message("+16155550101", "What is promises in javascript")
        assert result["action"] == "not_logged"
        assert observations.count("T-1") == 0
        assert incident_state.get_active_incident_id() == "T-1"

    def test_witness_report_during_an_incident_still_logs(self):
        from src.services.whatsapp_transport import handle_inbound_message
        incident_state.declare("T-1", {"incident_id": "T-1",
                                       "classification": {"incident_type": "active_threat",
                                                          "severity": "critical"}},
                               source="slack")
        result = handle_inbound_message("+16155550101", "he is moving toward the gym")
        assert result["action"] == "observation"
        assert observations.count("T-1") == 1


class TestSituationalQuestionsDuringAnIncident:
    """"what is happening" during a lockdown is someone trying to find out what
    is going on — the opposite of off-topic, even though it is a question."""

    def _lockdown(self):
        incident_state.declare(
            "T-1", {"incident_id": "T-1",
                    "classification": {"incident_type": "active_threat", "severity": "critical"}},
            source="slack")

    @pytest.mark.parametrize("text", [
        "what is happening? where is he?",
        "is it over?",
        "should we stay put?",
        "how long until police arrive",
        "any news",
    ])
    def test_allowed_during_an_incident(self, text):
        self._lockdown()
        assert declaration_guard.is_plausible_report(text)[0] is True

    def test_still_refused_with_nothing_running(self):
        """Outside an incident the same words are not a report of an emergency."""
        assert declaration_guard.is_plausible_report("what is happening?")[0] is False

    def test_off_topic_stays_refused_even_during_an_incident(self):
        self._lockdown()
        assert declaration_guard.is_plausible_report("What is promises in javascript")[0] is False

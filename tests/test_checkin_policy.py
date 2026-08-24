"""A check-in with no incident must never be reported as recorded."""

import os

import pytest

from src.core import checkin_policy, incident_state
from src.core.knowledge_base import KnowledgeBase, init_knowledge_base
from src.agents.accountability.tools import _checkin_store

SEED_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "seed",
)


@pytest.fixture(autouse=True)
def fresh(tmp_path, monkeypatch):
    monkeypatch.setenv("CRISISMESH_CONSENT_LOG", str(tmp_path / "consent.jsonl"))
    KnowledgeBase.reset()
    init_knowledge_base(SEED_DIR)
    incident_state.reset()
    _checkin_store.clear()
    from src.services.sms_transport import _phone_to_person
    from src.services.whatsapp_transport import _phone_to_person as wa_map
    _phone_to_person.clear()
    wa_map.clear()
    yield
    incident_state.reset()
    _checkin_store.clear()
    KnowledgeBase.reset()


class TestPolicy:
    def test_cannot_accept_without_an_incident(self):
        assert checkin_policy.can_accept() is False

    def test_can_accept_once_declared(self):
        incident_state.declare("INC-1", {"incident_id": "INC-1"}, source="sms")
        assert checkin_policy.can_accept() is True

    @pytest.mark.parametrize("status", ["safe", "evacuated"])
    def test_routine_refusal_never_claims_a_record(self, status):
        msg = checkin_policy.refusal_message(status)
        assert "recorded" in msg  # as "nothing was recorded"
        assert "nothing was recorded" in msg
        assert "911" in msg

    @pytest.mark.parametrize("status", ["need_help", "injured"])
    def test_urgent_refusal_says_no_responder_was_alerted(self, status):
        msg = checkin_policy.refusal_message(status)
        assert "NOT logged" in msg
        assert "no responder has been alerted" in msg
        assert "call 911" in msg

    def test_urgent_refusal_is_logged_at_error(self, caplog):
        import logging
        with caplog.at_level(logging.ERROR):
            checkin_policy.log_refusal("sms", "need_help", "+15551234567")
        assert "UNMATCHED DISTRESS" in caplog.text


class TestNoOrphanRows:
    """The phantom `_checkin_store["active"]` bucket must never be created."""

    def test_sms_checkin_writes_nothing(self):
        from src.services.sms_transport import handle_inbound_sms
        result = handle_inbound_sms("+16155550101", "SAFE")
        assert result["action"] == "no_active_incident"
        assert "active" not in _checkin_store
        assert _checkin_store == {}

    def test_sms_sos_writes_nothing_and_points_at_911(self):
        from src.services.sms_transport import handle_inbound_sms
        result = handle_inbound_sms("+16155550101", "SOS")
        assert result["action"] == "no_active_incident"
        assert "911" in result["twiml"]
        assert "NOT logged" in result["twiml"]
        assert _checkin_store == {}

    def test_whatsapp_checkin_writes_nothing(self):
        from src.services.whatsapp_transport import handle_inbound_message
        result = handle_inbound_message("+16155550101", "SAFE")
        assert result["action"] == "no_active_incident"
        assert _checkin_store == {}

    def test_slack_reaction_writes_nothing(self):
        from src.services.slack_transport import _handle_reaction_event, _build_slack_map
        _build_slack_map()
        _handle_reaction_event({
            "reaction": "white_check_mark",
            "user": "U_PRINCIPAL",
            "item": {"channel": "C123"},
        })
        assert _checkin_store == {}


class TestAcceptedWhenActive:
    def test_sms_checkin_lands_on_the_real_incident(self):
        from src.services.sms_transport import handle_inbound_sms
        incident_state.declare("FIRE-2026-1", {"incident_id": "FIRE-2026-1"}, source="slack")
        result = handle_inbound_sms("+16155550101", "SAFE")
        assert result["action"] == "checkin"
        assert "FIRE-2026-1" in _checkin_store
        assert "active" not in _checkin_store

    def test_whatsapp_checkin_joins_a_slack_declared_incident(self):
        """Cross-channel: declared in Slack, checked in over WhatsApp."""
        from src.services.whatsapp_transport import handle_inbound_message
        incident_state.declare("THREAT-1", {"incident_id": "THREAT-1"}, source="slack")
        result = handle_inbound_message("+16155550101", "SOS")
        assert result["action"] == "checkin"
        assert _checkin_store["THREAT-1"]["p001"]["status"] == "need_help"

    def test_checkin_after_resolve_is_refused(self):
        """Resolving used to leave later check-ins orphaned but confirmed."""
        from src.services.sms_transport import handle_inbound_sms
        incident_state.declare("FIRE-1", {"incident_id": "FIRE-1"}, source="slack")
        assert handle_inbound_sms("+16155550101", "SAFE")["action"] == "checkin"
        incident_state.clear()
        after = handle_inbound_sms("+16155550101", "SAFE")
        assert after["action"] == "no_active_incident"
        assert "active" not in _checkin_store

"""Tests for A2P 10DLC consent handling — opt-in, opt-out, and carrier keywords."""

import json
import os

import pytest

from src.core.knowledge_base import KnowledgeBase, init_knowledge_base
from src.core.observability import Tracer

SEED_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "seed",
)


@pytest.fixture(autouse=True)
def fresh_state(tmp_path, monkeypatch):
    log = tmp_path / "consent.jsonl"
    monkeypatch.setenv("CRISISMESH_CONSENT_LOG", str(log))
    from src.services import sms_consent
    sms_consent.reset()
    KnowledgeBase.reset()
    init_knowledge_base(SEED_DIR)
    Tracer.reset()
    from src.services.sms_transport import _phone_to_person
    _phone_to_person.clear()
    import src.services.slack_transport as st
    st._active_incident_id = ""
    st._latest_incident = {}
    yield log
    sms_consent.reset()
    KnowledgeBase.reset()
    _phone_to_person.clear()


class TestNormalizePhone:
    def test_ten_digit_us(self):
        from src.services.sms_consent import normalize_phone
        assert normalize_phone("555 123 4567") == "+15551234567"

    def test_already_e164(self):
        from src.services.sms_consent import normalize_phone
        assert normalize_phone("+15551234567") == "+15551234567"

    def test_leading_one(self):
        from src.services.sms_consent import normalize_phone
        assert normalize_phone("1 (555) 123-4567") == "+15551234567"

    def test_empty(self):
        from src.services.sms_consent import normalize_phone
        assert normalize_phone("") == ""


class TestConsentLifecycle:
    def test_optin_is_pending_until_confirmed(self):
        from src.services.sms_consent import has_consent, record_optin
        record_optin("+15551234567", name="Ada", organization="Lincoln High")
        assert has_consent("+15551234567") is False

    def test_confirm_grants_consent(self):
        from src.services.sms_consent import confirm_optin, has_consent, record_optin
        record_optin("+15551234567", name="Ada", organization="Lincoln High")
        confirm_optin("+15551234567")
        assert has_consent("+15551234567") is True

    def test_optout_revokes_consent(self):
        from src.services.sms_consent import (
            confirm_optin, has_consent, is_opted_out, record_optin, record_optout,
        )
        record_optin("+15551234567")
        confirm_optin("+15551234567")
        record_optout("+15551234567")
        assert is_opted_out("+15551234567") is True
        assert has_consent("+15551234567") is False

    def test_start_after_stop_resubscribes(self):
        from src.services.sms_consent import (
            confirm_optin, has_consent, is_opted_out, record_optout,
        )
        record_optout("+15551234567")
        confirm_optin("+15551234567")
        assert is_opted_out("+15551234567") is False
        assert has_consent("+15551234567") is True

    def test_record_captures_audit_fields(self):
        from src.services.sms_consent import CONSENT_DISCLOSURE, get_record, record_optin
        record_optin("+15551234567", name="Ada", organization="Lincoln High",
                     ip="203.0.113.9", user_agent="pytest")
        rec = get_record("+15551234567")
        assert rec["consent_text"] == CONSENT_DISCLOSURE
        assert rec["ip"] == "203.0.113.9"
        assert rec["opted_in_at"]

    def test_consent_persists_to_jsonl(self, fresh_state):
        from src.services import sms_consent
        sms_consent.record_optin("+15551234567", name="Ada")
        sms_consent.reset()
        assert sms_consent.get_record("+15551234567")["name"] == "Ada"
        lines = [json.loads(x) for x in fresh_state.read_text().splitlines() if x.strip()]
        assert lines[-1]["phone"] == "+15551234567"

    def test_summary_counts(self):
        from src.services.sms_consent import confirm_optin, consent_summary, record_optin, record_optout
        record_optin("+15551110001")
        record_optin("+15551110002")
        confirm_optin("+15551110002")
        record_optin("+15551110003")
        record_optout("+15551110003")
        assert consent_summary() == {
            "total": 3, "pending": 1, "confirmed": 1, "opted_out": 1,
        }


class TestThrottle:
    def test_phone_throttled_after_limit(self):
        from src.services.sms_consent import MAX_PER_PHONE_PER_HOUR, allow_optin_attempt
        for _ in range(MAX_PER_PHONE_PER_HOUR):
            assert allow_optin_attempt("+15551234567", "203.0.113.9") is True
        assert allow_optin_attempt("+15551234567", "203.0.113.9") is False

    def test_ip_throttled_across_numbers(self):
        from src.services.sms_consent import MAX_PER_IP_PER_HOUR, allow_optin_attempt
        for i in range(MAX_PER_IP_PER_HOUR):
            assert allow_optin_attempt(f"+1555111{i:04d}", "203.0.113.9") is True
        assert allow_optin_attempt("+15559999999", "203.0.113.9") is False


class TestCarrierKeywords:
    def test_stop_opts_out(self):
        from src.services.sms_consent import is_opted_out
        from src.services.sms_transport import handle_inbound_sms
        result = handle_inbound_sms("+15551234567", "STOP")
        assert result["action"] == "opt_out"
        assert "unsubscribed" in result["twiml"].lower()
        assert is_opted_out("+15551234567") is True

    @pytest.mark.parametrize("word", ["stop", "STOPALL", "Unsubscribe", "CANCEL", "end", "quit"])
    def test_all_optout_keywords(self, word):
        from src.services.sms_transport import handle_inbound_sms
        assert handle_inbound_sms("+15551234567", word)["action"] == "opt_out"

    @pytest.mark.parametrize("word", ["START", "unstop", "YES", "join"])
    def test_all_optin_keywords(self, word):
        from src.services.sms_consent import has_consent
        from src.services.sms_transport import handle_inbound_sms
        assert handle_inbound_sms("+15551234567", word)["action"] == "opt_in"
        assert has_consent("+15551234567") is True

    def test_help_includes_required_disclosures(self):
        from src.services.sms_transport import handle_inbound_sms
        twiml = handle_inbound_sms("+15551234567", "HELP")["twiml"]
        assert "Msg &amp; data rates may apply" in twiml
        assert "STOP to cancel" in twiml
        assert "/sms-terms" in twiml

    def test_stop_beats_incident_classification(self):
        """A STOP mid-incident must unsubscribe, not open a new incident."""
        from src.services.sms_transport import handle_inbound_sms
        assert handle_inbound_sms("+15551234567", "Stop.")["action"] == "opt_out"

    def test_incident_ack_carries_optout_notice(self):
        from src.services.sms_transport import handle_inbound_sms
        result = handle_inbound_sms("+15551234567", "Smoke in the cafeteria, kids inside")
        assert result["action"] == "incident"
        assert "STOP to unsubscribe" in result["twiml"]


class TestOutboundSuppression:
    def test_send_suppressed_after_stop(self, monkeypatch):
        monkeypatch.setenv("TWILIO_ACCOUNT_SID", "AC_test")
        monkeypatch.setenv("TWILIO_AUTH_TOKEN", "tok")
        monkeypatch.setenv("TWILIO_PHONE_NUMBER", "+15550000000")
        from src.services.sms_consent import record_optout
        from src.services.sms_transport import send_sms
        record_optout("+15551234567")
        result = send_sms("+15551234567", "SITREP")
        assert result["delivered"] is False
        assert result["suppressed"] is True

    def test_send_allowed_without_optout(self, monkeypatch):
        monkeypatch.setenv("TWILIO_ACCOUNT_SID", "AC_test")
        monkeypatch.setenv("TWILIO_AUTH_TOKEN", "tok")
        monkeypatch.setenv("TWILIO_PHONE_NUMBER", "+15550000000")
        sent = {}

        class _Resp:
            ok = True
            status_code = 201

            def json(self):
                return {"sid": "SM123", "status": "queued"}

        def _fake_post(url, **kwargs):
            sent["to"] = kwargs["data"]["To"]
            return _Resp()

        import requests
        monkeypatch.setattr(requests, "post", _fake_post)
        from src.services.sms_transport import send_sms
        result = send_sms("+15551234567", "SITREP")
        assert result["delivered"] is True
        assert sent["to"] == "+15551234567"

"""Resolving an incident from any channel, and refusing to guess."""

import json
import os
from io import BytesIO

import pytest

from src.core import incident_resolve, incident_state
from src.core.knowledge_base import KnowledgeBase, init_knowledge_base
from src.core.server import CrisisMeshHandler
from src.agents.accountability.tools import _checkin_store

SEED_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "seed",
)


@pytest.fixture(autouse=True)
def fresh(tmp_path, monkeypatch):
    monkeypatch.setenv("CRISISMESH_CONSENT_LOG", str(tmp_path / "consent.jsonl"))
    monkeypatch.delenv("CRISISMESH_RESOLVE_TOKEN", raising=False)
    KnowledgeBase.reset()
    init_knowledge_base(SEED_DIR)
    incident_state.reset()
    _checkin_store.clear()
    yield
    incident_state.reset()
    _checkin_store.clear()
    KnowledgeBase.reset()


def _declare(incident_id="FIRE-2026-1", incident_type="fire"):
    incident_state.declare(
        incident_id,
        {"incident_id": incident_id,
         "classification": {"incident_type": incident_type, "severity": "high"}},
        source="sms",
    )


class MockHandler(CrisisMeshHandler):
    def __init__(self, method, path, body=None, headers=None):
        self.response_code = None
        self._headers = {}
        raw = json.dumps(body).encode() if body else b""
        self.rfile = BytesIO(raw)
        self.wfile = BytesIO()
        self.path = path
        self.command = method
        self.headers = {"Content-Length": str(len(raw)), **(headers or {})}
        getattr(self, f"do_{method}")()

    def send_response(self, code):
        self.response_code = code

    def send_header(self, k, v):
        self._headers[k] = v

    def end_headers(self):
        pass

    def json(self):
        return json.loads(self.wfile.getvalue())


class TestCoreResolve:
    def test_refuses_with_no_active_incident(self):
        with pytest.raises(incident_resolve.ResolveRefused) as exc:
            incident_resolve.resolve("FIRE-1", resolved_by="tester")
        assert exc.value.code == "no_active_incident"

    def test_refuses_a_mismatched_id(self):
        """A stale tab or replayed request must not end a different incident."""
        _declare("FIRE-2026-1")
        with pytest.raises(incident_resolve.ResolveRefused) as exc:
            incident_resolve.resolve("THREAT-OLD-9", resolved_by="tester")
        assert exc.value.code == "incident_mismatch"
        assert incident_state.is_active() is True

    def test_resolves_and_clears(self):
        _declare("FIRE-2026-1")
        report = incident_resolve.resolve("FIRE-2026-1", resolved_by="U_PRINCIPAL")
        assert report["resolved"] is True
        assert report["incident_id"] == "FIRE-2026-1"
        assert incident_state.is_active() is False

    def test_report_carries_accountability(self):
        from src.agents.accountability.tools import process_checkin, send_checkin_request
        _declare("FIRE-2026-1")
        send_checkin_request("FIRE-2026-1", facility_id="jefferson")
        process_checkin("FIRE-2026-1", "p001", "safe")
        report = incident_resolve.resolve("FIRE-2026-1", resolved_by="tester")
        assert report["accountability"]["total_tracked"] > 0
        assert report["accountability"]["counts"]["safe"] == 1

    def test_build_report_does_not_resolve(self):
        _declare("FIRE-2026-1")
        incident_resolve.build_report(resolved_by="tester")
        assert incident_state.is_active() is True


class TestHttpEndpoint:
    def test_resolves_the_active_incident(self):
        _declare("FIRE-2026-1")
        h = MockHandler("POST", "/incident/FIRE-2026-1/resolve",
                        {"resolved_by": "console-operator"})
        assert h.response_code == 200
        assert h.json()["resolved"] is True
        assert incident_state.is_active() is False

    def test_requires_an_attributable_resolver(self):
        _declare("FIRE-2026-1")
        h = MockHandler("POST", "/incident/FIRE-2026-1/resolve", {})
        assert h.response_code == 400
        assert incident_state.is_active() is True

    def test_mismatched_id_conflicts(self):
        _declare("FIRE-2026-1")
        h = MockHandler("POST", "/incident/SOMETHING-ELSE/resolve",
                        {"resolved_by": "operator"})
        assert h.response_code == 409
        assert incident_state.is_active() is True

    def test_no_active_incident_is_404(self):
        h = MockHandler("POST", "/incident/FIRE-2026-1/resolve",
                        {"resolved_by": "operator"})
        assert h.response_code == 404

    def test_malformed_path_is_404(self):
        _declare("FIRE-2026-1")
        h = MockHandler("POST", "/incident/resolve", {"resolved_by": "operator"})
        assert h.response_code == 404
        assert incident_state.is_active() is True


class TestResolveToken:
    def test_token_required_when_configured(self, monkeypatch):
        monkeypatch.setenv("CRISISMESH_RESOLVE_TOKEN", "s3cret")
        _declare("FIRE-2026-1")
        h = MockHandler("POST", "/incident/FIRE-2026-1/resolve",
                        {"resolved_by": "operator"})
        assert h.response_code == 403
        assert incident_state.is_active() is True

    def test_correct_token_in_header_is_accepted(self, monkeypatch):
        monkeypatch.setenv("CRISISMESH_RESOLVE_TOKEN", "s3cret")
        _declare("FIRE-2026-1")
        h = MockHandler("POST", "/incident/FIRE-2026-1/resolve",
                        {"resolved_by": "operator"},
                        headers={"X-CrisisMesh-Token": "s3cret"})
        assert h.response_code == 200

    def test_wrong_token_refused(self, monkeypatch):
        monkeypatch.setenv("CRISISMESH_RESOLVE_TOKEN", "s3cret")
        _declare("FIRE-2026-1")
        h = MockHandler("POST", "/incident/FIRE-2026-1/resolve",
                        {"resolved_by": "operator", "token": "guess"})
        assert h.response_code == 403
        assert incident_state.is_active() is True


class TestAllClearFanOut:
    def test_resolving_triggers_the_all_clear(self, monkeypatch):
        from src.core import notify
        from src.core.event_bus import EventBus
        from src.services import sms_consent

        EventBus.reset()
        notify.unsubscribe_for_tests()
        notify.subscribe()
        sms_consent.reset()
        sms_consent.confirm_optin("+16155550101")

        sent = []
        monkeypatch.setattr(
            "src.services.sms_transport.send_sms",
            lambda to, body: (sent.append(body), {"delivered": True})[1],
        )

        _declare("FIRE-2026-1")
        incident_resolve.resolve("FIRE-2026-1", resolved_by="operator", channel="http")

        import time
        for _ in range(50):
            if sent:
                break
            time.sleep(0.02)
        assert sent, "no all-clear fan-out fired"
        assert "ALL CLEAR" in sent[0]
        sms_consent.reset()

    def test_lockdown_all_clear_warns_against_unlocking(self, monkeypatch):
        from src.core import notify
        from src.core.event_bus import EventBus
        from src.services import sms_consent

        EventBus.reset()
        notify.unsubscribe_for_tests()
        notify.subscribe()
        sms_consent.reset()
        sms_consent.confirm_optin("+16155550101")

        sent = []
        monkeypatch.setattr(
            "src.services.sms_transport.send_sms",
            lambda to, body: (sent.append(body), {"delivered": True})[1],
        )

        _declare("THREAT-1", incident_type="active_threat")
        incident_resolve.resolve("THREAT-1", resolved_by="operator", channel="http")

        import time
        for _ in range(50):
            if sent:
                break
            time.sleep(0.02)
        assert sent, "no all-clear fan-out fired"
        assert "Do NOT unlock" in sent[0]
        sms_consent.reset()

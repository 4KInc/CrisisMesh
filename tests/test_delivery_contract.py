"""The delivery seam, pinned before it is wired.

Everything built so far has the property that a bug's worst consequence stays
inside the system: a wrong transition, a lost check-in, a hung tick, a re-alarm.
The intent-recording boundary is what guaranteed that. Delivery removes it — the
first time the loop can send is the first time a bug pages a real person during
what they will read as a real emergency.

These are world-claims, in the form the last four bugs proved is the only form
that catches a correct-producer wired wrongly to a correct-sender. They are
skipped until delivery exists; whoever wires it removes the skip and makes them
pass, and the shape of the fix is dictated by what they demand rather than
retrofitted to it.

Six return paths, one flag, four distinct required behaviours. `send_sms`
returns `delivered: False` for: opted out, mode off, not configured, HTTP
rejection, a 2xx carrying a terminal status, and a thrown call. The first three
are known-not-sent and must never be retried — they are decisions, not
failures. The next two are known-not-sent and must be retried. The last is
unknown and must be retried while being recorded as unknown.

Rejected and unknown come from different code paths and must stay separate at
the source: the terminal-status inspection genuinely knows the provider refused,
while the exception boundary genuinely does not know whether the request reached
the wire before it timed out. Collapsing the first into the second because both
are "not delivered" re-creates this bug one level up, with better wording.

`unknown` has to be a returned value, not an exception the caller catches and
reinterprets — from inside the process, a timeout on a request that already hit
the wire is indistinguishable from one that never did, and sometimes the answer
stays unknown until a delivery receipt arrives asynchronously or never comes.

Three outcomes, not two. `send_sms` currently catches its own exception and
returns `delivered: False, "transport error, nothing was sent"` — so at the
boundary the loop decides on, a carrier refusal and a call that never completed
are indistinguishable, and the detail asserts nothing was sent when the honest
claim is that we do not know. A timeout after the request left the process may
have delivered. The transport has to surface accepted / rejected / unknown
before the loop can honour the distinction.
"""

import pytest

import os

from src.core import (
    incident_state,
    notify,
    reconciliation as rec,
    reconciliation_loop as loop,
    reconciliation_store as store,
)
from src.core.knowledge_base import KnowledgeBase, init_knowledge_base
from src.services import sms_transport

SEED_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "seed")


@pytest.fixture(autouse=True)
def fresh(tmp_path, monkeypatch):
    monkeypatch.setenv("CRISISMESH_CONSENT_LOG", str(tmp_path / "c.jsonl"))
    monkeypatch.setenv("CRISISMESH_RECONCILIATION_STORE", "memory")
    monkeypatch.setenv("CRISISMESH_INCIDENT_STORE", "memory")
    monkeypatch.setenv("CRISISMESH_DELIVERY", "on")
    monkeypatch.setenv("CRISISMESH_REPING_CAP", "2")
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setattr(notify, "_slack_ready", lambda: True)
    monkeypatch.setattr(notify, "slack_id_resolves", lambda sid: sid == "U0REAL")
    monkeypatch.setenv("CRISISMESH_DEMO_SLACK_MAP", "p001=U0REAL")
    notify.reset_slack_id_cache()
    KnowledgeBase.reset()
    init_knowledge_base(SEED_DIR)
    store.reset_backend()
    rec.reset()
    loop.reset()
    incident_state.declare(
        "T-1", {"incident_id": "T-1",
                "classification": {"incident_type": "active_threat", "severity": "critical"},
                "assembly": {"name": "Athletic Field"}},
        source="slack")
    yield
    notify.reset_slack_id_cache()
    rec.reset()
    loop.reset()
    incident_state.reset()
    KnowledgeBase.reset()


def _wire(monkeypatch, outcome, detail=""):
    """Make every send report one outcome, and capture what reached the wire."""
    sent = []

    def _fake(reach, message):
        sent.append({"person_id": reach.person_id, "channel": reach.channel,
                     "address": reach.address, "message": message})
        return {"delivered": outcome == "accepted", "outcome": outcome,
                "detail": detail or outcome}

    monkeypatch.setattr(notify, "_send", _fake)
    return sent


class TestOnlyTheLoopsIntentsReachTheWire:
    def test_nothing_sends_when_delivery_is_off(self, monkeypatch):
        monkeypatch.setenv("CRISISMESH_DELIVERY", "off")
        sent = _wire(monkeypatch, "accepted")
        loop.run_tick("T-1")
        assert sent == [], "delivery fired with the switch off"

    def test_only_reachable_people_reach_the_wire(self, monkeypatch):
        sent = _wire(monkeypatch, "accepted")
        loop.run_tick("T-1")
        assert {s["person_id"] for s in sent} == {"p001"}

    def test_each_intent_reaches_the_wire_exactly_once_per_tick(self, monkeypatch):
        sent = _wire(monkeypatch, "accepted")
        loop.run_tick("T-1")
        assert len(sent) == 1

    def test_an_accounted_person_is_never_sent_to(self, monkeypatch):
        rec.record_checkin("T-1", "p001", source="whatsapp")
        sent = _wire(monkeypatch, "accepted")
        loop.run_tick("T-1")
        assert sent == []


class TestOutcomeDecidesWhetherWeChaseAgain:
    def test_an_accepted_send_advances_attempts_and_is_not_rechased(self, monkeypatch):
        sent = _wire(monkeypatch, "accepted")
        loop.run_tick("T-1")
        assert rec.get_state("T-1", "p001").attempts == 1
        assert len(sent) == 1

    def test_a_rejected_send_does_not_advance_attempts_and_is_rechased(self, monkeypatch):
        sent = _wire(monkeypatch, "rejected", "carrier refused")
        loop.run_tick("T-1")
        assert rec.get_state("T-1", "p001").attempts == 0, "a refusal counted as a ping"
        loop.run_tick("T-1")
        assert len(sent) == 2, "the person was not chased again"

    def test_an_unknown_send_is_rechased_and_recorded_as_unknown(self, monkeypatch):
        sent = _wire(monkeypatch, "unknown", "the call did not complete")
        loop.run_tick("T-1")
        assert rec.get_state("T-1", "p001").attempts == 0
        recorded = [i for i in loop.intents("T-1") if i["person_id"] == "p001"]
        assert recorded[0]["outcome"] == "unknown", "an unknown send was recorded as failed"
        loop.run_tick("T-1")
        assert len(sent) == 2

    def test_a_suppressed_send_is_not_retried(self, monkeypatch):
        sent = _wire(monkeypatch, "suppressed", "recipient replied STOP")
        loop.run_tick("T-1")
        loop.run_tick("T-1")
        assert len(sent) == 1, "the system argued with a STOP"
        assert rec.get_state("T-1", "p001").status == rec.UNREACHABLE

    def test_escalation_happens_only_after_accepted_repings(self, monkeypatch):
        _wire(monkeypatch, "accepted")
        loop.run_tick("T-1")
        loop.run_tick("T-1")
        third = loop.run_tick("T-1")
        actions = {i["action"] for i in third["intents"] if i["person_id"] == "p001"}
        assert loop.ACTION_ESCALATE in actions

    def test_rejections_never_reach_the_escalation_cap(self, monkeypatch):
        """Attempts that never landed must not add up to "unanswered"."""
        _wire(monkeypatch, "rejected")
        for _ in range(4):
            loop.run_tick("T-1")
        assert rec.get_state("T-1", "p001").attempts == 0


class TestTheCriticIsOnTheDeliveryPath:
    def test_a_contradicting_message_is_stripped_before_the_wire(self, monkeypatch):
        """A claim about the wiring, not about enforce(). It only holds if the
        loop actually transmits through the funnel the critic guards."""
        wire = []

        def _capture(url=None, **kwargs):
            raise AssertionError("should not reach a transport directly")

        def _fake_channel_send(reach, message):
            from src.core import movement_policy
            cleaned, violation = movement_policy.enforce(
                "active_threat", message, assembly_name="Athletic Field",
                surface=f"fanout_{reach.channel}")
            wire.append(cleaned)
            return {"delivered": True, "outcome": "accepted", "detail": "ok"}

        monkeypatch.setattr(notify, "_send", _fake_channel_send)
        monkeypatch.setattr(loop, "_reping_message",
                            lambda *a, **kw: "Proceed to Athletic Field and evacuate now.")
        loop.run_tick("T-1")
        assert wire, "nothing reached the wire"
        assert "Athletic Field" not in wire[0]
        assert "withheld" in wire[0].lower()

    def test_the_loop_has_no_second_door_to_the_wire(self):
        """One funnel. If the loop calls a transport directly, the critic and
        the outcome recording are both bypassable."""
        import inspect

        source = inspect.getsource(loop)
        for forbidden in ("send_sms(", "send_whatsapp(", "chat_postMessage("):
            assert forbidden not in source, f"the loop calls {forbidden} directly"
        assert "notify.deliver(" in source


class TestTheTransportTellsTheTruthAboutWhatItKnows:
    def test_a_thrown_send_is_not_reported_as_nothing_was_sent(self, monkeypatch):
        monkeypatch.setenv("TWILIO_ACCOUNT_SID", "AC_t")
        monkeypatch.setenv("TWILIO_AUTH_TOKEN", "tok")
        monkeypatch.setenv("TWILIO_PHONE_NUMBER", "+15550000000")
        monkeypatch.delenv("CRISISMESH_SMS_MODE", raising=False)

        import requests

        def _boom(url, **kwargs):
            raise RuntimeError("connection reset")

        monkeypatch.setattr(requests, "post", _boom)
        result = sms_transport.send_sms("+15551110000", "hello")
        assert result["outcome"] == sms_transport.OUTCOME_UNKNOWN
        assert "nothing was sent" not in result["detail"], (
            "asserted more than the code knows — the request may have arrived"
        )

    def test_rejected_and_unknown_are_distinguishable(self, monkeypatch):
        monkeypatch.setenv("TWILIO_ACCOUNT_SID", "AC_t")
        monkeypatch.setenv("TWILIO_AUTH_TOKEN", "tok")
        monkeypatch.setenv("TWILIO_PHONE_NUMBER", "+15550000000")
        monkeypatch.delenv("CRISISMESH_SMS_MODE", raising=False)

        class _Terminal:
            ok = True
            status_code = 201

            def json(self):
                return {"sid": "SM1", "status": "undelivered",
                        "error_message": "unreachable"}

        import requests
        monkeypatch.setattr(requests, "post", lambda url, **kw: _Terminal())
        assert sms_transport.send_sms("+15551110000", "x")["outcome"] == \
            sms_transport.OUTCOME_REJECTED

    def test_a_decision_is_not_a_failure(self, monkeypatch):
        """Opted out is suppressed, not rejected — different retry behaviour."""
        from src.services import sms_consent

        monkeypatch.setenv("TWILIO_ACCOUNT_SID", "AC_t")
        monkeypatch.setenv("TWILIO_AUTH_TOKEN", "tok")
        monkeypatch.setenv("TWILIO_PHONE_NUMBER", "+15550000000")
        sms_consent.record_optout("+15551110000")
        assert sms_transport.send_sms("+15551110000", "x")["outcome"] == \
            sms_transport.OUTCOME_SUPPRESSED
        sms_consent.reset()


class TestTheSwitchGovernsEveryPathToTheWire:
    """CRISISMESH_DELIVERY gated the reconciliation loop but not the
    declare-time fan-out, so the switch reported off while lockdown alerts were
    still leaving the platform. A kill switch that covers one of two doors is
    not a kill switch."""

    def test_the_declare_fanout_is_suppressed_when_delivery_is_off(self, monkeypatch):
        monkeypatch.setenv("CRISISMESH_DELIVERY", "off")
        sent = []
        monkeypatch.setattr(notify, "_send",
                            lambda reach, msg: sent.append(msg) or {"delivered": True})
        result = notify.announce_incident({
            "incident_id": "T-1", "report": "shooter",
            "classification": {"incident_type": "active_threat", "severity": "critical"},
        })
        assert sent == [], "the fan-out sent with the switch off"
        assert result.kind.endswith("suppressed")

    def test_the_all_clear_is_suppressed_too(self, monkeypatch):
        monkeypatch.setenv("CRISISMESH_DELIVERY", "off")
        sent = []
        monkeypatch.setattr(notify, "_send",
                            lambda reach, msg: sent.append(msg) or {"delivered": True})
        notify.announce_resolution({"incident_id": "T-1", "incident_type": "fire"})
        assert sent == []

    def test_the_fanout_sends_when_the_switch_is_on(self, monkeypatch):
        monkeypatch.setenv("CRISISMESH_DELIVERY", "on")
        sent = []
        monkeypatch.setattr(notify, "_send",
                            lambda reach, msg: sent.append(msg) or
                            {"delivered": True, "outcome": "accepted"})
        notify.announce_incident({
            "incident_id": "T-1", "report": "shooter",
            "classification": {"incident_type": "active_threat", "severity": "critical"},
        })
        assert sent, "the fan-out was suppressed with the switch on"


class TestTheMessageAsksForSomethingPossible:
    """A Slack app DM can have inbound messages disabled in the app config, in
    which case "Reply SAFE" instructs something the recipient cannot do."""

    def test_slack_recipients_are_told_a_route_that_works(self):
        message = loop._reping_message("T-1", "Principal Johnson", channel="slack")
        assert "/checkin" in message
        assert "Reply SAFE" not in message

    def test_phone_recipients_are_still_told_to_reply(self):
        message = loop._reping_message("T-1", "Principal Johnson", channel="sms")
        assert "Reply SAFE" in message

    def test_repeat_requests_are_distinguishable(self):
        """Two identical messages read as a duplicate rather than a second ask."""
        first = loop._reping_message("T-1", "X", channel="sms", attempt=1)
        second = loop._reping_message("T-1", "X", channel="sms", attempt=2)
        assert first != second
        assert "request 2" in second

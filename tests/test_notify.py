"""Tests for cross-channel fan-out."""

import os

import pytest

from src.core import incident_state, notify
from src.core.knowledge_base import KnowledgeBase, init_knowledge_base
from src.services import sms_consent, whatsapp_transport

SEED_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "seed",
)


@pytest.fixture(autouse=True)
def fresh(tmp_path, monkeypatch):
    monkeypatch.setenv("CRISISMESH_CONSENT_LOG", str(tmp_path / "consent.jsonl"))
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    monkeypatch.setenv("CRISISMESH_WHATSAPP_MODE", "twilio")
    sms_consent.reset()
    whatsapp_transport.reset_session_windows()
    notify.reset()
    KnowledgeBase.reset()
    init_knowledge_base(SEED_DIR)
    incident_state.reset()
    yield
    sms_consent.reset()
    whatsapp_transport.reset_session_windows()
    notify.reset()
    KnowledgeBase.reset()
    incident_state.reset()


def _person(phone="615-555-0101", slack_id="", pid="p001"):
    return {"person_id": pid, "name": "Principal Johnson",
            "phone": phone, "slack_user_id": slack_id}


class TestReachResolution:
    def test_no_channel_without_consent_or_session(self):
        reach = notify.resolve_reach(_person())
        assert reach.reachable is False
        assert "no confirmed opt-in" in reach.reason

    def test_confirmed_optin_makes_sms_reachable(self):
        sms_consent.confirm_optin("+16155550101")
        reach = notify.resolve_reach(_person())
        assert reach.channel == notify.CHANNEL_SMS
        assert reach.address == "+16155550101"

    def test_opted_out_is_not_reachable_by_sms(self):
        sms_consent.confirm_optin("+16155550101")
        sms_consent.record_optout("+16155550101")
        reach = notify.resolve_reach(_person())
        assert reach.channel != notify.CHANNEL_SMS
        assert "opted out" in reach.reason

    def test_open_whatsapp_window_is_reachable(self):
        whatsapp_transport.note_inbound("+16155550101")
        reach = notify.resolve_reach(_person())
        assert reach.channel == notify.CHANNEL_WHATSAPP

    def test_sms_consent_wins_over_whatsapp_window(self):
        sms_consent.confirm_optin("+16155550101")
        whatsapp_transport.note_inbound("+16155550101")
        assert notify.resolve_reach(_person()).channel == notify.CHANNEL_SMS

    def test_slack_used_when_phone_channels_are_closed(self, monkeypatch):
        monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
        from src.services import slack_transport
        if slack_transport.WebClient is None:
            pytest.skip("slack_sdk not installed")
        reach = notify.resolve_reach(_person(slack_id="U_PRINCIPAL"))
        assert reach.channel == notify.CHANNEL_SLACK

    def test_person_with_no_phone_and_no_slack_is_unreachable(self):
        reach = notify.resolve_reach(_person(phone="", slack_id=""))
        assert reach.reachable is False
        assert "no phone number" in reach.reason


class TestFanOut:
    def test_unreachable_roster_is_named_not_counted_as_sent(self):
        """Nobody has opted in, so nobody is reachable — and the result says who."""
        result = notify.fan_out("test alert", incident_id="INC-1")
        assert result.notified == 0
        assert result.unreachable == len(KnowledgeBase.get().personnel)
        assert result.unreachable_people[0]["reason"]

    def test_consented_person_is_sent_to(self, monkeypatch):
        sms_consent.confirm_optin("+16155550101")
        sent = []
        monkeypatch.setattr(
            "src.services.sms_transport.send_sms",
            lambda to, body: (sent.append((to, body)), {"delivered": True})[1],
        )
        result = notify.fan_out("LOCKDOWN NOW", incident_id="INC-1")
        assert result.notified == 1
        assert result.by_channel["sms"] == 1
        assert sent[0][0] == "+16155550101"
        assert sent[0][1] == "LOCKDOWN NOW"

    def test_reporter_is_excluded(self, monkeypatch):
        sms_consent.confirm_optin("+16155550101")
        monkeypatch.setattr("src.services.sms_transport.send_sms",
                            lambda to, body: {"delivered": True})
        result = notify.fan_out("alert", exclude=("+16155550101",))
        assert result.skipped == 1
        assert result.notified == 0

    def test_delivery_failure_is_recorded_not_counted(self, monkeypatch):
        sms_consent.confirm_optin("+16155550101")
        monkeypatch.setattr(
            "src.services.sms_transport.send_sms",
            lambda to, body: {"delivered": False, "detail": "undelivered"},
        )
        result = notify.fan_out("alert")
        assert result.notified == 0
        assert result.failed == 1
        assert result.failures[0]["detail"] == "undelivered"

    def test_one_send_raising_does_not_abort_the_fan_out(self, monkeypatch):
        for phone in ("+16155550101", "615-555-0103"):
            sms_consent.confirm_optin(phone)

        def _explode(to, body):
            if to == "+16155550101":
                raise RuntimeError("carrier down")
            return {"delivered": True}

        monkeypatch.setattr("src.services.sms_transport.send_sms", _explode)
        result = notify.fan_out("alert")
        assert result.failed == 1
        assert result.notified == 1

    def test_result_is_retrievable_after_the_run(self, monkeypatch):
        notify.fan_out("alert", incident_id="INC-9")
        assert notify.get_last_result()["incident_id"] == "INC-9"


class TestMessages:
    def test_alert_names_type_severity_and_id(self):
        msg = notify.compose_alert({
            "incident_id": "FIRE-1",
            "classification": {"incident_type": "fire", "severity": "critical"},
            "location": {"zone_name": "Science Lab"},
            "assembly": {"name": "Athletic Field"},
        })
        assert "FIRE" in msg
        assert "critical" in msg
        assert "Science Lab" in msg
        assert "Athletic Field" in msg
        assert "FIRE-1" in msg
        assert "911" in msg

    def test_all_clear_names_the_incident(self):
        msg = notify.compose_all_clear({"incident_id": "INC-1", "elapsed_minutes": 12})
        assert "ALL CLEAR" in msg
        assert "INC-1" in msg
        assert "12 min" in msg


class TestEventWiring:
    def test_subscribe_is_idempotent(self):
        from src.core.event_bus import EventBus
        EventBus.reset()
        notify.unsubscribe_for_tests()
        notify.subscribe()
        notify.subscribe()
        bus = EventBus.get()
        handlers = bus._subscribers.get(str(notify.EventType.INCIDENT_DECLARED), [])
        assert len(handlers) == 1

    def test_declaring_an_incident_triggers_a_fan_out(self, monkeypatch):
        """The end-to-end point: declare on one channel, everyone else hears."""
        from src.core.event_bus import EventBus
        from src.services.slack_transport import run_incident_pipeline

        EventBus.reset()
        notify.unsubscribe_for_tests()
        notify.subscribe()
        sms_consent.confirm_optin("+16155550101")

        sent = []
        monkeypatch.setattr(
            "src.services.sms_transport.send_sms",
            lambda to, body: (sent.append(to), {"delivered": True})[1],
        )

        run_incident_pipeline("Shooter in the west hallway", source="slack")

        import time
        for _ in range(50):
            if sent:
                break
            time.sleep(0.02)
        assert sent == ["+16155550101"]


class TestLockdownMessaging:
    """The guidance that saves people in a fire is the guidance that kills them
    in a shooting, so the lockdown message is a different message."""

    LOCKDOWN = {
        "incident_id": "THREAT-1",
        "classification": {"incident_type": "active_threat", "severity": "critical"},
        "location": {"zone_name": "West Hallway"},
        "assembly": {"name": "Athletic Field"},
    }

    def test_opens_with_the_silence_instruction(self):
        """Lock-screen previews truncate early — it has to be readable without
        unlocking, because unlocking means looking at a lit screen."""
        msg = notify.compose_alert(self.LOCKDOWN)
        assert "SILENCE YOUR PHONE NOW" in msg[:60]

    def test_never_broadcasts_the_assembly_point(self):
        """A named open space is where a shooter would expect people to gather."""
        msg = notify.compose_alert(self.LOCKDOWN)
        assert "Athletic Field" not in msg
        assert "Assembly" not in msg

    def test_directs_a_silent_reply_not_a_call(self):
        msg = notify.compose_alert(self.LOCKDOWN)
        assert "Reply SOS silently" in msg
        assert "safe to speak" in msg

    def test_does_not_order_a_general_evacuation(self):
        msg = notify.compose_alert(self.LOCKDOWN)
        assert "lock and barricade" in msg
        assert "route confirmed clear" in msg

    def test_warns_against_the_fire_alarm(self):
        assert "Do NOT pull the fire alarm" in notify.compose_alert(self.LOCKDOWN)

    def test_bomb_threat_uses_the_lockdown_form(self):
        msg = notify.compose_alert({
            "incident_id": "BOMB-1",
            "classification": {"incident_type": "bomb_threat", "severity": "critical"},
        })
        assert "BOMB THREAT" in msg
        assert "SILENCE YOUR PHONE NOW" in msg

    def test_fire_still_gets_the_assembly_point(self):
        msg = notify.compose_alert({
            "incident_id": "FIRE-1",
            "classification": {"incident_type": "fire", "severity": "high"},
            "assembly": {"name": "Athletic Field"},
        })
        assert "Athletic Field" in msg
        assert "SILENCE" not in msg


class TestLockdownAllClear:
    def test_never_tells_someone_to_unlock(self):
        """A text can be premature, wrong, or spoofed, and the person reading it
        cannot verify it. It must not be what opens a barricaded door."""
        msg = notify.compose_all_clear({
            "incident_id": "THREAT-1", "elapsed_minutes": 22,
            "incident_type": "active_threat",
        })
        assert "Do NOT unlock" in msg
        assert "in person" in msg

    def test_ordinary_all_clear_is_unchanged(self):
        msg = notify.compose_all_clear({
            "incident_id": "FIRE-1", "elapsed_minutes": 9, "incident_type": "fire",
        })
        assert "No further check-ins needed" in msg
        assert "Do NOT unlock" not in msg


class TestLockdownFanOutSuppression:
    """Each extra message is another buzz in a room where someone is hiding."""

    @pytest.mark.parametrize("kind", ["declared", "resolved"])
    def test_alert_and_all_clear_still_go_out(self, kind):
        assert notify.should_fan_out(kind, "active_threat") is True

    @pytest.mark.parametrize("kind", ["sitrep", "update", "reminder"])
    def test_everything_else_is_held(self, kind):
        assert notify.should_fan_out(kind, "active_threat") is False

    @pytest.mark.parametrize("kind", ["sitrep", "update", "reminder"])
    def test_non_lockdown_incidents_are_unrestricted(self, kind):
        assert notify.should_fan_out(kind, "fire") is True

    def test_lockdown_types_match_the_safety_backstop(self):
        """The notifier and the backstop must not drift on what counts."""
        from src.core.tactical_reasoning import LOCKDOWN_TYPES
        assert notify.LOCKDOWN_TYPES is LOCKDOWN_TYPES


class TestWebConsoleParity:
    """POST /incident duplicates the pipeline body rather than calling it, so it
    is the path most likely to drift. A console-declared lockdown must produce
    the lockdown message, not the evacuation one."""

    def test_console_declaration_fans_out_with_the_right_wording(self, monkeypatch):
        from src.core.event_bus import EventBus
        from src.services.slack_transport import run_incident_pipeline

        EventBus.reset()
        notify.unsubscribe_for_tests()
        notify.subscribe()
        sms_consent.confirm_optin("+16155550101")

        sent = []
        monkeypatch.setattr(
            "src.services.sms_transport.send_sms",
            lambda to, body: (sent.append(body), {"delivered": True})[1],
        )

        run_incident_pipeline("Armed intruder near the west hallway", source="web")

        import time
        for _ in range(50):
            if sent:
                break
            time.sleep(0.02)
        assert sent, "no fan-out fired"
        assert "SILENCE YOUR PHONE NOW" in sent[0]
        assert "Assembly" not in sent[0]


class TestUnclassifiedFanOut:
    """A wrong number must not page a roster; an unnameable emergency must."""

    @pytest.mark.parametrize("severity", ["low", "moderate"])
    def test_routine_unclassified_is_held(self, severity):
        assert notify.should_fan_out("declared", "other", severity) is False

    @pytest.mark.parametrize("severity", ["high", "critical"])
    def test_urgent_unclassified_still_goes_out(self, severity):
        assert notify.should_fan_out("declared", "other", severity) is True

    def test_known_types_are_unaffected_by_severity(self):
        assert notify.should_fan_out("declared", "fire", "low") is True

    def test_announce_suppresses_a_noise_report(self):
        result = notify.announce_incident({
            "incident_id": "OTHER-1",
            "report": "hi",
            "classification": {"incident_type": "other", "severity": "moderate"},
        })
        assert result.kind == "suppressed"
        assert result.notified == 0

    def test_unclassified_alert_quotes_the_report(self):
        msg = notify.compose_alert({
            "incident_id": "OTHER-1",
            "report": "Something is very wrong, students still inside",
            "classification": {"incident_type": "other", "severity": "high"},
        })
        assert "UNCLASSIFIED INCIDENT" in msg
        assert "students still inside" in msg
        assert "911" in msg


class TestSlackIdMustResolve:
    """A string in the slack_user_id column is not a person. The seed roster
    carries U_PRINCIPAL, U_VP and so on — ids in shape, addressing nobody — and
    with a bot token present the loop reported 34 reachable when the true
    figure was roughly the reverse. Optimistic-green in production."""

    @pytest.fixture(autouse=True)
    def slack_configured(self, monkeypatch):
        # slack_sdk is absent locally and present on Cloud Run, which is the
        # fuller reason the deployed trace diverged from the in-process one.
        monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
        monkeypatch.setattr(notify, "_slack_ready", lambda: True)
        notify.reset_slack_id_cache()
        yield
        notify.reset_slack_id_cache()

    def test_a_placeholder_id_is_unreachable(self, monkeypatch):
        monkeypatch.setattr(notify, "slack_id_resolves", lambda sid: False)
        reach = notify.resolve_reach(_person(slack_id="U_PRINCIPAL"))
        assert reach.reachable is False
        assert notify.REASON_SLACK_UNVERIFIED in reach.reason

    def test_the_reason_is_distinguishable_from_having_no_id(self, monkeypatch):
        """"Reach her by radio, her id does not resolve" is a different
        instruction from "no channel on file"."""
        monkeypatch.setattr(notify, "slack_id_resolves", lambda sid: False)
        unverified = notify.resolve_reach(_person(slack_id="U_PRINCIPAL")).reason
        missing = notify.resolve_reach(_person(slack_id="")).reason
        assert unverified != missing
        assert notify.REASON_NO_SLACK_ID in missing

    def test_a_resolving_id_is_reachable(self, monkeypatch):
        monkeypatch.setattr(notify, "slack_id_resolves", lambda sid: True)
        reach = notify.resolve_reach(_person(slack_id="U0123REAL"))
        assert reach.channel == notify.CHANNEL_SLACK

    def test_a_lookup_failure_counts_as_unreachable(self, monkeypatch):
        """Fails closed. Returning True "to be safe" is failing open wearing a
        helmet — the person stops being chased and the IC is never told."""
        class _Boom:
            def __init__(self, token):
                pass

            def users_info(self, user):
                raise RuntimeError("rate limited")

        monkeypatch.setattr("src.services.slack_transport.WebClient", _Boom)
        assert notify.slack_id_resolves("U_ANY") is False

    def test_a_missing_token_counts_as_unverified(self, monkeypatch):
        monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
        assert notify.slack_id_resolves("U_ANY") is False

    def test_verification_is_cached_per_id(self, monkeypatch):
        """34 ids x every tick would be rate-limit surface and latency inside a
        synchronous /tick."""
        calls = {"n": 0}

        class _Counting:
            def __init__(self, token):
                pass

            def users_info(self, user):
                calls["n"] += 1
                return {"ok": True}

        monkeypatch.setattr("src.services.slack_transport.WebClient", _Counting)
        for _ in range(5):
            notify.slack_id_resolves("U0123REAL")
        assert calls["n"] == 1

    def test_the_cache_can_be_invalidated_for_a_roster_reload(self, monkeypatch):
        monkeypatch.setattr(notify, "slack_id_resolves", lambda sid: True)
        notify.reset_slack_id_cache()
        assert notify._slack_id_cache == {}

"""The integrator: what calls the units on a schedule.

890 tests exercise `tick_person`, `transition`, `commit_transition`. Nothing
exercises the thing that runs them on a timer, and orchestration has its own
bug class — did the loop visit everyone, advance the counter once, survive a
per-person throw without dying or double-counting the tick.

Two boundaries this file holds:

  The dict is a weaker-concurrency and faster-timing environment than a network
  store, so any timing assumption the loop makes will hold here and may not
  hold under latency. Re-entrancy is therefore pinned explicitly rather than
  left to be true by accident.

  The timer records intents. It does not send. Delivery is the one place a bug
  has a consequence outside the system, so it is wired last — after the loop
  has been watched running over several ticks.
"""

import threading
import time

import pytest

from src.core import reconciliation as rec
from src.core import reconciliation_loop as loop
from src.core import reconciliation_store as store
from src.core import incident_state
from src.core.knowledge_base import KnowledgeBase, init_knowledge_base

import os
SEED_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "seed")


@pytest.fixture(autouse=True)
def fresh(tmp_path, monkeypatch):
    monkeypatch.setenv("CRISISMESH_CONSENT_LOG", str(tmp_path / "c.jsonl"))
    monkeypatch.setenv("CRISISMESH_RECONCILIATION_STORE", "memory")
    monkeypatch.setenv("CRISISMESH_DELIVERY", "on")
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    KnowledgeBase.reset()
    init_knowledge_base(SEED_DIR)
    store.reset_backend()
    rec.reset()
    loop.reset()
    incident_state.declare(
        "T-1", {"incident_id": "T-1",
                "classification": {"incident_type": "active_threat", "severity": "critical"}},
        source="slack")
    yield
    loop.stop()
    loop.reset()
    rec.reset()
    incident_state.reset()
    KnowledgeBase.reset()


class TestItRecordsIntentsNotSends:
    """The hard boundary. Delivery goes last."""

    def test_a_tick_produces_intents(self):
        result = loop.run_tick("T-1")
        assert result["intents"]
        assert all("action" in i and "person_id" in i for i in result["intents"])

    def test_nothing_is_actually_sent(self, monkeypatch):
        def _explode(*a, **kw):
            raise AssertionError("the timer must not deliver")

        monkeypatch.setattr("src.services.sms_transport.send_sms", _explode)
        monkeypatch.setattr("src.services.whatsapp_transport.send_whatsapp", _explode)
        loop.run_tick("T-1")

    def test_an_intent_names_the_channel_it_would_have_used(self):
        result = loop.run_tick("T-1")
        assert all("channel" in i for i in result["intents"])

    def test_intents_accumulate_across_ticks(self):
        loop.run_tick("T-1")
        loop.run_tick("T-1")
        assert len(loop.intents("T-1")) >= 2


class TestTheLoopVisitsEveryone:
    def test_every_roster_person_is_evaluated(self):
        result = loop.run_tick("T-1")
        assert result["evaluated"] == len(KnowledgeBase.get().personnel)

    def test_an_accounted_person_is_not_acted_on(self):
        rec.record_checkin("T-1", "p001", source="whatsapp")
        result = loop.run_tick("T-1")
        assert "p001" not in {i["person_id"] for i in result["intents"]}

    def test_unreachable_people_are_flagged_once(self):
        first = loop.run_tick("T-1")
        second = loop.run_tick("T-1")
        flagged_first = [i for i in first["intents"] if i["action"] == loop.ACTION_FLAG_IC]
        flagged_second = [i for i in second["intents"] if i["action"] == loop.ACTION_FLAG_IC]
        assert flagged_first
        assert not flagged_second, "the IC was re-told about the same people"


class TestTickCounterAdvancesOnce:
    def test_the_counter_advances_one_per_tick(self):
        assert loop.run_tick("T-1")["tick"] == 1
        assert loop.run_tick("T-1")["tick"] == 2
        assert loop.run_tick("T-1")["tick"] == 3

    def test_a_thrown_person_does_not_double_count_the_tick(self, monkeypatch):
        real = store.tick_person

        def _explodes_on_p005(incident_id, person_id, target, tick):
            if person_id == "p005":
                raise RuntimeError("unanticipated")
            return real(incident_id, person_id, target, tick)

        monkeypatch.setattr(store, "tick_person", _explodes_on_p005)
        assert loop.run_tick("T-1")["tick"] == 1
        assert loop.run_tick("T-1")["tick"] == 2

    def test_counters_are_per_incident(self):
        loop.run_tick("T-1")
        incident_state.declare("T-2", {"incident_id": "T-2",
                                       "classification": {"incident_type": "fire",
                                                          "severity": "high"}},
                               source="slack")
        assert loop.run_tick("T-2")["tick"] == 1


class TestReEntrancy:
    """The dict is faster than a network store, so a timing assumption that
    holds here may not hold under latency. Skip the beat; never queue."""

    def test_a_second_tick_cannot_start_while_one_runs(self):
        started = threading.Event()
        release = threading.Event()
        outcomes = {}

        real = loop._reconcile

        def _slow(*a, **kw):
            started.set()
            release.wait(timeout=2)
            return real(*a, **kw)

        loop._reconcile = _slow
        try:
            t = threading.Thread(target=lambda: outcomes.update(first=loop.run_tick("T-1")))
            t.start()
            started.wait(timeout=2)
            outcomes["second"] = loop.run_tick("T-1")
            release.set()
            t.join(timeout=3)
        finally:
            loop._reconcile = real

        assert outcomes["second"]["skipped_reason"] == "already_running"
        assert outcomes["first"]["tick"] == 1

    def test_a_skipped_beat_does_not_advance_the_counter(self):
        release = threading.Event()
        started = threading.Event()
        real = loop._reconcile

        def _slow(*a, **kw):
            started.set()
            release.wait(timeout=2)
            return real(*a, **kw)

        loop._reconcile = _slow
        try:
            t = threading.Thread(target=lambda: loop.run_tick("T-1"))
            t.start()
            started.wait(timeout=2)
            loop.run_tick("T-1")
            release.set()
            t.join(timeout=3)
        finally:
            loop._reconcile = real
        assert loop.run_tick("T-1")["tick"] == 2


class TestAThrownTickStillSchedulesTheNext:
    """A silently dead loop is the missed-ping failure at maximum scale."""

    def test_the_timer_survives_a_throwing_tick(self, monkeypatch):
        calls = {"n": 0}

        def _throws_once(incident_id):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("tick blew up")
            return {"tick": calls["n"], "intents": [], "evaluated": 0}

        monkeypatch.setattr(loop, "run_tick", _throws_once)
        loop.start("T-1", interval_seconds=0.05)
        deadline = time.time() + 2
        while calls["n"] < 3 and time.time() < deadline:
            time.sleep(0.02)
        loop.stop()
        assert calls["n"] >= 3, "the loop died on the first throw"

    def test_stop_is_idempotent(self):
        loop.start("T-1", interval_seconds=0.05)
        loop.stop()
        loop.stop()
        assert loop.is_running() is False


class TestQuietTicksAreClean:
    """No phantom writes, no log spam burying the demo."""

    def test_a_tick_with_nobody_silent_writes_nothing(self):
        for person in KnowledgeBase.get().personnel:
            rec.record_checkin("T-1", person["person_id"], source="test")
        store.reset_counters()
        result = loop.run_tick("T-1")
        assert result["intents"] == []
        assert store.write_count() == 0

    def test_a_quiet_tick_is_still_counted(self):
        for person in KnowledgeBase.get().personnel:
            rec.record_checkin("T-1", person["person_id"], source="test")
        assert loop.run_tick("T-1")["tick"] == 1

    def test_no_active_incident_is_a_no_op(self):
        incident_state.reset()
        result = loop.run_tick("T-1")
        assert result["skipped_reason"] == "no_active_incident"
        assert result["intents"] == []


class TestTickEndpoint:
    """/tick advances real accountability state and will later be what triggers
    real pages. It is credentialed, and it fails closed — unlike the approval
    gate, which returns True when no ICs are configured."""

    def _post(self, path, body=None, ic_env=None, monkeypatch=None):
        import json
        from io import BytesIO
        from src.core.server import CrisisMeshHandler

        if monkeypatch is not None:
            if ic_env is None:
                monkeypatch.delenv("AUTHORIZED_IC_IDS", raising=False)
            else:
                monkeypatch.setenv("AUTHORIZED_IC_IDS", ic_env)

        h = CrisisMeshHandler.__new__(CrisisMeshHandler)
        h.response_code = None
        h._headers = {}
        raw = json.dumps(body).encode() if body else b""
        h.rfile = BytesIO(raw)
        h.wfile = BytesIO()
        h.path = path
        h.command = "POST"
        h.headers = {"Content-Length": str(len(raw))}
        h.send_response = lambda c: setattr(h, "response_code", c)
        h.send_header = lambda k, v: None
        h.end_headers = lambda: None
        h.do_POST()
        return h.response_code, json.loads(h.wfile.getvalue())

    def test_refuses_when_no_ics_are_configured(self, monkeypatch):
        """Fails closed. An unconfigured deployment must not let anyone
        advance a live incident's accountability state."""
        code, body = self._post("/incident/T-1/tick", {"ic_id": "U_ANY"},
                                ic_env=None, monkeypatch=monkeypatch)
        assert code == 503
        assert body["code"] == "no_authorized_ics"

    def test_rejects_an_unknown_caller(self, monkeypatch):
        code, _ = self._post("/incident/T-1/tick", {"ic_id": "U_INTRUDER"},
                             ic_env="U_PRINCIPAL", monkeypatch=monkeypatch)
        assert code == 403

    def test_requires_an_attributable_caller(self, monkeypatch):
        code, _ = self._post("/incident/T-1/tick", {},
                             ic_env="U_PRINCIPAL", monkeypatch=monkeypatch)
        assert code == 400

    def test_an_authorized_ic_gets_the_decisions_back(self, monkeypatch):
        code, body = self._post("/incident/T-1/tick", {"ic_id": "U_PRINCIPAL"},
                                ic_env="U_PRINCIPAL", monkeypatch=monkeypatch)
        assert code == 200
        assert body["tick"] == 1
        assert body["evaluated"] == len(KnowledgeBase.get().personnel)
        assert body["intents"], "the tick returned no decisions"
        assert body["store"] == "memory"

    def test_it_runs_synchronously_not_in_a_background_thread(self, monkeypatch):
        """The decisions must be in the response, not pending on a thread Cloud
        Run may reclaim after the response flushes."""
        code, body = self._post("/incident/T-1/tick", {"ic_id": "U_PRINCIPAL"},
                                ic_env="U_PRINCIPAL", monkeypatch=monkeypatch)
        assert code == 200
        assert len(body["intents"]) == len(loop.intents("T-1"))

    def test_stepping_twice_advances_the_counter(self, monkeypatch):
        self._post("/incident/T-1/tick", {"ic_id": "U_PRINCIPAL"},
                   ic_env="U_PRINCIPAL", monkeypatch=monkeypatch)
        _, body = self._post("/incident/T-1/tick", {"ic_id": "U_PRINCIPAL"},
                             ic_env="U_PRINCIPAL", monkeypatch=monkeypatch)
        assert body["tick"] == 2

    def test_it_still_does_not_send(self, monkeypatch):
        def _explode(*a, **kw):
            raise AssertionError("the endpoint must not deliver")

        monkeypatch.setattr("src.services.sms_transport.send_sms", _explode)
        monkeypatch.setattr("src.services.whatsapp_transport.send_whatsapp", _explode)
        code, _ = self._post("/incident/T-1/tick", {"ic_id": "U_PRINCIPAL"},
                             ic_env="U_PRINCIPAL", monkeypatch=monkeypatch)
        assert code == 200


class TestVerificationFailureIsPerPerson:
    """A users.info call that throttles on person 17 is the read-failure hazard
    through a new door — and it is now a network call inside what used to be a
    local lookup."""

    def test_a_throttled_lookup_mid_roster_does_not_end_the_tick(self, monkeypatch):
        from src.core import notify

        notify.reset_slack_id_cache()
        seen = {"n": 0}

        def _throttles_on_the_seventeenth(slack_id):
            seen["n"] += 1
            if seen["n"] == 17:
                raise RuntimeError("rate limited")
            return False

        monkeypatch.setattr(notify, "slack_id_resolves", _throttles_on_the_seventeenth)
        result = loop.run_tick("T-1")
        assert result["evaluated"] == len(KnowledgeBase.get().personnel)
        assert result["skipped_reason"] == ""

    def test_unverified_people_land_on_the_unreachable_list(self, monkeypatch):
        from src.core import notify

        notify.reset_slack_id_cache()
        monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
        monkeypatch.setattr(notify, "_slack_ready", lambda: True)
        monkeypatch.setattr(notify, "slack_id_resolves", lambda sid: False)
        result = loop.run_tick("T-1")
        flagged = [i for i in result["intents"] if i["action"] == loop.ACTION_FLAG_IC]
        assert flagged, "nobody was flagged despite no id resolving"
        assert any(notify.REASON_SLACK_UNVERIFIED in i["reason"] for i in flagged)


class TestWholeTickBudget:
    """Per-call deadlines bound one call. A healthy-but-slow tick makes ~100
    serial network calls at roster scale, and the sum can exceed the request
    budget — so the tick is bounded too, and a blown budget commits what
    completed rather than losing everything."""

    def test_a_uniformly_slow_tick_returns_within_budget(self, monkeypatch):
        """The healthy-degradation case: every call succeeds, just slowly."""
        import time as _time

        monkeypatch.setenv("CRISISMESH_TICK_BUDGET", "5")
        real = store.tick_person

        def _slow_but_fine(incident_id, person_id, target, tick):
            _time.sleep(0.4)
            return real(incident_id, person_id, target, tick)

        monkeypatch.setattr(store, "tick_person", _slow_but_fine)
        began = _time.monotonic()
        result = loop.run_tick("T-1")
        elapsed = _time.monotonic() - began

        assert elapsed < 12, f"tick ran {elapsed:.1f}s with a 5s budget"
        assert result["not_evaluated"], "nobody was reported as unevaluated"
        assert result["intents"], "no partial work was committed"

    def test_the_tail_is_named_not_silently_dropped(self, monkeypatch):
        import time as _time

        monkeypatch.setenv("CRISISMESH_TICK_BUDGET", "5")

        def _slow(incident_id, person_id, target, tick):
            _time.sleep(0.4)
            return None

        monkeypatch.setattr(store, "tick_person", _slow)
        result = loop.run_tick("T-1")
        assert len(result["not_evaluated"]) > 0
        assert result["evaluated"] == len(KnowledgeBase.get().personnel)

    def test_the_next_tick_picks_up_the_tail(self, monkeypatch):
        """`last_acted_tick` makes the re-run skip whoever was already reached,
        so the unevaluated tail is what tick N+1 acts on."""
        import time as _time

        monkeypatch.setenv("CRISISMESH_TICK_BUDGET", "5")
        real = store.tick_person
        calls = {"n": 0}

        def _slow_for_the_first_few(incident_id, person_id, target, tick):
            calls["n"] += 1
            if calls["n"] < 12:
                _time.sleep(0.5)
            return real(incident_id, person_id, target, tick)

        monkeypatch.setattr(store, "tick_person", _slow_for_the_first_few)
        first = loop.run_tick("T-1")
        tail = set(first["not_evaluated"])
        monkeypatch.setattr(store, "tick_person", real)
        second = loop.run_tick("T-1")
        acted_second = {i["person_id"] for i in second["intents"]}
        assert tail & acted_second, "the tail was never picked up"

    def test_a_quiet_tick_reports_an_empty_tail(self):
        result = loop.run_tick("T-1")
        assert result["not_evaluated"] == []

    def test_the_budget_is_configurable(self, monkeypatch):
        monkeypatch.setenv("CRISISMESH_TICK_BUDGET", "12")
        assert store.tick_budget_seconds() == 12.0

    def test_a_garbage_budget_falls_back(self, monkeypatch):
        monkeypatch.setenv("CRISISMESH_TICK_BUDGET", "not-a-number")
        assert store.tick_budget_seconds() == store.TICK_BUDGET_SECONDS


class TestCheckinsReachTheStateMachine:
    """Two stores hold per-person state. Six call sites wrote to accountability
    and none wrote to reconciliation, so a teacher who texted SAFE stayed SILENT
    to the loop — re-pinged, then escalated to their own floor warden. The
    contract was correct and unconnected."""

    def test_a_checkin_accounts_the_person_in_reconciliation(self):
        from src.agents.accountability.tools import process_checkin
        process_checkin("T-1", "p001", "safe")
        assert rec.get_state("T-1", "p001").status == rec.ACCOUNTED

    def test_the_loop_stops_chasing_someone_who_checked_in(self):
        """The world-claim, not the field-claim."""
        from src.agents.accountability.tools import process_checkin
        loop.run_tick("T-1")
        process_checkin("T-1", "p001", "safe")
        second = loop.run_tick("T-1")
        assert "p001" not in {i["person_id"] for i in second["intents"]}

    def test_a_checkin_cancels_a_pending_escalation(self, monkeypatch):
        from src.agents.accountability.tools import process_checkin
        monkeypatch.setenv("CRISISMESH_REPING_CAP", "1")
        rec.transition("T-1", "p001", rec.ESCALATED, tick=1)
        process_checkin("T-1", "p001", "safe")
        assert rec.get_state("T-1", "p001").pending_escalation is False

    @pytest.mark.parametrize("status", ["safe", "injured", "need_help", "evacuated"])
    def test_every_real_status_accounts_them(self, status):
        from src.agents.accountability.tools import process_checkin
        process_checkin("T-1", "p001", status)
        assert rec.get_state("T-1", "p001").status == rec.ACCOUNTED

    def test_a_seeded_unknown_row_does_not_account_anyone(self):
        """send_checkin_request seeds UNKNOWN rows for the whole roster. If that
        mirrored, the loop would think everyone was accounted for the moment an
        incident was declared."""
        from src.agents.accountability.tools import send_checkin_request
        send_checkin_request("T-1", facility_id="jefferson")
        assert rec.get_state("T-1", "p001").status == rec.SILENT

    def test_a_mirror_failure_does_not_lose_the_checkin(self, monkeypatch):
        from src.agents.accountability import tools

        def _boom(*a, **kw):
            raise RuntimeError("reconciliation unavailable")

        monkeypatch.setattr("src.core.reconciliation.record_checkin", _boom)
        result = tools.process_checkin("T-1", "p001", "safe")
        assert result["recorded"] is True


class TestRoomReportReachesTheStateMachine:
    """The second door. `rec.record_room_report` existed, was tested in the
    contract, and was called by nobody — so a teacher typing "room 101: all 25
    students are safe" was still re-pinged and escalated for being silent. The
    check-in funnel fix did not touch this path."""

    def _teacher_phone(self):
        from src.core.knowledge_base import KnowledgeBase
        person = KnowledgeBase.get().get_person("p005")
        raw = person["phone"].replace("-", "")
        return f"+1{raw}"

    def test_the_loop_stops_chasing_the_teacher_who_filed_the_report(self):
        """The world-claim: it can only pass if the room-report path actually
        reaches the state machine."""
        from src.services.whatsapp_transport import handle_inbound_message

        loop.run_tick("T-1")
        handle_inbound_message(self._teacher_phone(),
                               "room 101: all 25 students are safe")
        second = loop.run_tick("T-1")
        assert "p005" not in {i["person_id"] for i in second["intents"]}, (
            "the teacher who filed the report was chased for being silent"
        )

    def test_it_cancels_her_pending_escalation(self, monkeypatch):
        from src.services.whatsapp_transport import handle_inbound_message

        rec.transition("T-1", "p005", rec.ESCALATED, tick=1)
        handle_inbound_message(self._teacher_phone(),
                               "room 101: 23 students are safe, 2 are missing")
        assert rec.get_state("T-1", "p005").pending_escalation is False

    def test_it_accounts_for_the_reporter_only(self):
        """"23 of 25 safe" never says which 23."""
        from src.services.whatsapp_transport import handle_inbound_message

        handle_inbound_message(self._teacher_phone(),
                               "room 101: 23 students are safe, 2 are missing")
        assert rec.get_state("T-1", "p005").status == rec.ACCOUNTED
        assert rec.get_state("T-1", "p012").status == rec.SILENT

    def test_an_unrecognised_reporter_accounts_for_nobody(self):
        from src.services.whatsapp_transport import handle_inbound_message

        handle_inbound_message("+15559990000", "room 101: all 25 students are safe")
        assert rec.get_state("T-1", "p005").status == rec.SILENT


class TestTheMirrorDoesNotOverFire:
    """The inverse failure. Mirroring check-ins could over-fire and mirror the
    initial roster seed as if all 34 had reported — the fix for one direction of
    a seam bug is the likeliest place to introduce the other."""

    def test_declaring_an_incident_leaves_everyone_silent(self):
        from src.agents.accountability.tools import send_checkin_request

        send_checkin_request("T-1", facility_id="jefferson")
        statuses = {rec.get_state("T-1", p["person_id"]).status
                    for p in KnowledgeBase.get().personnel}
        assert statuses == {rec.SILENT}, "declaring an incident accounted for people"

    def test_the_first_tick_still_has_work_to_do(self):
        """The observable consequence of over-mirroring would be a loop with
        nothing to chase the moment an incident is declared."""
        from src.agents.accountability.tools import send_checkin_request

        send_checkin_request("T-1", facility_id="jefferson")
        assert loop.run_tick("T-1")["intents"], "the loop had nobody to chase"


class TestSlackReconciliationTrigger:
    """The loop is the marquee capability; making it reachable only from a
    terminal hides it. Same authorisation and same fail-closed rule as
    POST /incident/{id}/tick, because it advances the same state."""

    def test_it_refuses_when_no_ics_are_configured(self, monkeypatch):
        from src.services import slack_transport

        monkeypatch.delenv("AUTHORIZED_IC_IDS", raising=False)
        posted = []
        monkeypatch.setattr(slack_transport, "_post_bot_message",
                            lambda ch, msg, thread_ts="": posted.append(msg))
        slack_transport._handle_reconciliation_tick("C1", "U_ANY", "")
        assert "No incident commanders configured" in posted[0]

    def test_it_refuses_a_non_commander(self, monkeypatch):
        from src.services import slack_transport

        monkeypatch.setenv("AUTHORIZED_IC_IDS", "U_PRINCIPAL")
        posted = []
        monkeypatch.setattr(slack_transport, "_post_bot_message",
                            lambda ch, msg, thread_ts="": posted.append(msg))
        slack_transport._handle_reconciliation_tick("C1", "U_TEACHER", "")
        assert "Only an incident commander" in posted[0]

    def test_a_commander_gets_the_decisions(self, monkeypatch):
        from src.services import slack_transport

        monkeypatch.setenv("AUTHORIZED_IC_IDS", "U_PRINCIPAL")
        posted = []
        monkeypatch.setattr(slack_transport, "_post_bot_message",
                            lambda ch, msg, thread_ts="": posted.append(msg))
        slack_transport._handle_reconciliation_tick("C1", "U_PRINCIPAL", "")
        assert "RECONCILIATION — tick 1" in posted[0]
        assert "cannot be reached at all" in posted[0]

    def test_it_refuses_with_no_active_incident(self, monkeypatch):
        from src.core import incident_state
        from src.services import slack_transport

        incident_state.reset()
        posted = []
        monkeypatch.setattr(slack_transport, "_post_bot_message",
                            lambda ch, msg, thread_ts="": posted.append(msg))
        slack_transport._handle_reconciliation_tick("C1", "U_PRINCIPAL", "")
        assert "No active incident" in posted[0]

    def test_it_says_so_when_delivery_is_off(self, monkeypatch):
        from src.services import slack_transport

        monkeypatch.setenv("AUTHORIZED_IC_IDS", "U_PRINCIPAL")
        monkeypatch.setenv("CRISISMESH_DELIVERY", "off")
        posted = []
        monkeypatch.setattr(slack_transport, "_post_bot_message",
                            lambda ch, msg, thread_ts="": posted.append(msg))
        slack_transport._handle_reconciliation_tick("C1", "U_PRINCIPAL", "")
        assert "decisions, not messages that were sent" in posted[0]

    @pytest.mark.parametrize("phrase", [
        "chase the ones who haven't answered",
        "who hasn't answered",
        "run reconciliation",
        "tick",
    ])
    def test_the_phrases_route_to_the_loop(self, phrase, monkeypatch):
        from src.services import slack_transport

        called = []
        monkeypatch.setattr(slack_transport, "_handle_reconciliation_tick",
                            lambda ch, u, t: called.append(phrase))
        slack_transport._run_followup_query("C1", "U_PRINCIPAL", phrase, "")
        assert called == [phrase]


class TestUnreachableListIsReadable:
    """Thirty people with the same blocker rendered as thirty near-identical
    sentences, each cut mid-word at 110 characters — which buries the names an
    incident commander is going to read out over a radio."""

    REASON = ("SMS: no confirmed opt-in; WhatsApp: outside the 24h window, "
              "no template; Slack: id does not resolve to a workspace member")

    def test_a_blocker_chain_becomes_one_readable_phrase(self):
        from src.services.slack_transport import _summarise_reason
        assert _summarise_reason(self.REASON) == (
            "no SMS opt-in, no open WhatsApp session, Slack id does not resolve")

    def test_names_are_grouped_under_a_shared_reason(self):
        from src.services.slack_transport import _group_by_reason

        flagged = [{"name": n, "reason": self.REASON}
                   for n in ("Maria Santos", "Mr. Chen", "Ms. Williams")]
        grouped = _group_by_reason(flagged)
        assert len(grouped) == 1
        assert grouped[0][1] == ["Maria Santos", "Mr. Chen", "Ms. Williams"]

    def test_the_largest_group_comes_first(self):
        """The systemic gap before the one-offs."""
        from src.services.slack_transport import _group_by_reason

        flagged = [{"name": n, "reason": self.REASON} for n in ("A", "B", "C")]
        flagged.append({"name": "D", "reason": "no phone number on the roster"})
        assert [len(names) for _, names in _group_by_reason(flagged)] == [3, 1]

    def test_no_name_is_truncated_mid_word(self):
        from src.services.slack_transport import _format_tick, _group_by_reason
        from src.core import reconciliation_loop as loop

        flagged = [{"action": loop.ACTION_FLAG_IC, "person_id": f"p{i}",
                    "name": f"Person {i}", "reason": self.REASON}
                   for i in range(30)]
        rendered = _format_tick({"tick": 1, "evaluated": 34, "intents": flagged})
        assert "works" not in rendered
        # Every name, not a count: these are the people the commander has to
        # raise on a radio, and "and 22 more" is their problem restated.
        for i in range(30):
            assert f"Person {i}" in rendered
        assert "more" not in rendered

    def test_an_empty_reason_still_says_something(self):
        from src.services.slack_transport import _summarise_reason
        assert _summarise_reason("") == "no channel available"


class TestEscalationIsTerminalForTheLoop:
    """Harmless when a human asks for three ticks; on a schedule it is the same
    warden paged about the same person every minute, forever."""

    def test_an_escalated_person_is_not_acted_on_again(self, monkeypatch):
        monkeypatch.setenv("CRISISMESH_REPING_CAP", "2")
        for t in (1, 2):
            rec.transition("T-1", "p001", rec.REPINGED, tick=t)
        rec.transition("T-1", "p001", rec.ESCALATED, tick=3)
        for t in (4, 5, 6):
            assert rec.should_act("T-1", "p001", tick=t) is False

    def test_a_check_in_brings_them_back_to_accounted(self, monkeypatch):
        monkeypatch.setenv("CRISISMESH_REPING_CAP", "2")
        rec.transition("T-1", "p001", rec.ESCALATED, tick=1)
        rec.record_checkin("T-1", "p001", source="sms")
        assert rec.get_state("T-1", "p001").status == rec.ACCOUNTED

    def test_a_reopen_makes_them_chaseable_again(self):
        rec.transition("T-1", "p001", rec.ESCALATED, tick=1)
        rec.record_checkin("T-1", "p001", source="sms")
        rec.reopen("T-1", "p001", reason="zone re-blocked", tick=2)
        assert rec.should_act("T-1", "p001", tick=3) is True

    def test_ticking_forever_settles_instead_of_repeating(self, monkeypatch):
        """The scheduler case: run many ticks and assert the intents stop."""
        monkeypatch.setenv("CRISISMESH_REPING_CAP", "2")
        counts = [len(loop.run_tick("T-1")["intents"]) for _ in range(8)]
        assert counts[-1] == 0, f"the loop never settled: {counts}"
        assert sum(counts[4:]) == 0


class TestAutoTick:
    """One switch says "may this system transmit", the other says "may it
    decide without being asked". Both on is the fully autonomous posture and
    should take two deliberate acts."""

    def test_it_is_off_by_default(self, monkeypatch):
        monkeypatch.delenv("CRISISMESH_AUTO_TICK", raising=False)
        assert loop.auto_tick_enabled() is False

    @pytest.mark.parametrize("value", ["off", "none", "false", ""])
    def test_off_variants(self, monkeypatch, value):
        monkeypatch.setenv("CRISISMESH_AUTO_TICK", value)
        assert loop.auto_tick_enabled() is False

    def test_on_enables_it(self, monkeypatch):
        monkeypatch.setenv("CRISISMESH_AUTO_TICK", "on")
        assert loop.auto_tick_enabled() is True

    def test_declaring_does_not_start_a_timer_when_off(self, monkeypatch):
        monkeypatch.delenv("CRISISMESH_AUTO_TICK", raising=False)
        loop.start_for_incident("T-1")
        assert loop.is_running() is False

    def test_declaring_starts_a_timer_when_on(self, monkeypatch):
        monkeypatch.setenv("CRISISMESH_AUTO_TICK", "on")
        monkeypatch.setenv("CRISISMESH_TICK_SECONDS", "5")
        loop.start_for_incident("T-1")
        try:
            assert loop.is_running() is True
        finally:
            loop.stop()

    def test_resolving_stops_the_timer(self, monkeypatch):
        from src.core import notify

        monkeypatch.setenv("CRISISMESH_AUTO_TICK", "on")
        monkeypatch.setenv("CRISISMESH_TICK_SECONDS", "5")
        loop.start_for_incident("T-1")
        assert loop.is_running() is True
        notify._stop_reconciliation()
        assert loop.is_running() is False


class TestAskingReportsRatherThanActs:
    """With the scheduler running, "who hasn't answered" is a reading, not an
    action. Advancing on every question would let a commander's curiosity
    re-ping people ahead of schedule."""

    def test_asking_does_not_advance_the_tick_when_scheduled(self, monkeypatch):
        from src.services import slack_transport

        import time as _time

        monkeypatch.setenv("CRISISMESH_AUTO_TICK", "on")
        monkeypatch.setenv("CRISISMESH_TICK_SECONDS", "30")
        monkeypatch.setenv("AUTHORIZED_IC_IDS", "U_PRINCIPAL")
        loop.start_for_incident("T-1")            # the scheduler, ticking on its own
        try:
            for _ in range(50):
                if loop.last_result("T-1"):
                    break
                _time.sleep(0.05)
            before = loop.last_result("T-1")["tick"]

            posted = []
            monkeypatch.setattr(slack_transport, "_post_bot_message",
                                lambda ch, msg, thread_ts="": posted.append(msg))
            slack_transport._handle_reconciliation_tick("C1", "U_PRINCIPAL", "")

            assert loop.last_result("T-1")["tick"] == before, "asking advanced the tick"
            assert f"tick {before}" in posted[0]
        finally:
            loop.stop()

    def test_the_reading_says_it_ran_on_its_own(self, monkeypatch):
        from src.services import slack_transport

        import time as _time

        monkeypatch.setenv("CRISISMESH_AUTO_TICK", "on")
        monkeypatch.setenv("CRISISMESH_TICK_SECONDS", "30")
        monkeypatch.setenv("AUTHORIZED_IC_IDS", "U_PRINCIPAL")
        loop.start_for_incident("T-1")
        try:
            for _ in range(50):
                if loop.last_result("T-1"):
                    break
                _time.sleep(0.05)
            posted = []
            monkeypatch.setattr(slack_transport, "_post_bot_message",
                                lambda ch, msg, thread_ts="": posted.append(msg))
            slack_transport._handle_reconciliation_tick("C1", "U_PRINCIPAL", "")
            assert "on its own" in posted[0]
        finally:
            loop.stop()

    def test_asking_still_advances_when_the_scheduler_is_off(self, monkeypatch):
        from src.services import slack_transport

        monkeypatch.delenv("CRISISMESH_AUTO_TICK", raising=False)
        monkeypatch.setenv("AUTHORIZED_IC_IDS", "U_PRINCIPAL")
        monkeypatch.setattr(slack_transport, "_post_bot_message",
                            lambda ch, msg, thread_ts="": None)
        slack_transport._handle_reconciliation_tick("C1", "U_PRINCIPAL", "")
        assert loop.last_result("T-1")["tick"] == 1

    def test_the_first_ask_before_any_scheduled_tick_still_runs_one(self, monkeypatch):
        """Otherwise a commander asking in the first minute gets nothing.

        Asserted on what the commander is shown, not on loop._last_result.
        ensure_running() starts the scheduler, which ticks immediately in its
        own thread; the asking thread then hits run_tick's already_running
        guard, which returns a result without recording one. _last_result is
        therefore legitimately empty here, and a test that read it was passing
        on timing rather than on behaviour.
        """
        from src.services import slack_transport

        monkeypatch.setenv("CRISISMESH_AUTO_TICK", "on")
        monkeypatch.setenv("CRISISMESH_TICK_SECONDS", "300")
        monkeypatch.setenv("AUTHORIZED_IC_IDS", "U_PRINCIPAL")
        posted: list[str] = []
        monkeypatch.setattr(slack_transport, "_post_bot_message",
                            lambda ch, msg, thread_ts="": posted.append(msg))
        try:
            slack_transport._handle_reconciliation_tick("C1", "U_PRINCIPAL", "")
            assert posted, "the commander was answered with nothing"
            assert "no active incident" not in posted[0].lower()
            assert "tick" in posted[0].lower()
        finally:
            # ensure_running() starts a real timer; leaving it alive lets a
            # tick land in another test's state after its fixture has reset.
            loop.stop()


class TestTheTimerSurvivesAnInstanceRestart:
    """The incident is durable and the timer is a thread. A container replaced
    mid-incident comes back coordinating an emergency with nobody chasing the
    silent — and nothing errors, so reconciliation just quietly stops."""

    def test_a_live_incident_with_no_timer_restarts_it(self, monkeypatch):
        monkeypatch.setenv("CRISISMESH_AUTO_TICK", "on")
        monkeypatch.setenv("CRISISMESH_TICK_SECONDS", "30")
        loop.stop()                       # the restart: the thread is gone
        assert loop.is_running() is False
        try:
            assert loop.ensure_running() is True
            assert loop.is_running() is True
        finally:
            loop.stop()

    def test_it_does_not_start_a_second_timer(self, monkeypatch):
        monkeypatch.setenv("CRISISMESH_AUTO_TICK", "on")
        monkeypatch.setenv("CRISISMESH_TICK_SECONDS", "30")
        loop.ensure_running()
        try:
            assert loop.ensure_running() is False
        finally:
            loop.stop()

    def test_it_does_nothing_with_no_active_incident(self, monkeypatch):
        from src.core import incident_state

        monkeypatch.setenv("CRISISMESH_AUTO_TICK", "on")
        incident_state.reset()
        loop.stop()
        assert loop.ensure_running() is False
        assert loop.is_running() is False

    def test_it_respects_the_auto_tick_switch(self, monkeypatch):
        monkeypatch.delenv("CRISISMESH_AUTO_TICK", raising=False)
        loop.stop()
        assert loop.ensure_running() is False


class TestTheFirstTickIsPrompt:
    """Waiting a full interval before the opening tick left the first minute of
    an incident — the minute in which nobody has checked in yet and chasing
    matters most — with no reconciliation at all."""

    def test_a_tick_lands_without_waiting_an_interval(self, monkeypatch):
        import time as _time

        monkeypatch.setenv("CRISISMESH_AUTO_TICK", "on")
        monkeypatch.setenv("CRISISMESH_TICK_SECONDS", "60")
        loop.start_for_incident("T-1")
        try:
            for _ in range(60):
                if loop.last_result("T-1"):
                    break
                _time.sleep(0.05)
            assert loop.last_result("T-1").get("tick") == 1, (
                "no tick inside the first seconds of a 60s interval"
            )
        finally:
            loop.stop()

    def test_it_keeps_ticking_on_the_interval_afterwards(self, monkeypatch):
        import time as _time

        monkeypatch.setenv("CRISISMESH_AUTO_TICK", "on")
        monkeypatch.setenv("CRISISMESH_TICK_SECONDS", "5")
        loop.start_for_incident("T-1")
        try:
            _time.sleep(6.5)
            assert loop.last_result("T-1").get("tick", 0) >= 2
        finally:
            loop.stop()


class TestTickRenderingIsTight:
    """Each section emits its own spacer, so a tick where two of the three are
    empty rendered as a run of blank lines before the one with content."""

    def test_no_run_of_blank_lines(self):
        from itertools import groupby
        from src.services.slack_transport import _format_tick

        rendered = _format_tick({
            "tick": 3, "evaluated": 34, "at": "",
            "intents": [{"action": loop.ACTION_ESCALATE, "person_id": "p002",
                         "name": "VP Martinez", "channel": "slack",
                         "reason": "2 re-pings unanswered; warden X"}],
        })
        longest = max((sum(1 for _ in g)
                       for blank, g in groupby(rendered.split("\n"), key=lambda l: l == "")
                       if blank), default=0)
        assert longest <= 1

    def test_it_does_not_end_on_whitespace(self):
        from src.services.slack_transport import _format_tick

        rendered = _format_tick({"tick": 1, "evaluated": 34, "at": "", "intents": []})
        assert rendered == rendered.rstrip()

    def test_a_quiet_tick_still_says_something(self):
        from src.services.slack_transport import _format_tick

        rendered = _format_tick({"tick": 5, "evaluated": 34, "at": "", "intents": []})
        assert "Nobody left to chase" in rendered


class TestEscalationReachesTheWarden:
    """The escalation was delivered on the silent person's own channel, so
    "X has not answered, please locate them" went to X — the one person who
    demonstrably could not be reached was the only one told."""

    INCIDENT = "T-WARDEN"

    def _setup(self, monkeypatch, reachable):
        """Own incident id: earlier tests in this file start real timers, and a
        tick in flight during their teardown can write to a shared id after it
        has been reset."""
        from src.core import incident_state, notify
        from src.services import whatsapp_transport

        # Earlier tests in this file message in as p005, which opens their 24h
        # WhatsApp window — module state that outlives the test and quietly
        # makes the warden reachable here.
        whatsapp_transport.reset_session_windows()

        incident_state.declare(
            self.INCIDENT,
            {"incident_id": self.INCIDENT,
             "classification": {"incident_type": "active_threat", "severity": "critical"}},
            source="slack")

        monkeypatch.setenv("CRISISMESH_DELIVERY", "on")
        monkeypatch.setenv("CRISISMESH_REPING_CAP", "1")
        monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
        monkeypatch.setenv("CRISISMESH_DEMO_SLACK_MAP",
                           "^".join(f"{p}=U0REAL" for p in reachable))
        monkeypatch.setattr(notify, "_slack_ready", lambda: True)
        monkeypatch.setattr(notify, "slack_id_resolves", lambda sid: sid == "U0REAL")
        notify.reset_slack_id_cache()
        sent = []
        monkeypatch.setattr(notify, "_send",
                            lambda reach, msg: (sent.append((reach.person_id, msg)),
                                                {"delivered": True, "outcome": "accepted"})[1])
        return sent

    def test_the_warden_receives_it_not_the_silent_person(self, monkeypatch):
        sent = self._setup(monkeypatch, ["p001", "p005"])
        loop.run_tick(self.INCIDENT)     # re-ping
        sent.clear()
        loop.run_tick(self.INCIDENT)     # cap reached -> escalate

        escalations = [(pid, msg) for pid, msg in sent if "has not answered" in msg]
        assert escalations, "no escalation was sent"
        for recipient, message in escalations:
            subject = message.split(" has not answered")[0].replace("CrisisMesh: ", "")
            assert recipient != _person_id_for(subject), (
                f"escalation about {subject} was delivered to {subject}"
            )

    def test_an_unreachable_warden_is_flagged_to_the_ic(self, monkeypatch):
        """Only p001 reachable, so there is nobody to hand them to."""
        self._setup(monkeypatch, ["p001"])
        loop.run_tick(self.INCIDENT)
        loop.run_tick(self.INCIDENT)
        # The claim is that the commander is told rather than the person being
        # silently dropped: their recorded reason names the missing warden.
        state = rec.get_state(self.INCIDENT, "p001")
        assert state.status == rec.UNREACHABLE
        assert "warden" in state.reachability_reason
        assert "unreachable" in state.reachability_reason

    def test_a_warden_is_never_themselves(self):
        from src.core.knowledge_base import KnowledgeBase

        for person in KnowledgeBase.get().personnel:
            warden = loop._warden_for(person)
            if warden:
                assert warden["person_id"] != person["person_id"]

    def test_the_warden_is_on_the_same_floor_when_possible(self):
        from src.core.knowledge_base import KnowledgeBase

        person = KnowledgeBase.get().get_person("p012")
        warden = loop._warden_for(person)
        assert warden is not None
        assert str(warden.get("floor")) == str(person.get("floor"))


def _person_id_for(name: str) -> str:
    from src.core.knowledge_base import KnowledgeBase

    for p in KnowledgeBase.get().personnel:
        if p["name"] == name.strip():
            return p["person_id"]
    return ""


class TestAskingDuringTheSchedulersOwnTick:
    """The first ask of an incident lands while the scheduler's immediate tick
    is still running. run_tick's already_running guard is correct — a backlog of
    queued ticks would run stale decisions — but answering "who hasn't answered"
    with "skipped, already running" tells a commander about our locking instead
    of about the people who are missing."""

    def test_the_answer_is_the_tick_not_a_lock_message(self, monkeypatch):
        from src.services import slack_transport

        monkeypatch.setenv("CRISISMESH_AUTO_TICK", "on")
        monkeypatch.setenv("CRISISMESH_TICK_SECONDS", "300")
        monkeypatch.setenv("AUTHORIZED_IC_IDS", "U_PRINCIPAL")
        posted: list[str] = []
        monkeypatch.setattr(slack_transport, "_post_bot_message",
                            lambda ch, msg, thread_ts="": posted.append(msg))
        try:
            slack_transport._handle_reconciliation_tick("C1", "U_PRINCIPAL", "")
            assert posted
            assert "already_running" not in posted[0]
            assert "tick" in posted[0].lower()
        finally:
            loop.stop()

    def test_it_gives_up_rather_than_blocking_forever(self, monkeypatch):
        """Bounded wait. A commander on a Slack reply is not helped by a hang."""
        from src.services import slack_transport
        import time as _time

        t0 = _time.monotonic()
        got = slack_transport._await_running_tick("NOPE-1", timeout_seconds=0.3)
        assert got == {}
        assert _time.monotonic() - t0 < 2.0

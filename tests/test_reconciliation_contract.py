"""The tick contract, written before the loop or its persistence exist.

The critic was idempotent by nature: same inputs, same verdict, re-running costs
nothing. The tick is the opposite — its correctness is defined entirely by what
it remembers between runs, and what it remembers is a small state machine per
person. Persisting fields the loop happens to touch, before the states are
written down, means discovering the missing state on camera.

So: states and legal edges first, then the schema falls out of them. One
document per (incident, person) carrying state + last-acted tick + reachability
reason; one per (incident, tick) for replay.

Four rules this file exists to pin:

  1. Legal transitions, and the illegal ones that must be refused.
  2. A replayed tick is a no-op, not a second ping — the durability we add
     Firestore for must not itself cause the double-ping on redeploy.
  3. A check-in cancels a pending escalation before the next tick reads state.
  4. The unreachable set is told to the IC once, and re-surfaces only on a
     real state change.

Plus one found while auditing writers: filing a room report is evidence the
reporter is alive and typing, and must count as their check-in. Otherwise the
loop escalates the teacher who is doing the reporting.
"""

import pytest

from src.core import reconciliation as rec


@pytest.fixture(autouse=True)
def fresh():
    rec.reset()
    yield
    rec.reset()


class TestStates:
    def test_a_person_starts_silent(self):
        s = rec.get_state("INC-1", "p005")
        assert s.status == rec.SILENT
        assert s.attempts == 0
        assert s.last_acted_tick is None


class TestLegalTransitions:
    @pytest.mark.parametrize("target", [rec.REPINGED, rec.UNREACHABLE, rec.ACCOUNTED])
    def test_from_silent(self, target):
        rec.transition("INC-1", "p005", target, tick=1)
        assert rec.get_state("INC-1", "p005").status == target

    def test_repinged_to_escalated_at_the_attempt_cap(self):
        for tick in range(1, rec.MAX_ATTEMPTS + 1):
            rec.transition("INC-1", "p005", rec.REPINGED, tick=tick)
        assert rec.get_state("INC-1", "p005").attempts == rec.MAX_ATTEMPTS
        rec.transition("INC-1", "p005", rec.ESCALATED, tick=rec.MAX_ATTEMPTS + 1)
        assert rec.get_state("INC-1", "p005").status == rec.ESCALATED

    def test_unreachable_can_become_repinged_when_a_channel_appears(self):
        """An ID gets fixed or consent arrives mid-incident."""
        rec.transition("INC-1", "p005", rec.UNREACHABLE, tick=1)
        rec.transition("INC-1", "p005", rec.REPINGED, tick=2)
        assert rec.get_state("INC-1", "p005").status == rec.REPINGED

    def test_any_state_can_become_accounted(self):
        for status in (rec.SILENT, rec.REPINGED, rec.ESCALATED, rec.UNREACHABLE):
            rec.reset()
            rec.transition("INC-1", "p005", status, tick=1)
            rec.transition("INC-1", "p005", rec.ACCOUNTED, tick=2)
            assert rec.get_state("INC-1", "p005").status == rec.ACCOUNTED


class TestIllegalTransitions:
    """Each refused edge is a bug that would otherwise happen quietly."""

    def test_accounted_cannot_drift_back_to_silent(self):
        """A re-open is an event with a reason, never a plain transition —
        otherwise a stale tick silently un-accounts someone who checked in."""
        rec.transition("INC-1", "p005", rec.ACCOUNTED, tick=1)
        with pytest.raises(rec.IllegalTransition):
            rec.transition("INC-1", "p005", rec.SILENT, tick=2)

    def test_accounted_cannot_be_repinged_directly(self):
        rec.transition("INC-1", "p005", rec.ACCOUNTED, tick=1)
        with pytest.raises(rec.IllegalTransition):
            rec.transition("INC-1", "p005", rec.REPINGED, tick=2)

    def test_escalated_cannot_be_unescalated(self):
        """The warden was told. You cannot untell them."""
        rec.transition("INC-1", "p005", rec.ESCALATED, tick=1)
        with pytest.raises(rec.IllegalTransition):
            rec.transition("INC-1", "p005", rec.SILENT, tick=2)

    def test_repinged_cannot_forget_it_pinged(self):
        rec.transition("INC-1", "p005", rec.REPINGED, tick=1)
        with pytest.raises(rec.IllegalTransition):
            rec.transition("INC-1", "p005", rec.SILENT, tick=2)


class TestReopen:
    """Checked in, then a new blocked zone reopens their area."""

    def test_reopen_requires_a_reason_and_returns_to_silent(self):
        rec.transition("INC-1", "p005", rec.ACCOUNTED, tick=1)
        rec.reopen("INC-1", "p005", reason="west-wing-f1 newly blocked", tick=5)
        s = rec.get_state("INC-1", "p005")
        assert s.status == rec.SILENT
        assert "newly blocked" in s.reopen_reason

    def test_reopen_resets_the_attempt_cap(self):
        """Otherwise a re-opened person can never be re-pinged."""
        for tick in range(1, rec.MAX_ATTEMPTS + 1):
            rec.transition("INC-1", "p005", rec.REPINGED, tick=tick)
        rec.transition("INC-1", "p005", rec.ACCOUNTED, tick=9)
        rec.reopen("INC-1", "p005", reason="zone reopened", tick=10)
        assert rec.get_state("INC-1", "p005").attempts == 0

    def test_reopen_without_a_reason_is_refused(self):
        rec.transition("INC-1", "p005", rec.ACCOUNTED, tick=1)
        with pytest.raises(ValueError):
            rec.reopen("INC-1", "p005", reason="", tick=2)


class TestTickReplay:
    """The durability we add Firestore for must not cause the double-ping."""

    def test_a_committed_tick_replayed_is_a_no_op(self):
        assert rec.begin_tick("INC-1", 1) is True
        rec.transition("INC-1", "p005", rec.REPINGED, tick=1)
        rec.commit_tick("INC-1", 1)
        assert rec.begin_tick("INC-1", 1) is False
        assert rec.get_state("INC-1", "p005").attempts == 1

    def test_a_crashed_tick_may_rerun(self):
        """Acted-then-crashed-before-commit: re-running is allowed, because a
        missed ping is worse than a duplicate one in a life-safety system."""
        assert rec.begin_tick("INC-1", 1) is True
        assert rec.begin_tick("INC-1", 1) is True

    def test_per_person_guard_prevents_the_duplicate_anyway(self):
        """The coarse tick guard is belt; this is braces. A person already
        acted on at tick N is skipped when tick N re-runs."""
        rec.begin_tick("INC-1", 1)
        rec.transition("INC-1", "p005", rec.REPINGED, tick=1)
        assert rec.already_acted("INC-1", "p005", tick=1) is True
        assert rec.already_acted("INC-1", "p005", tick=2) is False

    def test_tick_numbers_are_scoped_to_the_incident(self):
        rec.begin_tick("INC-1", 1)
        rec.commit_tick("INC-1", 1)
        assert rec.begin_tick("INC-2", 1) is True


class TestCheckinCancelsPending:
    """A check-in between ticks must cancel escalation before the next tick
    reads state, or the loop escalates someone already accounted for."""

    def test_checkin_wins_over_a_pending_reping(self):
        rec.transition("INC-1", "p005", rec.REPINGED, tick=1)
        rec.record_checkin("INC-1", "p005", source="whatsapp")
        assert rec.get_state("INC-1", "p005").status == rec.ACCOUNTED

    def test_checkin_clears_a_pending_escalation(self):
        rec.transition("INC-1", "p005", rec.ESCALATED, tick=1)
        rec.record_checkin("INC-1", "p005", source="sms")
        s = rec.get_state("INC-1", "p005")
        assert s.status == rec.ACCOUNTED
        assert s.pending_escalation is False

    def test_the_next_tick_does_not_act_on_an_accounted_person(self):
        rec.transition("INC-1", "p005", rec.REPINGED, tick=1)
        rec.record_checkin("INC-1", "p005", source="slack")
        assert rec.should_act("INC-1", "p005", tick=2) is False

    def test_a_stale_tick_cannot_overwrite_a_newer_checkin(self):
        """The check-in writer and the tick reader race on one document. One
        instance today makes this tractable; the rule still has to be stated,
        because it is the first thing that breaks above one instance."""
        rec.record_checkin("INC-1", "p005", source="sms")
        with pytest.raises(rec.IllegalTransition):
            rec.transition("INC-1", "p005", rec.ESCALATED, tick=2)


class TestRoomReportCountsAsCheckin:
    """22 of 34 personnel sit in a numbered room. A teacher filing
    "room 101: all 25 students are safe" is demonstrably alive and typing, and
    their own per-person state used to stay UNKNOWN — so the loop would
    re-ping, then escalate, the person doing the reporting."""

    def test_the_reporter_is_accounted(self):
        rec.record_room_report("INC-1", room_id="101", reporter_person_id="p005")
        assert rec.get_state("INC-1", "p005").status == rec.ACCOUNTED

    def test_provenance_distinguishes_it_from_a_self_report(self):
        rec.record_room_report("INC-1", room_id="101", reporter_person_id="p005")
        assert rec.get_state("INC-1", "p005").accounted_via == "room_report"

    def test_it_cancels_a_pending_escalation(self):
        rec.transition("INC-1", "p005", rec.ESCALATED, tick=1)
        rec.record_room_report("INC-1", room_id="101", reporter_person_id="p005")
        assert rec.get_state("INC-1", "p005").pending_escalation is False

    def test_it_does_not_account_for_anyone_else_in_the_room(self):
        """"23 of 25 safe" does not say which 23, and never says the other
        occupants are fine."""
        rec.record_room_report("INC-1", room_id="101", reporter_person_id="p005")
        assert rec.get_state("INC-1", "p012").status == rec.SILENT

    def test_an_unattributed_room_report_accounts_for_nobody(self):
        rec.record_room_report("INC-1", room_id="101", reporter_person_id="")
        assert rec.get_state("INC-1", "p005").status == rec.SILENT


class TestUnreachableLedger:
    """Tell the IC once. Re-surface only on a real state change — anything else
    is noise that trains them to ignore it."""

    def test_first_flag_is_reported(self):
        rec.transition("INC-1", "p005", rec.UNREACHABLE, tick=1)
        rec.set_reachability_reason("INC-1", "p005", "no Slack ID, no consent")
        assert rec.unreported_unreachable("INC-1") == ["p005"]

    def test_after_reporting_it_stays_quiet(self):
        rec.transition("INC-1", "p005", rec.UNREACHABLE, tick=1)
        rec.set_reachability_reason("INC-1", "p005", "no Slack ID, no consent")
        rec.mark_unreachable_reported("INC-1", ["p005"], tick=1)
        assert rec.unreported_unreachable("INC-1") == []

    def test_a_changed_reason_resurfaces(self):
        rec.transition("INC-1", "p005", rec.UNREACHABLE, tick=1)
        rec.set_reachability_reason("INC-1", "p005", "no Slack ID, no consent")
        rec.mark_unreachable_reported("INC-1", ["p005"], tick=1)
        rec.set_reachability_reason("INC-1", "p005", "consent withdrawn mid-incident")
        assert rec.unreported_unreachable("INC-1") == ["p005"]

    def test_leaving_unreachable_does_not_resurface_as_unreachable(self):
        rec.transition("INC-1", "p005", rec.UNREACHABLE, tick=1)
        rec.set_reachability_reason("INC-1", "p005", "no Slack ID")
        rec.mark_unreachable_reported("INC-1", ["p005"], tick=1)
        rec.transition("INC-1", "p005", rec.ACCOUNTED, tick=2)
        assert rec.unreported_unreachable("INC-1") == []

    def test_becoming_unreachable_again_reports_again(self):
        rec.transition("INC-1", "p005", rec.UNREACHABLE, tick=1)
        rec.set_reachability_reason("INC-1", "p005", "no Slack ID")
        rec.mark_unreachable_reported("INC-1", ["p005"], tick=1)
        rec.transition("INC-1", "p005", rec.ACCOUNTED, tick=2)
        rec.reopen("INC-1", "p005", reason="zone reopened", tick=3)
        rec.transition("INC-1", "p005", rec.UNREACHABLE, tick=3)
        assert rec.unreported_unreachable("INC-1") == ["p005"]


class TestSchemaFallsOutOfTheContract:
    """The persisted shape is dictated by the states above, not chosen after."""

    def test_person_document_carries_exactly_what_the_rules_need(self):
        rec.transition("INC-1", "p005", rec.REPINGED, tick=3)
        doc = rec.get_state("INC-1", "p005").as_document()
        assert set(doc) >= {
            "incident_id", "person_id", "status", "attempts", "last_acted_tick",
            "reachability_reason", "unreachable_reported_at_tick",
            "pending_escalation", "accounted_via", "reopen_reason", "updated_at",
        }

    def test_a_document_round_trips(self):
        rec.transition("INC-1", "p005", rec.REPINGED, tick=3)
        doc = rec.get_state("INC-1", "p005").as_document()
        restored = rec.PersonState.from_document(doc)
        assert restored.status == rec.REPINGED
        assert restored.attempts == 1
        assert restored.last_acted_tick == 3


class TestKnobsAreParametersNotLiterals:
    """The cap and the cadence will both want retuning between a demo and a
    real deployment. If either is baked into a transition edge, "what if a
    school wants two re-pings, not three" is a code change."""

    def test_cap_is_read_from_config(self, monkeypatch):
        monkeypatch.setenv("CRISISMESH_REPING_CAP", "2")
        assert rec.attempt_cap() == 2

    def test_cadence_is_read_from_config(self, monkeypatch):
        monkeypatch.setenv("CRISISMESH_TICK_SECONDS", "20")
        assert rec.tick_interval_seconds() == 20

    def test_the_same_state_escalates_differently_under_a_different_cap(self, monkeypatch):
        """The proof it is not baked in: nothing about the person changes."""
        for tick in (1, 2):
            rec.transition("INC-1", "p005", rec.REPINGED, tick=tick)
        monkeypatch.setenv("CRISISMESH_REPING_CAP", "2")
        assert rec.should_escalate("INC-1", "p005") is True
        monkeypatch.setenv("CRISISMESH_REPING_CAP", "5")
        assert rec.should_escalate("INC-1", "p005") is False

    def test_the_transition_table_holds_no_numbers(self):
        """A cap encoded as an edge would show up here."""
        import inspect
        import re
        source = inspect.getsource(rec.transition)
        assert "attempt_cap" not in source
        assert not re.search(r"\battempts\s*[<>=]+\s*\d", source)

    def test_garbage_config_falls_back_to_the_default(self, monkeypatch):
        monkeypatch.setenv("CRISISMESH_REPING_CAP", "not-a-number")
        assert rec.attempt_cap() == rec.MAX_ATTEMPTS

    def test_a_zero_cap_is_clamped(self, monkeypatch):
        """A cap of zero would escalate everyone on the first tick."""
        monkeypatch.setenv("CRISISMESH_REPING_CAP", "0")
        assert rec.attempt_cap() >= 1


class TestCrashBlastRadius:
    """Worst case a crash re-pings the tail of one tick, never the whole roster."""

    def test_a_rerun_tick_skips_everyone_it_already_reached(self):
        reached = ["p001", "p002", "p003"]
        not_yet = ["p004", "p005"]
        rec.begin_tick("INC-1", 7)
        for pid in reached:
            rec.transition("INC-1", pid, rec.REPINGED, tick=7)
        # crash here — tick never committed

        assert rec.begin_tick("INC-1", 7) is True
        for pid in reached:
            assert rec.should_act("INC-1", pid, tick=7) is False, f"{pid} would be re-pinged"
        for pid in not_yet:
            assert rec.should_act("INC-1", pid, tick=7) is True

    def test_attempts_are_not_double_counted_on_rerun(self):
        rec.begin_tick("INC-1", 7)
        rec.transition("INC-1", "p001", rec.REPINGED, tick=7)
        if rec.should_act("INC-1", "p001", tick=7):
            rec.transition("INC-1", "p001", rec.REPINGED, tick=7)
        assert rec.get_state("INC-1", "p001").attempts == 1


class TestSelfLoopIsInert:
    """Legal and inert are two properties. The second matters more once the
    store is a network away: a no-op that writes is write amplification, an
    audit entry saying something happened when nothing did — and, worse, a
    stamped last_acted_tick makes the tick skip a person it never acted on."""

    def test_a_no_op_transition_changes_nothing(self):
        before = rec.get_state("INC-1", "p005").as_document()
        rec.transition("INC-1", "p005", rec.SILENT, tick=9)
        after = rec.get_state("INC-1", "p005").as_document()
        assert before == after

    def test_a_no_op_does_not_stamp_the_acting_tick(self):
        """The bug this catches: a defensive no-op silently skipping someone."""
        rec.transition("INC-1", "p005", rec.SILENT, tick=9)
        assert rec.should_act("INC-1", "p005", tick=9) is True

    @pytest.mark.parametrize("status", [rec.ESCALATED, rec.UNREACHABLE, rec.ACCOUNTED])
    def test_every_no_op_self_loop_is_inert(self, status):
        rec.transition("INC-1", "p005", status, tick=1)
        before = rec.get_state("INC-1", "p005").as_document()
        rec.transition("INC-1", "p005", status, tick=2)
        assert rec.get_state("INC-1", "p005").as_document() == before

    def test_a_repeat_reping_is_not_a_no_op(self):
        """A second re-ping is a real act even though the status is unchanged."""
        rec.transition("INC-1", "p005", rec.REPINGED, tick=1)
        rec.transition("INC-1", "p005", rec.REPINGED, tick=2)
        assert rec.get_state("INC-1", "p005").attempts == 2
        assert rec.get_state("INC-1", "p005").last_acted_tick == 2

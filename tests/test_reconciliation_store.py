"""What a network-backed store introduces that a dict never made us confront.

The dict was a correctness model. Firestore is the same model plus a failure
model, so the 42 contract tests port unchanged — that is the seam working — and
these are the only new ones: interleaving, read failure, write failure, and
write minimisation.

`--max-instances=1` removes concurrent ticks. It does not remove interleaving:
an inbound SAFE webhook and a tick can both be in flight in the same process
against the same document, because a network read and a network write are
separable where a dict access was not.
"""

import pytest

from src.core import reconciliation as rec
from src.core import reconciliation_store as store


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setenv("CRISISMESH_RECONCILIATION_STORE", "memory")
    store.reset_backend()
    rec.reset()
    yield
    store.reset_backend()
    rec.reset()


class TestBackendSelection:
    """The dict staying valid is the proof the separation held. Firestore is the
    durable backing under the logic, never a dependency of it."""

    def test_memory_is_the_default(self, monkeypatch):
        monkeypatch.delenv("CRISISMESH_RECONCILIATION_STORE", raising=False)
        store.reset_backend()
        assert store.backend_name() == "memory"

    def test_firestore_is_opt_in(self, monkeypatch):
        monkeypatch.setenv("CRISISMESH_RECONCILIATION_STORE", "firestore")
        store.reset_backend()
        assert store.backend_name() == "firestore"

    def test_an_unknown_backend_falls_back_to_memory(self, monkeypatch):
        """Never fail closed into "no state at all" — a loop with no memory
        re-pings everyone every tick."""
        monkeypatch.setenv("CRISISMESH_RECONCILIATION_STORE", "cassandra")
        store.reset_backend()
        assert store.backend_name() == "memory"


class TestInterleaving:
    """The single most likely place a "mechanical" swap introduces a real bug.

    Read-modify-write stops being atomic. Between the tick's read and the
    tick's write, a check-in can land — and a tick that computed from pre-cancel
    state must not write post-tick state on top of it.
    """

    def test_a_checkin_landing_mid_tick_wins(self):
        rec.transition("INC-1", "p005", rec.REPINGED, tick=1)

        # tick 2 reads, intending to escalate
        snapshot = store.read("INC-1", "p005")
        assert snapshot.status == rec.REPINGED

        # ...the SAFE webhook lands here...
        rec.record_checkin("INC-1", "p005", source="whatsapp")

        # ...and only now does the tick write what it computed. Version is
        # checked before legality: the caller's problem is a stale read, and
        # naming it an illegal transition points them at the wrong thing.
        with pytest.raises(store.StaleWrite):
            store.commit_transition("INC-1", "p005", rec.ESCALATED,
                                    tick=2, expected_version=snapshot.version)

        assert rec.get_state("INC-1", "p005").status == rec.ACCOUNTED

    def test_a_stale_write_is_refused_by_version(self):
        """Compare-and-set on the document version, which is what a Firestore
        transaction gives us and a dict never needed."""
        snapshot = store.read("INC-1", "p005")
        rec.record_checkin("INC-1", "p005", source="sms")
        with pytest.raises(store.StaleWrite):
            store.commit_transition("INC-1", "p005", rec.REPINGED,
                                    tick=2, expected_version=snapshot.version)

    def test_a_fresh_write_succeeds(self):
        snapshot = store.read("INC-1", "p005")
        store.commit_transition("INC-1", "p005", rec.REPINGED,
                                tick=1, expected_version=snapshot.version)
        assert rec.get_state("INC-1", "p005").status == rec.REPINGED

    def test_the_version_advances_on_a_real_write_only(self):
        first = store.read("INC-1", "p005").version
        rec.transition("INC-1", "p005", rec.SILENT, tick=1)   # inert
        assert store.read("INC-1", "p005").version == first
        rec.transition("INC-1", "p005", rec.REPINGED, tick=1)
        assert store.read("INC-1", "p005").version > first


class TestReadFailure:
    """A dict get never throws. What a tick does when it cannot read a person's
    state is a safety decision, not error handling."""

    def test_an_unreadable_person_is_skipped_not_acted_on(self, monkeypatch):
        """Uncertainty withholds action. Treating unreadable as SILENT and
        re-pinging is manufacturing a state we could not confirm."""
        def _boom(*a, **kw):
            raise store.StoreUnavailable("read timed out")

        monkeypatch.setattr(store, "read", _boom)
        assert store.safe_should_act("INC-1", "p005", tick=1) is False

    def test_a_read_failure_does_not_advance_the_person(self, monkeypatch):
        def _boom(*a, **kw):
            raise store.StoreUnavailable("throttled")

        monkeypatch.setattr(store, "read", _boom)
        store.safe_should_act("INC-1", "p005", tick=1)
        assert rec.get_state("INC-1", "p005").attempts == 0

    def test_a_read_failure_is_surfaced_not_swallowed(self, monkeypatch, caplog):
        import logging

        def _boom(*a, **kw):
            raise store.StoreUnavailable("unavailable")

        monkeypatch.setattr(store, "read", _boom)
        with caplog.at_level(logging.ERROR):
            store.safe_should_act("INC-1", "p005", tick=1)
        assert "could not read" in caplog.text.lower()


class TestWriteFailure:
    """The act happened, the commit did not — the crash case through a
    different door. It must land in the same guarded state."""

    def test_a_failed_commit_leaves_the_tick_uncommitted(self, monkeypatch):
        rec.begin_tick("INC-1", 5)

        def _boom(*a, **kw):
            raise store.StoreUnavailable("write failed")

        monkeypatch.setattr(store, "_persist", _boom)
        with pytest.raises(store.StoreUnavailable):
            store.commit_transition("INC-1", "p005", rec.REPINGED, tick=5)
        assert rec.begin_tick("INC-1", 5) is True

    def test_a_rerun_after_a_failed_write_does_not_double_count(self, monkeypatch):
        """The act may have reached the person even though the write failed, so
        the re-run must be guarded by whatever did land."""
        store.commit_transition("INC-1", "p005", rec.REPINGED, tick=5)
        assert rec.should_act("INC-1", "p005", tick=5) is False
        assert rec.get_state("INC-1", "p005").attempts == 1


class TestWriteMinimisation:
    """In memory, touching state every tick is free. On Firestore it is N writes
    x ticks x incident duration — and the pressure that makes someone raise
    max-instances, at which point the interleaving above stops being hidden."""

    def test_a_no_op_transition_writes_nothing(self):
        store.reset_counters()
        rec.transition("INC-1", "p005", rec.SILENT, tick=1)
        assert store.write_count() == 0

    def test_only_state_changes_write(self):
        store.reset_counters()
        rec.transition("INC-1", "p005", rec.REPINGED, tick=1)   # writes
        rec.transition("INC-1", "p005", rec.REPINGED, tick=2)   # writes (advances)
        rec.transition("INC-1", "p005", rec.ACCOUNTED, tick=3)  # writes
        rec.transition("INC-1", "p005", rec.ACCOUNTED, tick=4)  # inert
        assert store.write_count() == 3

    def test_observing_an_unchanged_person_costs_nothing(self):
        """A tick that looks at 34 people and finds 30 unchanged writes 4."""
        store.reset_counters()
        for pid in [f"p{i:03d}" for i in range(1, 35)]:
            rec.transition("INC-1", pid, rec.SILENT, tick=1)
        assert store.write_count() == 0


class TestVersionIsInertAware:
    """The concurrency token is the same bug family as the SILENT->SILENT stamp,
    one layer up. A version that moves for an unchanged document is a phantom
    conflict generator: a defensive no-op bumps it, and the next legitimate
    write from a concurrent path is refused for a change nobody made."""

    def test_an_inert_transition_does_not_advance_the_version(self):
        before = store.read("INC-1", "p005").version
        rec.transition("INC-1", "p005", rec.SILENT, tick=1)
        assert store.read("INC-1", "p005").version == before

    def test_version_updated_at_and_tick_move_together_or_not_at_all(self):
        """No transition may advance one without the others."""
        before = store.read("INC-1", "p005").as_document()
        rec.transition("INC-1", "p005", rec.SILENT, tick=7)
        after = store.read("INC-1", "p005").as_document()
        moved = {k for k in ("version", "updated_at", "last_acted_tick")
                 if before[k] != after[k]}
        assert moved == set(), f"phantom movement in {moved}"

        rec.transition("INC-1", "p005", rec.REPINGED, tick=7)
        real = store.read("INC-1", "p005").as_document()
        moved = {k for k in ("version", "updated_at", "last_acted_tick")
                 if after[k] != real[k]}
        assert moved == {"version", "updated_at", "last_acted_tick"}

    def test_a_no_op_does_not_create_a_phantom_conflict(self):
        """The scenario: a tick reads, a defensive no-op runs, the tick writes.
        Nothing changed, so the write must still be accepted."""
        snapshot = store.read("INC-1", "p005")
        rec.transition("INC-1", "p005", rec.SILENT, tick=1)   # inert
        store.commit_transition("INC-1", "p005", rec.REPINGED,
                                tick=1, expected_version=snapshot.version)
        assert rec.get_state("INC-1", "p005").status == rec.REPINGED


class TestStaleWriteIsReDecidedNotReForced:
    """"The guard fired and then we overrode it" is a plausible and lethal way
    to get this wrong. On StaleWrite the tick re-reads and re-decides; it never
    retries the stale decision."""

    def test_a_person_who_checked_in_mid_tick_is_not_repinged(self):
        rec.transition("INC-1", "p005", rec.REPINGED, tick=1)
        original = rec.get_state("INC-1", "p005").attempts

        # The tick decides to re-ping, then the SAFE lands before it writes.
        def _checkin_lands_mid_decision(*a, **kw):
            rec.record_checkin("INC-1", "p005", source="whatsapp")
            return real_read(*a, **kw)

        real_read = store.read
        store.read = _checkin_lands_mid_decision
        try:
            result = store.tick_person("INC-1", "p005", rec.REPINGED, tick=2)
        finally:
            store.read = real_read

        assert result is None, "a stale decision was forced through"
        assert rec.get_state("INC-1", "p005").status == rec.ACCOUNTED
        assert rec.get_state("INC-1", "p005").attempts == original

    def test_a_stale_write_on_a_still_silent_person_succeeds_after_reread(self):
        """Re-decide means re-decide, not give up: if the person is still
        silent after the re-read, the tick acts."""
        bumped = {"done": False}
        real_read = store.read

        def _something_else_writes_first(*a, **kw):
            if not bumped["done"]:
                bumped["done"] = True
                rec.set_reachability_reason("INC-1", "p005", "no Slack ID")
                rec.transition("INC-1", "p005", rec.UNREACHABLE, tick=1)
            return real_read(*a, **kw)

        store.read = _something_else_writes_first
        try:
            result = store.tick_person("INC-1", "p005", rec.REPINGED, tick=2)
        finally:
            store.read = real_read
        assert result == rec.REPINGED


class TestOnePersonCannotEndTheTick:
    """A throttled read on p017 must not silently drop p018-p034 — the
    missed-ping failure arriving through the error path."""

    ROSTER = [f"p{i:03d}" for i in range(1, 35)]

    def test_a_read_failure_mid_roster_does_not_abort(self, monkeypatch):
        real_read = store.read

        def _fails_on_p017(incident_id, person_id):
            if person_id == "p017":
                raise store.StoreUnavailable("throttled")
            return real_read(incident_id, person_id)

        monkeypatch.setattr(store, "read", _fails_on_p017)
        result = store.tick_roster("INC-1", self.ROSTER, rec.REPINGED, tick=1)

        assert result["evaluated"] == 34
        assert "p017" in result["skipped"]
        assert "p018" in result["acted"], "the roster after the failure was dropped"
        assert "p034" in result["acted"]
        assert len(result["acted"]) == 33

    def test_an_unexpected_error_is_contained_to_one_person(self, monkeypatch):
        real_tick = store.tick_person

        def _explodes_on_p005(incident_id, person_id, target, tick):
            if person_id == "p005":
                raise RuntimeError("something nobody anticipated")
            return real_tick(incident_id, person_id, target, tick)

        monkeypatch.setattr(store, "tick_person", _explodes_on_p005)
        result = store.tick_roster("INC-1", self.ROSTER, rec.REPINGED, tick=1)
        assert result["evaluated"] == 34
        assert "p005" in result["skipped"]
        assert len(result["acted"]) == 33

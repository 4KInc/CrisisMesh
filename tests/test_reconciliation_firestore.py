"""What the real client introduces that the dict could not simulate.

The lesson from `slack_sdk`: a branch gated on an optional import or a
deployed-only credential is untested until it runs there, whatever the local
number says. These narrow that gap where it can be narrowed locally — real
exception types, real serialisation semantics — and the deployed durability run
is the acceptance gate for the rest.
"""

import pytest

from google.api_core import exceptions as gapi

from src.core import reconciliation as rec
from src.core import reconciliation_store as store


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setenv("CRISISMESH_RECONCILIATION_STORE", "memory")
    store.reset_backend()
    store.reset_client()
    rec.reset()
    yield
    store.reset_backend()
    store.reset_client()
    rec.reset()


class TestRoundTripPreservesNoneVersusZero:
    """`unreachable_reported_at_tick` None (never told the IC) versus 0 (told at
    tick 0) is the re-surface rule. If None reads back as 0 — or 0 reads back as
    absent — the IC is either re-alarmed with 34 names after a redeploy, or
    never told at all. This is the durability proof's real failure mode, more
    than "did anything persist"."""

    def test_none_survives(self):
        state = rec.get_state("INC-1", "p005")
        assert state.unreachable_reported_at_tick is None
        restored = rec.PersonState.from_document(state.as_document())
        assert restored.unreachable_reported_at_tick is None

    def test_zero_survives_and_is_not_none(self):
        rec.transition("INC-1", "p005", rec.UNREACHABLE, tick=0)
        rec.mark_unreachable_reported("INC-1", ["p005"], tick=0)
        doc = rec.get_state("INC-1", "p005").as_document()
        assert doc["unreachable_reported_at_tick"] == 0
        restored = rec.PersonState.from_document(doc)
        assert restored.unreachable_reported_at_tick == 0
        assert restored.unreachable_reported_at_tick is not None

    def test_an_absent_field_does_not_become_reported(self):
        """A document written by an older schema must read as never-told, not
        told-at-tick-0 — the direction that silences a real alarm."""
        partial = {"incident_id": "INC-1", "person_id": "p005",
                   "status": rec.UNREACHABLE, "attempts": 1}
        restored = rec.PersonState.from_document(partial)
        assert restored.unreachable_reported_at_tick is None

    def test_the_resurface_rule_survives_a_round_trip(self):
        """The world-claim, not the field-claim: after a round trip, is the IC
        re-told?"""
        rec.set_reachability_reason("INC-1", "p005", "no Slack id")
        rec.transition("INC-1", "p005", rec.UNREACHABLE, tick=0)
        rec.mark_unreachable_reported("INC-1", ["p005"], tick=0)
        doc = rec.get_state("INC-1", "p005").as_document()

        rec.reset()  # a redeploy: the process forgets
        rehydrated = rec.PersonState.from_document(doc)
        rec._people[("INC-1", "p005")] = rehydrated

        assert rec.unreported_unreachable("INC-1") == [], (
            "the IC would be re-alarmed about someone already reported"
        )


class TestRealExceptionTypesAreContained:
    """The dict tests raised RuntimeError. Firestore raises
    google.api_core.exceptions.*, so an `except RuntimeError` that looked
    complete against the dict is blind to the real one."""

    @pytest.mark.parametrize("exc", [
        gapi.DeadlineExceeded("timed out"),
        gapi.ServiceUnavailable("unavailable"),
        gapi.TooManyRequests("throttled"),
        gapi.PermissionDenied("no auth"),
    ])
    def test_a_client_error_becomes_store_unavailable(self, monkeypatch, exc):
        monkeypatch.setenv("CRISISMESH_RECONCILIATION_STORE", "firestore")
        store.reset_backend()

        def _raises(*a, **kw):
            raise exc

        monkeypatch.setattr(store, "_firestore_client", _raises)
        with pytest.raises(store.StoreUnavailable):
            store._load_firestore("INC-1", "p005")

    @pytest.mark.parametrize("exc", [
        gapi.DeadlineExceeded("timed out"),
        gapi.ServiceUnavailable("unavailable"),
    ])
    def test_a_tick_degrades_rather_than_500ing(self, monkeypatch, exc):
        """The endpoint must not fail; the person is skipped and retried."""
        def _raises(*a, **kw):
            raise store.StoreUnavailable(str(exc))

        monkeypatch.setattr(store, "read", _raises)
        assert store.safe_should_act("INC-1", "p005", tick=1) is False

    def test_a_failed_hydrate_does_not_reset_someone_to_silent(self, monkeypatch):
        """A read that failed is not evidence the person is new. Treating it as
        new re-pings someone already escalated."""
        monkeypatch.setenv("CRISISMESH_RECONCILIATION_STORE", "firestore")
        store.reset_backend()

        def _raises(*a, **kw):
            raise gapi.ServiceUnavailable("unavailable")

        monkeypatch.setattr(store, "_firestore_client", _raises)
        with pytest.raises(store.StoreUnavailable):
            store.load("INC-1", "p005")

    def test_the_roster_survives_one_persons_client_error(self, monkeypatch):
        roster = [f"p{i:03d}" for i in range(1, 35)]
        real = store.read

        def _fails_on_p017(incident_id, person_id):
            if person_id == "p017":
                raise store.StoreUnavailable(str(gapi.TooManyRequests("throttled")))
            return real(incident_id, person_id)

        monkeypatch.setattr(store, "read", _fails_on_p017)
        result = store.tick_roster("INC-1", roster, rec.REPINGED, tick=1)
        assert result["evaluated"] == 34
        assert "p017" in result["skipped"]
        assert len(result["acted"]) == 33


class TestVersionCheckIsInsideTheTransaction:
    """Reading the version outside the transaction and only writing inside it
    rebuilds the inert guard the detached-snapshot fix removed."""

    def test_the_transaction_body_reads_the_version_itself(self):
        import inspect

        source = inspect.getsource(store.commit_in_transaction)
        body = source[source.index("def _apply"):]
        assert "ref.get(transaction=txn" in body, "the read is outside the transaction"
        assert "expected_version" in body, "the comparison is outside the transaction"
        assert body.index("ref.get(transaction=txn") < body.index("txn.set(ref")


class TestTransactionOwnsItsOwnWrite:
    """`rec.transition` runs inside the transaction body and calls back into
    `note_write`. Left unsuppressed that fires a second, non-transactional
    write to the document the transaction has open — contention against itself.
    Firestore retries, each retry writes again, and it never commits: no
    exception, no document, no log. Silence, which an exception handler cannot
    catch."""

    def test_note_write_does_not_persist_while_a_transaction_is_open(self, monkeypatch):
        persisted = []
        monkeypatch.setattr(store, "_persist", lambda state: persisted.append(state.person_id))

        state = rec.get_state("INC-1", "p005")
        store.note_write(state)
        assert persisted == ["p005"]

        store._in_transaction.active = True
        try:
            store.note_write(state)
        finally:
            store._in_transaction.active = False
        assert persisted == ["p005"], "a second write fired inside the transaction"

    def test_the_counter_still_advances_inside_a_transaction(self):
        """Suppression is of the out-of-band write only. The version must still
        move, because the transaction writes the document carrying it."""
        store.reset_counters()
        state = rec.get_state("INC-1", "p005")
        before = state.version
        store._in_transaction.active = True
        try:
            store.note_write(state)
        finally:
            store._in_transaction.active = False
        assert state.version == before + 1
        assert store.write_count() == 1

    def test_the_flag_clears_even_when_the_transaction_raises(self, monkeypatch):
        """A stuck flag would silently disable persistence for every later
        write on this thread."""
        monkeypatch.setenv("CRISISMESH_RECONCILIATION_STORE", "firestore")
        store.reset_backend()

        def _raises(*a, **kw):
            raise gapi.ServiceUnavailable("unavailable")

        monkeypatch.setattr(store, "_firestore_client", _raises)
        with pytest.raises(store.StoreUnavailable):
            store.commit_in_transaction("INC-1", "p005", rec.REPINGED, 1, 0)
        assert store._transaction_owns_the_write() is False


class TestEveryCallCarriesADeadline:
    """Silence is not an exception. A client that retries internally blocks
    rather than raising, so every containment built for errors walks straight
    past it. The deadline is what converts silence into `no`."""

    def test_the_constant_is_bounded(self):
        assert 0 < store.CALL_DEADLINE_SECONDS <= 30

    def test_reads_writes_and_transactions_all_pass_a_timeout(self):
        import inspect

        for fn in (store._load_firestore, store._persist_firestore,
                   store.commit_in_transaction):
            source = inspect.getsource(fn)
            assert "timeout=" in source, f"{fn.__name__} can block forever"

    def test_a_deadline_becomes_store_unavailable(self, monkeypatch):
        """And StoreUnavailable is what the existing containment handles."""
        monkeypatch.setenv("CRISISMESH_RECONCILIATION_STORE", "firestore")
        store.reset_backend()

        def _times_out(*a, **kw):
            raise gapi.DeadlineExceeded("deadline exceeded")

        monkeypatch.setattr(store, "_firestore_client", _times_out)
        with pytest.raises(store.StoreUnavailable):
            store._load_firestore("INC-1", "p005")

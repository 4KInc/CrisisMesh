"""The witness log, the room board and the session window outlive one instance.

These were the singletons named in Known Limits: the incident is durable in
Firestore and these were not, so replacing a container mid-incident reset the
board under a live emergency and capped the deployment at --max-instances=1.

None of them needs the compare-and-set the reconciliation state machine uses.
Observations are append-only, a room report replaces that room's entry, and a
session window is one timestamp per handset. Adding CAS here would be machinery
for the look of it.

What they do need is a read failure that stays a read failure. An unreadable
sighting store returning [] is not "no sightings" — and the egress assessment
consumes exactly that list to decide which corridors are clear.
"""

import os
from unittest.mock import patch

import pytest

from src.core import durable_store, observations, room_board


@pytest.fixture(autouse=True)
def memory_backend(monkeypatch):
    monkeypatch.setenv("CRISISMESH_DURABLE_STORE", "memory")
    from src.agents.accountability import tools as acct
    from src.core import reconciliation, reconciliation_store

    durable_store.reset_backend()
    reconciliation_store.reset_backend()
    observations.reset()
    room_board.reset()
    acct._checkin_store.clear()
    yield
    # Backends are cached after the first read, so a test that switched to
    # firestore leaves every later test on it unless this resets too.
    durable_store.reset_backend()
    reconciliation_store.reset_backend()
    reconciliation.reset()
    observations.reset()
    room_board.reset()
    acct._checkin_store.clear()


class TestTheBackendAnnouncesItself:
    def test_memory_is_the_default(self, monkeypatch):
        monkeypatch.delenv("CRISISMESH_DURABLE_STORE", raising=False)
        durable_store.reset_backend()
        assert durable_store.backend_name() == "memory"

    def test_an_unknown_value_does_not_silently_mean_memory(self, monkeypatch):
        """A deploy that meant to say firestore and typo'd must not look
        identical to one that meant memory."""
        monkeypatch.setenv("CRISISMESH_DURABLE_STORE", "firestoer")
        durable_store.reset_backend()
        with pytest.raises(ValueError):
            durable_store.backend_name()


class TestObservationsSurviveAnInstance:
    def test_a_sighting_written_by_one_instance_is_read_by_another(self, monkeypatch):
        monkeypatch.setenv("CRISISMESH_DURABLE_STORE", "firestore")
        fake = _FakeFirestore()
        with patch.object(durable_store, "_client", return_value=fake):
            durable_store.reset_backend()
            observations.record("T-1", "shooter last seen heading toward the gym",
                                source="whatsapp", person_name="Mrs. Rodriguez")
            observations.reset()          # the container is replaced
            got = observations.get("T-1")
        assert len(got) == 1
        assert "gym" in got[0]["text"]

    def test_order_is_preserved_across_the_boundary(self, monkeypatch):
        """A trail read out of order says the threat moved the other way."""
        monkeypatch.setenv("CRISISMESH_DURABLE_STORE", "firestore")
        fake = _FakeFirestore()
        with patch.object(durable_store, "_client", return_value=fake):
            durable_store.reset_backend()
            for text in ["shooter spotted in the east wing",
                         "shooter last seen heading toward the gym",
                         "shooter now near the cafeteria"]:
                observations.record("T-1", text, source="whatsapp")
            observations.reset()
            got = observations.get("T-1")
        assert [g["text"].split()[-1] for g in got] == ["wing", "gym", "cafeteria"]


class TestAnUnreadableSightingStoreIsNotAnEmptyOne:
    def test_get_raises_rather_than_returning_nothing(self, monkeypatch):
        monkeypatch.setenv("CRISISMESH_DURABLE_STORE", "firestore")
        broken = _FakeFirestore(fail_reads=True)
        with patch.object(durable_store, "_client", return_value=broken):
            durable_store.reset_backend()
            with pytest.raises(durable_store.StoreUnavailable):
                observations.get("T-1")

    def test_the_egress_assessment_refuses_to_call_anything_clear(self, monkeypatch):
        """The consequence, stated: a route is called clear because no sighting
        lies on it. If the sightings cannot be read, that sentence is unfounded
        — and it is the one a responder acts on."""
        from src.core import incident_state
        from src.core.knowledge_base import KnowledgeBase, init_knowledge_base
        from src.services import slack_transport

        SEED = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "data", "seed")
        KnowledgeBase.reset()
        init_knowledge_base(SEED)
        incident_state.reset()
        incident_state.declare("T-1", {
            "incident_id": "T-1", "report": "active shooter in the east wing",
            "classification": {"incident_type": "active_threat", "severity": "critical"},
            "location": {"zone_id": "east-wing-f1", "zone_name": "East Wing Floor 1"},
        }, source="whatsapp")

        monkeypatch.setenv("CRISISMESH_DURABLE_STORE", "firestore")
        broken = _FakeFirestore(fail_reads=True)
        posted = []
        with patch.object(durable_store, "_client", return_value=broken), \
             patch.object(slack_transport, "_post_bot_message",
                          lambda ch, t, **kw: posted.append(t)):
            durable_store.reset_backend()
            slack_transport._handle_arrival_brief("C1", "")
        text = "\n".join(posted)
        incident_state.reset()

        assert "No reported sighting on these paths" not in text
        assert "could not be read" in text.lower() or "unavailable" in text.lower()


class TestTheRoomBoardSurvivesAnInstance:
    def test_a_room_report_is_readable_from_another_instance(self, monkeypatch):
        monkeypatch.setenv("CRISISMESH_DURABLE_STORE", "firestore")
        fake = _FakeFirestore()
        with patch.object(durable_store, "_client", return_value=fake):
            durable_store.reset_backend()
            room_board.record("T-1", {"room": "104", "safe": 23, "missing": 1,
                                      "notes": ""}, source="whatsapp")
            room_board.reset()
            board = room_board.get("T-1")
        assert board["104"]["safe"] == 23

    def test_a_later_report_replaces_the_earlier_one(self, monkeypatch):
        """Last writer wins per room, which is what a re-count means."""
        monkeypatch.setenv("CRISISMESH_DURABLE_STORE", "firestore")
        fake = _FakeFirestore()
        with patch.object(durable_store, "_client", return_value=fake):
            durable_store.reset_backend()
            room_board.record("T-1", {"room": "104", "safe": 23, "missing": 1, "notes": ""})
            room_board.record("T-1", {"room": "104", "safe": 24, "missing": 0, "notes": ""})
            room_board.reset()
            board = room_board.get("T-1")
        assert len(board) == 1
        assert board["104"]["safe"] == 24

    def test_an_unreadable_board_is_not_an_empty_school(self, monkeypatch):
        monkeypatch.setenv("CRISISMESH_DURABLE_STORE", "firestore")
        broken = _FakeFirestore(fail_reads=True)
        with patch.object(durable_store, "_client", return_value=broken):
            durable_store.reset_backend()
            with pytest.raises(durable_store.StoreUnavailable):
                room_board.get("T-1")


class TestTheSessionWindowSurvivesAnInstance:
    def test_a_window_opened_on_one_instance_is_seen_by_another(self, monkeypatch):
        from src.services import whatsapp_transport

        monkeypatch.setenv("CRISISMESH_DURABLE_STORE", "firestore")
        fake = _FakeFirestore()
        with patch.object(durable_store, "_client", return_value=fake):
            durable_store.reset_backend()
            whatsapp_transport.note_inbound("+16692167706")
            whatsapp_transport.reset_session_windows()
            assert whatsapp_transport.in_session_window("+16692167706") is True

    def test_an_unreadable_window_assumes_closed(self, monkeypatch):
        """The safe direction here is the opposite of the others: assuming the
        window is open sends a free-form message Meta rejects, and the person
        gets nothing. Assuming it is closed sends a template, which arrives."""
        from src.services import whatsapp_transport

        monkeypatch.setenv("CRISISMESH_DURABLE_STORE", "firestore")
        broken = _FakeFirestore(fail_reads=True)
        with patch.object(durable_store, "_client", return_value=broken):
            durable_store.reset_backend()
            assert whatsapp_transport.in_session_window("+16692167706") is False


# ── A Firestore stand-in shaped like the client the store actually calls ──

class _FakeDoc:
    def __init__(self, doc_id, data):
        self.id, self._data = doc_id, data

    def to_dict(self):
        return dict(self._data)


class _FakeCollection:
    def __init__(self, store, name, fail_reads):
        self._store, self._name, self._fail = store, name, fail_reads

    def document(self, doc_id):
        return _FakeDocRef(self._store, self._name, doc_id, self._fail)

    def where(self, filter=None):  # noqa: A002 - mirrors the real kwarg
        return _FakeQuery(self._store, self._name, filter, self._fail)


class _FakeDocRef:
    def __init__(self, store, name, doc_id, fail):
        self._store, self._name, self._id, self._fail = store, name, doc_id, fail

    def set(self, data):
        self._store.setdefault(self._name, {})[self._id] = dict(data)

    def create(self, data):
        """Create-if-absent, like the real client — the primitive `claim` needs.
        Without this the double had no create() at all, `claim` fell into its
        own except-path and reported success to every caller."""
        from google.api_core.exceptions import AlreadyExists

        docs = self._store.setdefault(self._name, {})
        if self._id in docs:
            raise AlreadyExists(f"{self._name}/{self._id}")
        docs[self._id] = dict(data)

    def get(self):
        if self._fail:
            raise RuntimeError("firestore unavailable")
        data = self._store.get(self._name, {}).get(self._id)
        doc = _FakeDoc(self._id, data or {})
        doc.exists = data is not None
        return doc

    def delete(self):
        self._store.get(self._name, {}).pop(self._id, None)


class _FakeQuery:
    def __init__(self, store, name, flt, fail):
        self._store, self._name, self._filter, self._fail = store, name, flt, fail
        self._order = None

    def order_by(self, field):
        self._order = field
        return self

    def stream(self):
        if self._fail:
            raise RuntimeError("firestore unavailable")
        # The real client is handed a FieldFilter, not a tuple. Mirroring that
        # here is the difference between a double that proves the code works and
        # one that proves the double works.
        field = getattr(self._filter, "field_path", None)
        value = getattr(self._filter, "value", None)
        rows = [(k, v) for k, v in self._store.get(self._name, {}).items()
                if field is None or v.get(field) == value]
        if self._order:
            rows.sort(key=lambda kv: kv[1].get(self._order, ""))
        return [_FakeDoc(k, v) for k, v in rows]


class _FakeFirestore:
    def __init__(self, fail_reads=False):
        self._data: dict = {}
        self._fail = fail_reads

    def collection(self, name):
        return _FakeCollection(self._data, name, self._fail)


class TestTheCheckinLedgerSurvivesAnInstance:
    """The reconciliation state machine was already durable and the ledger it
    mirrors was not, so two instances would each have held half the check-ins —
    and the status card counts from the ledger."""

    def test_a_checkin_recorded_by_one_instance_is_counted_by_another(self, monkeypatch):
        from src.agents.accountability import tools as acct

        monkeypatch.setenv("CRISISMESH_DURABLE_STORE", "firestore")
        fake = _FakeFirestore()
        with patch.object(durable_store, "_client", return_value=fake):
            durable_store.reset_backend()
            acct.process_checkin("T-1", "p001", "safe")
            acct._checkin_store.clear()          # the container is replaced
            summary = acct.compute_accountability_summary("T-1")
        assert summary["accounted"] == 1
        assert summary["ledger_readable"] is True

    def test_a_later_status_replaces_the_earlier_one(self, monkeypatch):
        from src.agents.accountability import tools as acct

        monkeypatch.setenv("CRISISMESH_DURABLE_STORE", "firestore")
        fake = _FakeFirestore()
        with patch.object(durable_store, "_client", return_value=fake):
            durable_store.reset_backend()
            acct.process_checkin("T-1", "p001", "safe")
            acct.process_checkin("T-1", "p001", "injured")
            acct._checkin_store.clear()
            summary = acct.compute_accountability_summary("T-1")
        assert summary["counts"]["injured"] == 1
        assert summary["counts"]["safe"] == 0

    def test_an_unreadable_ledger_says_so_and_counts_everyone_missing(self, monkeypatch):
        """The safe direction is already taken by the roster denominator: no
        record means unaccounted. What was missing is the ability to tell that
        apart from nobody having checked in."""
        from src.agents.accountability import tools as acct

        monkeypatch.setenv("CRISISMESH_DURABLE_STORE", "firestore")
        broken = _FakeFirestore(fail_reads=True)
        with patch.object(durable_store, "_client", return_value=broken):
            durable_store.reset_backend()
            summary = acct.compute_accountability_summary("T-1")
        assert summary["ledger_readable"] is False
        assert summary["accounted"] == 0
        assert summary["unaccounted"] == summary["total_tracked"] == 34


class TestAnUnreadableLedgerIsVisibleToTheReader:
    def test_the_status_card_says_it(self, monkeypatch):
        from src.agents.accountability import tools as acct
        from src.core import incident_state
        from src.services import slack_transport

        incident_state.reset()
        incident_state.declare("T-1", {
            "incident_id": "T-1",
            "classification": {"incident_type": "active_threat", "severity": "critical"},
        }, source="whatsapp")
        monkeypatch.setenv("CRISISMESH_DURABLE_STORE", "firestore")
        broken = _FakeFirestore(fail_reads=True)
        with patch.object(durable_store, "_client", return_value=broken):
            durable_store.reset_backend()
            text = slack_transport._handle_status("C1", "U1")["text"]
        incident_state.reset()
        assert "could not be read" in text
        assert "not because nobody answered" in text


class TestOnlyOneInstanceRunsEachTick:
    """A reconciliation scheduler runs in every container. The tick guard was a
    process-local dict, so at --max-instances=4 each instance would run its own
    tick N and one silent teacher would be pinged four times."""

    def test_a_second_instance_does_not_re_run_the_same_tick(self, monkeypatch):
        from src.core import reconciliation as rec, reconciliation_store as rstore

        monkeypatch.setenv("CRISISMESH_DURABLE_STORE", "firestore")
        monkeypatch.setenv("CRISISMESH_RECONCILIATION_STORE", "firestore")
        fake = _FakeFirestore()
        with patch.object(durable_store, "_client", return_value=fake), \
             patch.object(rstore, "_firestore_client", return_value=fake):
            durable_store.reset_backend()
            rstore.reset_backend()
            first = rstore.begin_tick_guard("T-1", 7)
            rec.reset()                      # a different container
            second = rstore.begin_tick_guard("T-1", 7)
        assert first is True
        assert second is False, "both instances would have run tick 7"

    def test_a_different_tick_still_runs(self, monkeypatch):
        from src.core import reconciliation as rec, reconciliation_store as rstore

        monkeypatch.setenv("CRISISMESH_DURABLE_STORE", "firestore")
        monkeypatch.setenv("CRISISMESH_RECONCILIATION_STORE", "firestore")
        fake = _FakeFirestore()
        with patch.object(durable_store, "_client", return_value=fake), \
             patch.object(rstore, "_firestore_client", return_value=fake):
            durable_store.reset_backend()
            rstore.reset_backend()
            rstore.begin_tick_guard("T-1", 7)
            rec.reset()
            assert rstore.begin_tick_guard("T-1", 8) is True

    def test_an_unreadable_lease_runs_rather_than_stalls(self, monkeypatch):
        """A duplicate ping beats a missed one, and last_acted_tick still bounds
        the blast radius."""
        monkeypatch.setenv("CRISISMESH_DURABLE_STORE", "firestore")
        broken = _FakeFirestore(fail_reads=True)
        with patch.object(durable_store, "_client", return_value=broken):
            durable_store.reset_backend()
            assert durable_store.claim("c", "d") is True

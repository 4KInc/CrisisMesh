"""Versioning, failure semantics and backend choice for per-person state.

`reconciliation.py` is a correctness model: states, legal edges, replay
ordering. This is the same model plus a failure model — the things a dict never
made anyone confront because a dict access is instantaneous and cannot throw.

Three of them:

  Interleaving. A network read and a network write are separable where a dict
  access was not, so a check-in can land between a tick's read and its write.
  `--max-instances=1` removes concurrent ticks; it does not remove interleaving
  inside one process. Writes therefore carry the version they were computed
  from, and a stale one is refused rather than applied.

  Read failure. A tick that cannot read a person's state skips them. Treating
  unreadable as SILENT and re-pinging manufactures a state nobody confirmed —
  the same withhold-on-uncertainty rule as the movement policy.

  Write cost. In memory, touching state every tick is free. On a network store
  it is N writes x ticks x incident duration, so only real changes write. A
  version that moved for an unchanged document is a phantom conflict generator.

The dict remaining a valid backing is the proof the separation held.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any, Callable

from src.core import reconciliation as rec

logger = logging.getLogger(__name__)

MEMORY = "memory"
FIRESTORE = "firestore"
_KNOWN_BACKENDS = (MEMORY, FIRESTORE)

_backend: str | None = None
_writes = 0
_lock = threading.RLock()

# Set while a Firestore transaction owns a document. `rec.transition` runs
# inside the transaction body and calls back into `note_write`, which would
# otherwise fire a second, non-transactional write to the document the
# transaction has open — contention against itself. Firestore retries, each
# retry writes again, and the transaction never commits: no exception, no
# document, no log. Silence by construction, which is the one failure mode a
# handler built for errors cannot catch.
_in_transaction = threading.local()


def _transaction_owns_the_write() -> bool:
    return getattr(_in_transaction, "active", False)


class StoreUnavailable(Exception):
    """The store could not be read or written. Not the same as a refusal."""


class StaleWrite(Exception):
    """The document moved between the caller's read and this write."""


def backend_name() -> str:
    """Which backing is in use. An unknown value degrades to memory.

    Degrading is the safe direction — a loop with no durable memory re-pings
    everyone after a restart, which is annoying rather than dangerous. But it
    announces itself: a production deploy that meant to say `firestore` and
    typed something else must not run durably-forgetful and silent.
    """
    global _backend
    with _lock:
        if _backend is not None:
            return _backend
        raw = (os.environ.get("CRISISMESH_RECONCILIATION_STORE") or MEMORY).strip().lower()
        if raw in _KNOWN_BACKENDS:
            _backend = raw
            logger.info(f"reconciliation store: {_backend}")
        else:
            _backend = MEMORY
            logger.error(
                f"reconciliation store: memory (unknown value {raw!r} -> memory). "
                "State will not survive a restart."
            )
        return _backend


def reset_backend() -> None:
    global _backend
    with _lock:
        _backend = None


def reset_counters() -> None:
    global _writes
    with _lock:
        _writes = 0


def write_count() -> int:
    with _lock:
        return _writes


def _persist(state: rec.PersonState) -> None:
    """Push one document to the backing. Raises StoreUnavailable on failure.

    Memory is a no-op — the state object *is* the store. The Firestore
    implementation replaces this and nothing above it changes.
    """
    if backend_name() == FIRESTORE:
        _persist_firestore(state)


COLLECTION = "crisismesh_reconciliation"

# The document id. Flat rather than a subcollection so a single get is one
# round trip inside a synchronous /tick.
def _doc_id(incident_id: str, person_id: str) -> str:
    return f"{incident_id}__{person_id}"


_client: Any = None


def _firestore_client() -> Any:
    global _client
    if _client is None:
        from google.cloud import firestore

        _client = firestore.Client()
    return _client


def reset_client() -> None:
    global _client
    _client = None


CALL_DEADLINE_SECONDS = 5.0

# The whole tick, not just one call. Per-call deadlines convert one silent call
# into a handled error, but a healthy-but-slow tick makes ~100 serial network
# calls at roster scale, and the sum can exceed Cloud Run's request budget. A
# tick that blows this commits what it finished and reports the tail as not
# evaluated — the crash blast-radius guarantee arriving through the timeout
# door, made safe to re-run by the per-person `last_acted_tick` guard.
TICK_BUDGET_SECONDS = 45.0


def tick_budget_seconds() -> float:
    try:
        return max(5.0, float(os.environ.get("CRISISMESH_TICK_BUDGET", TICK_BUDGET_SECONDS)))
    except (TypeError, ValueError):
        return TICK_BUDGET_SECONDS


def hydrate_roster(incident_id: str, person_ids: list[str]) -> int:
    """Pull the whole roster's state in one round trip instead of N.

    34 individual gets is 34 serial round trips before the tick has decided
    anything. One query is one.
    """
    if backend_name() != FIRESTORE:
        return 0

    try:
        db = _firestore_client()
        docs = db.collection(COLLECTION).where(
            "incident_id", "==", incident_id
        ).stream(timeout=CALL_DEADLINE_SECONDS)
        loaded = 0
        wanted = set(person_ids)
        for snap in docs:
            document = snap.to_dict() or {}
            if document.get("person_id") in wanted:
                rec.prime(rec.PersonState.from_document(document))
                loaded += 1
        return loaded
    except Exception as exc:  # noqa: BLE001
        # A failed batch is not fatal: each person falls back to an individual
        # hydrate, which fails closed per person rather than ending the tick.
        logger.error(f"Roster hydrate failed for {incident_id} ({exc}) — falling back per person")
        return 0


def _persist_firestore(state: rec.PersonState) -> None:
    try:
        db = _firestore_client()
        db.collection(COLLECTION).document(
            _doc_id(state.incident_id, state.person_id)
        ).set(state.as_document(), timeout=CALL_DEADLINE_SECONDS)
    except Exception as exc:  # noqa: BLE001 - any client failure is the store's
        raise StoreUnavailable(f"Firestore write failed for {state.person_id}: {exc}") from exc


def _load_firestore(incident_id: str, person_id: str) -> rec.PersonState | None:
    """Fetch a person's standing, or None if this incident has never seen them.

    Raises StoreUnavailable on a client error rather than returning None: a
    read that failed is not evidence the person is new, and treating it as new
    would reset their state to SILENT and re-ping someone already escalated.
    """
    try:
        snap = _firestore_client().collection(COLLECTION).document(
            _doc_id(incident_id, person_id)
        ).get(timeout=CALL_DEADLINE_SECONDS)
    except Exception as exc:  # noqa: BLE001 - google.api_core raises its own types
        raise StoreUnavailable(f"Firestore read failed for {person_id}: {exc}") from exc

    if not snap.exists:
        return None
    return rec.PersonState.from_document(snap.to_dict() or {})


def load(incident_id: str, person_id: str) -> rec.PersonState | None:
    """Hydrate one person from the durable backing, if there is one."""
    if backend_name() != FIRESTORE:
        return None
    return _load_firestore(incident_id, person_id)


def commit_in_transaction(
    incident_id: str,
    person_id: str,
    target: str,
    tick: int,
    expected_version: int,
) -> rec.PersonState:
    """Compare-and-set inside a Firestore transaction.

    The version check lives *inside* the transaction on purpose. Reading the
    version outside it and only writing inside would rebuild the inert guard
    that the detached-snapshot fix removed — a comparison against state that
    can move between the read and the write is not a guard.

    With `--max-instances=1` the in-process lock is what actually serialises
    writers today; this is what keeps the guard true above one instance.
    """
    from google.cloud import firestore

    try:
        # Inside the try: constructing the client can itself fail (credentials,
        # metadata server), and that failure has to arrive as StoreUnavailable
        # like every other one, not as a raw google.api_core exception the
        # callers upstream do not catch.
        db = _firestore_client()
    except Exception as exc:  # noqa: BLE001
        raise StoreUnavailable(f"Firestore client unavailable: {exc}") from exc

    ref = db.collection(COLLECTION).document(_doc_id(incident_id, person_id))

    @firestore.transactional
    def _apply(txn: Any) -> dict[str, Any]:
        snap = ref.get(transaction=txn, timeout=CALL_DEADLINE_SECONDS)
        current = snap.to_dict() if snap.exists else None
        live_version = (current or {}).get("version", 0)
        if live_version != expected_version:
            raise StaleWrite(
                f"{person_id}: computed from version {expected_version}, "
                f"document is now {live_version} — re-read and re-decide"
            )
        state = rec.transition(incident_id, person_id, target, tick)
        document = state.as_document()
        txn.set(ref, document)
        return document

    try:
        _in_transaction.active = True
        document = _apply(db.transaction())
    except (StaleWrite, rec.IllegalTransition):
        raise
    except Exception as exc:  # noqa: BLE001
        raise StoreUnavailable(f"Firestore transaction failed for {person_id}: {exc}") from exc
    finally:
        _in_transaction.active = False
    return rec.PersonState.from_document(document)


def note_write(state: rec.PersonState) -> None:
    """Record that a real change happened: bump the version, count it, persist.

    Called only when something actually changed. An inert transition must not
    reach here, or the version moves for an unchanged document and the next
    legitimate write from a concurrent path gets a conflict that nothing caused.
    """
    global _writes
    with _lock:
        state.version += 1
        _writes += 1
    if _transaction_owns_the_write():
        return
    _persist(state)


def read(incident_id: str, person_id: str) -> rec.PersonState:
    """A point-in-time snapshot, carrying the version it was read at.

    Detached on purpose. Returning the live object would make `version` read
    the current value at comparison time rather than the value at read time,
    so the compare-and-set could never fail — a guard that is inert against a
    dict and only starts working against Firestore, which is the wrong way
    round for finding bugs.
    """
    live = rec.get_state(incident_id, person_id)
    return rec.PersonState.from_document(live.as_document())


def commit_transition(
    incident_id: str,
    person_id: str,
    target: str,
    tick: int,
    expected_version: int | None = None,
) -> rec.PersonState:
    """Apply a transition, refusing it if the document moved since the read."""
    with _lock:
        if expected_version is not None and backend_name() == FIRESTORE:
            return commit_in_transaction(
                incident_id, person_id, target, tick, expected_version)

        if expected_version is not None:
            current = rec.get_state(incident_id, person_id)
            if current.version != expected_version:
                raise StaleWrite(
                    f"{person_id}: computed from version {expected_version}, "
                    f"document is now {current.version} — re-read and re-decide"
                )
        return rec.transition(incident_id, person_id, target, tick)


def begin_tick_guard(incident_id: str, tick: int) -> bool:
    """Whether this tick should run, surviving a store that cannot answer.

    An unreadable guard means run: a duplicate ping beats a missed one, and the
    per-person `last_acted_tick` still contains the blast radius.
    """
    try:
        return rec.begin_tick(incident_id, tick)
    except Exception as exc:  # noqa: BLE001
        logger.error(f"Tick guard unreadable for {incident_id}/{tick} ({exc}) — running")
        return True


def safe_should_act(incident_id: str, person_id: str, tick: int) -> bool:
    """Should the tick act on this person? False when their state is unreadable.

    Withhold on uncertainty. A read that timed out is not evidence of silence.
    """
    try:
        state = read(incident_id, person_id)
    except StoreUnavailable as exc:
        logger.error(
            f"Tick {tick}: could not read {person_id} on {incident_id} "
            f"({exc}) — skipping this tick, will retry next"
        )
        return False
    # The whole terminal set, not just ACCOUNTED. The guard was added to
    # rec.should_act and this is the function the running loop calls, so an
    # escalated person stayed actionable and their warden was paged about them
    # again on every tick — three names, every 25 seconds, forever.
    if state.status in rec.TERMINAL_FOR_THE_LOOP:
        return False
    return not rec.already_acted(incident_id, person_id, tick)


def tick_person(
    incident_id: str, person_id: str, target: str, tick: int,
) -> str | None:
    """One person's turn in a tick. Returns their resulting status, or None.

    On StaleWrite the decision is re-made, never re-forced: something moved
    underneath us, and the most likely something is the check-in this guard
    exists to protect. Retrying the write would clobber it — reintroducing the
    bug the version check prevents, inside the handler for that check.
    """
    if not safe_should_act(incident_id, person_id, tick):
        return None

    try:
        snapshot = read(incident_id, person_id)
    except StoreUnavailable:
        return None

    try:
        state = commit_transition(incident_id, person_id, target, tick,
                                  expected_version=snapshot.version)
        return state.status
    except StaleWrite:
        logger.info(f"Tick {tick}: {person_id} moved mid-decision — re-reading")
    except rec.IllegalTransition as exc:
        logger.info(f"Tick {tick}: {exc}")
        return None
    except StoreUnavailable as exc:
        logger.error(f"Tick {tick}: write failed for {person_id}: {exc}")
        return None

    # Re-decide against fresh state. If they checked in, this correctly does
    # nothing rather than forcing a stale escalation through.
    if not safe_should_act(incident_id, person_id, tick):
        return None
    try:
        fresh = read(incident_id, person_id)
        state = commit_transition(incident_id, person_id, target, tick,
                                  expected_version=fresh.version)
        return state.status
    except (StaleWrite, rec.IllegalTransition, StoreUnavailable) as exc:
        logger.info(f"Tick {tick}: {person_id} not acted on after re-read ({exc})")
        return None


def tick_roster(
    incident_id: str,
    person_ids: list[str],
    target: str,
    tick: int,
    on_person: Callable[[str, str | None], None] | None = None,
) -> dict[str, Any]:
    """Run one tick across a roster. One person's failure never ends the tick.

    A throttled read on p017 must not silently drop p018-p034 — that is the
    missed-ping failure arriving through the error path.
    """
    acted: dict[str, str] = {}
    skipped: list[str] = []
    for person_id in person_ids:
        try:
            result = tick_person(incident_id, person_id, target, tick)
        except Exception as exc:  # noqa: BLE001 - one person cannot end the tick
            logger.error(f"Tick {tick}: unhandled error for {person_id}: {exc}")
            result = None
        if result:
            acted[person_id] = result
        else:
            skipped.append(person_id)
        if on_person:
            on_person(person_id, result)
    return {"tick": tick, "acted": acted, "skipped": skipped,
            "evaluated": len(person_ids)}

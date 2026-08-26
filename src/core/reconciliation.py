"""Per-person accountability state across ticks.

The critic is idempotent by nature — same inputs, same verdict. This is the
opposite: correctness is defined by what it remembers between runs, and what it
remembers is a small state machine per person.

    SILENT ──────► REPINGED ──(cap)──► ESCALATED ──┐
       │              │                            │
       ├──────────► UNREACHABLE ──(channel appears)┤
       │              │                            │
       └──────────────┴────────────────────────────┴──► ACCOUNTED
                                                            │
                                       reopen(reason) ◄─────┘

Refused edges matter as much as legal ones. `ACCOUNTED → SILENT` is refused so a
stale tick cannot quietly un-account someone who checked in; `ESCALATED →
SILENT` is refused because the warden was told and you cannot untell them.
Returning to SILENT happens only through `reopen(reason)` — an event with a
recorded cause — and that resets the attempt cap, because otherwise a person
whose area is re-blocked after they checked in can never be re-pinged at the
exact moment they have become silent in a newly dangerous zone.

Replay ordering: act, then commit. A crashed uncommitted tick may re-run, since
a duplicate ping beats a missed one in a life-safety system. Per-person
`last_acted_tick` is the real guard and the tick commit is the coarse one, so
the blast radius of a crash is the tail of one tick — the people not yet
processed when it died — never the whole roster.

The store is a dict. The state machine and the persistence backend are separate
concerns: `as_document` / `from_document` are the seam a Firestore backing slots
into without touching any rule above.
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

SILENT = "silent"
REPINGED = "repinged"
ESCALATED = "escalated"
UNREACHABLE = "unreachable"
ACCOUNTED = "accounted"

# Defaults. Both are read through the functions below rather than referenced
# directly, so a deployment can retune "re-ping twice, not three times" or a
# slower cadence without touching a transition.
MAX_ATTEMPTS = 3
TICK_INTERVAL_SECONDS = 60

_LEGAL: dict[str, frozenset[str]] = {
    # Self-loops are permitted from every state: re-pinging again, staying
    # unreachable, remaining silent. Refusing a transition to the state a
    # person is already in would surprise a caller for no safety gain.
    SILENT: frozenset({SILENT, REPINGED, UNREACHABLE, ACCOUNTED, ESCALATED}),
    REPINGED: frozenset({REPINGED, ESCALATED, UNREACHABLE, ACCOUNTED}),
    ESCALATED: frozenset({ESCALATED, UNREACHABLE, ACCOUNTED}),
    UNREACHABLE: frozenset({REPINGED, ESCALATED, UNREACHABLE, ACCOUNTED}),
    ACCOUNTED: frozenset({ACCOUNTED}),
}


class IllegalTransition(Exception):
    """A refused edge. Each one is a bug that would otherwise happen quietly."""


def attempt_cap() -> int:
    """Re-pings before escalating to a floor warden. Tunable per deployment."""
    try:
        return max(1, int(os.environ.get("CRISISMESH_REPING_CAP", MAX_ATTEMPTS)))
    except (TypeError, ValueError):
        return MAX_ATTEMPTS


def tick_interval_seconds() -> int:
    """Seconds between reconciliation ticks. Tunable per deployment."""
    try:
        return max(5, int(os.environ.get("CRISISMESH_TICK_SECONDS", TICK_INTERVAL_SECONDS)))
    except (TypeError, ValueError):
        return TICK_INTERVAL_SECONDS


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class PersonState:
    """One person's standing in one incident. This is the persisted document."""

    incident_id: str
    person_id: str
    status: str = SILENT
    attempts: int = 0
    last_acted_tick: int | None = None
    reachability_reason: str = ""
    unreachable_reported_at_tick: int | None = None
    unreachable_reported_reason: str = ""
    pending_escalation: bool = False
    accounted_via: str = ""
    reopen_reason: str = ""
    # The transport refused to carry this, for a reason that is a decision
    # rather than a failure — they replied STOP, or the channel is switched
    # off. Durable, because `resolve_reach` would happily keep saying they are
    # reachable and the loop would keep arguing with the STOP every tick.
    delivery_suppressed: bool = False
    version: int = 0
    updated_at: str = field(default_factory=_now)

    def as_document(self) -> dict[str, Any]:
        return {
            "incident_id": self.incident_id,
            "person_id": self.person_id,
            "status": self.status,
            "attempts": self.attempts,
            "last_acted_tick": self.last_acted_tick,
            "reachability_reason": self.reachability_reason,
            "unreachable_reported_at_tick": self.unreachable_reported_at_tick,
            "unreachable_reported_reason": self.unreachable_reported_reason,
            "pending_escalation": self.pending_escalation,
            "accounted_via": self.accounted_via,
            "reopen_reason": self.reopen_reason,
            "delivery_suppressed": self.delivery_suppressed,
            "version": self.version,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_document(cls, doc: dict[str, Any]) -> PersonState:
        return cls(**{k: doc[k] for k in doc if k in cls.__dataclass_fields__})


_people: dict[tuple[str, str], PersonState] = {}
_ticks: dict[tuple[str, int], bool] = {}
_lock = threading.RLock()


def reset() -> None:
    with _lock:
        _people.clear()
        _ticks.clear()


def prime(state: PersonState) -> None:
    """Seed a hydrated document into the cache without a per-person round trip."""
    with _lock:
        _people[(state.incident_id, state.person_id)] = state


def _hydrate(incident_id: str, person_id: str) -> PersonState | None:
    """Pull a person's standing from the durable backing on a cache miss.

    This is what survives a redeploy: the process starts with an empty dict, and
    the first look at anyone fetches what the last process recorded. A store
    that cannot answer raises rather than returning None — a failed read is not
    evidence the person is new, and treating it as new resets them to SILENT.
    """
    try:
        from src.core import reconciliation_store
        return reconciliation_store.load(incident_id, person_id)
    except ImportError:
        return None


def _note_write(state: PersonState) -> None:
    """Announce a real change to the store: version, counter, persistence.

    Imported late so the state machine never depends on the store — the dict
    remaining a valid backing is what proves that separation held.
    """
    try:
        from src.core import reconciliation_store
        reconciliation_store.note_write(state)
    except ImportError:
        state.version += 1


def get_state(incident_id: str, person_id: str) -> PersonState:
    """Current standing, defaulting to SILENT for anyone not yet seen."""
    with _lock:
        key = (incident_id, person_id)
        if key not in _people:
            _people[key] = _hydrate(incident_id, person_id) or PersonState(
                incident_id, person_id)
        return _people[key]


def transition(incident_id: str, person_id: str, target: str, tick: int) -> PersonState:
    """Move a person to `target`, or refuse. Records the acting tick."""
    with _lock:
        state = get_state(incident_id, person_id)
        if target not in _LEGAL.get(state.status, frozenset()):
            raise IllegalTransition(
                f"{person_id}: {state.status} -> {target} is not a legal edge "
                f"(incident {incident_id}, tick {tick})"
            )

        # A transition that changes nothing must change nothing. Without this a
        # defensive `transition(p, SILENT)` on an already-silent person stamps
        # last_acted_tick, so `should_act` returns False and the tick SKIPS
        # them — a silent miss, the failure we chose against. Under a Firestore
        # backing it is also a write nobody asked for, and an audit entry
        # saying something happened when nothing did.
        #
        # REPINGED is the exception: a second re-ping is a real act even though
        # the status is unchanged.
        if target == state.status and target != REPINGED:
            return state

        if target == REPINGED:
            state.attempts += 1
        if target == ESCALATED:
            state.pending_escalation = True
        if state.status == UNREACHABLE and target != UNREACHABLE:
            # Leaving the unreachable set clears the ledger marker, so becoming
            # unreachable again is reported to the IC again.
            state.unreachable_reported_at_tick = None
            state.unreachable_reported_reason = ""

        state.status = target
        state.last_acted_tick = tick
        state.updated_at = _now()
        _note_write(state)
        return state


def reopen(incident_id: str, person_id: str, reason: str, tick: int) -> PersonState:
    """Return an accounted person to SILENT — an event, never a plain edge.

    Resets the attempt cap: without that, someone whose area is re-blocked after
    checking in is permanently silenced by their first round's attempts.
    """
    if not reason.strip():
        raise ValueError("reopen requires a reason — an unexplained re-open is a drift")

    with _lock:
        state = get_state(incident_id, person_id)
        state.status = SILENT
        state.attempts = 0
        state.pending_escalation = False
        state.accounted_via = ""
        state.reopen_reason = reason.strip()
        state.last_acted_tick = tick
        state.updated_at = _now()
        _note_write(state)
        logger.info(f"Reopened {person_id} on {incident_id}: {reason}")
        return state


def record_checkin(incident_id: str, person_id: str, source: str = "") -> PersonState:
    """A check-in wins over anything pending. Never refused."""
    with _lock:
        state = get_state(incident_id, person_id)
        state.status = ACCOUNTED
        state.pending_escalation = False
        state.accounted_via = source or "self_report"
        state.updated_at = _now()
        _note_write(state)
        return state


def record_room_report(
    incident_id: str, room_id: str, reporter_person_id: str = "",
) -> PersonState | None:
    """A room report accounts for its reporter, and for nobody else.

    Someone typing "room 101: all 25 students are safe" is demonstrably alive
    and functional, so the loop must not re-ping and then escalate the person
    doing the reporting. But "23 of 25 safe" never says which 23, so no other
    occupant is marked — a falsely-accounted person is one nobody looks for.
    """
    if not reporter_person_id:
        logger.info(
            f"Room {room_id} reported on {incident_id} with no attributable "
            "reporter — accounting for nobody"
        )
        return None

    state = record_checkin(incident_id, reporter_person_id, source="room_report")
    logger.info(f"Room {room_id} report attributed to {reporter_person_id}")
    return state


def suppress_delivery(incident_id: str, person_id: str, reason: str) -> None:
    """Record that the transport refused this person for a standing reason."""
    with _lock:
        state = get_state(incident_id, person_id)
        state.delivery_suppressed = True
        state.reachability_reason = reason
        _note_write(state)


def is_delivery_suppressed(incident_id: str, person_id: str) -> bool:
    return get_state(incident_id, person_id).delivery_suppressed


def set_reachability_reason(incident_id: str, person_id: str, reason: str) -> None:
    with _lock:
        get_state(incident_id, person_id).reachability_reason = reason


def already_acted(incident_id: str, person_id: str, tick: int) -> bool:
    """Has this person already been acted on at or after this tick?

    The real replay guard. A re-run of tick N skips everyone tick N reached, so
    a crash re-pings only the tail it had not processed.
    """
    last = get_state(incident_id, person_id).last_acted_tick
    return last is not None and last >= tick


# States where the loop has done everything it can and further ticks would only
# repeat themselves. Harmless when a human asks for three ticks; on a schedule
# it is the same warden paged about the same person every minute forever.
TERMINAL_FOR_THE_LOOP = frozenset({ACCOUNTED, ESCALATED})


def should_act(incident_id: str, person_id: str, tick: int) -> bool:
    """Does this tick have anything to do for this person?

    An escalated person is finished as far as the loop is concerned: the cap
    was reached, a named human was told, and there is no further autonomous
    step. Only a check-in or an explicit reopen brings them back.
    """
    state = get_state(incident_id, person_id)
    if state.status in TERMINAL_FOR_THE_LOOP:
        return False
    return not already_acted(incident_id, person_id, tick)


def should_escalate(incident_id: str, person_id: str) -> bool:
    """Has this person absorbed the configured number of re-pings?

    The cap is read here, not encoded in an edge, so retuning it is config.
    """
    return get_state(incident_id, person_id).attempts >= attempt_cap()


def begin_tick(incident_id: str, tick: int) -> bool:
    """True when this tick should run. False when it already committed."""
    with _lock:
        return not _ticks.get((incident_id, tick), False)


def commit_tick(incident_id: str, tick: int) -> None:
    """Mark a tick complete — after acting, never before."""
    with _lock:
        _ticks[(incident_id, tick)] = True


def unreported_unreachable(incident_id: str) -> list[str]:
    """Unreachable people the IC has not been told about, or whose reason changed.

    Everything else is silence: re-listing the same names every tick is the
    noise that teaches an incident commander to ignore the list.
    """
    with _lock:
        out = []
        for (inc, pid), state in _people.items():
            if inc != incident_id or state.status != UNREACHABLE:
                continue
            if state.unreachable_reported_at_tick is None:
                out.append(pid)
            elif state.unreachable_reported_reason != state.reachability_reason:
                out.append(pid)
        return sorted(out)


def mark_unreachable_reported(incident_id: str, person_ids: list[str], tick: int) -> None:
    with _lock:
        for pid in person_ids:
            state = get_state(incident_id, pid)
            state.unreachable_reported_at_tick = tick
            state.unreachable_reported_reason = state.reachability_reason
            state.updated_at = _now()
            _note_write(state)


def snapshot(incident_id: str) -> list[dict[str, Any]]:
    """Every person document for an incident — the audit view, and what a
    Firestore backing would write."""
    with _lock:
        return [s.as_document() for (inc, _), s in _people.items() if inc == incident_id]

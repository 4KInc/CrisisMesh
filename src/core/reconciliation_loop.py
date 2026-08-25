"""The integrator — what runs a tick on a schedule.

The units are proven: transitions, compare-and-set, per-person failure
containment. This is the thing that calls them in order, on a timer, and it has
its own bug class: visiting everyone, advancing the counter exactly once,
surviving a per-person throw without dying or double-counting.

Two boundaries.

**It records intents; it does not send.** A tick produces "would re-ping p004
via slack" — decisions, with the channel each would have used. Delivery is the
one place a bug has a consequence outside the system, so it is wired last,
after the loop has been watched running over several ticks. Everything above
delivery is provable on a laptop; delivery is the part that pages a human.

**Skip the beat, never queue.** If a tick is still running when the next fires,
the next is dropped. Queueing would let a slow store build a backlog of ticks
that then all run against stale decisions — and the dict is a faster timing
environment than a network store, so this is pinned rather than left to be
true by accident.

A tick that throws must still schedule its successor. A silently dead loop is
the missed-ping failure at maximum scale: nobody is chased, and nothing says so.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from src.core import incident_state
from src.core import reconciliation as rec
from src.core import reconciliation_store as store

logger = logging.getLogger(__name__)

ACTION_REPING = "reping"
ACTION_ESCALATE = "escalate_to_warden"
ACTION_FLAG_IC = "flag_to_ic_manual_reach"


@dataclass
class Intent:
    """A decision the loop made. Not a message that was sent."""

    incident_id: str
    tick: int
    person_id: str
    name: str
    action: str
    channel: str = ""
    address: str = ""
    reason: str = ""
    at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


_intents: dict[str, list[dict[str, Any]]] = {}
_tick_counter: dict[str, int] = {}
_running_incidents: set[str] = set()
_lock = threading.RLock()

_timer: threading.Thread | None = None
_stop = threading.Event()


def reset() -> None:
    with _lock:
        _intents.clear()
        _tick_counter.clear()
        _running_incidents.clear()


def intents(incident_id: str) -> list[dict[str, Any]]:
    with _lock:
        return list(_intents.get(incident_id, []))


def _record(intent: Intent) -> dict[str, Any]:
    with _lock:
        _intents.setdefault(intent.incident_id, []).append(intent.as_dict())
    logger.info(
        f"tick {intent.tick} — would {intent.action} {intent.name} "
        f"({intent.person_id}) via {intent.channel or 'no channel'}"
        + (f": {intent.reason}" if intent.reason else "")
    )
    return intent.as_dict()


def _next_tick(incident_id: str) -> int:
    with _lock:
        _tick_counter[incident_id] = _tick_counter.get(incident_id, 0) + 1
        return _tick_counter[incident_id]


def run_tick(incident_id: str) -> dict[str, Any]:
    """One reconciliation pass. Decides, records, never delivers."""
    with _lock:
        if incident_id in _running_incidents:
            # Skip the beat. A backlog of queued ticks would run stale
            # decisions against state that has moved on.
            logger.info(f"Tick for {incident_id} skipped — previous tick still running")
            return {"incident_id": incident_id, "tick": _tick_counter.get(incident_id, 0),
                    "intents": [], "evaluated": 0, "skipped_reason": "already_running"}
        _running_incidents.add(incident_id)

    try:
        if not incident_state.is_active():
            return {"incident_id": incident_id, "tick": _tick_counter.get(incident_id, 0),
                    "intents": [], "evaluated": 0, "skipped_reason": "no_active_incident"}

        tick = _next_tick(incident_id)
        if not store.begin_tick_guard(incident_id, tick):
            return {"incident_id": incident_id, "tick": tick, "intents": [],
                    "evaluated": 0, "skipped_reason": "already_committed"}

        recorded = _reconcile(incident_id, tick)
        rec.commit_tick(incident_id, tick)
        return recorded
    finally:
        with _lock:
            _running_incidents.discard(incident_id)


def _reconcile(incident_id: str, tick: int) -> dict[str, Any]:
    """Decide what to do about every person who has not been accounted for."""
    from src.core.knowledge_base import KnowledgeBase

    people = KnowledgeBase.get().personnel
    produced: list[dict[str, Any]] = []
    person_ids = [p.get("person_id", "") for p in people if p.get("person_id")]

    # One round trip for the roster instead of one per person.
    store.hydrate_roster(incident_id, person_ids)

    deadline = time.monotonic() + store.tick_budget_seconds()
    not_evaluated: list[str] = []

    for index, person in enumerate(people):
        if time.monotonic() > deadline:
            # Out of budget. Commit what completed and name the tail, rather
            # than letting the request exceed Cloud Run's limit and lose
            # everything. `last_acted_tick` makes the re-run skip whoever was
            # already reached.
            not_evaluated = [p.get("person_id", "") for p in people[index:]]
            logger.error(
                f"Tick {tick} exceeded its {store.tick_budget_seconds()}s budget "
                f"after {index}/{len(people)} people — {len(not_evaluated)} not "
                "evaluated this tick, will be picked up next"
            )
            break
        person_id = person.get("person_id", "")
        if not person_id:
            continue
        try:
            produced.extend(_reconcile_person(incident_id, tick, person))
        except Exception as exc:  # noqa: BLE001 - one person cannot end the tick
            logger.error(f"Tick {tick}: unhandled error for {person_id}: {exc}")

    return _finish(incident_id, tick, produced, len(people), not_evaluated)


def _reconcile_person(
    incident_id: str, tick: int, person: dict[str, Any],
) -> list[dict[str, Any]]:
    """Decide one person's turn. Raising here costs only this person."""
    from src.core import notify

    produced: list[dict[str, Any]] = []
    person_id = person["person_id"]
    if not store.safe_should_act(incident_id, person_id, tick):
        return produced

    reach = notify.resolve_reach(person)
    name = person.get("name", person_id)

    if not reach.reachable:
        # Move them into the unreachable set, but do not emit here: the
        # ledger below is the only thing that knows whether the IC has
        # already been told, and an inert self-loop still returns a status.
        rec.set_reachability_reason(incident_id, person_id, reach.reason)
        store.tick_person(incident_id, person_id, rec.UNREACHABLE, tick)
        return produced

    if rec.should_escalate(incident_id, person_id):
        if store.tick_person(incident_id, person_id, rec.ESCALATED, tick):
            warden = _warden_for(person)
            produced.append(_record(Intent(
                incident_id, tick, person_id, name, ACTION_ESCALATE,
                channel=reach.channel, address=reach.address,
                reason=f"{rec.attempt_cap()} re-pings unanswered; warden {warden}")))
        return produced

    if store.tick_person(incident_id, person_id, rec.REPINGED, tick):
        produced.append(_record(Intent(
            incident_id, tick, person_id, name, ACTION_REPING,
            channel=reach.channel, address=reach.address)))

    return produced


def _finish(
    incident_id: str,
    tick: int,
    produced: list[dict[str, Any]],
    evaluated: int,
    not_evaluated: list[str] | None = None,
) -> dict[str, Any]:
    """Tell the incident commander about the unreachable set — once.

    Re-listing the same names every tick is the noise that teaches them to
    ignore it, so the ledger, not the transition, decides who is named.
    """
    unreported = rec.unreported_unreachable(incident_id)
    if unreported:
        rec.mark_unreachable_reported(incident_id, unreported, tick)
        for person_id in unreported:
            produced.append(_record(Intent(
                incident_id, tick, person_id, _name_for(person_id), ACTION_FLAG_IC,
                reason=rec.get_state(incident_id, person_id).reachability_reason)))

    return {"incident_id": incident_id, "tick": tick, "intents": produced,
            "evaluated": evaluated, "not_evaluated": not_evaluated or [],
            "skipped_reason": ""}


def _name_for(person_id: str) -> str:
    from src.core.knowledge_base import KnowledgeBase

    person = KnowledgeBase.get().get_person(person_id)
    return person["name"] if person else person_id


def _warden_for(person: dict[str, Any]) -> str:
    """The floor warden a re-ping escalates to, or the incident commander."""
    from src.core.knowledge_base import KnowledgeBase

    kb = KnowledgeBase.get()
    floor = str(person.get("floor", ""))
    for warden in kb.get_floor_wardens():
        if str(warden.get("floor", "")) == floor and warden["person_id"] != person.get("person_id"):
            return warden["name"]
    return "incident commander"


# ── Timer ───────────────────────────────────────────────────────────────────

def is_running() -> bool:
    return bool(_timer and _timer.is_alive())


def start(incident_id: str, interval_seconds: float | None = None) -> None:
    """Begin ticking. A tick that throws must still schedule its successor."""
    global _timer

    if is_running():
        logger.info("Reconciliation timer already running")
        return

    interval = interval_seconds or rec.tick_interval_seconds()
    _stop.clear()

    def _pump() -> None:
        logger.info(f"Reconciliation timer started for {incident_id} ({interval}s)")
        while not _stop.wait(interval):
            try:
                run_tick(incident_id)
            except Exception as exc:  # noqa: BLE001 - a dead loop chases nobody
                logger.error(f"Tick failed for {incident_id}, continuing: {exc}")
        logger.info(f"Reconciliation timer stopped for {incident_id}")

    _timer = threading.Thread(target=_pump, daemon=True, name="reconciliation")
    _timer.start()


def stop() -> None:
    """Idempotent."""
    global _timer
    _stop.set()
    if _timer and _timer.is_alive():
        _timer.join(timeout=2)
    _timer = None

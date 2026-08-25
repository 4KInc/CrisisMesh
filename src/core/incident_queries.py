"""Answering questions about the running incident, on any channel.

Slack could already take "who is still unaccounted?" or "room 101: all 25
students are safe" through its @mention handler. The phone channels could not —
anything that was not a check-in keyword became a witness observation, so a
teacher on WhatsApp could report information but never ask for any.

The routing and the answers live here, in plain text, so every channel gives
the same answer to the same question. Slack keeps its Block Kit rendering for
the incident card; these replies are deliberately terse because they are read
on a phone, sometimes by someone who should not be looking at a lit screen.

One capability is deliberately NOT available on phone channels: the law
enforcement arrival brief. It names the locations of occupants with mobility
limitations and is marked as requiring incident-commander approval before
sharing. Any handset that texts the number can reach these replies, and the
number is published to staff, so that brief stays in Slack where the audience
is a known workspace.
"""

from __future__ import annotations

import logging

from src.core import incident_state, room_board

logger = logging.getLogger(__name__)

KIND_ROOM_CHECKIN = "room_checkin"
KIND_BOARD = "board"
KIND_UNACCOUNTED = "unaccounted"
KIND_ONCALL = "oncall"
KIND_ROUTES = "routes"
KIND_ARRIVAL_BRIEF = "arrival_brief"
KIND_NONE = ""

_BOARD_WORDS = ("board", "classroom board", "status board", "which rooms", "room report")
_UNACCOUNTED_WORDS = ("unaccounted", "who is still", "who's still", "still missing",
                      "not accounted", "accountability")
_ONCALL_WORDS = ("on call", "on-call", "who is responding", "who's responding",
                 "floor warden", "wardens", "who is on duty", "first aid", "trained")
_ROUTE_WORDS = ("route", "way out", "exit", "fastest way", "how do we get out", "evacuate from")
_BRIEF_WORDS = ("arrival brief", "law enforcement", "handoff", "le brief")


def classify(text: str) -> str:
    """What is this message asking for? KIND_NONE when it is not a query."""
    if room_board.parse(text):
        return KIND_ROOM_CHECKIN

    lowered = text.lower()
    if any(w in lowered for w in _BRIEF_WORDS):
        return KIND_ARRIVAL_BRIEF
    if any(w in lowered for w in _UNACCOUNTED_WORDS):
        return KIND_UNACCOUNTED
    if any(w in lowered for w in _BOARD_WORDS):
        return KIND_BOARD
    if any(w in lowered for w in _ONCALL_WORDS):
        return KIND_ONCALL
    if any(w in lowered for w in _ROUTE_WORDS):
        return KIND_ROUTES
    return KIND_NONE


def answer(text: str, source: str = "", allow_sensitive: bool = False) -> str | None:
    """Answer a query in plain text, or return None if it is not one."""
    kind = classify(text)
    if not kind:
        return None

    if kind == KIND_ARRIVAL_BRIEF and not allow_sensitive:
        return (
            "The law enforcement arrival brief names the locations of people with "
            "mobility limitations and needs incident-commander approval before it is "
            "shared. It is available in Slack, not over text."
        )

    incident_id = incident_state.get_active_incident_id()
    if not incident_id and kind != KIND_ONCALL:
        return "CrisisMesh: no active incident right now. If this is an emergency, call 911."

    if kind == KIND_ROOM_CHECKIN:
        return _record_room(text, incident_id, source)
    if kind == KIND_BOARD:
        return room_board.as_text(incident_id)
    if kind == KIND_UNACCOUNTED:
        return _unaccounted(incident_id)
    if kind == KIND_ONCALL:
        return _on_call()
    if kind == KIND_ROUTES:
        return _routes(text)
    if kind == KIND_ARRIVAL_BRIEF:
        return None  # Slack renders this itself
    return None


def _record_room(text: str, incident_id: str, source: str) -> str:
    entry = room_board.parse(text)
    room_board.record(incident_id, entry, source=source)
    _account_for_the_reporter(incident_id, source)
    s = room_board.summarise(incident_id)

    confirmation = f"Room {entry['room']} recorded: {entry['safe']} safe"
    if entry["missing"]:
        confirmation += f", {entry['missing']} MISSING"
    confirmation += "."
    return (
        f"{confirmation} Board now {s['reported_count']}/{s['total_rooms']} rooms, "
        f"{s['total_safe']} safe, {s['total_missing']} missing."
    )


def _account_for_the_reporter(incident_id: str, from_address: str) -> None:
    """Someone typing a room report is demonstrably alive and functional.

    Without this the loop re-pings and then escalates the teacher who is doing
    the reporting — the same disconnect as check-ins reaching accountability but
    not the reconciliation state machine, arriving through the room-report door
    instead of the SAFE-keyword one.

    The reporter only. "23 of 25 safe" never says which 23, and a falsely
    accounted person is one nobody goes looking for.
    """
    if not from_address:
        return
    try:
        from src.core import reconciliation
        from src.services.sms_consent import normalize_phone

        person_id = _person_for_address(normalize_phone(from_address))
        if person_id:
            reconciliation.record_room_report(
                incident_id, room_id="", reporter_person_id=person_id)
    except Exception as exc:  # noqa: BLE001 - never lose the room report itself
        logger.error(f"Room report recorded but reporter not accounted ({exc})")


def _person_for_address(phone: str) -> str:
    from src.services.sms_transport import _build_phone_map, _phone_to_person

    _build_phone_map()
    return _phone_to_person.get(phone, "")


def _unaccounted(incident_id: str) -> str:
    """Named people who have not checked in, plus rooms that have not reported.

    Two different unknowns, kept separate on purpose: a person with no check-in
    is not the same claim as a room nobody has heard from.
    """
    from src.agents.accountability.tools import escalate_missing_checkins

    try:
        result = escalate_missing_checkins(incident_id)
    except Exception as exc:
        logger.error(f"Unaccounted lookup failed: {exc}")
        result = {}

    people = result.get("missing_personnel", [])
    mobility = result.get("missing_with_mobility_needs", [])

    names = [p.get("name", p.get("person_id", "?")) for p in people][:15]
    lines = []
    if names:
        lines.append(f"Unaccounted personnel ({len(people)}): " + ", ".join(names)
                     + ("…" if len(people) > len(names) else ""))
    else:
        lines.append("All tracked personnel have checked in.")

    if mobility:
        # Surfaced first-class: these are the people who cannot self-evacuate,
        # and burying them in a list is how they get reached last.
        lines.append(
            f"PRIORITY — {len(mobility)} unaccounted person(s) have mobility "
            "limitations: " + ", ".join(m.get("name", "?") for m in mobility[:6]) + "."
        )

    s = room_board.summarise(incident_id)
    if s["silent_rooms"]:
        lines.append(
            f"{len(s['silent_rooms'])} rooms have not reported "
            f"(~{s['estimated_unaccounted_in_silent_rooms']} students, estimated)."
        )
    if s["total_missing"]:
        lines.append(f"{s['total_missing']} students reported missing by their teacher.")
    return " ".join(lines)


def _on_call() -> str:
    """Who is designated to respond, from the roster."""
    from src.core.knowledge_base import KnowledgeBase

    kb = KnowledgeBase.get()
    wardens = kb.get_floor_wardens()
    commanders = [p for p in kb.personnel if "commander" in (p.get("evacuation_role") or "").lower()]
    medics = [p for p in kb.personnel
              if str(p.get("trained_first_aid", "")).lower() == "true"
              or str(p.get("trained_cpr", "")).lower() == "true"]

    lines = []
    if commanders:
        lines.append("Incident Commander: " + ", ".join(p["name"] for p in commanders[:3]) + ".")
    if wardens:
        lines.append("Floor wardens: " + ", ".join(
            f"{w['name']} (floor {w.get('floor', '?')})" for w in wardens[:6]) + ".")
    if medics:
        lines.append(f"First aid/CPR trained on roster: {len(medics)}.")
    if not lines:
        return "No responder roles are recorded on the roster."
    return " ".join(lines)


def _routes(text: str) -> str:
    """Safe routes out of a named zone, excluding anything reported blocked."""
    from src.agents.safety_intel.tools import find_safe_routes
    from src.agents.intake.tools import extract_location

    location = extract_location(text)
    zone_id = location.get("zone_id", "")
    if not zone_id:
        return (
            "Name a zone I know so I can look up its routes — for example "
            "\"fastest route out of the east wing\", the gym, the library or the cafeteria."
        )

    record = incident_state.get_latest_incident()
    blocked = ((record.get("blocked_zones", {}) or {}).get("blocked_routes", []) or [])
    result = find_safe_routes("jefferson", zone_id, blocked_zones=zone_id)
    routes = result.get("routes", []) or result.get("safe_routes", [])

    if not routes:
        return (
            f"No clear route is recorded out of {location.get('zone_name', zone_id)}. "
            "Do not guess a path — hold position and call 911."
        )

    described = []
    for r in routes[:3]:
        exit_name = r.get("to_exit") or r.get("name") or "exit"
        via = r.get("description", "")
        step_free = " [step-free]" if "accessible" in str(r.get("accessibility", "")).lower() else ""
        described.append(f"{exit_name}{step_free}" + (f" — {via}" if via else ""))

    line = (
        f"Routes out of {location.get('zone_name', zone_id)}: "
        + " | ".join(described) + "."
    )
    if blocked:
        line += f" {len(blocked)} route(s) reported blocked — avoid them."
    return line

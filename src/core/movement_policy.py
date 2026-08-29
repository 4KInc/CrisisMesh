"""Should people move, and may we tell them where to gather?

One question, one answer, consumed read-only by every surface.

Six surfaces rendered the assembly point and only two consulted the incident
type, so a Slack card once printed "Assembly: Athletic Field" for the same
active-threat incident whose WhatsApp alert said lock and barricade. Shelter
versus evacuate is the axis that gets people killed, and it was being decided
independently in each renderer.

The classification (which incident types are lockdowns) already had one home in
tactical_reasoning. What was duplicated was the *policy* — what a renderer
should do with that knowledge. This module owns the policy, so a surface
consumes an answer instead of re-deriving one.

Fail closed, everywhere. An unknown incident type does not get an assembly
point, and a lookup that raises does not fall back to permissive. A critic that
cannot determine the policy must restrict, because the failure mode of guessing
"evacuate" is people walking into a hallway the threat is standing in.
"""

from __future__ import annotations

import logging
import re
from typing import Any
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# Movement directives.
SHELTER = "shelter"      # lock and barricade; move only on a confirmed-clear route
EVACUATE = "evacuate"    # leave the building, assemble at the rally point
STAY = "stay"            # no building-wide movement; incident is localised

# Imported lazily in `_lockdown_types` so a failure there restricts rather than
# raising into a renderer mid-incident.
_LOCKDOWN_FALLBACK = frozenset({"active_threat", "bomb_threat"})
_EVACUATE_TYPES = frozenset({"fire", "hazmat", "flood", "severe_weather", "utility_outage"})
_STAY_TYPES = frozenset({"medical", "cyber_ransomware", "data_breach"})

WITHHELD_LINE = (
    "withheld during lockdown — do not direct movement to a published open "
    "area until law enforcement clears it"
)

# Language that moves people out of the building. During a shelter directive
# this contradicts the guidance even when no rally point is named.
#
# Measured against model-generated phrasings rather than hand-written ones: a
# matcher tuned to "evacuate" alone caught 4 of 12 realistic paraphrases and
# missed "exit the building via the north stairwell" and "leave the building
# immediately" — the two most dangerous during a lockdown.
_MOVEMENT_OUT = re.compile(
    r"\b("
    r"evacuat\w*"
    r"|(?:proceed|report|head|go|move|relocate|regroup|gather|assemble)\s+"
    r"(?:calmly\s+|immediately\s+|quickly\s+)?"
    r"(?:to|toward|towards|out|outside|through|via|at|on)\b"
    r"|(?:leave|exit|vacate|clear)\s+(?:the\s+)?(?:building|premises|school|classroom)"
    r"|(?:nearest|closest)\s+exit"
    r"|make (?:your|their|his|her|its|a) way (?:out|outside)"
    r"|muster at|rally point|assembly point"
    r"|out (?:the|through the) (?:west|east|north|south)?\s*doors?"
    r")",
    re.IGNORECASE,
)

# A negated instruction is the opposite of a violation — "Do NOT direct a
# general evacuation" is the safety backstop, not a contradiction of it. Without
# this the critic flags the system's own correct guidance at error level, which
# is the noise that teaches an operator to ignore the alarm.
_NEGATION_WINDOW = 40
_NEGATORS = re.compile(
    r"\b(do not|don't|never|no|without|rather than|instead of|avoid|refrain from|"
    r"must not|should not|cannot|can't)\b",
    re.IGNORECASE,
)


def _is_negated(text: str, match: re.Match[str]) -> bool:
    """Is this movement instruction being forbidden rather than given?"""
    window = text[max(0, match.start() - _NEGATION_WINDOW):match.start()]
    return bool(_NEGATORS.search(window))


def _movement_violation(text: str) -> str:
    """The first un-negated movement instruction, or empty string."""
    for match in _MOVEMENT_OUT.finditer(text):
        if not _is_negated(text, match):
            return match.group(0)
    return ""


@dataclass(frozen=True)
class Directive:
    """The answer every surface consumes. Never re-derived downstream."""

    incident_type: str
    movement: str
    may_publish_assembly_point: bool
    reason: str = ""


@dataclass(frozen=True)
class Violation:
    """A rendering that contradicts the directive."""

    surface: str
    incident_type: str
    detail: str


def _lockdown_types() -> frozenset[str]:
    """Shared with the safety backstop, but never at the cost of restricting."""
    try:
        from src.core.tactical_reasoning import LOCKDOWN_TYPES
        return frozenset(LOCKDOWN_TYPES)
    except Exception as exc:  # noqa: BLE001 - restrict, do not raise into a renderer
        logger.error(f"Lockdown type lookup failed, restricting: {exc}")
        return _LOCKDOWN_FALLBACK


def for_incident(incident_type: Any) -> Directive:
    """The movement directive for an incident type. Never raises."""
    try:
        normalized = (incident_type or "").strip().lower()
    except Exception:  # noqa: BLE001
        normalized = ""

    if not normalized:
        return Directive("", SHELTER, False, "no incident type — restricting")

    try:
        if normalized in _lockdown_types():
            return Directive(normalized, SHELTER, False, "lockdown: threat is a person")
        if normalized in _EVACUATE_TYPES:
            return Directive(normalized, EVACUATE, True, "hazard: leaving is escaping it")
        if normalized in _STAY_TYPES:
            return Directive(normalized, STAY, True, "localised: no building-wide movement")
    except Exception as exc:  # noqa: BLE001
        logger.error(f"Movement policy lookup failed for {normalized!r}, restricting: {exc}")
        return Directive(normalized, SHELTER, False, "policy lookup failed — restricting")

    # Unrecognised. Not knowing the hazard is not a reason to move people
    # through it, and not a reason to publish a gathering place.
    return Directive(normalized, STAY, False, "unrecognised incident type — restricting")


def assembly_line(incident_type: Any, assembly_name: str, label: str = "Assembly") -> str:
    """The assembly field, rendered per policy.

    Withheld rather than blank: an absent field reads as missing data, which
    invites someone to go and look the rally point up.
    """
    directive = for_incident(incident_type)
    if directive.may_publish_assembly_point:
        return f"*{label}:* {assembly_name}"
    return f"*{label}:* {WITHHELD_LINE}"


def check_rendering(
    incident_type: Any,
    text: str,
    assembly_name: str = "",
    surface: str = "",
) -> Violation | None:
    """Does this rendering contradict the directive? This is what the critic runs."""
    directive = for_incident(incident_type)
    if directive.may_publish_assembly_point and directive.movement != SHELTER:
        return None

    if assembly_name and assembly_name.lower() in text.lower():
        return Violation(
            surface, directive.incident_type,
            f"names the assembly point {assembly_name!r} while the directive is "
            f"{directive.movement} ({directive.reason})",
        )

    if directive.movement == SHELTER:
        found = _movement_violation(text)
        if found:
            return Violation(
                surface, directive.incident_type,
                f"directs movement out of the building ({found!r}) while the "
                "directive is shelter",
            )
    return None


def enforce(
    incident_type: Any,
    text: str,
    assembly_name: str = "",
    surface: str = "",
) -> tuple[str, Violation | None]:
    """Strip the contradiction, don't just report it.

    A verdict nobody acts on is the same defect as an `escalate` that notifies
    nobody. The critic blocks the contradictory content; the violation is
    returned so it can be traced and surfaced, not so it can substitute for
    acting.
    """
    violation = check_rendering(incident_type, text, assembly_name, surface)
    if violation is None:
        return text, None

    cleaned = text
    if assembly_name:
        cleaned = re.sub(
            re.escape(assembly_name) + r"(\s*\([^)]*\))?",
            WITHHELD_LINE,
            cleaned,
            flags=re.IGNORECASE,
        )

    logger.error(
        f"MOVEMENT POLICY VIOLATION on {surface or 'unknown surface'}: {violation.detail} "
        "— contradiction stripped before send"
    )
    return cleaned, violation

# ── Egress against reported sightings ──────────────────────────────────────
#
# Route data is static building layout. It knows where the doors are and has no
# idea where the threat is. The arrival brief printed "Last known location: gym"
# and then, four lines below it, "Safe Routes: East Wing F1 Alternate -> Door 7
# (Gym Exit)". Both facts were correct; nothing was comparing them.

_STOPWORDS = frozenset({"the", "a", "an", "near", "in", "at", "by", "of", "to"})


def _significant_words(phrase: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", (phrase or "").lower())
            if w not in _STOPWORDS and len(w) > 2]


def flag_routes_against_threat(
    routes: list[str], threat_locations: list[str],
) -> list[dict[str, Any]]:
    """Mark any route that runs through somewhere the threat has been reported.

    Every reported position, not just the most recent: a threat seen in the east
    wing and then the gym has been in both, and the east wing is not clear
    because it has moved on. Matches whole words — "gym" must not fire on an
    unrelated word that happens to contain it.
    """
    flagged: list[dict[str, Any]] = []
    for route in routes or []:
        hit = ""
        haystack = (route or "").lower()
        for location in threat_locations or []:
            words = _significant_words(location)
            if words and all(re.search(rf"\b{re.escape(w)}\b", haystack) for w in words):
                hit = location
                break
        flagged.append({
            "route": route,
            "conflicts": bool(hit),
            "reason": (f"passes {hit}, where the threat has been reported"
                       if hit else ""),
        })
    return flagged

CLEAR_CAVEAT = (
    "Clear means no reported sighting lies on this path. It is not a clearance: "
    "law enforcement has not swept it, and it cannot account for a threat nobody "
    "has reported."
)


def _route_text(route: dict[str, Any]) -> str:
    return " ".join(str(route.get(k, "")) for k in
                    ("from_zone", "name", "to_exit", "route_description")).lower()


def _conflict(haystack: str, threat_locations: list[str]) -> str:
    for location in threat_locations or []:
        words = _significant_words(location)
        if words and all(re.search(rf"\b{re.escape(w)}\b", haystack) for w in words):
            return location
    return ""


def assess_egress(
    routes: list[dict[str, Any]],
    threat_locations: list[str],
    blocked_names: list[str] | tuple[str, ...] = (),
) -> dict[str, Any]:
    """Split every way out into paths no sighting touches, and paths that one does.

    The floor plan and the sighting trail live in the same process, and until
    this existed nothing joined them: the brief could say "Door 7 passes the
    gym" and still leave a reader to work out, across thirteen routes and two
    reported positions, which door does not. That is not a calculation to hand
    to somebody during a shooting.

    Every route is judged against every reported position, not just the latest —
    a threat seen in the east wing and then the gym has been in both. Nothing is
    promoted when the answer is unwelcome: if every path touches a sighting the
    clear list is empty and says so, because the least-bad route is not a safe
    one.
    """
    clear: list[dict[str, Any]] = []
    conflicting: list[dict[str, Any]] = []
    blocked = set(blocked_names or ())

    for route in routes or []:
        name = route.get("name", "")
        hit = _conflict(_route_text(route), threat_locations)
        # A sighting outranks the floor plan when both apply: both are reasons
        # not to use the route, and a person in the corridor is the more urgent
        # of the two to say out loud.
        # Kept short: the heading above the list already says these are
        # sightings and floor-plan blocks, and repeating the full sentence on
        # every row pushed the brief past Slack's message limit — which splits
        # it, through the middle of this section.
        if hit:
            kind, detail = "sighting", hit
        elif name in blocked:
            kind, detail = "floor plan", "blocked for this incident zone"
        else:
            kind, detail = "", ""
        reason = f"{kind}: {detail}" if kind else ""
        entry = {
            "name": name,
            "from_zone": route.get("from_zone", ""),
            "to_exit": route.get("to_exit", ""),
            "route_description": route.get("route_description", ""),
            "step_free": "wheelchair" in str(route.get("accessibility", "")).lower(),
            "conflict": reason,
            "conflict_kind": kind,
            "conflict_detail": detail,
        }
        (conflicting if reason else clear).append(entry)

    return {
        "clear": clear,
        "conflicting": conflicting,
        "checked_against": list(threat_locations or []),
        "caveat": CLEAR_CAVEAT,
    }


def group_egress_by_exit(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One line per exit, naming the areas it can be reached from.

    Six routes printed one per line with their walking directions pushed the
    brief past Slack's message limit and split it through the middle of this
    section. Three exits is three lines, and the unit a responder acts on is the
    door, not each corridor that leads to it.
    """
    grouped: dict[str, dict[str, Any]] = {}
    for e in entries:
        row = grouped.setdefault(e["to_exit"], {
            "to_exit": e["to_exit"], "zones": [], "step_free": False,
            "_reasons": {},
        })
        if e["from_zone"] not in row["zones"]:
            row["zones"].append(e["from_zone"])
        row["step_free"] = row["step_free"] or e["step_free"]
        kind = e.get("conflict_kind", "")
        if kind:
            # Merged by kind, so two sightings share one label. Different kinds
            # stay apart: folding them would have the floor plan reporting a
            # sighting.
            details = row["_reasons"].setdefault(kind, [])
            if e["conflict_detail"] not in details:
                details.append(e["conflict_detail"])

    rows = []
    for row in grouped.values():
        row["conflicts"] = [f"{kind}: {', '.join(details)}"
                            for kind, details in row.pop("_reasons").items()]
        rows.append(row)
    return rows

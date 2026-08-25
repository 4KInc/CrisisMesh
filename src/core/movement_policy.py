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

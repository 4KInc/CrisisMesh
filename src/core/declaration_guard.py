"""Whether a message is a report of an emergency at all.

Anything that was not a check-in keyword used to become an incident
declaration, so "What is promises in javascript" opened OTHER-2026-183453 with
a trace, a playbook and a console panel. The fan-out gate held the alert back
because the severity was moderate, but an incident commander still saw a live
incident that did not exist — and during a real event that is a distraction at
the exact moment attention is scarcest.

This is a different question from the one the content scanner answers. That
asks "is this input hostile" (prompt injection, PII extraction). This asks "is
this input about an emergency here" — an ordinary, harmless question fails this
check while passing that one.

The bar is deliberately low. Declining a real report is far worse than allowing
a junk one, so a message is refused only when it looks like a question about
something else AND contains nothing that reads as an emergency. Anything
ambiguous is allowed through and declared.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# Words that make a message plausibly about an emergency here. Any hit and the
# declaration proceeds, whatever else the message contains.
_EMERGENCY_SIGNALS = (
    "fire", "smoke", "flame", "burn", "alarm",
    "shooter", "gun", "weapon", "armed", "intruder", "threat", "lockdown", "shots",
    "bomb", "explosive", "suspicious package",
    "injur", "hurt", "bleed", "unconscious", "breathing", "collapse", "seizure",
    "medical", "ambulance", "911", "emergency", "evacuat", "trapped", "stuck",
    "flood", "water", "leak", "gas", "chemical", "spill", "fumes", "hazmat",
    "tornado", "storm", "lightning", "shelter",
    "help", "urgent", "danger", "missing", "unaccounted", "hiding", "barricad",
    "student", "teacher", "classroom", "hallway", "cafeteria", "gym", "library",
    "power", "outage", "ransomware", "breach",
)

# Asking about the situation itself. "what is happening", "where is he" and
# "are we safe" are people trying to find out what is going on during an
# incident — the opposite of off-topic, even though they are questions.
_SITUATIONAL_SIGNALS = (
    "happening", "going on", "where is", "where are", "are we", "is it over",
    "should we", "what now", "how long", "any news", "update",
)

# General-knowledge and small-talk shapes. Only consulted when no emergency
# signal is present.
_OFF_TOPIC_PATTERNS = (
    r"^\s*(what|who|when|where|why|how)\s+(is|are|was|were|do|does|did|can|should)\b",
    r"\b(javascript|python|java|typescript|react|sql|api|code|function|variable|"
    r"array|promise|async|css|html|docker|kubernetes)\b",
    r"\b(recipe|weather|joke|movie|song|lyrics|football|cricket|stock|bitcoin)\b",
    r"\b(translate|summari[sz]e|write me|explain|define|tell me about)\b",
    r"^\s*(hi|hey|hello|yo|test|testing|ping|thanks|thank you|ok|okay|lol)\s*[!.?]*\s*$",
)

MIN_REPORT_WORDS = 2


# Slack slash commands typed into WhatsApp or SMS. The channel has no such
# concept, so the prefix became part of the report and the arrival brief read
# "Location: 1200 Oak Street — /incident active shooter reported in the east
# wing". Strip it: the person meant the words after it.
_COMMAND_PREFIXES = ("/incident", "/checkin", "/crisismesh", "@crisismesh")


def strip_command_prefix(text: str) -> str:
    """Remove a Slack-style command prefix from a phone-channel message."""
    stripped = text.strip()
    lowered = stripped.lower()
    for prefix in _COMMAND_PREFIXES:
        if lowered.startswith(prefix):
            return stripped[len(prefix):].strip(" :,-") or stripped
    return stripped


def is_plausible_report(text: str) -> tuple[bool, str]:
    """Return (allowed, reason). Reason is empty when allowed."""
    stripped = text.strip()
    lowered = stripped.lower()

    if not stripped:
        return False, "empty message"

    # An emergency signal beats everything. "Is there a fire in the gym?" is a
    # question in form and a report in substance.
    if any(signal in lowered for signal in _EMERGENCY_SIGNALS):
        return True, ""

    # During an incident, a question about the situation is not off-topic.
    # Outside one it still is: "what is happening" with nothing running is not
    # a report of an emergency and must not declare one.
    from src.core import incident_state
    if incident_state.is_active() and any(s in lowered for s in _SITUATIONAL_SIGNALS):
        return True, ""

    if len(stripped.split()) < MIN_REPORT_WORDS:
        return False, "too short to be an incident report"

    for pattern in _OFF_TOPIC_PATTERNS:
        if re.search(pattern, lowered, re.IGNORECASE):
            return False, "reads as a general question, not a report of an emergency here"

    return True, ""


def refusal_message(reason: str) -> str:
    """What to tell someone whose message did not open an incident.

    Says plainly that nothing was declared, and how to declare one for real —
    a person who genuinely meant to report something must not walk away
    believing they have.
    """
    return (
        "CrisisMesh did not open an incident — that message "
        f"{reason}. To report a real emergency, describe what you see and where "
        "(for example \"smoke in the east wing, students still inside\"). "
        "If this is a life-threatening emergency, call 911 now."
    )


def log_refusal(channel: str, who: str, text: str, reason: str) -> None:
    logger.info(
        f"Declaration refused on {channel} from {who} ({reason}): {text[:120]}"
    )

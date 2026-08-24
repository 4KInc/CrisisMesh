"""What an inbound text message means, given what is already happening.

The same words mean different things at different moments. "Smoke in the gym"
with nothing running is a declaration. The same sentence while a lockdown is
active is a witness report about that lockdown — and treating it as a
declaration overwrote the incident everyone was coordinating around.

So the decision is made from state, not from the words alone:

  no active incident  → declare
  active incident     → observation (or a status request, or an explicit new
                        incident if the sender said NEW:)

Only NEW: replaces a running incident, and only because two real emergencies
can overlap — a fire alarm pulled during a lockdown is a genuine second event.
Making it explicit means it can never happen by accident.
"""

from __future__ import annotations

from src.core import incident_state

# Asking what is happening. Deliberately not a check-in: someone typing this is
# requesting information, not reporting their own status.
STATUS_KEYWORDS = frozenset({"status", "sitrep", "update", "what's happening", "whats happening"})

# The only way to replace a running incident from a text channel.
NEW_INCIDENT_PREFIX = "new:"

ACTION_DECLARE = "declare"
ACTION_OBSERVATION = "observation"
ACTION_STATUS = "status"
ACTION_NEW_INCIDENT = "new_incident"


def route(text: str) -> tuple[str, str]:
    """Decide what to do with a non-check-in message.

    Returns (action, payload) where payload is the text to act on — the NEW:
    prefix is stripped so a declaration does not carry it into the report.
    """
    stripped = text.strip()
    lowered = stripped.lower()

    if lowered.startswith(NEW_INCIDENT_PREFIX):
        return ACTION_NEW_INCIDENT, stripped[len(NEW_INCIDENT_PREFIX):].strip()

    if lowered.rstrip("?.! ") in STATUS_KEYWORDS:
        return ACTION_STATUS, stripped

    if incident_state.is_active():
        return ACTION_OBSERVATION, stripped

    return ACTION_DECLARE, stripped

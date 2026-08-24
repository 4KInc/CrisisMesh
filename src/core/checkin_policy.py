"""Whether a check-in has an incident to belong to.

Every channel used to write an orphan check-in into `_checkin_store["active"]`
— a bucket whose key no real incident id ever matches — and then tell the
sender "Check-in recorded". The row was never read by
`compute_accountability_summary`, never surfaced in the console, and never seen
by an incident commander. Someone hiding in a classroom could text SOS, be told
their call for help was logged, and have it go nowhere.

Refusing is the safe answer, but it is not the same answer for every status.
"SAFE" with nothing declared is an administrative no-op. "SOS" with nothing
declared is a person in trouble whose message just failed to reach anyone, and
the reply has to say so and point at 911 rather than merely decline.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Statuses that mean someone needs help right now, not that they are fine.
URGENT_STATUSES = frozenset({"need_help", "injured"})


def can_accept() -> bool:
    """True when there is a real incident for a check-in to attach to."""
    from src.core import incident_state
    return incident_state.is_active()


def refusal_message(status: str, name: str = "") -> str:
    """What to tell someone whose check-in cannot be recorded.

    Never implies the check-in was stored, because it was not.
    """
    who = f"{name}, " if name else ""
    if status in URGENT_STATUSES:
        return (
            f"{who}CrisisMesh has no active incident, so this was NOT logged and "
            "no responder has been alerted. If you need help right now, call 911. "
            "Then tell a staff member to declare an incident in CrisisMesh."
        ).strip()
    return (
        f"{who}there is no active CrisisMesh incident, so there is nothing to "
        "check in against and nothing was recorded. If this is an emergency, "
        "call 911."
    ).strip()


def log_refusal(channel: str, status: str, who: str) -> None:
    """Make an unmatched distress signal visible in the logs.

    An urgent status arriving with no incident is an operational event: someone
    tried to reach the system and could not. It is logged at error level so it
    surfaces without anyone going looking for it.
    """
    if status in URGENT_STATUSES:
        logger.error(
            f"UNMATCHED DISTRESS: {status!r} received on {channel} from {who} "
            "with no active incident — not recorded, sender told to call 911"
        )
    else:
        logger.info(
            f"Check-in {status!r} on {channel} from {who} refused: no active incident"
        )

"""Tell the Slack room about an incident declared somewhere else.

The sync used to run one way. Declaring in Slack reached every phone, because
the fan-out walks the roster and the roster has phone numbers. Declaring from a
phone reached every phone too — and the room where the response is actually
coordinated heard nothing at all, so the people running it learned about the
incident from a handset buzz rather than from the board in front of them.

During a lockdown the phone is the device in someone's hand, so the phone is
where the report comes from. That direction has to arrive.

Two things this deliberately does not do:

  * It does not guess a channel. Announcing a lockdown into whichever room the
    bot happens to be in is worse than announcing nowhere.
  * It does not print the reporter's number. A DM has one reader; a channel has
    the whole team, and an unrecognised handset gets described rather than
    quoted.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

CHANNEL_ENV = "SLACK_INCIDENT_CHANNEL"

REASON_ORIGIN_SLACK = "declared in Slack — the room already has the incident card"
REASON_NO_CHANNEL = f"no Slack incident channel configured ({CHANNEL_ENV})"
REASON_DELIVERY_OFF = "delivery is off — the decision is real, the post is not"
REASON_POST_FAILED = "Slack did not accept the message"

UNKNOWN_REPORTER = "an unlisted number"

_SOURCE_LABELS = {
    "whatsapp": "WhatsApp",
    "sms": "SMS",
    "web": "the web console",
    "api": "the API",
}


def incident_channel() -> str:
    """The room that hears about incidents declared on other channels."""
    return (os.environ.get(CHANNEL_ENV) or "").strip()


def _reporter_name(address: str) -> str:
    """The roster name behind a handset, or "" if we do not know it."""
    if not address:
        return ""
    try:
        from src.core.knowledge_base import KnowledgeBase
        from src.services import whatsapp_transport

        whatsapp_transport._build_phone_map()
        normalized = address.replace("-", "").replace(" ", "")
        if not normalized.startswith("+"):
            normalized = "+" + normalized
        person_id = whatsapp_transport._phone_to_person.get(normalized, "")
        if not person_id:
            return ""
        person = KnowledgeBase.get().get_person(person_id)
        return person.get("name", "") if person else ""
    except Exception as exc:  # noqa: BLE001 - a name is never worth an exception
        logger.warning(f"Could not name the reporter: {exc}")
        return ""


def _source_label(source: str) -> str:
    return _SOURCE_LABELS.get(source, source or "another channel")


def compose_declaration(record: dict[str, Any], reporter_address: str = "") -> str:
    """What the room reads.

    Deliberately quotes the report rather than summarising it — the sentence
    someone typed carries detail no classifier keeps, and the room can judge it
    faster than any label we could put in front of it.
    """
    classification = record.get("classification", {}) or {}
    incident_type = classification.get("incident_type", "incident")
    severity = classification.get("severity", "")
    incident_id = record.get("incident_id", "")
    location = (record.get("location", {}) or {}).get("zone_name", "")
    report = (record.get("report", "") or "").strip()
    source = _source_label(record.get("source", ""))

    who = _reporter_name(reporter_address) if reporter_address else ""
    if reporter_address and not who:
        who = UNKNOWN_REPORTER

    headline = f":rotating_light: *INCIDENT DECLARED — {incident_id}*"
    lines = [headline, f"*Type:* {incident_type.replace('_', ' ').upper()}"]
    if severity:
        lines[-1] += f"  ·  *Severity:* {severity}"
    if location:
        lines.append(f"*Location:* {location}")
    origin = f"*Reported via:* {source}"
    if who:
        origin += f" by {who}"
    lines.append(origin)
    if report:
        # Blank lines around the quote: Slack renders the blockquote either way,
        # but without them a copy-paste out of Slack runs the report into the
        # line above and below it.
        lines.extend(["", f"> {report}", ""])
    lines.append(
        "The roster has been alerted and reconciliation is running. "
        "`/incident status` for the current count, `/incident resolve` to stand down."
    )
    return "\n".join(lines)


def compose_resolution(previous: dict[str, Any]) -> str:
    incident_id = previous.get("incident_id", "")
    source = _source_label(previous.get("source", ""))
    return (
        f":white_check_mark: *ALL CLEAR — {incident_id}*\n"
        f"Resolved via {source}. The roster has been told to stand down."
    )


def _post(channel: str, text: str) -> bool:
    """Send it. Never raises — a failed announcement must not break a declaration."""
    try:
        from src.services import slack_transport

        token = os.environ.get("SLACK_BOT_TOKEN", "")
        if not (token and slack_transport.WebClient):
            return False
        slack_transport._post_bot_message(channel, text)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.error(f"Could not announce in Slack: {exc}")
        return False


def _announce(record: dict[str, Any], text: str) -> dict[str, Any]:
    from src.core import notify

    channel = incident_channel()
    if (record.get("source", "") or "").lower() == "slack":
        return {"posted": False, "reason": REASON_ORIGIN_SLACK, "channel": channel}
    if not channel:
        logger.info(f"Nothing announced: {REASON_NO_CHANNEL}")
        return {"posted": False, "reason": REASON_NO_CHANNEL, "channel": ""}
    if not notify.delivery_enabled():
        return {"posted": False, "reason": REASON_DELIVERY_OFF, "channel": channel,
                "text": text}
    if not _post(channel, text):
        return {"posted": False, "reason": REASON_POST_FAILED, "channel": channel,
                "text": text}
    return {"posted": True, "reason": "", "channel": channel, "text": text}


def announce_declaration(record: dict[str, Any], reporter_address: str = "") -> dict[str, Any]:
    return _announce(record, compose_declaration(record, reporter_address))


def announce_resolution(previous: dict[str, Any]) -> dict[str, Any]:
    return _announce(previous, compose_resolution(previous))

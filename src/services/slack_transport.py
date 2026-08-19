"""Slack transport — creates incident channels, sends check-ins, posts SITREPs.

Uses Slack Bolt for event-driven interaction. Check-ins come in via message
reactions (:white_check_mark: = safe, :warning: = need_help, :ambulance: = injured,
:runner: = evacuated) or slash commands.
"""

from __future__ import annotations

import logging
import os
from typing import Any

try:
    from slack_bolt.async_app import AsyncApp
    from slack_sdk.web.async_client import AsyncWebClient
    HAS_SLACK = True
except ImportError:
    HAS_SLACK = False
    AsyncApp = None  # type: ignore[assignment,misc]
    AsyncWebClient = None  # type: ignore[assignment,misc]

from src.agents.accountability.tools import process_checkin
from src.core.knowledge_base import KnowledgeBase

logger = logging.getLogger(__name__)

# Reaction → PersonStatus mapping
REACTION_STATUS_MAP = {
    "white_check_mark": "safe",
    "heavy_check_mark": "safe",
    "warning": "need_help",
    "ambulance": "injured",
    "runner": "evacuated",
    "sos": "need_help",
}

# Slack user ID → person_id mapping (built from personnel CSV slack_user_id field)
_slack_to_person: dict[str, str] = {}


def _build_slack_map() -> None:
    """Build Slack user ID to person_id mapping from the knowledge base."""
    if _slack_to_person:
        return
    kb = KnowledgeBase.get()
    for p in kb.personnel:
        slack_id = p.get("slack_user_id", "")
        if slack_id:
            _slack_to_person[slack_id] = p["person_id"]


def create_slack_app() -> AsyncApp:
    """Create and configure the Slack Bolt async app."""
    app = AsyncApp(
        token=os.environ.get("SLACK_BOT_TOKEN", ""),
        signing_secret=os.environ.get("SLACK_SIGNING_SECRET", ""),
    )

    # ── Slash command: /incident ──
    @app.command("/incident")
    async def handle_incident_command(ack, body, client: AsyncWebClient):
        await ack()
        report_text = body.get("text", "")
        user_id = body.get("user_id", "")

        if not report_text:
            await client.chat_postEphemeral(
                channel=body["channel_id"],
                user=user_id,
                text="Usage: `/incident <description>` — e.g. `/incident Smoke near science lab floor 2`",
            )
            return

        # Post acknowledgment — actual processing happens via the coordinator
        await client.chat_postMessage(
            channel=body["channel_id"],
            text=(
                f":rotating_light: *Incident Report Received*\n"
                f"> {report_text}\n\n"
                f"Reported by <@{user_id}>. CrisisMesh is classifying and coordinating.\n"
                f":telephone_receiver: *If this is a life-threatening emergency, call 911 immediately.*"
            ),
        )

    # ── Slash command: /checkin ──
    @app.command("/checkin")
    async def handle_checkin_command(ack, body, client: AsyncWebClient):
        await ack()
        _build_slack_map()
        user_id = body.get("user_id", "")
        text = body.get("text", "safe").strip().lower()

        person_id = _slack_to_person.get(user_id, "")
        if not person_id:
            await client.chat_postEphemeral(
                channel=body["channel_id"],
                user=user_id,
                text="You are not registered in the CrisisMesh personnel roster.",
            )
            return

        status = text if text in ("safe", "injured", "need_help", "evacuated") else "safe"
        # Use the active incident (simplified — in production, tracks current incident)
        result = process_checkin("active", person_id, status)

        await client.chat_postEphemeral(
            channel=body["channel_id"],
            user=user_id,
            text=f":white_check_mark: Check-in recorded: *{result['name']}* — status: *{status}*",
        )

    # ── Reaction-based check-ins ──
    @app.event("reaction_added")
    async def handle_reaction_checkin(event, client: AsyncWebClient):
        _build_slack_map()
        reaction = event.get("reaction", "")
        user_id = event.get("user", "")

        status = REACTION_STATUS_MAP.get(reaction)
        if not status:
            return

        person_id = _slack_to_person.get(user_id, "")
        if not person_id:
            return

        result = process_checkin("active", person_id, status)
        logger.info(f"Reaction check-in: {result['name']} -> {status}")

    return app


# ── Message formatting helpers ──

async def create_incident_channel(
    client: AsyncWebClient,
    incident_id: str,
    incident_type: str,
) -> str:
    """Create a dedicated Slack channel for an incident. Returns channel ID."""
    channel_name = f"inc-{incident_id.lower().replace('_', '-')[:50]}"

    try:
        result = await client.conversations_create(
            name=channel_name,
            is_private=False,
        )
        channel_id = result["channel"]["id"]

        await client.chat_postMessage(
            channel=channel_id,
            text=(
                f":rotating_light: *Incident Channel Created: {incident_id}*\n"
                f"Type: `{incident_type}`\n\n"
                f"This channel is for incident coordination only.\n"
                f":telephone_receiver: *If this is a life-threatening emergency, call 911 immediately.*\n\n"
                f"React to check in:\n"
                f":white_check_mark: Safe | :runner: Evacuated | :warning: Need Help | :ambulance: Injured"
            ),
        )
        return channel_id
    except Exception as e:
        logger.error(f"Failed to create incident channel: {e}")
        return ""


async def post_checkin_request(
    client: AsyncWebClient,
    channel_id: str,
    zone_name: str,
    personnel: list[dict[str, Any]],
) -> str:
    """Post a check-in request message for a zone's personnel."""
    people_lines = "\n".join(
        f"  - {p['name']} ({p.get('role', '')})" for p in personnel[:20]
    )
    remaining = len(personnel) - 20 if len(personnel) > 20 else 0

    text = (
        f":mega: *Roll Call — {zone_name}*\n"
        f"Personnel to account for ({len(personnel)}):\n"
        f"{people_lines}\n"
    )
    if remaining:
        text += f"  ...and {remaining} more\n"

    text += (
        f"\n*React to check in:*\n"
        f":white_check_mark: Safe | :runner: Evacuated | :warning: Need Help | :ambulance: Injured\n"
        f"Or use `/checkin safe|injured|need_help|evacuated`"
    )

    try:
        result = await client.chat_postMessage(channel=channel_id, text=text)
        return result["ts"]
    except Exception as e:
        logger.error(f"Failed to post check-in request: {e}")
        return ""


async def post_sitrep(
    client: AsyncWebClient,
    channel_id: str,
    sitrep: dict[str, Any],
) -> str:
    """Post a formatted SITREP to the incident channel."""
    situation = sitrep.get("situation", {})
    accountability = sitrep.get("accountability", {})
    hazards = sitrep.get("hazards", {})

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"SITREP — {sitrep.get('incident_id', 'Unknown')}"},
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Type:* `{situation.get('incident_type', '')}`"},
                {"type": "mrkdwn", "text": f"*Severity:* `{situation.get('severity', '')}`"},
                {"type": "mrkdwn", "text": f"*Location:* {situation.get('location', '')}"},
                {"type": "mrkdwn", "text": f"*Status:* {situation.get('status', '')}"},
            ],
        },
        {"type": "divider"},
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*Accountability*\n"
                    f":busts_in_silhouette: Total: *{accountability.get('total', 0)}* | "
                    f":white_check_mark: Accounted: *{accountability.get('accounted', 0)}* | "
                    f":question: Unaccounted: *{accountability.get('unaccounted', 0)}*"
                ),
            },
        },
    ]

    blocked = hazards.get("blocked_zones", [])
    if blocked:
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f":no_entry: *Blocked Zones:* {', '.join(blocked)}",
            },
        })

    blocks.append({
        "type": "context",
        "elements": [
            {
                "type": "mrkdwn",
                "text": (
                    f":telephone_receiver: *If 911 has not been called, do so immediately.*\n"
                    f"Generated: {sitrep.get('generated_at', '')}"
                ),
            }
        ],
    })

    try:
        result = await client.chat_postMessage(
            channel=channel_id,
            text=f"SITREP — {sitrep.get('incident_id', '')}",
            blocks=blocks,
        )
        return result["ts"]
    except Exception as e:
        logger.error(f"Failed to post SITREP: {e}")
        return ""


async def post_responder_card(
    client: AsyncWebClient,
    channel_id: str,
    card: dict[str, Any],
) -> str:
    """Post a responder one-card handoff brief."""
    headcount = card.get("headcount", {})
    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"RESPONDER HANDOFF — {card.get('incident_id', '')}"},
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": ":warning: *REQUIRES INCIDENT COMMANDER APPROVAL BEFORE SHARING WITH RESPONDERS*",
            },
        },
        {"type": "divider"},
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Threat:* `{card.get('threat', '')}`"},
                {"type": "mrkdwn", "text": f"*Severity:* `{card.get('severity', '')}`"},
                {"type": "mrkdwn", "text": f"*Time Declared:* {card.get('time_declared', '')}"},
                {"type": "mrkdwn", "text": f"*Location:* {card.get('location', '')}"},
            ],
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*Headcount*\n"
                    f"Total: {headcount.get('total', 0)} | "
                    f"Unaccounted: {headcount.get('unaccounted', 0)} | "
                    f"Injured: {headcount.get('injured', 0)} | "
                    f"Need Help: {headcount.get('need_help', 0)}"
                ),
            },
        },
    ]

    safe_routes = card.get("safe_routes", [])
    if safe_routes:
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Safe Routes:* {', '.join(safe_routes[:5])}",
            },
        })

    blocked = card.get("blocked_routes", [])
    if blocked:
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f":no_entry: *Blocked Routes:* {', '.join(blocked[:5])}",
            },
        })

    blocks.append({
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": f"*Command Contact:* {card.get('command_contact', 'N/A')}",
        },
    })

    try:
        result = await client.chat_postMessage(
            channel=channel_id,
            text=f"RESPONDER HANDOFF — {card.get('incident_id', '')}",
            blocks=blocks,
        )
        return result["ts"]
    except Exception as e:
        logger.error(f"Failed to post responder card: {e}")
        return ""


async def post_accountability_update(
    client: AsyncWebClient,
    channel_id: str,
    summary: dict[str, Any],
) -> str:
    """Post an accountability status update to the incident channel."""
    text = (
        f":clipboard: *Accountability Update — {summary.get('incident_id', '')}*\n"
        f"Total tracked: *{summary.get('total_tracked', 0)}* | "
        f"Accounted: *{summary.get('accounted', 0)}* | "
        f"Unaccounted: *{summary.get('unaccounted', 0)}*\n"
    )

    counts = summary.get("counts", {})
    for status, count in counts.items():
        if count > 0 and status not in ("unknown", "silent"):
            emoji = {"safe": ":white_check_mark:", "injured": ":ambulance:",
                     "need_help": ":warning:", "evacuated": ":runner:"}.get(status, ":grey_question:")
            text += f"  {emoji} {status}: {count}\n"

    unaccounted = summary.get("unaccounted", 0)
    if unaccounted > 0:
        text += f"\n:rotating_light: *{unaccounted} person(s) still unaccounted for*"

    try:
        result = await client.chat_postMessage(channel=channel_id, text=text)
        return result["ts"]
    except Exception as e:
        logger.error(f"Failed to post accountability update: {e}")
        return ""

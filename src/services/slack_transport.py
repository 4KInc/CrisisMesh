"""Slack transport — wires Slack commands and events to the CrisisMesh agent fleet.

Two integration modes:

  1. Events API (recommended for Cloud Run):
     - POST /slack/commands — slash commands (/incident, /checkin)
     - POST /slack/events  — event subscriptions (reaction_added for one-tap check-ins)
     - Mounted on the existing HTTP server in server.py

  2. Socket Mode (via create_slack_app):
     - Legacy Bolt-based integration (requires min-instances on Cloud Run)
     - Uses SLACK_APP_TOKEN for WebSocket connection

Requires: SLACK_BOT_TOKEN and SLACK_SIGNING_SECRET env vars.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import os
import threading
import time
from typing import Any

try:
    from slack_bolt.async_app import AsyncApp
    from slack_sdk.web import WebClient
    from slack_sdk.web.async_client import AsyncWebClient
    HAS_SLACK = True
except ImportError:
    HAS_SLACK = False
    AsyncApp = None  # type: ignore[assignment,misc]
    WebClient = None  # type: ignore[assignment,misc]
    AsyncWebClient = None  # type: ignore[assignment,misc]

from src.agents.accountability.tools import (
    process_checkin,
    read_roster,
    send_checkin_request,
)
from src.agents.intake.tools import classify_incident, extract_location, select_playbook
from src.agents.learning.tools import find_similar_incidents
from src.agents.safety_intel.tools import (
    find_assembly_point,
    find_blocked_zones,
    find_nearby_services,
    find_safe_routes,
    locate_resource,
)
from src.core.content_scanner import ContentScanner
from src.core.event_bus import EventBus, create_event
from src.core.knowledge_base import KnowledgeBase
from src.core.observability import Tracer
from src.models.events import EventType

logger = logging.getLogger(__name__)

# ── State ──

REACTION_STATUS_MAP = {
    "white_check_mark": "safe",
    "heavy_check_mark": "safe",
    "+1": "safe",
    "thumbsup": "safe",
    "ok_hand": "safe",
    "warning": "need_help",
    "sos": "need_help",
    "raised_hand": "need_help",
    "ambulance": "injured",
    "hospital": "injured",
    "runner": "evacuated",
    "door": "evacuated",
}

_slack_to_person: dict[str, str] = {}
_active_incident_id: str = ""
_latest_incident: dict[str, Any] = {}


def _build_slack_map() -> None:
    """Build Slack user ID → person_id mapping from the knowledge base."""
    if _slack_to_person:
        return
    kb = KnowledgeBase.get()
    for p in kb.personnel:
        slack_id = p.get("slack_user_id", "")
        if slack_id:
            _slack_to_person[slack_id] = p["person_id"]


def get_active_incident_id() -> str:
    return _active_incident_id


def get_latest_incident() -> dict[str, Any]:
    return dict(_latest_incident)


def set_latest_incident(result: dict[str, Any], source: str = "web") -> None:
    global _active_incident_id, _latest_incident
    _latest_incident = {**result, "source": source}
    if result.get("incident_id"):
        _active_incident_id = result["incident_id"]


# ── Signature verification ──


def verify_slack_signature(
    signing_secret: str,
    timestamp: str,
    body: str,
    signature: str,
) -> bool:
    """Verify a Slack request signature (HMAC-SHA256).

    Slack sends X-Slack-Request-Timestamp and X-Slack-Signature headers.
    Rejects requests older than 5 minutes to prevent replay attacks.
    """
    if not signing_secret or not timestamp or not signature:
        return False
    try:
        if abs(time.time() - float(timestamp)) > 300:
            return False
    except (ValueError, TypeError):
        return False
    basestring = f"v0:{timestamp}:{body}"
    computed = "v0=" + hmac.new(
        signing_secret.encode(), basestring.encode(), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(computed, signature)


# ── Incident pipeline (deterministic — no Gemini) ──


def run_incident_pipeline(
    report: str,
    facility_id: str = "jefferson",
    source: str = "web",
) -> dict[str, Any]:
    """Run the deterministic incident pipeline. Stores result as the latest incident.

    This is the same pipeline as POST /incident but callable from any trigger
    (Slack, SMS, web). Returns the full incident result dict.
    """
    global _active_incident_id, _latest_incident

    armor = ContentScanner.get().scan_message(report)
    if armor["blocked"]:
        return {
            "blocked": True,
            "reason": armor["reason"],
            "policy": armor["policy"],
            "quarantined_text": armor.get("quarantined_text", ""),
        }

    classification = classify_incident(report)
    location = extract_location(report)
    playbook = select_playbook(classification["incident_type"])
    incident_id = classification["incident_id"]

    tracer = Tracer.get()
    trace = tracer.start_trace(incident_id)
    root = trace.start_span("incident_lifecycle", "coordinator")
    root.set_attribute("incident_type", classification["incident_type"])
    root.set_attribute("severity", classification["severity"])

    intake_span = trace.start_span("intake_classification", "intake", root.span_id)
    intake_span.set_attribute("incident_type", classification["incident_type"])
    intake_span.set_attribute("location_resolved", location.get("resolved", False))
    intake_span.end()

    bus = EventBus.get()
    loop = asyncio.new_event_loop()
    loop.run_until_complete(bus.publish(create_event(
        EventType.INCIDENT_DECLARED, incident_id, "coordinator",
        {"type": classification["incident_type"], "severity": classification["severity"]},
    )))

    zone_id = location.get("zone_id", "")
    safety_span = trace.start_span("safety_intel", "safety_intel", root.span_id)
    blocked = find_blocked_zones(facility_id, zone_id) if zone_id else {}
    routes = find_safe_routes(facility_id, zone_id) if zone_id else {}
    resources = locate_resource(facility_id, "aed")
    assembly = find_assembly_point(facility_id, primary_only=True)
    nearby = find_nearby_services("fire_station")
    safety_span.set_attribute("blocked_routes", len(blocked.get("blocked_routes", [])))
    safety_span.set_attribute("safe_routes", routes.get("total_routes", 0))
    safety_span.end()

    acct_span = trace.start_span("accountability", "accountability", root.span_id)
    roster = read_roster(facility_id)
    send_result = send_checkin_request(incident_id, facility_id=facility_id)
    acct_span.set_attribute("personnel_tracked", send_result["requests_sent"])
    acct_span.end()

    learn_span = trace.start_span("lesson_recall", "learning", root.span_id)
    lessons = find_similar_incidents(classification["incident_type"], facility_id)
    learn_span.set_attribute("lessons_found", lessons["lessons_found"])
    learn_span.end()

    loop.close()

    result = {
        "incident_id": incident_id,
        "report": report,
        "classification": classification,
        "location": location,
        "playbook": playbook,
        "blocked_zones": blocked,
        "safe_routes": routes,
        "assembly_point": assembly,
        "nearby_fire_station": nearby,
        "accountability": {
            "personnel_tracked": send_result["requests_sent"],
            "mobility_needs": roster.get("mobility_needs", []),
        },
        "prior_lessons": lessons,
        "trace_id": trace.trace_id,
        "source": source,
    }

    _active_incident_id = incident_id
    _latest_incident = result
    return result


# ── Command/event dispatchers (Events API mode) ──


def dispatch_slash_command(command: str, form_data: dict[str, str]) -> dict[str, Any]:
    """Dispatch a Slack slash command. Returns the immediate ack response.

    For /incident, pipeline work runs in a background thread.
    For /checkin, the result is returned directly.
    """
    channel_id = form_data.get("channel_id", "")
    user_id = form_data.get("user_id", "")
    text = form_data.get("text", "")
    response_url = form_data.get("response_url", "")

    if command == "/incident":
        if not text:
            return {
                "response_type": "ephemeral",
                "text": "Usage: `/incident <description>` — e.g. `/incident Smoke near science lab floor 2`",
            }
        result = run_incident_pipeline(text, source="slack")
        if result.get("blocked"):
            return {
                "response_type": "ephemeral",
                "text": f":no_entry: *Blocked by content safety:* {result.get('reason', '')}",
            }
        threading.Thread(
            target=_post_slack_results,
            args=(channel_id, result),
            daemon=True,
        ).start()
        return {
            "response_type": "in_channel",
            "text": (
                ":rotating_light: *Incident Report Received*\n"
                f"> {text}\n\n"
                f"Reported by <@{user_id}>. CrisisMesh agent fleet is activating.\n"
                f"Incident ID: `{result.get('incident_id', '')}`\n"
                ":telephone_receiver: *If this is a life-threatening emergency, call 911 immediately.*"
            ),
        }

    elif command == "/checkin":
        return _handle_checkin_command(channel_id, user_id, text)

    return {"response_type": "ephemeral", "text": f"Unknown command: {command}"}


def dispatch_slack_event(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Dispatch a Slack Events API payload.

    Returns a dict for URL verification challenges, or None for processed events.
    """
    if payload.get("type") == "url_verification":
        return {"challenge": payload["challenge"]}

    if payload.get("type") == "event_callback":
        event = payload.get("event", {})
        if event.get("type") == "reaction_added":
            _handle_reaction_event(event)

    return None


# ── Handlers ──


def _post_slack_results(channel_id: str, result: dict[str, Any]) -> None:
    """Post Block Kit SITREP to Slack (runs in background thread)."""
    bot_token = os.environ.get("SLACK_BOT_TOKEN", "")
    if not HAS_SLACK or not bot_token:
        logger.info("SLACK_BOT_TOKEN not set — skipping Block Kit post")
        return

    client = WebClient(token=bot_token)
    _post_incident_block_kit(client, channel_id, result)


def _handle_checkin_command(
    channel_id: str,
    user_id: str,
    text: str,
) -> dict[str, Any]:
    """Handle /checkin slash command. Returns immediate ack response."""
    _build_slack_map()
    person_id = _slack_to_person.get(user_id, "")
    if not person_id:
        return {
            "response_type": "ephemeral",
            "text": "You are not registered in the CrisisMesh personnel roster.",
        }

    status = text.strip().lower() if text.strip().lower() in (
        "safe", "injured", "need_help", "evacuated",
    ) else "safe"
    incident_id = _active_incident_id or "active"
    result = process_checkin(incident_id, person_id, status)

    return {
        "response_type": "ephemeral",
        "text": f":white_check_mark: Check-in recorded: *{result['name']}* — status: *{status}*",
    }


def _handle_reaction_event(event: dict[str, Any]) -> None:
    """Handle reaction_added event for one-tap check-ins."""
    _build_slack_map()
    reaction = event.get("reaction", "")
    user_id = event.get("user", "")

    status = REACTION_STATUS_MAP.get(reaction)
    if not status:
        return

    person_id = _slack_to_person.get(user_id, "")
    if not person_id:
        return

    incident_id = _active_incident_id or "active"
    result = process_checkin(incident_id, person_id, status)
    logger.info(f"Reaction check-in: {result['name']} -> {status} (incident: {incident_id})")


# ── Block Kit formatting ──


def _post_incident_block_kit(
    client: Any,
    channel_id: str,
    result: dict[str, Any],
) -> None:
    """Post the full incident result as Block Kit to Slack."""
    c = result["classification"]
    loc = result["location"]
    playbook = result["playbook"]
    routes_list = result.get("safe_routes", {}).get("routes", [])
    blocked_list = result.get("blocked_zones", {}).get("blocked_routes", [])
    assembly_list = result.get("assembly_point", {}).get("assembly_points", [])
    nearby_list = result.get("nearby_fire_station", {}).get("services", [])
    lessons_list = result.get("prior_lessons", {}).get("lessons", [])
    acct = result.get("accountability", {})

    assembly_name = assembly_list[0]["name"] if assembly_list else "Athletic Field"
    fire_info = (
        f"{nearby_list[0]['name']} (ETA {nearby_list[0]['eta_minutes']}min)"
        if nearby_list else "—"
    )

    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"INCIDENT DECLARED — {result['incident_id']}",
            },
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Type:* `{c['incident_type'].upper()}`"},
                {"type": "mrkdwn", "text": f"*Severity:* `{c['severity'].upper()}`"},
                {"type": "mrkdwn", "text": f"*Location:* {loc.get('zone_name') or loc.get('zone_id') or '—'}"},
                {"type": "mrkdwn", "text": f"*Playbook:* `{playbook['playbook_id']}`"},
            ],
        },
        {"type": "divider"},
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*Accountability*\n"
                    f":busts_in_silhouette: Personnel tracked: *{acct.get('personnel_tracked', 0)}*\n"
                    f"Mobility needs: *{len(acct.get('mobility_needs', []))}*\n\n"
                    f"*Assembly:* {assembly_name}\n"
                    f"*Nearest Fire Station:* {fire_info}"
                ),
            },
        },
    ]

    if blocked_list:
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": ":no_entry: *Blocked Routes:* " + ", ".join(
                    r["name"] for r in blocked_list[:5]
                ),
            },
        })

    if routes_list:
        route_lines = "\n".join(
            f"  :white_check_mark: {r['name']} → {r['to_exit']}" for r in routes_list[:5]
        )
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Safe Routes:*\n{route_lines}"},
        })

    if lessons_list:
        lesson_lines = "\n".join(
            f"  :brain: {l['title']}" for l in lessons_list[:3]
        )
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Prior Lessons:*\n{lesson_lines}"},
        })

    blocks.append({
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": (
                "*React to check in:*\n"
                ":white_check_mark: Safe | :thumbsup: Safe | :runner: Evacuated | "
                ":warning: Need Help | :ambulance: Injured | :hospital: Injured\n"
                "Or use `/checkin safe|injured|need_help|evacuated`"
            ),
        },
    })

    blocks.append({
        "type": "context",
        "elements": [{
            "type": "mrkdwn",
            "text": (
                ":telephone_receiver: *If 911 has not been called, do so immediately.*\n"
                f"Incident ID: `{result['incident_id']}` | "
                f"Trace: `{result.get('trace_id', '—')}`"
            ),
        }],
    })

    try:
        client.chat_postMessage(
            channel=channel_id,
            text=f"INCIDENT DECLARED — {result['incident_id']}",
            blocks=blocks,
        )
    except Exception as e:
        logger.error(f"Failed to post incident Block Kit: {e}")


# ── Legacy Bolt app (Socket Mode) ──


def create_slack_app() -> AsyncApp:
    """Create and configure the Slack Bolt async app (Socket Mode)."""
    app = AsyncApp(
        token=os.environ.get("SLACK_BOT_TOKEN", ""),
        signing_secret=os.environ.get("SLACK_SIGNING_SECRET", ""),
    )

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

        await client.chat_postMessage(
            channel=body["channel_id"],
            text=(
                f":rotating_light: *Incident Report Received*\n"
                f"> {report_text}\n\n"
                f"Reported by <@{user_id}>. CrisisMesh is classifying and coordinating.\n"
                f":telephone_receiver: *If this is a life-threatening emergency, call 911 immediately.*"
            ),
        )

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
        incident_id = _active_incident_id or "active"
        result = process_checkin(incident_id, person_id, status)

        await client.chat_postEphemeral(
            channel=body["channel_id"],
            user=user_id,
            text=f":white_check_mark: Check-in recorded: *{result['name']}* — status: *{status}*",
        )

    @app.event("reaction_added")
    async def handle_reaction_checkin(event, client: AsyncWebClient):
        _handle_reaction_event(event)

    return app


# ── Legacy async helpers ──


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

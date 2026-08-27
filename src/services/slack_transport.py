"""Slack transport — wires Slack commands and events to the CrisisMesh agent fleet.

Two integration modes:

  1. Events API (recommended for Cloud Run):
     - POST /slack/commands — slash commands (/incident, /checkin)
     - POST /slack/events  — event subscriptions (reaction_added, app_mention)
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
import re
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
    compute_accountability_summary,
    process_checkin,
    read_roster,
    send_checkin_request,
)
from src.agents.intake.tools import classify_incident, extract_location, select_playbook
from src.agents.learning.tools import find_similar_incidents
from src.agents.sitrep.tools import extract_threat_observation, generate_arrival_brief
from src.core.tactical_reasoning import build_provenance_record, get_tactical_context
from src.agents.safety_intel.tools import (
    find_assembly_point,
    find_blocked_zones,
    find_nearby_services,
    find_safe_routes,
    locate_resource,
)
from src.config.playbooks import PLAYBOOKS
from src.core.content_scanner import ContentScanner
from src.core.event_bus import EventBus, create_event
from src.core import checkin_policy, incident_state, room_board
from src.core.knowledge_base import KnowledgeBase, init_knowledge_base
from src.core.observability import Tracer
from src.core.tactical_reasoning import strip_origin_from_payload
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
# The active incident now lives in src/core/incident_state — it belongs to the
# system, not to this channel. Slack-specific origin details (which channel,
# which user) are attached there too, so /incident status and the arrival brief
# can render them without owning them.

INCIDENT_TYPES = {
    "earthquake": {"label": "Earthquake", "emoji": "earth_americas"},
    "fire": {"label": "Fire", "emoji": "fire"},
    "flood": {"label": "Flood", "emoji": "ocean"},
    "active_threat": {"label": "Active Threat", "emoji": "rotating_light"},
    "cyberattack": {"label": "Cyber Attack", "emoji": "skull_and_crossbones"},
    "data_breach": {"label": "Data Breach", "emoji": "lock"},
    "outage": {"label": "Service Outage", "emoji": "electric_plug"},
    "weather": {"label": "Severe Weather", "emoji": "thunder_cloud_and_rain"},
    "medical": {"label": "Medical Emergency", "emoji": "ambulance"},
    "other": {"label": "Other Incident", "emoji": "warning"},
}

PLAYBOOK_MAP = {
    "earthquake": "earthquake", "fire": "fire", "flood": "flood",
    "active_threat": "active_threat", "cyberattack": "cyberattack",
    "data_breach": "data_breach", "outage": "outage",
    "weather": "weather", "medical": "medical", "other": "generic",
}


def _build_slack_map() -> None:
    """Build Slack user ID → person_id mapping from the knowledge base."""
    if _slack_to_person:
        return
    kb = KnowledgeBase.get()
    for p in kb.personnel:
        slack_id = p.get("slack_user_id", "")
        if slack_id:
            _slack_to_person[slack_id] = p["person_id"]


def _origin_user() -> str:
    """The Slack user id that declared the incident, if it came from Slack."""
    return incident_state.get_origin()["declared_by"]


def get_active_incident_id() -> str:
    return incident_state.get_active_incident_id()


def get_latest_incident() -> dict[str, Any]:
    return incident_state.get_latest_incident()


def set_latest_incident(result: dict[str, Any], source: str = "web") -> None:
    incident_state.set_latest_incident(result, source)


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


def _publish_resolved(previous: dict[str, Any]) -> None:
    """Announce resolution. INCIDENT_RESOLVED had never been published at all,
    so nothing downstream — fan-out, audit, console — could learn an incident
    had ended."""
    try:
        bus = EventBus.get()
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(bus.publish(create_event(
                EventType.INCIDENT_RESOLVED, previous.get("incident_id", ""), "coordinator",
                {
                    "incident_id": previous.get("incident_id", ""),
                    "elapsed_minutes": previous.get("elapsed_minutes", 0),
                    "source": previous.get("source", ""),
                    "incident_type": (
                        (previous.get("record", {}) or {}).get("classification", {}) or {}
                    ).get("incident_type", ""),
                },
            )))
        finally:
            loop.close()
    except Exception as exc:
        logger.error(f"INCIDENT_RESOLVED publish failed: {exc}")


def _publish_declared(
    incident_id: str,
    classification: dict[str, Any],
    location: dict[str, Any],
    reporter_address: str = "",
) -> None:
    """Announce the declaration on the bus. Never lets a subscriber break intake."""
    try:
        bus = EventBus.get()
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(bus.publish(create_event(
                EventType.INCIDENT_DECLARED, incident_id, "coordinator",
                {
                    "type": classification.get("incident_type", ""),
                    "severity": classification.get("severity", ""),
                    "zone_name": location.get("zone_name", ""),
                    "reporter_address": reporter_address,
                },
            )))
        finally:
            loop.close()
    except Exception as exc:
        logger.error(f"INCIDENT_DECLARED publish failed: {exc}")


def run_incident_pipeline(
    report: str,
    facility_id: str = "jefferson",
    source: str = "web",
    reporter_address: str = "",
) -> dict[str, Any]:
    """Run the deterministic incident pipeline. Stores result as the latest incident.

    This is the same pipeline as POST /incident but callable from any trigger
    (Slack, SMS, web). Returns the full incident result dict.
    """

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


    zone_id = location.get("zone_id", "")
    safety_span = trace.start_span("safety_intel", "safety_intel", root.span_id)
    blocked = find_blocked_zones(facility_id, zone_id) if zone_id else {}
    routes = find_safe_routes(facility_id, zone_id, blocked_zones=zone_id) if zone_id else {}
    resources = locate_resource(facility_id, "aed")
    assembly = find_assembly_point(facility_id, primary_only=True)
    incident_type = classification.get("incident_type", "")
    _SERVICE_FOR_TYPE = {
        "active_shooter": "police_station",
        "active_threat": "police_station",
        "medical": "hospital",
    }
    nearby_service_type = _SERVICE_FOR_TYPE.get(incident_type, "fire_station")
    nearby = find_nearby_services(nearby_service_type)
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

    tactical = get_tactical_context(
        incident_type=classification["incident_type"],
        playbook_id=playbook.get("playbook_id", ""),
        severity=classification.get("severity", ""),
    )
    provenance = build_provenance_record(tactical, incident_id)
    root.set_attribute("tactical_origin", provenance["origin"])

    result = {
        "incident_id": incident_id,
        "report": report,
        "classification": classification,
        "location": location,
        "playbook": playbook,
        "blocked_zones": blocked,
        "safe_routes": routes,
        "assembly_point": assembly,
        "nearby_service": nearby,
        "nearby_service_type": nearby_service_type,
        "accountability": {
            "personnel_tracked": send_result["requests_sent"],
            "mobility_needs": roster.get("mobility_needs", []),
        },
        "prior_lessons": lessons,
        "trace_id": trace.trace_id,
        "source": source,
        "tactical_provenance": provenance,
    }

    incident_state.declare(incident_id, result, source=source,
                           reporter_address=reporter_address)

    # Published only once the state is consistent: the fan-out subscriber reads
    # incident_state, and firing before declare() would hand it the previous
    # incident. The payload carries enough to alert on without that read.
    _publish_declared(incident_id, classification, location, reporter_address)
    return strip_origin_from_payload(result)


# ── Command/event dispatchers (Events API mode) ──


def dispatch_slash_command(command: str, form_data: dict[str, str]) -> dict[str, Any]:
    """Dispatch a Slack slash command. Returns the immediate ack response.

    /incident supports subcommands:
      /incident <description>     — declare a new incident (bare text)
      /incident status            — view active incident status
      /incident checkin [status]  — check in to the active incident
      /incident resolve           — resolve the active incident
      /incident approve <id>      — approve a pending action (IC only)
      /incident deny <id>         — deny a pending action (IC only)
      /incident playbook [type]   — view a response playbook
      /incident help              — show all commands
    """
    channel_id = form_data.get("channel_id", "")
    user_id = form_data.get("user_id", "")
    text = form_data.get("text", "").strip()
    response_url = form_data.get("response_url", "")

    if command == "/incident":
        if not text:
            return _handle_help(user_id)

        parts = text.split(maxsplit=1)
        subcommand = parts[0].lower()

        if subcommand == "help":
            return _handle_help(user_id)
        elif subcommand == "status":
            return _handle_status(channel_id, user_id)
        elif subcommand == "checkin":
            status_text = parts[1] if len(parts) > 1 else "safe"
            return _handle_checkin_command(channel_id, user_id, status_text)
        elif subcommand == "resolve":
            return _handle_resolve(channel_id, user_id)
        elif subcommand == "approve":
            action_id = parts[1].strip() if len(parts) > 1 else ""
            return _handle_approve(user_id, action_id)
        elif subcommand == "deny":
            action_id = parts[1].strip() if len(parts) > 1 else ""
            return _handle_deny(user_id, action_id)
        elif subcommand == "playbook":
            playbook_type = parts[1].strip().lower() if len(parts) > 1 else ""
            return _handle_playbook(channel_id, user_id, playbook_type)
        else:
            return _start_incident(channel_id, user_id, text)

    elif command == "/checkin":
        return _handle_checkin_command(channel_id, user_id, text)

    return {"response_type": "ephemeral", "text": f"Unknown command: {command}"}


def _start_incident(channel_id: str, user_id: str, text: str) -> dict[str, Any]:
    """Declare a new incident — fast-ack, deterministic fallback, agentic in background."""

    result = run_incident_pipeline(text, source="slack")
    if result.get("blocked"):
        return {
            "response_type": "ephemeral",
            "text": f":no_entry: *Blocked by content safety:* {result.get('reason', '')}",
        }

    incident_state.attach_origin(declared_by=user_id, origin_channel=channel_id)

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


def _handle_help(user_id: str) -> dict[str, Any]:
    types_list = ", ".join(INCIDENT_TYPES.keys())
    return {
        "response_type": "ephemeral",
        "text": (
            "*CrisisMesh — Incident Coordination Commands*\n\n"
            "`/incident <description>` — Declare a new incident\n"
            "`/incident status` — View active incident status\n"
            "`/incident checkin [safe|injured|need_help|evacuated]` — Check in\n"
            "`/incident resolve` — Resolve the active incident\n"
            "`/incident approve <id>` — Approve a pending action (IC only)\n"
            "`/incident deny <id>` — Deny a pending action (IC only)\n"
            "`/incident playbook <type>` — View a response playbook\n"
            "`/incident help` — Show this help message\n"
            "`/checkin [status]` — Quick check-in (alias)\n\n"
            f"*Incident types:* {types_list}\n\n"
            ":telephone_receiver: *CrisisMesh coordinates alongside 911 — it never replaces emergency services.*"
        ),
    }


def _declared_by_label() -> str:
    """Who declared this, on whichever channel they used.

    The card said "Declared by: —" for an incident a named person had reported
    from a handset two minutes earlier: the field only ever read the Slack
    origin, and a phone declaration has no Slack user to mention.
    """
    if _origin_user():
        return f"<@{_origin_user()}>"

    origin = incident_state.get_origin()
    source = (origin.get("source", "") or "").lower()
    reporter = origin.get("reporter_address", "")
    if not source:
        return "—"

    label = {"whatsapp": "WhatsApp", "sms": "SMS", "web": "the web console"}.get(
        source, source)
    if reporter:
        from src.core import channel_sync
        # Never the raw number: a channel has the whole team as readers.
        name = channel_sync._reporter_name(reporter) or channel_sync.UNKNOWN_REPORTER
        return f"{name} (via {label})"
    return f"via {label}"


def _handle_status(channel_id: str, user_id: str) -> dict[str, Any]:
    if not incident_state.is_active():
        return {
            "response_type": "ephemeral",
            "text": ":white_check_mark: No active incidents.",
        }

    inc = incident_state.get_latest_incident()
    classification = inc.get("classification", {})
    incident_type = classification.get("incident_type", "other")
    type_info = INCIDENT_TYPES.get(incident_type, INCIDENT_TYPES["other"])

    summary = compute_accountability_summary(incident_state.get_active_incident_id())
    duration_min = incident_state.elapsed_minutes()

    missing_names = []
    breakdown = summary.get("breakdown", {})
    for person in breakdown.get("unknown", []):
        missing_names.append(person.get("name", person.get("person_id", "?")))
    for person in breakdown.get("silent", []):
        missing_names.append(person.get("name", person.get("person_id", "?")))

    status_text = (
        f":{type_info['emoji']}: *{incident_state.get_active_incident_id()} — {type_info['label']}*\n\n"
        f"*Severity:* `{classification.get('severity', '—').upper()}`\n"
        f"*Duration:* {duration_min} minutes\n"
        f"*Declared by:* {_declared_by_label()}\n"
        f"*Check-ins:* {summary['accounted']}/{summary['total_tracked']}\n"
    )

    if missing_names:
        # Every name. "and 24 more" is the problem restated as a number — these
        # are the people someone has to go and find.
        status_text += (f"\n:red_circle: *Missing ({len(missing_names)}):* "
                        + ", ".join(missing_names))

    status_text += (
        f"\n\n:telephone_receiver: *If 911 has not been called, do so immediately.*"
    )

    return {"response_type": "in_channel", "text": status_text}


def _handle_resolve(channel_id: str, user_id: str) -> dict[str, Any]:
    """Slack rendering of a resolution. The state change lives in
    core.incident_resolve so every channel ends an incident the same way."""
    from src.core import incident_resolve

    try:
        report = incident_resolve.resolve(
            incident_id=incident_state.get_active_incident_id(),
            resolved_by=user_id,
            channel="slack",
        )
    except incident_resolve.ResolveRefused as refused:
        return {"response_type": "ephemeral", "text": f":warning: {refused.reason}"}

    type_info = INCIDENT_TYPES.get(report["incident_type"], INCIDENT_TYPES["other"])
    acct = report["accountability"]

    report_text = (
        f":heavy_check_mark: *Incident {report['incident_id']} RESOLVED*\n\n"
        f"*Type:* {type_info['label']}\n"
        f"*Severity:* `{(report['severity'] or '—').upper()}`\n"
        f"*Duration:* {report['duration_minutes']} minutes\n"
        f"*Resolved by:* <@{user_id}>\n\n"
        f"---\n\n"
        f"*Personnel Accountability*\n"
        f"- Total tracked: {acct['total_tracked']}\n"
        f"- Accounted: {acct['accounted']}\n"
        f"- Unaccounted: {acct['unaccounted']}\n"
    )

    for status, count in acct.get("counts", {}).items():
        if count > 0 and status not in ("unknown", "silent"):
            emoji = {"safe": ":white_check_mark:", "injured": ":ambulance:",
                     "need_help": ":warning:", "evacuated": ":runner:"}.get(status, ":grey_question:")
            report_text += f"  {emoji} {status}: {count}\n"

    lessons = report.get("prior_lessons", {}) or {}
    if lessons.get("lessons_found", 0) > 0:
        report_text += "\n*Lessons from Prior Incidents:*\n"
        for lesson in lessons.get("lessons", [])[:3]:
            report_text += f"  :brain: {lesson.get('title', '')}\n"

    return {"response_type": "in_channel", "text": report_text}


def _handle_approve(user_id: str, action_id: str) -> dict[str, Any]:
    """IC approves a pending action via Slack."""
    if not action_id:
        from src.core.agent_gateway import AgentGateway
        gw = AgentGateway.get()
        pending = gw.get_pending_actions(incident_id=incident_state.get_active_incident_id())
        if not pending:
            return {
                "response_type": "ephemeral",
                "text": ":white_check_mark: No pending actions awaiting approval.",
            }
        lines = [":clipboard: *Pending Actions:*"]
        for pa in pending:
            lines.append(f"  `{pa.id}` — {pa.action} (requested by {pa.requesting_agent})")
        lines.append("\nUsage: `/incident approve <id>`")
        return {"response_type": "ephemeral", "text": "\n".join(lines)}

    from src.core.agent_gateway import AgentGateway
    gw = AgentGateway.get()

    loop = asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(gw.approve_action(action_id, user_id))
    finally:
        loop.close()

    if "error" in result:
        return {"response_type": "ephemeral", "text": f":no_entry: {result['error']}"}

    return {
        "response_type": "in_channel",
        "text": (
            f":white_check_mark: *Action approved:* `{result['action']}` "
            f"(ID: `{result['action_id']}`)\n"
            f"Approved by <@{user_id}>."
        ),
    }


def _handle_deny(user_id: str, action_id: str) -> dict[str, Any]:
    """IC denies a pending action via Slack."""
    if not action_id:
        return {
            "response_type": "ephemeral",
            "text": ":warning: Usage: `/incident deny <action_id>`",
        }

    from src.core.agent_gateway import AgentGateway
    gw = AgentGateway.get()

    loop = asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(gw.deny_action(action_id, user_id))
    finally:
        loop.close()

    if "error" in result:
        return {"response_type": "ephemeral", "text": f":no_entry: {result['error']}"}

    return {
        "response_type": "in_channel",
        "text": (
            f":x: *Action denied:* `{result['action']}` "
            f"(ID: `{result['action_id']}`)\n"
            f"Denied by <@{user_id}>."
        ),
    }


def _handle_playbook(channel_id: str, user_id: str, playbook_type: str) -> dict[str, Any]:
    if not playbook_type:
        types_list = ", ".join(f"`{k}`" for k in INCIDENT_TYPES.keys())
        return {
            "response_type": "ephemeral",
            "text": f":warning: Usage: `/incident playbook <type>`\nTypes: {types_list}",
        }

    playbook_key = PLAYBOOK_MAP.get(playbook_type, "")
    if not playbook_key:
        types_list = ", ".join(f"`{k}`" for k in INCIDENT_TYPES.keys())
        return {
            "response_type": "ephemeral",
            "text": f":warning: Unknown type `{playbook_type}`. Available: {types_list}",
        }

    return {
        "response_type": "ephemeral",
        "text": format_playbook_message(playbook_key),
    }


def dispatch_slack_event(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Dispatch a Slack Events API payload.

    Returns a dict for URL verification challenges, or None for processed events.
    """
    if payload.get("type") == "url_verification":
        return {"challenge": payload["challenge"]}

    if payload.get("type") == "event_callback":
        event = payload.get("event", {})
        event_type = event.get("type", "")

        if event_type == "reaction_added":
            _handle_reaction_event(event)
        elif event_type == "app_mention":
            _handle_app_mention(event)
        elif event_type == "message" and event.get("channel_type") == "im":
            if not event.get("subtype") and not event.get("bot_id"):
                _handle_app_mention(event)
        elif event_type == "file_shared":
            threading.Thread(
                target=_handle_file_shared,
                args=(event,),
                daemon=True,
            ).start()

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


def _clean_markdown_for_slack(text: str) -> str:
    """Strip markdown artifacts and convert to Slack-friendly mrkdwn."""
    import re
    t = text.strip()
    t = re.sub(r"#{1,6}\s+", "", t)
    t = t.replace("---", "")
    t = re.sub(r"\*{3,}", "", t)
    t = re.sub(r"\*\*(.+?)\*\*", r"*\1*", t)
    t = re.sub(r"__(.+?)__", r"*\1*", t)
    t = re.sub(r"(?m)^\*\s+", "- ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def _split_text(text: str, limit: int = 2900) -> list[str]:
    """Split text into chunks that fit within Slack's block text limit."""
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    while text:
        if len(text) <= limit:
            chunks.append(text)
            break
        cut = text.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = limit
        chunks.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    return chunks


def _gemini_text_to_blocks(
    text: str, incident_id: str,
) -> list[dict[str, Any]]:
    """Convert Gemini markdown output into clean Slack Block Kit blocks."""
    import re

    blocks: list[dict[str, Any]] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"GEMINI FLEET SITREP — {incident_id}"},
        },
    ]

    sections = re.split(r"\n#{1,3}\s+", text)
    for raw in sections:
        raw = raw.strip()
        if not raw:
            continue
        lines = raw.split("\n", 1)
        title = lines[0].strip().strip("#").strip()
        title = title.strip()
        body = lines[1].strip() if len(lines) > 1 else ""
        body = _clean_markdown_for_slack(body)

        if not body and not title:
            continue

        if title and body:
            section_text = f"*{title}*\n{body}"
        elif title:
            section_text = f"*{title}*"
        else:
            section_text = body

        for chunk in _split_text(section_text, 2900):
            blocks.append({"type": "divider"})
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": chunk},
            })
        continue

    blocks.append({"type": "divider"})

    if len(blocks) > 50:
        blocks = blocks[:49] + [blocks[-1]]

    return blocks


def _run_agentic_and_post(channel_id: str, report: str, incident_id: str) -> None:
    """Run the Gemini-driven agentic pipeline in a background thread and post SITREP."""
    try:
        from src.core.server import _run_agentic
        loop = asyncio.new_event_loop()
        logger.info(f"Agentic pipeline starting for {incident_id}")
        result = loop.run_until_complete(asyncio.wait_for(_run_agentic(report), timeout=180))
        loop.close()
        logger.info(f"Agentic pipeline finished for {incident_id}, final_response length={len(result.get('final_response', ''))}")
    except asyncio.TimeoutError:
        logger.error(f"Agentic pipeline timed out after 180s for {incident_id}")
        loop.close()
        return
    except Exception as e:
        logger.error(f"Agentic pipeline failed (deterministic fallback already posted): {e}")
        return

    final_text = result.get("final_response", "")
    if not final_text:
        logger.info("Agentic pipeline returned no final text — deterministic fallback stands")
        return

    id_pattern = re.compile(r"[A-Z][A-Z_]+-\d{4}-\d+")
    for gen_id in set(id_pattern.findall(final_text)):
        if gen_id != incident_id:
            final_text = final_text.replace(gen_id, incident_id)

    bot_token = os.environ.get("SLACK_BOT_TOKEN", "")
    if not HAS_SLACK or not bot_token:
        logger.info("SLACK_BOT_TOKEN not set — skipping Gemini SITREP post")
        return

    blocks = _gemini_text_to_blocks(final_text, incident_id)

    try:
        client = WebClient(token=bot_token)
        client.chat_postMessage(
            channel=channel_id,
            text=f"Gemini Fleet SITREP — {incident_id}",
            blocks=blocks,
        )
        logger.info(f"Gemini SITREP posted to Slack channel {channel_id}")
    except Exception as e:
        logger.error(f"Failed to post Gemini SITREP: {e}")


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
    if not checkin_policy.can_accept():
        checkin_policy.log_refusal("slack", status, user_id)
        return {
            "response_type": "ephemeral",
            "text": f":warning: {checkin_policy.refusal_message(status)}",
        }

    incident_id = incident_state.get_active_incident_id()
    result = process_checkin(incident_id, person_id, status)

    return {
        "response_type": "ephemeral",
        "text": f":white_check_mark: Check-in recorded: *{result['name']}* — status: *{status}*",
    }


def _handle_reaction_event(event: dict[str, Any]) -> None:
    """Handle reaction_added event for one-tap check-ins.

    Posts a confirmation message to the channel with check-in count and missing list.
    """
    _build_slack_map()
    reaction = event.get("reaction", "")
    user_id = event.get("user", "")
    channel_id = event.get("item", {}).get("channel", "")

    status = REACTION_STATUS_MAP.get(reaction)
    if not status:
        return

    person_id = _slack_to_person.get(user_id, "")
    if not person_id:
        return

    if not checkin_policy.can_accept():
        # A reaction has no reply channel of its own, so the refusal has to go
        # to the person directly rather than being swallowed.
        checkin_policy.log_refusal("slack-reaction", status, user_id)
        if channel_id:
            threading.Thread(
                target=_post_ephemeral,
                args=(channel_id, user_id, f":warning: {checkin_policy.refusal_message(status)}"),
                daemon=True,
            ).start()
        return

    incident_id = incident_state.get_active_incident_id()
    result = process_checkin(incident_id, person_id, status)
    logger.info(f"Reaction check-in: {result['name']} -> {status} (incident: {incident_id})")

    if channel_id:
        threading.Thread(
            target=_post_checkin_confirmation,
            args=(channel_id, user_id, result["name"], status, incident_id),
            daemon=True,
        ).start()


def _post_ephemeral(channel_id: str, user_id: str, text: str) -> None:
    """Send one person a message only they can see. Used to refuse a check-in
    that has no incident, which otherwise would have no reply path at all."""
    bot_token = os.environ.get("SLACK_BOT_TOKEN", "")
    if not HAS_SLACK or not bot_token:
        return
    try:
        WebClient(token=bot_token).chat_postEphemeral(
            channel=channel_id, user=user_id, text=text,
        )
    except Exception as exc:
        logger.error(f"Failed to post refusal to {user_id}: {exc}")


def _post_checkin_confirmation(
    channel_id: str, user_id: str, name: str, status: str, incident_id: str,
) -> None:
    """Post a check-in confirmation to the channel (background thread)."""
    bot_token = os.environ.get("SLACK_BOT_TOKEN", "")
    if not HAS_SLACK or not bot_token:
        return

    status_emoji = {
        "safe": ":white_check_mark:",
        "injured": ":ambulance:",
        "need_help": ":warning:",
        "evacuated": ":runner:",
    }

    summary = compute_accountability_summary(incident_id)
    accounted = summary["accounted"]
    total = summary["total_tracked"]

    msg = f"{status_emoji.get(status, ':white_check_mark:')} <@{user_id}> checked in as *{status}* ({accounted}/{total})"

    if accounted == total and total > 0:
        msg += "\n:tada: *All personnel accounted for!*"
    else:
        missing_names = []
        breakdown = summary.get("breakdown", {})
        for person in breakdown.get("unknown", []):
            missing_names.append(person.get("name", "?"))
        for person in breakdown.get("silent", []):
            missing_names.append(person.get("name", "?"))
        if missing_names:
            msg += f"\n:red_circle: Still missing: " + ", ".join(missing_names)

    try:
        client = WebClient(token=bot_token)
        client.chat_postMessage(channel=channel_id, text=msg)
    except Exception as e:
        logger.error(f"Failed to post check-in confirmation: {e}")


def _handle_app_mention(event: dict[str, Any]) -> None:
    """Handle app_mention or DM — always route to Gemini as a conversational query.

    Incidents are created only via /incident. @mentions are always follow-up
    queries routed through the Gemini agentic pipeline.
    """
    channel_id = event.get("channel", "")
    user_id = event.get("user", "")
    text = event.get("text", "")
    thread_ts = event.get("thread_ts") or event.get("ts", "")

    cleaned = re.sub(r"<@[A-Z0-9]+>", "", text).strip()
    logger.info(f"app_mention received: user={user_id}, cleaned={cleaned[:80]!r}")

    if not cleaned:
        _post_bot_message(
            channel_id,
            ":rotating_light: *CrisisMesh standing by.* Tell me what's happening and I'll coordinate the response.\n"
            ":telephone_receiver: *If this is a life-threatening emergency, call 911 immediately.*",
            thread_ts=thread_ts,
        )
        return

    threading.Thread(
        target=_run_followup_query,
        args=(channel_id, user_id, cleaned, thread_ts),
        daemon=True,
    ).start()


_facility_data_cache: str = ""
_room_checkins: dict[str, dict[str, Any]] = {}


def _parse_checkin(text: str) -> dict[str, Any] | None:
    """Detect check-in messages like 'room 101: all 25 students are safe'."""
    import re
    m = re.search(
        r"room\s+(\w+)\s*[:\-–—]\s*(.+)",
        text, re.IGNORECASE,
    )
    if not m:
        return None
    room = m.group(1)
    body = m.group(2).lower()

    safe = 0
    missing = 0
    status = "reported"
    notes = m.group(2).strip()

    safe_m = re.search(r"(?:all\s+)?(\d+)\s*(?:students?\s+)?(?:are\s+)?safe", body)
    if safe_m:
        safe = int(safe_m.group(1))
        status = "safe"

    miss_m = re.search(r"(\d+)\s*(?:are\s+)?(?:missing|unaccounted)", body)
    if miss_m:
        missing = int(miss_m.group(1))
        status = "partial" if safe > 0 else "missing"

    if "all" in body and "safe" in body and safe == 0:
        safe_m2 = re.search(r"all\s+(\d+)", body)
        if safe_m2:
            safe = int(safe_m2.group(1))
            status = "safe"

    return {"room": room, "safe": safe, "missing": missing, "status": status, "notes": notes}


def _format_checkin_board() -> str:
    """Format current check-in state as text for the Gemini prompt."""
    board = _reported_rooms()
    if not board:
        return "No rooms have reported yet."
    lines = ["CURRENT CHECK-IN BOARD:"]
    total_safe = 0
    total_missing = 0
    for room, info in sorted(board.items()):
        icon = ":white_check_mark:" if info["missing"] == 0 else ":red_circle:"
        lines.append(
            f"{icon} Room {room}: {info['safe']} safe, {info['missing']} missing — {info['notes']}"
        )
        total_safe += info["safe"]
        total_missing += info["missing"]
    lines.append(f"Totals: {total_safe} safe · {total_missing} missing · {len(board)} rooms reported")
    return "\n".join(lines)


def _load_facility_data() -> str:
    """Load all seed CSV files into a single context string (cached).

    Strips internal-only columns (slack_user_id, emergency_contact_*,
    medical_notes) so placeholder IDs and PII don't leak into Gemini output.
    """
    global _facility_data_cache
    if _facility_data_cache:
        return _facility_data_cache
    import csv as _csv
    import io as _io
    import pathlib
    _STRIP_COLS = {"slack_user_id", "emergency_contact_name", "emergency_contact_phone", "medical_notes"}
    seed_dir = pathlib.Path(__file__).resolve().parent.parent.parent / "data" / "seed"
    parts: list[str] = []
    for csv_file in sorted(seed_dir.glob("*.csv")):
        raw = csv_file.read_text().strip()
        reader = _csv.DictReader(_io.StringIO(raw))
        keep = [f for f in (reader.fieldnames or []) if f not in _STRIP_COLS]
        if keep != list(reader.fieldnames or []):
            buf = _io.StringIO()
            writer = _csv.DictWriter(buf, fieldnames=keep, extrasaction="ignore")
            writer.writeheader()
            for row in reader:
                writer.writerow(row)
            raw = buf.getvalue().strip()
        parts.append(f"=== {csv_file.stem.upper()} ===\n{raw}")
    _facility_data_cache = "\n\n".join(parts)
    return _facility_data_cache


def _handle_checkin_direct(channel_id: str, user_id: str, checkin: dict[str, Any], thread_ts: str) -> None:
    """Post a direct check-in confirmation without calling Gemini."""
    room = checkin["room"]
    safe = checkin["safe"]
    missing = checkin["missing"]

    if missing > 0:
        header = f":red_circle: *Room {room}* logged: {safe} safe, *{missing} MISSING* ({checkin['notes']})"
    else:
        header = f":white_check_mark: *Room {room}* logged: {safe}/{safe} safe, 0 missing."

    board = _reported_rooms()
    board_lines = [f"\n:clipboard: *Board — {len(board)} rooms reported*"]
    total_safe = 0
    total_missing = 0
    for r, info in sorted(board.items()):
        icon = ":white_check_mark:" if info["missing"] == 0 else ":red_circle:"
        board_lines.append(f"{icon} Room {r}: {info['safe']} safe, {info['missing']} missing")
        total_safe += info["safe"]
        total_missing += info["missing"]
    board_lines.append(f"\nTotals: *{total_safe} safe* · *{total_missing} missing*")

    if missing > 0:
        board_lines.append(f"\n_Initiate search for {missing} missing from Room {room}?_")

    text = header + "\n" + "\n".join(board_lines)

    bot_token = os.environ.get("SLACK_BOT_TOKEN", "")
    if not HAS_SLACK or not bot_token:
        return
    try:
        client = WebClient(token=bot_token)
        kwargs: dict[str, Any] = {
            "channel": channel_id,
            "text": text,
            "blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": text}}],
        }
        if thread_ts:
            kwargs["thread_ts"] = thread_ts
        client.chat_postMessage(**kwargs)
    except Exception as e:
        logger.error(f"Failed to post check-in response: {e}")


def _ago(iso_timestamp: str) -> str:
    """"2 min ago" rather than an ISO string or the word unknown.

    A responder reads this while moving. An absolute UTC timestamp forces them
    to do arithmetic; "unknown" tells them nothing at all.
    """
    if not iso_timestamp:
        return "time not recorded"
    try:
        from datetime import datetime, timezone

        seen = datetime.fromisoformat(iso_timestamp.replace("Z", "+00:00"))
        seconds = int((datetime.now(timezone.utc) - seen).total_seconds())
    except (ValueError, TypeError):
        return iso_timestamp

    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{seconds // 60} min ago"
    return f"{seconds // 3600}h {(seconds % 3600) // 60}m ago"


def _reported_rooms() -> dict[str, Any]:
    """Rooms that have reported, from every channel.

    `_room_checkins` is Slack-only; `room_board` is the shared store that SMS
    and WhatsApp write to. Reading just the former meant a teacher who reported
    her room by text was still listed as silent in the law-enforcement brief —
    the document responders act on fastest.
    """
    from src.core import room_board

    merged = dict(_room_checkins)
    merged.update(room_board.get(incident_state.get_active_incident_id()))
    return merged


def _handle_board_query(channel_id: str, thread_ts: str) -> None:
    """Show the full accountability board with all rooms."""
    import csv
    import io
    import pathlib

    seed_dir = pathlib.Path(__file__).resolve().parent.parent.parent / "data" / "seed"
    rooms_file = seed_dir / "rooms.csv"
    all_rooms: dict[str, str] = {}
    if rooms_file.exists():
        reader = csv.DictReader(io.StringIO(rooms_file.read_text()))
        for row in reader:
            rid = row.get("room_id", "")
            teacher = row.get("notes", "").split(" - ")[-1] if " - " in row.get("notes", "") else ""
            all_rooms[rid] = teacher

    total_safe = 0
    total_missing = 0
    reported_lines: list[str] = []
    silent_rooms: list[str] = []
    reported = _reported_rooms()

    for rid in sorted(all_rooms.keys()):
        if rid in _reported_rooms():
            info = reported[rid]
            total_safe += info["safe"]
            total_missing += info["missing"]
            if info["missing"] > 0:
                reported_lines.append(
                    f":red_circle: Room {rid} ({all_rooms[rid]}): "
                    f"{info['safe']} safe, *{info['missing']} MISSING* ({info['notes']})"
                )
            else:
                reported_lines.append(
                    f":white_check_mark: Room {rid} ({all_rooms[rid]}): {info['safe']}/{info['safe']} safe"
                )
        else:
            teacher = all_rooms[rid]
            cap = 25
            silent_rooms.append(f"{rid}")

    total_rooms = len(all_rooms)
    reported_count = len(_room_checkins)
    silent_count = len(silent_rooms)
    est_unaccounted = silent_count * 25

    lines = [
        f":clipboard: *BOARD — {reported_count} of {total_rooms} rooms reported*",
        "",
    ]
    lines.extend(reported_lines)

    if silent_rooms:
        lines.append(
            f":black_circle: {silent_count} rooms NO REPORT (~{est_unaccounted} students): "
            + ", ".join(silent_rooms)
        )

    lines.append("")
    lines.append(f"Totals: *{total_safe} safe* · *{total_missing} missing* · *~{est_unaccounted} unaccounted*")

    if silent_rooms:
        lines.append("")
        lines.append(f"_Chase order: {', '.join(silent_rooms[:5])} — teachers report by room now._")

    text = "\n".join(lines)

    bot_token = os.environ.get("SLACK_BOT_TOKEN", "")
    if not HAS_SLACK or not bot_token:
        return
    try:
        client = WebClient(token=bot_token)
        kwargs: dict[str, Any] = {
            "channel": channel_id,
            "text": text,
            "blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": text}}],
        }
        if thread_ts:
            kwargs["thread_ts"] = thread_ts
        client.chat_postMessage(**kwargs)
    except Exception as e:
        logger.error(f"Failed to post board response: {e}")


def _summarise_reason(reason: str) -> str:
    """Turn a chain of per-channel blockers into one readable phrase."""
    parts = [p.strip() for p in (reason or "").split(";") if p.strip()]
    if not parts:
        return "no channel available"

    short = []
    for part in parts:
        lowered = part.lower()
        if "no confirmed opt-in" in lowered:
            short.append("no SMS opt-in")
        elif "opted out" in lowered:
            short.append("opted out of SMS")
        elif "24h window" in lowered:
            short.append("no open WhatsApp session")
        elif "does not resolve" in lowered:
            short.append("Slack id does not resolve")
        elif "no slack user id" in lowered:
            short.append("no Slack id on file")
        elif "no phone number" in lowered:
            short.append("no phone on file")
        elif "no bot token" in lowered:
            short.append("Slack not configured")
        elif "transport off" in lowered:
            short.append("WhatsApp off")
        else:
            short.append(part)
    return ", ".join(dict.fromkeys(short))


def _group_by_reason(flagged: list[dict[str, Any]]) -> list[tuple[str, list[str]]]:
    """Largest group first — the systemic gap before the one-offs."""
    grouped: dict[str, list[str]] = {}
    for intent in flagged:
        grouped.setdefault(_summarise_reason(intent.get("reason", "")), []).append(
            intent.get("name", intent.get("person_id", "?")))
    return sorted(grouped.items(), key=lambda kv: (-len(kv[1]), kv[0]))


def _handle_reconciliation_tick(channel_id: str, user_id: str, thread_ts: str) -> None:
    """Advance the reconciliation loop one tick and report what it decided.

    Same authorisation as POST /incident/{id}/tick and the same fail-closed
    rule: this advances real accountability state and, with delivery on, is
    what pages people. An unconfigured IC list refuses rather than opening.
    """
    from src.core.agent_gateway import AUTHORIZED_IC_IDS, _load_authorized_ics
    from src.core import reconciliation_loop

    if not incident_state.is_active():
        _post_bot_message(channel_id, ":warning: No active incident to reconcile.",
                          thread_ts=thread_ts)
        return

    _load_authorized_ics()
    if not AUTHORIZED_IC_IDS:
        _post_bot_message(
            channel_id,
            ":no_entry: No incident commanders configured, so I will not advance "
            "incident state. Set `AUTHORIZED_IC_IDS`.", thread_ts=thread_ts)
        return
    if not any(hmac.compare_digest(user_id, ic) for ic in AUTHORIZED_IC_IDS):
        _post_bot_message(
            channel_id,
            ":no_entry: Only an incident commander can run reconciliation.",
            thread_ts=thread_ts)
        return

    incident_id = incident_state.get_active_incident_id()
    reconciliation_loop.ensure_running()

    # When the scheduler is running, asking reports the latest completed tick
    # rather than forcing an extra one. Advancing on every question would let
    # a commander's curiosity re-ping people ahead of schedule, and the answer
    # to "who hasn't answered" is a reading, not an action.
    latest = reconciliation_loop.last_result(incident_id)
    if reconciliation_loop.auto_tick_enabled() and latest:
        _post_bot_message(channel_id, _format_tick(latest, live=True),
                          thread_ts=thread_ts)
        return

    result = reconciliation_loop.run_tick(incident_id)
    _post_bot_message(channel_id, _format_tick(result), thread_ts=thread_ts)


def _format_tick(result: dict[str, Any], live: bool = False) -> str:
    """Render a tick as what it decided and, honestly, what it could not do."""
    from src.core import notify, reconciliation, reconciliation_loop as loop

    if result.get("skipped_reason"):
        return f":warning: Reconciliation skipped — {result['skipped_reason']}."

    intents = result.get("intents", [])
    by_action: dict[str, list[dict[str, Any]]] = {}
    for i in intents:
        by_action.setdefault(i["action"], []).append(i)

    header = f":arrows_counterclockwise: *RECONCILIATION — tick {result.get('tick')}*"
    subtitle = (f"_Evaluated all {result.get('evaluated')} on the roster. "
                f"Re-ping cap {reconciliation.attempt_cap()}._")
    if live:
        from src.core import incident_state as _st

        due = loop.seconds_until_next_tick(_st.get_active_incident_id())
        ran = _ago(result.get("at", ""))
        subtitle = (f"_Ran {ran} on its own"
                    + (f", next in {due}s" if due is not None else "")
                    + f". Evaluated all {result.get('evaluated')} on the roster. "
                    f"Re-ping cap {reconciliation.attempt_cap()}._")
    lines = [header, subtitle, ""]

    repings = by_action.get(loop.ACTION_REPING, [])
    if repings:
        lines.append(f":satellite_antenna: *Re-pinged {len(repings)}* "
                     "— each on the channel that reaches them:")
        for i in repings[:6]:
            lines.append(f"  - *{i['name']}* via {i['channel']} "
                         f"(`{i.get('outcome') or 'recorded'}`)")

    escalations = by_action.get(loop.ACTION_ESCALATE, [])
    if escalations:
        lines.append("")
        lines.append(f":arrow_up: *Escalated {len(escalations)} to a floor warden* "
                     "— repeated requests unanswered:")
        for i in escalations[:6]:
            lines.append(f"  - *{i['name']}* — {i.get('reason', '')}")

    flagged = by_action.get(loop.ACTION_FLAG_IC, [])
    if flagged:
        lines.append("")
        lines.append(f":telephone_receiver: *{len(flagged)} cannot be reached at all "
                     "— reach these by radio:*")
        # Grouped by reason. Thirty people with the same blocker rendered as
        # thirty near-identical sentences, each cut mid-word, which buries the
        # names an incident commander is actually going to read out.
        for reason, names in _group_by_reason(flagged):
            lines.append(f"  _{reason}_")
            # Every name, not a count. "and 25 more" is the incident
            # commander's problem restated as a number — these are the people
            # they have to raise on a radio, and they need to read them out.
            lines.append(f"    {', '.join(names)}")

    if not intents:
        lines.append(":white_check_mark: Nobody left to chase — everyone tracked "
                     "is accounted for.")

    if not notify.delivery_enabled():
        lines.append("")
        lines.append("_Delivery is switched off: these are decisions, not messages "
                     "that were sent._")

    # Sections emit their own spacer, so a tick where two of the three are
    # empty renders as a run of blank lines before the one that has content.
    tightened: list[str] = []
    for line in lines:
        if not line and (not tightened or not tightened[-1]):
            continue
        tightened.append(line)
    return "\n".join(tightened).rstrip()


def _handle_arrival_brief(channel_id: str, thread_ts: str) -> None:
    """Generate and post a Law Enforcement Arrival Brief for the active incident."""
    if not incident_state.is_active():
        _post_bot_message(channel_id, ":warning: No active incident.", thread_ts=thread_ts)
        return

    inc = incident_state.get_latest_incident()
    classification = inc.get("classification", {})
    location = inc.get("location", {})
    zone_id = location.get("zone_id", "") if isinstance(location, dict) else ""
    report_text = inc.get("report", "")
    accountability = compute_accountability_summary(incident_state.get_active_incident_id())
    # The most recent witness report, not a re-parse of the original message.
    # A teacher texting "he is headed towards the gym" is the freshest thing
    # anyone knows, and the brief was still quoting the opening report.
    from src.core import observations

    incident_id = incident_state.get_active_incident_id()
    threat_loc = (observations.latest_threat_location(incident_id)
                  or extract_threat_observation(report_text))
    threat_seen_at = ""
    for entry in reversed(observations.get(incident_id)):
        if entry.get("threat_location_reported"):
            threat_seen_at = entry.get("at", "")
            break
    if not threat_seen_at and threat_loc:
        # No witness report yet, so the position is the one in the opening
        # message and its time is the declaration. Rendering that as "reported
        # unknown" hid the freshest fact the brief had.
        track = observations.threat_track(incident_id)
        threat_seen_at = track[0]["at"] if track else ""

    brief = generate_arrival_brief(
        incident_id=incident_state.get_active_incident_id(),
        incident_type=classification.get("incident_type", "unknown"),
        severity=classification.get("severity", "unknown"),
        location=location.get("resolved_location", report_text) if isinstance(location, dict) else str(location),
        time_declared=classification.get("timestamp", ""),
        accountability=accountability,
        incident_zone=zone_id,
        facility_id=classification.get("facility_id", "jefferson"),
        reported_threat_location=threat_loc,
        threat_last_seen_time=threat_seen_at,
    )

    incident = brief["incident"]
    headcount = brief["headcount"]
    egress = brief["egress"]
    threat = brief.get("threat_observation")
    nearby = brief["nearby_services"]
    kb = KnowledgeBase.get()

    elapsed_min = incident_state.elapsed_minutes()

    lines = [
        f":shield: *LAW ENFORCEMENT ARRIVAL BRIEF — {incident_state.get_active_incident_id()}*",
        f":warning: *REQUIRES INCIDENT COMMANDER APPROVAL BEFORE SHARING*",
        "",
        f"*{brief['scope_notice']}*",
        "",
        f"*Incident:* `{incident['type'].upper()}` | *Severity:* `{incident['severity'].upper()}` | *Elapsed:* {elapsed_min} min",
        f"*Location:* {incident['location']}",
        f"*Zone:* {incident.get('incident_zone', '—')}",
    ]

    # ── Building overview ──
    facility = kb.get_facility("jefferson")
    floor_summary = brief.get("floor_summary", [])
    total_rooms = len(kb.rooms)
    total_personnel = len(kb.personnel)
    total_floors = len(floor_summary)
    if facility:
        lines.append("")
        lines.append(f":school: *Building:* {facility.get('name', 'Jefferson Elementary')} — {total_floors} floors, {total_rooms} rooms, {total_personnel} staff/students tracked")

    # ── Threat observation ──
    if threat:
        lines.append("")
        lines.append(f":rotating_light: *THREAT OBSERVATION:* `{threat['status']}`")
        lines.append(
            f"  Last known location: *{threat['last_reported_location']}*"
            f" — reported {_ago(threat.get('last_reported_time', ''))}")

        # Two sightings tell a responder which way it is moving, which is the
        # difference between arriving behind it and arriving in front of it.
        track = observations.threat_track(incident_state.get_active_incident_id())
        if len(track) > 1:
            trail = " → ".join(t["location"] for t in track)
            lines.append(f"  Reported movement: {trail}")
            for t in reversed(track[:-1]):
                who = t.get("reported_by") or t.get("source") or "unattributed"
                lines.append(f"    · {t['location']} — {_ago(t['at'])}, via {who}")
        lines.append(f"  _{threat['caveat']}_")

    # ── Headcount ──
    lines.append("")
    # Labelled, because the room totals below count a different population.
    # The brief carried "34 total | 0 accounted" directly above "48 safe" and
    # nothing on the page said one was staff and the other was students.
    lines.append(f"*Headcount (tracked staff roster):* {headcount['total']} total | "
                 f"{headcount['accounted']} accounted | {headcount['unaccounted']} unaccounted")
    if headcount.get("injured"):
        lines.append(f":ambulance: Injured: {headcount['injured']}")
    if headcount.get("need_help"):
        lines.append(f":warning: Need help: {headcount['need_help']}")

    # ── Room check-ins + silent rooms ──
    # Reads the merged board, not the Slack-only dict. This is the third copy
    # of the room logic in the file and the one that matters most: a teacher
    # who reported her room by WhatsApp was still listed as silent in the
    # document handed to police.
    board = _reported_rooms()
    all_rooms = {r["room_id"]: r for r in kb.rooms}
    silent_rooms = sorted(set(all_rooms.keys()) - set(board.keys()))

    if board:
        total_safe = sum(r["safe"] for r in board.values())
        total_missing = sum(r["missing"] for r in board.values())
        lines.append("")
        lines.append(f":clipboard: *Room Check-ins ({len(board)}/{len(all_rooms)} rooms reported):*")
        for room_id, info in sorted(board.items()):
            icon = ":white_check_mark:" if info["missing"] == 0 else ":red_circle:"
            room_data = all_rooms.get(room_id, {})
            teacher = room_data.get("notes", "")
            line = f"  {icon} Room {room_id}"
            if teacher:
                line += f" ({teacher})"
            line += f": {info['safe']} safe, {info['missing']} missing"
            if info.get("notes") and info["notes"] != f"{info['safe']} students are safe, {info['missing']} are missing":
                line += f" — {info['notes']}"
            lines.append(line)
        lines.append(f"  *Totals (room-reported occupants):* {total_safe} safe · "
                     f"{total_missing} missing")

    if silent_rooms:
        est_unaccounted = len(silent_rooms) * 25
        lines.append("")
        lines.append(f":black_circle: *SILENT ROOMS — NO REPORT ({len(silent_rooms)} rooms, ~{est_unaccounted} people):*")
        silent_in_threat_zone = []
        silent_other = []
        for rid in silent_rooms:
            room_data = all_rooms.get(rid, {})
            room_zone = room_data.get("zone_id", "")
            if room_zone == zone_id:
                silent_in_threat_zone.append(rid)
            else:
                silent_other.append(rid)
        if silent_in_threat_zone:
            zone_name = brief["incident"].get("incident_zone", zone_id)
            room_list = ", ".join(silent_in_threat_zone)
            lines.append(f"  :red_circle: *IN THREAT ZONE ({zone_name}):* {room_list}")
        if silent_other:
            room_list = ", ".join(silent_other)
            lines.append(f"  :grey_question: Other: {room_list}")

    # ── People needing assistance ──
    people = brief.get("people_needing_assistance", [])
    if people:
        lines.append("")
        lines.append(f":wheelchair: *People Needing Assistance ({len(people)}):*")
        for p in people:
            lines.append(f"  - *{p['name']}* — Room {p['last_known_location']}")

    # ── Floor wardens (on-site contacts for each floor) ──
    wardens = brief.get("floor_wardens", [])
    if wardens:
        lines.append("")
        lines.append(f":bust_in_silhouette: *Floor Wardens (on-site contacts):*")
        for w in wardens:
            lines.append(f"  - *{w['name']}* — Floor {w['floor']}, {w['location']}")

    # ── Egress ──
    lines.append("")
    if egress.get("blocked_routes"):
        lines.append(f":no_entry: *Blocked Routes:* {', '.join(egress['blocked_routes'])}")
    if egress.get("safe_routes"):
        lines.append(f":white_check_mark: *Safe Routes:* {', '.join(egress['safe_routes'])}")
    if egress.get("accessible_routes"):
        lines.append(f":wheelchair: *Accessible Routes:* {', '.join(egress['accessible_routes'])}")

    # ── Hazards ──
    hazards = brief.get("hazards", [])
    if hazards:
        lines.append("")
        lines.append(f":biohazard_sign: *Hazards:*")
        for h in hazards:
            lines.append(f"  - {h}")

    # ── On-site resources ──
    resources = brief.get("on_site_resources", [])
    if resources:
        lines.append("")
        lines.append(f":package: *On-Site Resources:*")
        for r in resources:
            lines.append(f"  - {r}")

    # ── Command & services ──
    lines.append("")
    lines.append(f"*Assembly:* {brief['assembly_point']}")
    lines.append(f"*Command Contact:* {brief['command_contact']}")

    if nearby.get("nearest_police_station", {}).get("name"):
        p = nearby["nearest_police_station"]
        lines.append(f":police_car: *Police:* {p['name']} (ETA {p['eta_minutes']}min) `{p['phone']}`")
    if nearby.get("nearest_fire_station", {}).get("name"):
        f = nearby["nearest_fire_station"]
        lines.append(f":fire_engine: *Fire:* {f['name']} (ETA {f['eta_minutes']}min) `{f['phone']}`")
    if nearby.get("nearest_hospital", {}).get("name"):
        h = nearby["nearest_hospital"]
        lines.append(f":hospital: *Hospital:* {h['name']} (ETA {h['eta_minutes']}min)")

    lines.append("")
    lines.append(f":telephone_receiver: *{brief['emergency_notice']}*")

    text = "\n".join(lines)
    _post_bot_message(channel_id, text, thread_ts=thread_ts)


def _run_followup_query(channel_id: str, user_id: str, query: str, thread_ts: str = "") -> None:
    """Route a question to Gemini with a single direct API call."""
    logger.info(f"Follow-up query for {incident_state.get_active_incident_id()}: {query[:80]}")

    checkin = _parse_checkin(query)
    if checkin:
        _room_checkins[checkin["room"]] = checkin
        # Shared store, so a teacher reporting from WhatsApp and an incident
        # commander reading the board in Slack see the same rooms.
        room_board.record(incident_state.get_active_incident_id(), checkin, source="slack")
        logger.info(f"Check-in logged: Room {checkin['room']} — {checkin['safe']} safe, {checkin['missing']} missing")
        _handle_checkin_direct(channel_id, user_id, checkin, thread_ts)
        return

    query_lower = query.lower()
    if any(kw in query_lower for kw in ["unaccounted", "board", "accountability", "who is still", "status board"]):
        _handle_board_query(channel_id, thread_ts)
        return

    if any(kw in query_lower for kw in ["arrival brief", "law enforcement", "handoff", "le brief", "handoff package"]):
        _handle_arrival_brief(channel_id, thread_ts)
        return

    if any(kw in query_lower for kw in
           ["chase", "who hasn't answered", "who hasnt answered", "reconcil",
            "run the loop", "tick", "follow up on the silent"]):
        _handle_reconciliation_tick(channel_id, user_id, thread_ts)
        return

    incident_ctx = ""
    if incident_state.is_active():
        inc = incident_state.get_latest_incident()
        classification = inc.get("classification", {})
        location = inc.get("location", {})
        location_name = location.get("primary_zone", "") if isinstance(location, dict) else str(location)
        zone_id = location.get("zone_id", "") if isinstance(location, dict) else ""
        report_text = inc.get("report", "")

        ctx_parts = [
            f"ACTIVE INCIDENT: {incident_state.get_active_incident_id()}",
            f"Type: {classification.get('incident_type', 'unknown')}",
            f"Severity: {classification.get('severity', 'unknown')}",
            f"Location: {location_name}",
            f"Zone: {zone_id}" if zone_id else "",
            f"Original report: {report_text}" if report_text else "",
        ]

        blocked = inc.get("blocked_zones", {})
        blocked_routes = blocked.get("blocked_routes", [])
        if blocked_routes:
            names = [r.get("name", "") for r in blocked_routes]
            ctx_parts.append(f"BLOCKED ROUTES (run through threat zone — DO NOT USE): {', '.join(names)}")

        safe = inc.get("safe_routes", {})
        safe_routes = safe.get("routes", [])
        if safe_routes:
            route_strs = [f"{r.get('name', '')} → {r.get('to_exit', '')}" for r in safe_routes]
            ctx_parts.append(f"SAFE ROUTES (away from threat): {'; '.join(route_strs)}")

        if zone_id and not blocked_routes:
            ctx_parts.append(
                f"ROUTE SAFETY: The incident is in zone '{zone_id}'. "
                f"Any route passing through '{zone_id}' is UNSAFE. "
                f"Routes from OTHER zones that do not pass through '{zone_id}' are safe."
            )

        acct = inc.get("accountability", {})
        mobility = acct.get("mobility_needs", [])
        if mobility:
            mob_strs = [f"{p.get('name', '')} (Room {p.get('location', '')})" for p in mobility]
            ctx_parts.append(f"MOBILITY NEEDS: {', '.join(mob_strs)}")

        incident_ctx = "\n".join(p for p in ctx_parts if p)

    facility_data = _load_facility_data()

    prompt = (
        "You are CrisisMesh, an emergency coordination bot in Slack. "
        "This is a LIVE CRISIS. Every second counts.\n\n"
        "CRITICAL RULE: Answer ONLY the specific question asked. Nothing else.\n"
        "- If asked for a phone number → return ONLY the name and phone number.\n"
        "- If asked about routes → return ONLY the relevant routes.\n"
        "- If asked about accountability → return ONLY the check-in board.\n"
        "- NEVER add evacuation routes, contact lists, or safety info unless specifically asked.\n"
        "- NEVER dump all data. Pick only what answers the question.\n\n"
        "FORMAT RULES:\n"
        "• Put phone numbers in backticks: `615-555-0138`\n"
        "• Bold names: *Mrs. Davis* — *Room 104*\n"
        "• Use → for routes: East hallway → Door 3\n"
        "• Use emoji: :white_check_mark: safe, :red_circle: danger/missing, :warning: caution\n"
        "• End with ONE actionable follow-up in _italics_ if relevant\n"
        "• NO paragraphs. NO headers. NO disclaimers.\n"
        "• Keep the entire response under 6 lines.\n\n"
        f"{incident_ctx}\n\n"
        f"FACILITY DATA:\n{facility_data}\n\n"
        f"{_format_checkin_board()}\n\n"
        f"Question: {query}"
    )

    try:
        from google.genai import Client
        project = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
        location = os.environ.get("GOOGLE_CLOUD_REGION", "us-central1")
        client = Client(vertexai=True, project=project, location=location)
        response = client.models.generate_content(
            model="gemini-2.5-flash-lite",
            contents=prompt,
        )
        final_text = response.text or ""
    except Exception as e:
        logger.error(f"Follow-up query failed: {e}")
        _post_bot_message(channel_id, ":warning: Something went wrong. Please try again.", thread_ts=thread_ts)
        return

    if not final_text:
        _post_bot_message(channel_id, ":warning: I couldn't generate a response. Please rephrase your question.", thread_ts=thread_ts)
        return

    bot_token = os.environ.get("SLACK_BOT_TOKEN", "")
    if not HAS_SLACK or not bot_token:
        return

    text_clean = _clean_markdown_for_slack(final_text)

    blocks: list[dict[str, Any]] = []
    for chunk in _split_text(text_clean, 2900):
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": chunk},
        })

    if len(blocks) > 50:
        blocks = blocks[:50]

    try:
        client = WebClient(token=bot_token)
        kwargs: dict[str, Any] = {
            "channel": channel_id,
            "text": f"CrisisMesh — {incident_state.get_active_incident_id() or 'query'}",
            "blocks": blocks,
        }
        if thread_ts:
            kwargs["thread_ts"] = thread_ts
        client.chat_postMessage(**kwargs)
        logger.info(f"Follow-up response posted for {incident_state.get_active_incident_id()}")
    except Exception as e:
        logger.error(f"Failed to post follow-up response: {e}")


VALID_CSV_FILES = {
    "assembly_points.csv", "emergency_resources.csv", "evacuation_routes.csv",
    "facility.csv", "nearby_services.csv", "personnel.csv", "rooms.csv", "zones.csv",
}

_knowledge_base_counts: dict[str, int] = {}


def _handle_file_shared(event: dict[str, Any]) -> None:
    """Handle file uploads — if CSV, download and update seed data."""
    global _facility_data_cache
    file_id = event.get("file_id", "")
    if not file_id:
        return

    bot_token = os.environ.get("SLACK_BOT_TOKEN", "")
    if not HAS_SLACK or not bot_token:
        return

    try:
        client = WebClient(token=bot_token)
        info = client.files_info(file=file_id)
        file_data = info.get("file", {})
        filename = file_data.get("name", "")
        filetype = file_data.get("filetype", "")
        channel_id = ""
        channels = file_data.get("channels", [])
        if channels:
            channel_id = channels[0]

        if filetype != "csv":
            return

        url_private = file_data.get("url_private", "")
        if not url_private:
            return

        import urllib.request
        req = urllib.request.Request(url_private)
        req.add_header("Authorization", f"Bearer {bot_token}")
        with urllib.request.urlopen(req) as resp:
            csv_content = resp.read().decode("utf-8")

        import pathlib
        seed_dir = pathlib.Path(__file__).resolve().parent.parent.parent / "data" / "seed"
        target = seed_dir / filename
        target.write_text(csv_content)

        _facility_data_cache = ""

        KnowledgeBase.reset()
        init_knowledge_base(str(seed_dir))

        row_count = len(csv_content.strip().split("\n")) - 1
        label = filename.replace(".csv", "").replace("_", " ").title()
        _knowledge_base_counts[label] = row_count
        logger.info(f"CSV uploaded: {filename} ({row_count} rows)")

        if channel_id:
            summary_lines = [f"- {k}: {v}" for k, v in sorted(_knowledge_base_counts.items())]
            summary = "\n".join(summary_lines)
            _post_bot_message(
                channel_id,
                f":white_check_mark: File `{filename}` loaded successfully!\n\n"
                f"{filename.replace('.csv', '')}: {row_count} rows loaded\n\n"
                f"*Knowledge Base Summary:*\n{summary}",
            )
    except Exception as e:
        logger.error(f"File upload handling failed: {e}")


def _run_mention_pipeline(channel_id: str, user_id: str, text: str) -> None:
    """Run the incident pipeline from an @mention (background thread)."""

    result = run_incident_pipeline(text, source="slack")
    if result.get("blocked"):
        _post_bot_message(
            channel_id,
            f":no_entry: *Blocked by content safety:* {result.get('reason', '')}",
        )
        return

    incident_state.attach_origin(declared_by=user_id, origin_channel=channel_id)

    _post_bot_message(
        channel_id,
        (
            f":rotating_light: *Incident Report Received*\n"
            f"> {text}\n\n"
            f"Reported by <@{user_id}>. CrisisMesh agent fleet is activating.\n"
            f"Incident ID: `{result.get('incident_id', '')}`\n"
            ":telephone_receiver: *If this is a life-threatening emergency, call 911 immediately.*"
        ),
    )

    _post_slack_results(channel_id, result)


def _enforced(text: str, surface: str) -> str:
    """Strip any movement-policy contradiction before this text leaves.

    The critic acts here rather than filing a verdict: a violation that is only
    logged is the same defect as an `escalate` that notifies nobody. Runs on
    the current incident, and is a no-op when none is active.
    """
    from src.core import incident_state, movement_policy

    record = incident_state.get_latest_incident()
    if not record:
        return text
    incident_type = (record.get("classification", {}) or {}).get("incident_type", "")
    assembly = (record.get("assembly", {}) or {}).get("name", "")
    cleaned, violation = movement_policy.enforce(
        incident_type, text, assembly_name=assembly, surface=surface,
    )
    if violation:
        _record_policy_violation(violation)
    return cleaned


def _record_policy_violation(violation: Any) -> None:
    """Trace the block so it is visible, having already acted on it."""
    try:
        from src.core.observability import Tracer
        from src.core import incident_state

        trace = Tracer.get().get_trace(incident_state.get_active_incident_id())
        if trace:
            span = trace.start_span("movement_policy_violation", "critic")
            span.set_attribute("surface", violation.surface)
            span.set_attribute("incident_type", violation.incident_type)
            span.set_attribute("detail", violation.detail)
            span.end()
    except Exception as exc:  # noqa: BLE001 - never let tracing break a send
        logger.error(f"Could not trace policy violation: {exc}")


SLACK_TEXT_LIMIT = 3000


def _split_for_slack(text: str, limit: int = SLACK_TEXT_LIMIT) -> list[str]:
    """Break a long message on line boundaries, never mid-word.

    Slack truncates a text block past ~3000 characters, which is how the
    arrival brief came to end "East Wing F2it)" — a document handed to
    responders, cut off mid-sentence with no indication that anything was
    missing. Splitting on newlines keeps every line whole and every part
    readable on its own.
    """
    if len(text) <= limit:
        return [text]

    parts: list[str] = []
    current: list[str] = []
    size = 0
    for line in text.split("\n"):
        # A single line longer than the limit is hard-wrapped; nothing else can
        # be done with it, but it is the only case where a break lands
        # mid-line.
        while len(line) > limit:
            if current:
                parts.append("\n".join(current))
                current, size = [], 0
            parts.append(line[:limit])
            line = line[limit:]
        if size + len(line) + 1 > limit and current:
            parts.append("\n".join(current))
            current, size = [], 0
        current.append(line)
        size += len(line) + 1
    if current:
        parts.append("\n".join(current))
    return parts


def _post_bot_message(channel_id: str, text: str, thread_ts: str = "") -> None:
    """Post a message as the bot, splitting anything past Slack's text limit."""
    bot_token = os.environ.get("SLACK_BOT_TOKEN", "")
    if not HAS_SLACK or not bot_token:
        logger.info("SLACK_BOT_TOKEN not set — skipping bot message")
        return

    parts = _split_for_slack(_enforced(text, surface="slack_bot_message"))
    if len(parts) > 1:
        total = len(parts)
        parts = [f"{p}\n\n_(part {i + 1} of {total})_" for i, p in enumerate(parts)]
        for part in parts:
            _post_one(channel_id, part, thread_ts)
        return

    try:
        _post_one(channel_id, parts[0], thread_ts)
    except Exception as e:
        logger.error(f"Failed to post bot message: {e}")


def _post_one(channel_id: str, text: str, thread_ts: str = "") -> None:
    """Send one already-sized, already-enforced chunk."""
    bot_token = os.environ.get("SLACK_BOT_TOKEN", "")
    if not HAS_SLACK or not bot_token:
        return
    try:
        client = WebClient(token=bot_token)
        kwargs: dict[str, Any] = {"channel": channel_id, "text": text}
        if thread_ts:
            kwargs["thread_ts"] = thread_ts
        client.chat_postMessage(**kwargs)
    except Exception as e:
        logger.error(f"Failed to post bot message: {e}")


# ── Playbook formatting ──


def format_playbook_message(playbook_key: str) -> str:
    """Format a playbook as a Slack message."""
    playbook = PLAYBOOKS.get(playbook_key, PLAYBOOKS["generic"])

    lines = [f"*{playbook['title']}*\n"]

    lines.append("*Immediate Actions:*")
    for i, action in enumerate(playbook["immediate_actions"], 1):
        lines.append(f">{i}. {action}")

    lines.append("\n*Roles Needed:*")
    for role in playbook["roles"]:
        lines.append(f">*{role['role']}* — {role['resp']}")

    lines.append("\n*Resources:*")
    for resource in playbook["resources"]:
        lines.append(f">- {resource}")

    lines.append(
        "\n:telephone_receiver: *CrisisMesh coordinates alongside 911 — it never replaces emergency services.*"
    )

    return "\n".join(lines)


# ── Block Kit formatting ──


def _assembly_line(incident_type: str, assembly_name: str) -> str:
    """Delegates to the single movement policy every surface consumes."""
    from src.core import movement_policy

    return movement_policy.assembly_line(incident_type, assembly_name)


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
    nearby_list = result.get("nearby_service", {}).get("services", [])
    lessons_list = result.get("prior_lessons", {}).get("lessons", [])
    acct = result.get("accountability", {})

    assembly_name = assembly_list[0]["name"] if assembly_list else "Athletic Field"
    nearby_label_map = {
        "police_station": "Nearest Police",
        "fire_station": "Nearest Fire Station",
        "hospital": "Nearest Hospital",
    }
    nearby_type = result.get("nearby_service_type", "fire_station")
    nearby_label = nearby_label_map.get(nearby_type, "Nearest Service")
    nearby_info = (
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
                    f"{_assembly_line(c['incident_type'], assembly_name)}\n"
                    f"*{nearby_label}:* {nearby_info}"
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
        if not checkin_policy.can_accept():
            checkin_policy.log_refusal("slack-bolt", status, user_id)
            await client.chat_postEphemeral(
                channel=body["channel_id"],
                user=user_id,
                text=f":warning: {checkin_policy.refusal_message(status)}",
            )
            return

        incident_id = incident_state.get_active_incident_id()
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

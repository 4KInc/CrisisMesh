"""Cloud Run HTTP server — exposes CrisisMesh APIs for incident management.

Endpoints:
  POST /incident                — deterministic incident pipeline (fast, no Gemini)
  POST /incident/agentic        — Gemini-driven ADK Runner pipeline (model-driven delegation)
  POST /incident/agentic/stream — SSE streaming variant of the agentic pipeline
  POST /checkin                 — process a check-in
  POST /slack/commands          — Slack slash commands (Events API mode)
  POST /slack/events            — Slack event subscriptions (reaction_added, etc.)
  POST /sms                     — Twilio inbound SMS webhook
  POST /sms/optin               — SMS consent capture (web opt-in form)
  GET  /sms-optin               — SMS opt-in page (A2P 10DLC)
  GET  /sms-terms               — SMS terms & conditions (A2P 10DLC)
  GET  /privacy                 — privacy policy (A2P 10DLC)
  GET  /whatsapp                 — WhatsApp webhook verification
  POST /whatsapp                 — WhatsApp inbound webhook (Meta Cloud API)
  POST /whatsapp/twilio          — WhatsApp inbound webhook (Twilio-hosted)
  POST /incident/{id}/tick      — single-step reconciliation (IC-scoped)
  POST /incident/{id}/intents   — decisions the loop has recorded
  POST /incident/{id}/resolve   — end the active incident (any channel)
  POST /incident/{id}/approve   — approve a pending gated action
  POST /incident/{id}/deny      — deny a pending gated action
  GET  /incident/{id}/arrival-brief — law enforcement arrival brief
  GET  /incident/{id}           — get incident status + accountability
  GET  /incident/latest         — latest incident (for console real-time binding)
  GET  /incident/{id}/observations — witness reports attached to an incident
  GET  /notify/last              — result of the most recent fan-out
  GET  /registry                — view agent registry
  GET  /trace/{id}              — get observability trace
  GET  /audit/{id}              — export audit bundle
  GET  /health                  — health check
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, parse_qsl, urlparse

from src.config.agent_registry import AGENT_REGISTRY
from src.core.agent_gateway import AgentGateway
from src.core.content_scanner import ContentScanner
from src.core.event_bus import EventBus
from src.core.knowledge_base import KnowledgeBase, init_knowledge_base
from src.core.memory_bank import MemoryBank, init_memory_bank
from src.core.observability import Tracer, export_audit_bundle
from src.agents.accountability.tools import (
    compute_accountability_summary,
    process_checkin,
    read_roster,
    send_checkin_request,
)
from src.agents.intake.tools import classify_incident, extract_location, select_playbook
from src.agents.safety_intel.tools import (
    find_assembly_point,
    find_blocked_zones,
    find_nearby_services,
    find_safe_routes,
    find_zone_info,
    locate_resource,
)
from src.agents.sitrep.tools import (
    extract_threat_observation,
    generate_arrival_brief,
    generate_responder_card,
    generate_sitrep,
)
from src.agents.learning.tools import find_similar_incidents, store_lesson
from src.core.tactical_reasoning import (
    apply_safety_backstop,
    build_provenance_record,
    get_tactical_context,
    strip_origin_from_payload,
    validate_routing_directives,
)
from src.core import (
    incident_resolve,
    incident_state,
    notify,
    observations,
    reconciliation_store,
)
from src.services.slack_transport import (
    _publish_declared,
    dispatch_slash_command,
    dispatch_slack_event,
    verify_slack_signature,
)
from src.services.sms_transport import (
    can_send_sms,
    handle_inbound_sms,
    has_twilio_credentials,
    public_url,
    send_sms,
    twiml_response,
    verify_twilio_signature,
)
from src.services.sms_consent import (
    allow_optin_attempt,
    normalize_phone,
    record_optin,
)
from src.services.whatsapp_transport import (
    extract_messages,
    whatsapp_mode,
    handle_inbound_message,
    has_whatsapp_credentials,
    send_reply_async,
    verify_webhook_challenge,
    verify_webhook_signature,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

# Initialize on import
init_knowledge_base()
init_memory_bank()
# Attach the fan-out. Without this the bus has no subscribers at all and an
# incident declared on one channel reaches nobody on the others.
notify.subscribe()
# Pull the active incident back after a restart. Reconciliation state is not
# eagerly restored: the first tick rebuilds it from the surviving incident's
# roster, which makes a restart between declare and first tick correct without
# a repair path rather than a window to guard.
try:
    incident_state.rehydrate()
    # A container replaced mid-incident comes back with the incident restored
    # from Firestore and no timer, so reconciliation would silently stop.
    from src.core import reconciliation_loop as _loop
    _loop.ensure_running()
except Exception as _exc:  # noqa: BLE001
    logger.error(f"Incident rehydrate failed at startup: {_exc}")


def _json_response(handler: BaseHTTPRequestHandler, data: Any, status: int = 200) -> None:
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.end_headers()
    handler.wfile.write(json.dumps(data, default=str).encode())


def _read_body(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length", 0))
    if length == 0:
        return {}
    return json.loads(handler.rfile.read(length))


def _read_raw_body(handler: BaseHTTPRequestHandler) -> str:
    length = int(handler.headers.get("Content-Length", 0))
    if length == 0:
        return ""
    return handler.rfile.read(length).decode("utf-8")


def _parse_form(raw: str) -> dict[str, str]:
    parsed = parse_qs(raw, keep_blank_values=True)
    return {k: v[0] for k, v in parsed.items()}


def _parse_form_pairs(raw: str) -> list[tuple[str, str]]:
    """Form fields in arrival order, keeping repeats — what Twilio signed."""
    return parse_qsl(raw, keep_blank_values=True)


async def _run_agentic(report: str) -> dict[str, Any]:
    """Run the incident report through the ADK Runner → Coordinator → Gemini.

    Returns the delegation log and final response.
    """
    from google.adk.apps import App
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai.types import Content, Part
    from src.agents.coordinator.agent import coordinator_agent
    from src.core.agent_gateway import GatewayPlugin

    app = App(name="crisismesh", root_agent=coordinator_agent, plugins=[GatewayPlugin()])

    session_service = InMemorySessionService()
    session = await session_service.create_session(
        app_name="crisismesh",
        user_id="commander",
    )

    runner = Runner(
        app=app,
        session_service=session_service,
    )

    user_message = Content(role="user", parts=[Part(text=report)])

    event_log: list[dict] = []
    final_text = ""
    incident_type = ""
    blocked_zones: list[str] = []
    tactical_origin: dict[str, Any] = {}

    async for event in runner.run_async(
        user_id="commander",
        session_id=session.id,
        new_message=user_message,
    ):
        author = getattr(event, "author", "")
        entry: dict = {"author": author, "timestamp": datetime.now(timezone.utc).isoformat()}

        if event.actions and event.actions.transfer_to_agent:
            entry["type"] = "delegation"
            entry["target_agent"] = event.actions.transfer_to_agent
            event_log.append(entry)

        elif event.content and event.content.parts:
            for part in event.content.parts:
                if hasattr(part, "function_call") and part.function_call:
                    fc = part.function_call
                    fc_name = fc.name if hasattr(fc, "name") else str(fc)
                    fc_args = _safe_args(fc.args if hasattr(fc, "args") else {})
                    entry = {
                        "author": author,
                        "type": "tool_call",
                        "tool_name": fc_name,
                        "tool_args": fc_args,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                    event_log.append(entry)
                    if fc_name == "classify_incident" and isinstance(fc_args, dict):
                        incident_type = fc_args.get("incident_type", incident_type)
                    if fc_name == "find_blocked_zones" and isinstance(fc_args, dict):
                        zone = fc_args.get("incident_zone", "")
                        if zone:
                            blocked_zones.append(zone)

                elif hasattr(part, "function_response") and part.function_response:
                    fr = part.function_response
                    fr_name = fr.name if hasattr(fr, "name") else ""
                    entry = {
                        "author": author,
                        "type": "tool_result",
                        "tool_name": fr_name,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                    event_log.append(entry)
                    if fr_name == "classify_incident" and hasattr(fr, "response"):
                        resp = fr.response if isinstance(fr.response, dict) else {}
                        incident_type = resp.get("incident_type", incident_type)
                    if fr_name == "get_tactical_context" and hasattr(fr, "response"):
                        resp = fr.response if isinstance(fr.response, dict) else {}
                        tactical_origin = resp

                elif hasattr(part, "text") and part.text and event.is_final_response():
                    final_text = part.text

    if final_text:
        final_text = apply_safety_backstop(final_text, incident_type)
        final_text = validate_routing_directives(final_text, blocked_zones)

    delegations = [e for e in event_log if e.get("type") == "delegation"]
    tool_calls_list = [e for e in event_log if e.get("type") == "tool_call"]

    result: dict[str, Any] = {
        "model": "gemini-3.5-flash",
        "backend": "vertex_ai",
        "orchestration": "model_driven",
        "total_events": len(event_log),
        "delegations": len(delegations),
        "delegation_path": [e["target_agent"] for e in delegations],
        "tool_calls": len(tool_calls_list),
        "tools_invoked": [e["tool_name"] for e in tool_calls_list],
        "event_log": event_log,
        "final_response": final_text,
    }

    return strip_origin_from_payload(result)


def _sse_write(wfile, data: dict) -> None:
    wfile.write(f"data: {json.dumps(data, default=str)}\n\n".encode())
    wfile.flush()


async def _stream_agentic_sse(wfile, report: str) -> None:
    """Stream ADK Runner events as SSE lines."""
    from google.adk.apps import App
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai.types import Content, Part
    from src.agents.coordinator.agent import coordinator_agent
    from src.core.agent_gateway import GatewayPlugin

    app = App(name="crisismesh", root_agent=coordinator_agent, plugins=[GatewayPlugin()])

    session_service = InMemorySessionService()
    session = await session_service.create_session(
        app_name="crisismesh",
        user_id="commander",
    )
    runner = Runner(
        app=app,
        session_service=session_service,
    )
    user_message = Content(role="user", parts=[Part(text=report)])

    delegations = 0
    tool_calls = 0
    total_events = 0
    incident_type = ""
    blocked_zones: list[str] = []

    async for event in runner.run_async(
        user_id="commander",
        session_id=session.id,
        new_message=user_message,
    ):
        author = getattr(event, "author", "")
        ts = datetime.now(timezone.utc).isoformat()

        if event.actions and event.actions.transfer_to_agent:
            delegations += 1
            total_events += 1
            _sse_write(wfile, {
                "type": "delegation",
                "author": author,
                "target_agent": event.actions.transfer_to_agent,
                "timestamp": ts,
            })

        elif event.content and event.content.parts:
            for part in event.content.parts:
                if hasattr(part, "function_call") and part.function_call:
                    fc = part.function_call
                    fc_name = fc.name if hasattr(fc, "name") else str(fc)
                    fc_args = _safe_args(fc.args if hasattr(fc, "args") else {})
                    tool_calls += 1
                    total_events += 1
                    _sse_write(wfile, strip_origin_from_payload({
                        "type": "tool_call",
                        "author": author,
                        "tool_name": fc_name,
                        "tool_args": fc_args,
                        "timestamp": ts,
                    }))
                    if fc_name == "find_blocked_zones" and isinstance(fc_args, dict):
                        zone = fc_args.get("incident_zone", "")
                        if zone:
                            blocked_zones.append(zone)
                elif hasattr(part, "function_response") and part.function_response:
                    fr = part.function_response
                    fr_name = fr.name if hasattr(fr, "name") else ""
                    total_events += 1
                    _sse_write(wfile, strip_origin_from_payload({
                        "type": "tool_result",
                        "author": author,
                        "tool_name": fr_name,
                        "timestamp": ts,
                    }))
                    if fr_name == "classify_incident" and hasattr(fr, "response"):
                        resp = fr.response if isinstance(fr.response, dict) else {}
                        incident_type = resp.get("incident_type", incident_type)
                elif hasattr(part, "text") and part.text and event.is_final_response():
                    final_text = part.text
                    final_text = apply_safety_backstop(final_text, incident_type)
                    final_text = validate_routing_directives(final_text, blocked_zones)
                    _sse_write(wfile, {
                        "type": "final_response",
                        "text": final_text,
                        "timestamp": ts,
                    })

    _sse_write(wfile, {
        "type": "summary",
        "model": "gemini-3.5-flash",
        "delegations": delegations,
        "tool_calls": tool_calls,
        "total_events": total_events,
    })
    _sse_write(wfile, {"type": "done"})


def _run_agentic_background(report: str) -> None:
    """Fire the agentic pipeline in a background thread. Updates latest incident."""
    try:
        loop = asyncio.new_event_loop()
        result = loop.run_until_complete(_run_agentic(report))
        loop.close()
        result["pipeline"] = "agentic"
        incident_state.set_latest_incident(result, source="web")
        logger.info(
            f"Agentic pipeline complete: {result.get('delegations', 0)} delegations, "
            f"{result.get('tool_calls', 0)} tool calls"
        )
    except Exception as e:
        logger.error(f"Background agentic pipeline failed: {e}")


def _safe_args(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _safe_args(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_safe_args(v) for v in obj]
    if isinstance(obj, (str, int, float, bool, type(None))):
        return obj
    return str(obj)


class CrisisMeshHandler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        # Serve UI
        if path in ("", "/ui"):
            self._serve_static("index.html")
            return

        # A2P 10DLC compliance pages — must stay publicly reachable with no
        # authentication so carrier vetting can review them.
        if path == "/privacy":
            self._serve_static("privacy.html")
            return

        if path in ("/sms-terms", "/terms"):
            self._serve_static("sms-terms.html")
            return

        if path in ("/sms-optin", "/sms-opt-in"):
            self._serve_static("sms-optin.html")
            return

        if path == "/health":
            kb = KnowledgeBase.get()
            bus = EventBus.get()
            _json_response(self, {
                "status": "ok",
                "service": "crisismesh",
                "model": "gemini-3.5-flash",
                "event_bus_backend": bus.backend,
                "scanner_backend": ContentScanner.get().backend,
                # Named so the managed-vs-local question is answerable from
                # outside the process, and so a fallback is visible rather than
                # silent — the facade degrades quietly by design.
                "memory_backend": MemoryBank.get().backend,
                "knowledge_base": {
                    "personnel": len(kb.personnel),
                    "zones": len(kb.zones),
                    "rooms": len(kb.rooms),
                },
            })

        elif path.endswith("/observations") and path.startswith("/incident/"):
            parts = path.split("/")
            if len(parts) == 4:
                entries = observations.get(parts[2])
                _json_response(self, {
                    "incident_id": parts[2],
                    "count": len(entries),
                    "latest_threat_location": observations.latest_threat_location(parts[2]),
                    "observations": entries,
                })
            else:
                _json_response(self, {"error": "Not found"}, 404)

        elif path == "/notify/last":
            _json_response(self, notify.get_last_result() or {"kind": "", "notified": 0})

        elif path == "/registry":
            registry = {
                aid: {
                    "name": entry.name,
                    "version": entry.version,
                    "description": entry.description,
                    "data_class": entry.data_class,
                    "approved_tools": entry.approved_tools,
                    "denied_tools": entry.denied_tools,
                    "purpose": entry.purpose,
                }
                for aid, entry in AGENT_REGISTRY.items()
            }
            _json_response(self, {"agents": registry, "total": len(registry)})

        elif path.startswith("/trace/"):
            incident_id = path.split("/trace/")[1]
            tracer = Tracer.get()
            trace = tracer.get_trace(incident_id)
            if trace:
                _json_response(self, trace.to_dict())
            else:
                _json_response(self, {"error": "Trace not found"}, 404)

        elif path == "/traces":
            tracer = Tracer.get()
            _json_response(self, {"traces": tracer.list_traces()})

        elif path.startswith("/audit/"):
            incident_id = path.split("/audit/")[1]
            bundle = export_audit_bundle(incident_id)
            _json_response(self, bundle)

        elif path == "/gateway/summary":
            gateway = AgentGateway.get()
            _json_response(self, gateway.get_policy_summary())

        elif path == "/gateway/denials":
            gateway = AgentGateway.get()
            _json_response(self, {"denials": gateway.get_deny_log()})

        elif path == "/gateway/pending":
            gateway = AgentGateway.get()
            pending = gateway.get_pending_actions()
            _json_response(self, {
                "pending": [a.to_dict() for a in pending if a.state == "pending"],
            })

        elif path == "/incident/latest":
            latest = incident_state.get_latest_incident()
            if latest:
                _json_response(self, strip_origin_from_payload(latest))
            else:
                _json_response(self, {"incident_id": None}, 200)

        elif path == "/whatsapp":
            query = parse_qs(parsed.query)
            mode = query.get("hub.mode", [""])[0]
            token = query.get("hub.verify_token", [""])[0]
            challenge = query.get("hub.challenge", [""])[0]

            result = verify_webhook_challenge(mode, token, challenge)
            if result:
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(result.encode())
            else:
                self.send_response(403)
                self.end_headers()

        elif path.endswith("/arrival-brief") and path.startswith("/incident/"):
            parts = path.split("/")
            if len(parts) == 4:
                incident_id = parts[2]
                latest = incident_state.get_latest_incident()
                if not latest or latest.get("incident_id") != incident_id:
                    _json_response(self, {"error": "Incident not found"}, 404)
                    return
                classification = latest.get("classification", {})
                location = latest.get("location", {})
                report_text = latest.get("report", "")
                accountability_summary = compute_accountability_summary(incident_id)
                threat_loc = extract_threat_observation(report_text)
                brief = generate_arrival_brief(
                    incident_id=incident_id,
                    incident_type=classification.get("incident_type", "unknown"),
                    severity=classification.get("severity", "unknown"),
                    location=location.get("resolved_location", report_text),
                    time_declared=latest.get("classification", {}).get("timestamp", ""),
                    accountability=accountability_summary,
                    incident_zone=location.get("zone_id", ""),
                    facility_id=latest.get("classification", {}).get("facility_id", "jefferson"),
                    reported_threat_location=threat_loc,
                )
                _json_response(self, brief)
            else:
                _json_response(self, {"error": "Not found"}, 404)

        elif path.startswith("/incident/"):
            incident_id = path.split("/incident/")[1]
            if incident_id.startswith("agentic"):
                _json_response(self, {"error": "Use POST for this endpoint"}, 405)
            else:
                summary = compute_accountability_summary(incident_id)
                _json_response(self, summary)

        else:
            _json_response(self, {"error": "Not found"}, 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path == "/incident/agentic/stream":
            body = _read_body(self)
            report = body.get("report", "")

            if not report:
                _json_response(self, {"error": "Missing 'report' field"}, 400)
                return

            scan = ContentScanner.get().scan_message(report)
            if scan["blocked"]:
                _json_response(self, {
                    "blocked": True,
                    "reason": scan["reason"],
                    "policy": scan["policy"],
                    "quarantined_text": scan.get("quarantined_text", ""),
                }, 403)
                return

            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            try:
                loop = asyncio.new_event_loop()
                loop.run_until_complete(_stream_agentic_sse(self.wfile, report))
                loop.close()
            except Exception as e:
                logger.exception("Streaming endpoint error")
                _sse_write(self.wfile, {"type": "error", "message": str(e)})

        elif path == "/incident/agentic":
            body = _read_body(self)
            report = body.get("report", "")

            if not report:
                _json_response(self, {"error": "Missing 'report' field"}, 400)
                return

            # Content scan first
            scan = ContentScanner.get().scan_message(report)
            if scan["blocked"]:
                _json_response(self, {
                    "blocked": True,
                    "reason": scan["reason"],
                    "policy": scan["policy"],
                    "quarantined_text": scan.get("quarantined_text", ""),
                }, 403)
                return

            # Run through ADK Runner → Gemini
            try:
                loop = asyncio.new_event_loop()
                result = loop.run_until_complete(_run_agentic(report))
                loop.close()
                _json_response(self, result, 200)
            except Exception as e:
                logger.exception("Agentic endpoint error")
                _json_response(self, {
                    "error": str(e),
                    "hint": "Ensure GOOGLE_GENAI_USE_VERTEXAI=TRUE and GOOGLE_CLOUD_PROJECT are set",
                }, 500)

        elif path == "/slack/commands":
            raw = _read_raw_body(self)
            signing_secret = os.environ.get("SLACK_SIGNING_SECRET", "")
            ts = self.headers.get("X-Slack-Request-Timestamp", "")
            sig = self.headers.get("X-Slack-Signature", "")

            # Fail closed. This used to be `if signing_secret and not verify(...)`,
            # so an unset secret skipped verification entirely and the endpoint
            # accepted anything — and a forged /incident declares an incident and
            # pages real phones. A missing secret is a misconfiguration, not
            # permission to trust the caller. Matches /sms, which refuses without
            # its credentials rather than accepting unverified requests.
            if not signing_secret:
                logger.error(
                    "SLACK_SIGNING_SECRET is not set — refusing Slack requests "
                    "rather than accepting them unverified"
                )
                _json_response(self, {"error": "Slack verification not configured"}, 503)
                return

            if not verify_slack_signature(signing_secret, ts, raw, sig):
                _json_response(self, {"error": "Invalid signature"}, 401)
                return

            form = _parse_form(raw)
            command = form.get("command", "")
            result = dispatch_slash_command(command, form)
            _json_response(self, result)

        elif path == "/slack/events":
            raw = _read_raw_body(self)
            signing_secret = os.environ.get("SLACK_SIGNING_SECRET", "")
            ts = self.headers.get("X-Slack-Request-Timestamp", "")
            sig = self.headers.get("X-Slack-Signature", "")

            # Fail closed. This used to be `if signing_secret and not verify(...)`,
            # so an unset secret skipped verification entirely and the endpoint
            # accepted anything — and a forged /incident declares an incident and
            # pages real phones. A missing secret is a misconfiguration, not
            # permission to trust the caller. Matches /sms, which refuses without
            # its credentials rather than accepting unverified requests.
            if not signing_secret:
                logger.error(
                    "SLACK_SIGNING_SECRET is not set — refusing Slack requests "
                    "rather than accepting them unverified"
                )
                _json_response(self, {"error": "Slack verification not configured"}, 503)
                return

            if not verify_slack_signature(signing_secret, ts, raw, sig):
                _json_response(self, {"error": "Invalid signature"}, 401)
                return

            payload = json.loads(raw) if raw else {}
            result = dispatch_slack_event(payload)
            if result:
                _json_response(self, result)
            else:
                _json_response(self, {"ok": True})

        elif path == "/sms/optin":
            body = _read_body(self)
            phone_raw = str(body.get("phone", "")).strip()
            name = str(body.get("name", "")).strip()[:120]
            organization = str(body.get("organization", "")).strip()[:120]
            consent = body.get("consent")

            if consent is not True:
                _json_response(self, {
                    "error": "Consent checkbox must be actively selected to sign up.",
                }, 400)
                return

            phone = normalize_phone(phone_raw)
            if len(phone) < 11 or not phone[1:].isdigit():
                _json_response(self, {"error": "Enter a valid mobile number."}, 400)
                return

            if not name or not organization:
                _json_response(self, {
                    "error": "Name and organization are required.",
                }, 400)
                return

            client_ip = (
                self.headers.get("X-Forwarded-For", "").split(",")[0].strip()
                or getattr(self, "client_address", ("", 0))[0]
            )

            if not allow_optin_attempt(phone, client_ip):
                _json_response(self, {
                    "error": "Too many sign-up attempts. Please try again later.",
                }, 429)
                return

            record_optin(
                phone=phone,
                name=name,
                organization=organization,
                source="web_form",
                ip=client_ip,
                user_agent=self.headers.get("User-Agent", ""),
            )

            confirmation = (
                "CrisisMesh: you signed up for emergency coordination alerts for "
                f"{organization}. Reply YES to confirm, STOP to cancel, HELP for help. "
                "Msg frequency varies by incident. Msg & data rates may apply."
            )
            delivery = send_sms(phone, confirmation) if can_send_sms() else {
                "delivered": False,
                "detail": "Outbound SMS not configured on this deployment.",
            }

            _json_response(self, {
                "ok": True,
                "status": "pending",
                "confirmation_sent": bool(delivery.get("delivered")),
                "message": (
                    "Check your phone and reply YES to confirm your enrollment."
                    if delivery.get("delivered")
                    else "Sign-up recorded. Text START to the CrisisMesh number to "
                         "confirm your enrollment."
                ),
            })

        elif path == "/sms":
            if not has_twilio_credentials():
                self.send_response(503)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(
                    b"SMS transport not configured. "
                    b"Set TWILIO_AUTH_TOKEN and TWILIO_PHONE_NUMBER env vars."
                )
                return

            raw = _read_raw_body(self)
            # Verify against the pairs exactly as they arrived — rebuilding them
            # from a collapsed dict is the classic way this check silently
            # starts passing everything.
            pairs = _parse_form_pairs(raw)
            form = dict(pairs)
            auth_token = os.environ.get("TWILIO_AUTH_TOKEN", "")
            twilio_sig = self.headers.get("X-Twilio-Signature", "")
            request_url = public_url(self.headers, self.path)

            if not verify_twilio_signature(auth_token, request_url, pairs, twilio_sig):
                _json_response(self, {"error": "Invalid signature"}, 401)
                return

            result = handle_inbound_sms(
                from_number=form.get("From", ""),
                body=form.get("Body", ""),
                request_url=request_url,
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/xml")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(result["twiml"].encode())

        elif path == "/whatsapp/twilio":
            # Twilio-hosted WhatsApp. Same webhook shape as /sms — form-encoded,
            # signed with X-Twilio-Signature — so it is verified with the same
            # check, never with Meta's. Only reachable in twilio mode.
            if whatsapp_mode() != "twilio":
                self.send_response(503)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(
                    b"Twilio-hosted WhatsApp is not the configured provider. "
                    b"Set CRISISMESH_WHATSAPP_MODE=twilio."
                )
                return

            if not has_whatsapp_credentials():
                self.send_response(503)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(
                    b"Twilio WhatsApp not configured. Set TWILIO_ACCOUNT_SID, "
                    b"TWILIO_WHATSAPP_FROM and an API key or TWILIO_AUTH_TOKEN."
                )
                return

            raw = _read_raw_body(self)
            pairs = _parse_form_pairs(raw)
            form = dict(pairs)
            auth_token = os.environ.get("TWILIO_AUTH_TOKEN", "")
            twilio_sig = self.headers.get("X-Twilio-Signature", "")
            request_url = public_url(self.headers, self.path)

            if not verify_twilio_signature(auth_token, request_url, pairs, twilio_sig):
                _json_response(self, {"error": "Invalid signature"}, 401)
                return

            # Twilio prefixes both addresses on the WhatsApp channel.
            from_number = form.get("From", "").removeprefix("whatsapp:")

            # Acknowledge first, work after. Twilio gives a webhook 15 seconds;
            # the pipeline behind this can spend that on one model call, and a
            # webhook that overruns is not a late reply — it is error 11200 and
            # a lost message. The reply goes back over the REST API, which is
            # how every other message here is already sent.
            from src.services.whatsapp_transport import process_inbound_async
            process_inbound_async(from_number, form.get("Body", ""))

            self.send_response(200)
            self.send_header("Content-Type", "text/xml")
            self.end_headers()
            self.wfile.write(twiml_response("").encode())

        elif path == "/whatsapp":
            if whatsapp_mode() != "meta":
                self.send_response(503)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(
                    b"Meta Cloud API is not the configured provider. "
                    b"Set CRISISMESH_WHATSAPP_MODE=meta."
                )
                return

            if not has_whatsapp_credentials():
                self.send_response(503)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(
                    b"WhatsApp transport not configured. "
                    b"Set WHATSAPP_ACCESS_TOKEN and WHATSAPP_PHONE_NUMBER_ID env vars."
                )
                return

            raw = _read_raw_body(self)
            app_secret = os.environ.get("WHATSAPP_APP_SECRET", "")
            hub_sig = self.headers.get("X-Hub-Signature-256", "")

            if app_secret and hub_sig:
                if not verify_webhook_signature(app_secret, raw, hub_sig):
                    _json_response(self, {"error": "Invalid signature"}, 401)
                    return

            payload = json.loads(raw) if raw else {}
            messages = extract_messages(payload)

            for msg in messages:
                result = handle_inbound_message(
                    from_number=msg["from"],
                    body=msg["body"],
                )
                send_reply_async(msg["from"], result["reply"])

            _json_response(self, {"status": "ok"})

        elif path == "/incident":
            body = _read_body(self)
            report = body.get("report", "")
            facility_id = body.get("facility_id", "jefferson")

            if not report:
                _json_response(self, {"error": "Missing 'report' field"}, 400)
                return

            armor = ContentScanner.get().scan_message(report)
            if armor["blocked"]:
                _json_response(self, {
                    "blocked": True,
                    "reason": armor["reason"],
                    "policy": armor["policy"],
                    "quarantined_text": armor.get("quarantined_text", ""),
                }, 403)
                return

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
            inc_type = classification.get("incident_type", "")
            _SVC = {"active_shooter": "police_station", "active_threat": "police_station", "medical": "hospital"}
            nearby_svc_type = _SVC.get(inc_type, "fire_station")
            nearby = find_nearby_services(nearby_svc_type)
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

            result_data: dict[str, Any] = {
                "incident_id": incident_id,
                "report": report,
                "classification": classification,
                "location": location,
                "playbook": playbook,
                "blocked_zones": blocked,
                "safe_routes": routes,
                "assembly_point": assembly,
                "nearby_service": nearby,
                "nearby_service_type": nearby_svc_type,
                "accountability": {
                    "personnel_tracked": send_result["requests_sent"],
                    "mobility_needs": roster.get("mobility_needs", []),
                },
                "prior_lessons": lessons,
                "trace_id": trace.trace_id,
                "tactical_provenance": provenance,
            }
            result_data["pipeline"] = "deterministic"
            incident_state.set_latest_incident(result_data, source="web")
            # Same helper the other channels use: published only once the state
            # is consistent, and carrying enough for the fan-out to pick the
            # right message. Publishing early with a thin payload would have
            # sent a console-declared lockdown the evacuation wording.
            _publish_declared(incident_id, classification, location)
            _json_response(self, strip_origin_from_payload(result_data), 201)

            import threading
            threading.Thread(
                target=_run_agentic_background,
                args=(report,),
                daemon=True,
            ).start()

        elif path == "/checkin":
            body = _read_body(self)
            incident_id = body.get("incident_id", "")
            person_id = body.get("person_id", "")
            status = body.get("status", "safe")

            if not incident_id or not person_id:
                _json_response(self, {"error": "Missing incident_id or person_id"}, 400)
                return

            result = process_checkin(incident_id, person_id, status)
            _json_response(self, result)

        elif path == "/gateway/check":
            body = _read_body(self)
            agent_id = body.get("agent_id", "")
            tool_name = body.get("tool_name", "")
            incident_id = body.get("incident_id", "")

            gateway = AgentGateway.get()
            loop = asyncio.new_event_loop()
            decision = loop.run_until_complete(
                gateway.check_tool_call(agent_id, tool_name, incident_id=incident_id)
            )
            loop.close()
            _json_response(self, decision.to_dict())

        elif path == "/armor/scan":
            body = _read_body(self)
            text = body.get("text", "")
            result = ContentScanner.get().scan_message(text)
            _json_response(self, result)

        elif path.endswith("/tick") and path.startswith("/incident/"):
            # Single-step the reconciliation loop and report what it decided.
            #
            # Synchronous on purpose. Kicking a background task and returning
            # would leave the tick's execution dependent on Cloud Run not
            # reclaiming the thread after the response flushes — a property
            # currently asserted only in YAML (cpu-throttling: false), never
            # observed. Running inside the request sidesteps that question for
            # the observation phase and returns the decisions as JSON rather
            # than burying them in logs.
            #
            # IC-scoped and fails closed: this advances real accountability
            # state, and once delivery is wired it is what triggers real pages.
            # Unlike the approval gate, an unconfigured IC list refuses rather
            # than opening.
            parts = path.split("/")
            if len(parts) != 4:
                _json_response(self, {"error": "Not found"}, 404)
                return

            from src.core.agent_gateway import AUTHORIZED_IC_IDS, _load_authorized_ics
            from src.core import reconciliation_loop

            _load_authorized_ics()
            if not AUTHORIZED_IC_IDS:
                _json_response(self, {
                    "error": "No incident commanders configured; refusing to "
                             "advance incident state. Set AUTHORIZED_IC_IDS.",
                    "code": "no_authorized_ics",
                }, 503)
                return

            body = _read_body(self)
            ic_id = str(body.get("ic_id", "")).strip()
            if not ic_id:
                _json_response(self, {
                    "error": "Missing ic_id — advancing an incident has to be "
                             "attributable.",
                }, 400)
                return

            if not any(hmac.compare_digest(ic_id, known) for known in AUTHORIZED_IC_IDS):
                logger.error(f"Rejected /tick from unauthorized id {ic_id!r}")
                _json_response(self, {"error": "Not an authorized incident commander"}, 403)
                return

            result = reconciliation_loop.run_tick(parts[2])
            result["ic_id"] = ic_id
            result["store"] = reconciliation_store.backend_name()
            _json_response(self, result)

        elif path.endswith("/intents") and path.startswith("/incident/"):
            from src.core import reconciliation_loop

            parts = path.split("/")
            if len(parts) != 4:
                _json_response(self, {"error": "Not found"}, 404)
                return
            recorded = reconciliation_loop.intents(parts[2])
            _json_response(self, {"incident_id": parts[2], "count": len(recorded),
                                  "intents": recorded})

        elif path.endswith("/resolve") and path.startswith("/incident/"):
            # Ending an incident is destructive: it stops the coordination
            # everyone is working from and fires an all-clear to every reachable
            # person. Two guards, because this service is public.
            parts = path.split("/")
            if len(parts) != 4:
                _json_response(self, {"error": "Not found"}, 404)
                return

            body = _read_body(self)
            resolve_token = os.environ.get("CRISISMESH_RESOLVE_TOKEN", "").strip()
            if resolve_token:
                presented = (
                    self.headers.get("X-CrisisMesh-Token", "")
                    or str(body.get("token", ""))
                ).strip()
                if not hmac.compare_digest(presented, resolve_token):
                    _json_response(self, {"error": "Not authorized to resolve"}, 403)
                    return

            resolved_by = str(body.get("resolved_by", "")).strip()
            if not resolved_by:
                _json_response(self, {
                    "error": "Missing resolved_by — a resolution has to be attributable.",
                }, 400)
                return

            try:
                report = incident_resolve.resolve(
                    incident_id=parts[2],
                    resolved_by=resolved_by,
                    channel="http",
                )
            except incident_resolve.ResolveRefused as refused:
                _json_response(
                    self,
                    {"error": refused.reason, "code": refused.code},
                    404 if refused.code == "no_active_incident" else 409,
                )
                return

            _json_response(self, report)

        elif path.endswith("/approve") and path.startswith("/incident/"):
            parts = path.split("/")
            if len(parts) == 4:
                body = _read_body(self)
                action_id = body.get("action_id", "")
                approver_id = body.get("approver_id", "")
                if not action_id or not approver_id:
                    _json_response(self, {"error": "Missing action_id or approver_id"}, 400)
                    return
                gateway = AgentGateway.get()
                loop = asyncio.new_event_loop()
                result = loop.run_until_complete(
                    gateway.approve_action(action_id, approver_id)
                )
                loop.close()
                status = result.pop("status", 200)
                if isinstance(status, int):
                    _json_response(self, result, status)
                else:
                    _json_response(self, {"status": status, **result})
            else:
                _json_response(self, {"error": "Not found"}, 404)

        elif path.endswith("/deny") and path.startswith("/incident/"):
            parts = path.split("/")
            if len(parts) == 4:
                body = _read_body(self)
                action_id = body.get("action_id", "")
                approver_id = body.get("approver_id", "")
                if not action_id or not approver_id:
                    _json_response(self, {"error": "Missing action_id or approver_id"}, 400)
                    return
                gateway = AgentGateway.get()
                loop = asyncio.new_event_loop()
                result = loop.run_until_complete(
                    gateway.deny_action(action_id, approver_id)
                )
                loop.close()
                status = result.pop("status", 200)
                if isinstance(status, int):
                    _json_response(self, result, status)
                else:
                    _json_response(self, {"status": status, **result})
            else:
                _json_response(self, {"error": "Not found"}, 404)

        else:
            _json_response(self, {"error": "Not found"}, 404)

    def _serve_static(self, filename: str) -> None:
        static_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "static",
        )
        filepath = os.path.join(static_dir, filename)
        if not os.path.isfile(filepath):
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        ct = "text/html" if filename.endswith(".html") else "application/octet-stream"
        self.send_header("Content-Type", ct)
        self.end_headers()
        with open(filepath, "rb") as f:
            self.wfile.write(f.read())

    def log_message(self, format, *args):
        pass


def run_server(host: str = "0.0.0.0", port: int = 8080) -> None:
    server = ThreadingHTTPServer((host, port), CrisisMeshHandler)
    print(f"CrisisMesh server running on {host}:{port}")
    server.serve_forever()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    run_server(port=port)

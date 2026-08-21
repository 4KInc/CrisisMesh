"""Cloud Run HTTP server — exposes CrisisMesh APIs for incident management.

Endpoints:
  POST /incident                — deterministic incident pipeline (fast, no Gemini)
  POST /incident/agentic        — Gemini-driven ADK Runner pipeline (model-driven delegation)
  POST /incident/agentic/stream — SSE streaming variant of the agentic pipeline
  POST /checkin                 — process a check-in
  POST /slack/commands          — Slack slash commands (Events API mode)
  POST /slack/events            — Slack event subscriptions (reaction_added, etc.)
  GET  /whatsapp                 — WhatsApp webhook verification
  POST /whatsapp                 — WhatsApp inbound message webhook
  GET  /incident/{id}           — get incident status + accountability
  GET  /incident/latest         — latest incident (for console real-time binding)
  GET  /registry                — view agent registry
  GET  /trace/{id}              — get observability trace
  GET  /audit/{id}              — export audit bundle
  GET  /health                  — health check
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Any
from urllib.parse import parse_qs, urlparse

from src.config.agent_registry import AGENT_REGISTRY
from src.core.agent_gateway import AgentGateway
from src.core.content_scanner import ContentScanner
from src.core.event_bus import EventBus, create_event
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
from src.agents.sitrep.tools import generate_responder_card, generate_sitrep
from src.agents.learning.tools import find_similar_incidents, store_lesson
from src.models.events import EventType
from src.services.slack_transport import (
    dispatch_slash_command,
    dispatch_slack_event,
    get_latest_incident,
    set_latest_incident,
    verify_slack_signature,
)
from src.services.whatsapp_transport import (
    extract_messages,
    handle_inbound_message,
    has_whatsapp_credentials,
    send_reply_async,
    verify_webhook_challenge,
    verify_webhook_signature,
)

logger = logging.getLogger(__name__)

# Initialize on import
init_knowledge_base()
init_memory_bank()


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


async def _run_agentic(report: str) -> dict[str, Any]:
    """Run the incident report through the ADK Runner → Coordinator → Gemini.

    Returns the delegation log and final response.
    """
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai.types import Content, Part
    from src.agents.coordinator.agent import coordinator_agent

    session_service = InMemorySessionService()
    session = await session_service.create_session(
        app_name="crisismesh",
        user_id="commander",
    )

    runner = Runner(
        agent=coordinator_agent,
        app_name="crisismesh",
        session_service=session_service,
    )

    user_message = Content(role="user", parts=[Part(text=report)])

    event_log: list[dict] = []
    final_text = ""

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
                    entry = {
                        "author": author,
                        "type": "tool_call",
                        "tool_name": fc.name if hasattr(fc, "name") else str(fc),
                        "tool_args": _safe_args(fc.args if hasattr(fc, "args") else {}),
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                    event_log.append(entry)

                elif hasattr(part, "function_response") and part.function_response:
                    entry = {
                        "author": author,
                        "type": "tool_result",
                        "tool_name": part.function_response.name if hasattr(part.function_response, "name") else "",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                    event_log.append(entry)

                elif hasattr(part, "text") and part.text and event.is_final_response():
                    final_text = part.text

    delegations = [e for e in event_log if e.get("type") == "delegation"]
    tool_calls = [e for e in event_log if e.get("type") == "tool_call"]

    return {
        "model": "gemini-3.5-flash",
        "backend": "vertex_ai",
        "orchestration": "model_driven",
        "total_events": len(event_log),
        "delegations": len(delegations),
        "delegation_path": [e["target_agent"] for e in delegations],
        "tool_calls": len(tool_calls),
        "tools_invoked": [e["tool_name"] for e in tool_calls],
        "event_log": event_log,
        "final_response": final_text,
    }


def _sse_write(wfile, data: dict) -> None:
    wfile.write(f"data: {json.dumps(data, default=str)}\n\n".encode())
    wfile.flush()


async def _stream_agentic_sse(wfile, report: str) -> None:
    """Stream ADK Runner events as SSE lines."""
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai.types import Content, Part
    from src.agents.coordinator.agent import coordinator_agent

    session_service = InMemorySessionService()
    session = await session_service.create_session(
        app_name="crisismesh",
        user_id="commander",
    )
    runner = Runner(
        agent=coordinator_agent,
        app_name="crisismesh",
        session_service=session_service,
    )
    user_message = Content(role="user", parts=[Part(text=report)])

    delegations = 0
    tool_calls = 0
    total_events = 0

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
                    tool_calls += 1
                    total_events += 1
                    _sse_write(wfile, {
                        "type": "tool_call",
                        "author": author,
                        "tool_name": fc.name if hasattr(fc, "name") else str(fc),
                        "tool_args": _safe_args(fc.args if hasattr(fc, "args") else {}),
                        "timestamp": ts,
                    })
                elif hasattr(part, "function_response") and part.function_response:
                    total_events += 1
                    _sse_write(wfile, {
                        "type": "tool_result",
                        "author": author,
                        "tool_name": part.function_response.name if hasattr(part.function_response, "name") else "",
                        "timestamp": ts,
                    })
                elif hasattr(part, "text") and part.text and event.is_final_response():
                    _sse_write(wfile, {
                        "type": "final_response",
                        "text": part.text,
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

        if path == "/health":
            kb = KnowledgeBase.get()
            bus = EventBus.get()
            _json_response(self, {
                "status": "ok",
                "service": "crisismesh",
                "model": "gemini-3.5-flash",
                "event_bus_backend": bus.backend,
                "scanner_backend": ContentScanner.get().backend,
                "knowledge_base": {
                    "personnel": len(kb.personnel),
                    "zones": len(kb.zones),
                    "rooms": len(kb.rooms),
                },
            })

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

        elif path == "/incident/latest":
            latest = get_latest_incident()
            if latest:
                _json_response(self, latest)
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

            if signing_secret and not verify_slack_signature(signing_secret, ts, raw, sig):
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

            if signing_secret and not verify_slack_signature(signing_secret, ts, raw, sig):
                _json_response(self, {"error": "Invalid signature"}, 401)
                return

            payload = json.loads(raw) if raw else {}
            result = dispatch_slack_event(payload)
            if result:
                _json_response(self, result)
            else:
                _json_response(self, {"ok": True})

        elif path == "/whatsapp":
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

            result_data = {
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
            }
            set_latest_incident(result_data, source="web")
            _json_response(self, result_data, 201)

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
    server = HTTPServer((host, port), CrisisMeshHandler)
    print(f"CrisisMesh server running on {host}:{port}")
    server.serve_forever()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    run_server(port=port)

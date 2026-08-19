"""Cloud Run HTTP server — exposes CrisisMesh APIs for incident management.

Endpoints:
  POST /incident          — declare a new incident
  POST /checkin            — process a check-in
  GET  /incident/{id}      — get incident status + accountability
  GET  /registry           — view agent registry
  GET  /trace/{id}         — get observability trace
  GET  /audit/{id}         — export audit bundle
  GET  /health             — health check
"""

from __future__ import annotations

import json
import os
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Any
from urllib.parse import urlparse, parse_qs

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

        if path == "/health":
            _json_response(self, {"status": "ok", "service": "crisismesh"})

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

        elif path.startswith("/incident/"):
            incident_id = path.split("/incident/")[1]
            summary = compute_accountability_summary(incident_id)
            _json_response(self, summary)

        else:
            _json_response(self, {"error": "Not found"}, 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path == "/incident":
            body = _read_body(self)
            report = body.get("report", "")
            facility_id = body.get("facility_id", "jefferson")

            if not report:
                _json_response(self, {"error": "Missing 'report' field"}, 400)
                return

            # Model Armor — scan incoming message
            armor = ContentScanner.get().scan_message(report)
            if armor["blocked"]:
                _json_response(self, {
                    "blocked": True,
                    "reason": armor["reason"],
                    "policy": armor["policy"],
                    "quarantined_text": armor.get("quarantined_text", ""),
                }, 403)
                return

            # Run intake pipeline
            classification = classify_incident(report)
            location = extract_location(report)
            playbook = select_playbook(classification["incident_type"])
            incident_id = classification["incident_id"]

            # Start observability trace
            tracer = Tracer.get()
            trace = tracer.start_trace(incident_id)
            root = trace.start_span("incident_lifecycle", "coordinator")
            root.set_attribute("incident_type", classification["incident_type"])
            root.set_attribute("severity", classification["severity"])

            # Intake span
            intake_span = trace.start_span("intake_classification", "intake", root.span_id)
            intake_span.set_attribute("incident_type", classification["incident_type"])
            intake_span.set_attribute("location_resolved", location.get("resolved", False))
            intake_span.end()

            # Emit events
            bus = EventBus.get()
            import asyncio
            loop = asyncio.new_event_loop()
            loop.run_until_complete(bus.publish(create_event(
                EventType.INCIDENT_DECLARED, incident_id, "coordinator",
                {"type": classification["incident_type"], "severity": classification["severity"]},
            )))

            # Safety intel
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

            # Accountability
            acct_span = trace.start_span("accountability", "accountability", root.span_id)
            roster = read_roster(facility_id)
            send_result = send_checkin_request(incident_id, facility_id=facility_id)
            acct_span.set_attribute("personnel_tracked", send_result["requests_sent"])
            acct_span.end()

            # Learning — check past lessons
            learn_span = trace.start_span("lesson_recall", "learning", root.span_id)
            lessons = find_similar_incidents(classification["incident_type"], facility_id)
            learn_span.set_attribute("lessons_found", lessons["lessons_found"])
            learn_span.end()

            loop.close()

            _json_response(self, {
                "incident_id": incident_id,
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
            }, 201)

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
            import asyncio
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

    def log_message(self, format, *args):
        # Suppress default logging for cleaner output
        pass


def run_server(host: str = "0.0.0.0", port: int = 8080) -> None:
    server = HTTPServer((host, port), CrisisMeshHandler)
    print(f"CrisisMesh server running on {host}:{port}")
    server.serve_forever()


# For Cloud Run / uvicorn compatibility, expose a simple WSGI-like entry
app = None  # Placeholder — Cloud Run uses `run_server()` directly

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    run_server(port=port)

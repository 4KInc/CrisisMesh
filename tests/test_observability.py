"""Tests for observability tracing and audit export."""

import pytest

from src.core.agent_gateway import AgentGateway
from src.core.event_bus import EventBus, create_event
from src.core.observability import Span, Trace, Tracer, export_audit_bundle
from src.models.events import EventType


@pytest.fixture(autouse=True)
def fresh_state():
    Tracer.reset()
    EventBus.reset()
    AgentGateway.reset()
    yield
    Tracer.reset()
    EventBus.reset()
    AgentGateway.reset()


class TestSpan:
    def test_span_lifecycle(self):
        span = Span("trace-1", "test_span", "coordinator")
        assert span.status == "in_progress"
        assert span.duration_ms is None

        span.set_attribute("key", "value")
        span.add_event("something_happened", {"detail": "yes"})
        span.end()

        assert span.status == "ok"
        assert span.duration_ms is not None
        assert span.duration_ms >= 0
        assert span.attributes["key"] == "value"
        assert len(span.events) == 1

    def test_span_to_dict(self):
        span = Span("trace-1", "test", "intake")
        span.end("error")
        d = span.to_dict()
        assert d["trace_id"] == "trace-1"
        assert d["status"] == "error"
        assert d["agent_id"] == "intake"


class TestTrace:
    def test_trace_with_spans(self):
        trace = Trace("trace-1", "FIRE-2026-001")
        root = trace.start_span("incident_lifecycle", "coordinator")
        intake = trace.start_span("intake", "intake", root.span_id)
        safety = trace.start_span("safety_intel", "safety_intel", root.span_id)
        acct = trace.start_span("accountability", "accountability", root.span_id)

        intake.end()
        safety.end()
        acct.end()
        root.end()

        assert len(trace.spans) == 4
        assert trace.root_span == root

    def test_trace_to_dict(self):
        trace = Trace("trace-1", "INC-001")
        root = trace.start_span("root", "coordinator")
        root.end()

        d = trace.to_dict()
        assert d["trace_id"] == "trace-1"
        assert d["incident_id"] == "INC-001"
        assert d["total_spans"] == 1

    def test_span_tree(self):
        trace = Trace("trace-1", "INC-001")
        root = trace.start_span("root", "coordinator")
        child1 = trace.start_span("child1", "intake", root.span_id)
        child2 = trace.start_span("child2", "safety", root.span_id)
        grandchild = trace.start_span("grandchild", "safety", child2.span_id)

        tree = trace.get_span_tree()
        assert len(tree) == 4
        assert tree[0]["depth"] == 0  # root
        assert tree[1]["depth"] == 1  # child1
        assert tree[2]["depth"] == 1  # child2
        assert tree[3]["depth"] == 2  # grandchild


class TestTracer:
    def test_start_trace(self):
        tracer = Tracer.get()
        trace = tracer.start_trace("FIRE-2026-001")
        assert trace.incident_id == "FIRE-2026-001"

    def test_get_trace(self):
        tracer = Tracer.get()
        tracer.start_trace("INC-001")
        t = tracer.get_trace("INC-001")
        assert t is not None
        assert t.incident_id == "INC-001"

    def test_start_span_auto_creates_trace(self):
        tracer = Tracer.get()
        span = tracer.start_span("INC-002", "test_span", "coordinator")
        assert span is not None
        trace = tracer.get_trace("INC-002")
        assert trace is not None

    def test_list_traces(self):
        tracer = Tracer.get()
        tracer.start_trace("INC-001")
        tracer.start_trace("INC-002")
        traces = tracer.list_traces()
        assert len(traces) == 2


class TestEndToEndTrace:
    """Simulates a complete incident trace — the demo proof beat."""

    def test_full_incident_trace(self):
        tracer = Tracer.get()
        trace = tracer.start_trace("FIRE-2026-001")

        # Root span — incident lifecycle
        root = trace.start_span("incident_lifecycle", "coordinator")
        root.set_attribute("incident_type", "fire")
        root.set_attribute("severity", "high")
        root.set_attribute("facility_id", "jefferson")

        # Intake classification
        intake = trace.start_span("intake_classification", "intake", root.span_id)
        intake.set_attribute("incident_type", "fire")
        intake.set_attribute("severity", "high")
        intake.set_attribute("location_zone", "west-wing-f2")
        intake.add_event("classified", {"playbook": "playbook-fire-v1"})
        intake.end()

        # Safety intel
        safety = trace.start_span("safety_resource_intel", "safety_intel", root.span_id)
        safety.set_attribute("blocked_routes", 2)
        safety.set_attribute("safe_routes", 3)
        safety.set_attribute("aeds_found", 3)
        safety.add_event("gas_shutoff_identified", {"location": "Room 215 east wall"})
        safety.end()

        # Accountability
        acct = trace.start_span("accountability_tracking", "accountability", root.span_id)
        acct.set_attribute("total_personnel", 34)
        acct.set_attribute("checkins_sent", 34)
        acct.add_event("mobility_flagged", {"count": 2, "people": ["Mrs. Davis", "Mrs. Thompson"]})
        acct.end()

        # SITREP generation
        sitrep = trace.start_span("sitrep_generation", "sitrep", root.span_id)
        sitrep.set_attribute("sitrep_type", "IC_SITREP")
        sitrep.add_event("responder_card_generated", {"requires_approval": True})
        sitrep.end()

        # Learning — prior lesson recall
        learn = trace.start_span("lesson_recall", "learning", root.span_id)
        learn.set_attribute("lessons_found", 3)
        learn.add_event("lesson_surfaced", {
            "title": "Floor 2 west stairwell bottleneck during fire drill",
            "source": "FIRE-2025-DRILL-001",
        })
        learn.end()

        # AAR
        aar = trace.start_span("after_action_review", "learning", root.span_id)
        aar.set_attribute("accountability_rate", 94.1)
        aar.add_event("lesson_stored", {"title": "New lesson from this incident"})
        aar.end()

        root.end()

        # Verify the complete trace
        d = trace.to_dict()
        assert d["total_spans"] == 7
        assert d["status"] == "ok"
        assert d["duration_ms"] is not None

        tree = trace.get_span_tree()
        assert tree[0]["name"] == "incident_lifecycle"
        assert tree[0]["depth"] == 0
        assert all(t["depth"] == 1 for t in tree[1:])


class TestAuditExport:
    @pytest.mark.asyncio
    async def test_export_bundle(self):
        # Set up trace
        tracer = Tracer.get()
        trace = tracer.start_trace("INC-001")
        root = trace.start_span("root", "coordinator")
        root.end()

        # Set up events
        bus = EventBus.get()
        await bus.publish(create_event(EventType.INCIDENT_DECLARED, "INC-001", "coordinator"))
        await bus.publish(create_event(EventType.TASK_CREATED, "INC-001", "intake"))

        # Set up gateway decisions
        gw = AgentGateway.get()
        await gw.check_tool_call("intake", "classify_incident", incident_id="INC-001")
        await gw.check_tool_call("accountability", "send_external_message", incident_id="INC-001")

        # Export
        bundle = export_audit_bundle("INC-001")

        assert bundle["type"] == "AUDIT_BUNDLE"
        assert bundle["incident_id"] == "INC-001"
        assert bundle["trace"] is not None
        assert bundle["trace"]["total_spans"] == 1
        assert len(bundle["event_log"]) == 3  # 2 events + 1 policy violation
        assert len(bundle["gateway_decisions"]) == 2
        assert len(bundle["gateway_denials"]) == 1
        assert bundle["summary"]["total_spans"] == 1
        assert bundle["summary"]["gateway_denials"] == 1

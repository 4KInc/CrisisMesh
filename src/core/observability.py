"""Observability — OpenTelemetry-style trace per incident, agent, tool call, handoff, retry, decision.

Provides structured tracing without requiring OTel collector for local development.
Each incident gets a trace with spans for every agent delegation, tool call, and decision.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any


class Span:
    """A single unit of work within a trace."""

    def __init__(
        self,
        trace_id: str,
        name: str,
        agent_id: str = "",
        parent_span_id: str | None = None,
    ) -> None:
        self.span_id = str(uuid.uuid4())[:8]
        self.trace_id = trace_id
        self.parent_span_id = parent_span_id
        self.name = name
        self.agent_id = agent_id
        self.status = "in_progress"
        self.start_time = datetime.now(timezone.utc)
        self.end_time: datetime | None = None
        self.attributes: dict[str, Any] = {}
        self.events: list[dict[str, Any]] = []

    def set_attribute(self, key: str, value: Any) -> None:
        self.attributes[key] = value

    def add_event(self, name: str, attributes: dict[str, Any] | None = None) -> None:
        self.events.append({
            "name": name,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "attributes": attributes or {},
        })

    def end(self, status: str = "ok") -> None:
        self.end_time = datetime.now(timezone.utc)
        self.status = status

    @property
    def duration_ms(self) -> float | None:
        if self.end_time:
            return (self.end_time - self.start_time).total_seconds() * 1000
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "span_id": self.span_id,
            "trace_id": self.trace_id,
            "parent_span_id": self.parent_span_id,
            "name": self.name,
            "agent_id": self.agent_id,
            "status": self.status,
            "start_time": self.start_time.isoformat(),
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "duration_ms": self.duration_ms,
            "attributes": self.attributes,
            "events": self.events,
        }


class Trace:
    """A collection of spans representing an end-to-end incident execution."""

    def __init__(self, trace_id: str, incident_id: str) -> None:
        self.trace_id = trace_id
        self.incident_id = incident_id
        self.spans: list[Span] = []
        self.created_at = datetime.now(timezone.utc)

    def start_span(
        self,
        name: str,
        agent_id: str = "",
        parent_span_id: str | None = None,
    ) -> Span:
        span = Span(
            trace_id=self.trace_id,
            name=name,
            agent_id=agent_id,
            parent_span_id=parent_span_id,
        )
        self.spans.append(span)
        return span

    @property
    def root_span(self) -> Span | None:
        for s in self.spans:
            if s.parent_span_id is None:
                return s
        return self.spans[0] if self.spans else None

    def to_dict(self) -> dict[str, Any]:
        root = self.root_span
        return {
            "trace_id": self.trace_id,
            "incident_id": self.incident_id,
            "created_at": self.created_at.isoformat(),
            "total_spans": len(self.spans),
            "duration_ms": root.duration_ms if root else None,
            "status": root.status if root else "unknown",
            "spans": [s.to_dict() for s in self.spans],
        }

    def get_span_tree(self) -> list[dict[str, Any]]:
        """Build a hierarchical span tree for display."""
        by_id = {s.span_id: s for s in self.spans}
        children: dict[str | None, list[Span]] = {}
        for s in self.spans:
            children.setdefault(s.parent_span_id, []).append(s)

        def _build(parent_id: str | None, depth: int = 0) -> list[dict[str, Any]]:
            result = []
            for s in children.get(parent_id, []):
                result.append({
                    "depth": depth,
                    "span_id": s.span_id,
                    "name": s.name,
                    "agent_id": s.agent_id,
                    "status": s.status,
                    "duration_ms": s.duration_ms,
                })
                result.extend(_build(s.span_id, depth + 1))
            return result

        return _build(None)


class Tracer:
    """Singleton tracer — manages traces across incidents."""

    _instance: Tracer | None = None

    def __init__(self) -> None:
        self._traces: dict[str, Trace] = {}

    @classmethod
    def get(cls) -> Tracer:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        cls._instance = None

    def start_trace(self, incident_id: str) -> Trace:
        trace_id = str(uuid.uuid4())[:12]
        trace = Trace(trace_id=trace_id, incident_id=incident_id)
        self._traces[incident_id] = trace
        return trace

    def get_trace(self, incident_id: str) -> Trace | None:
        return self._traces.get(incident_id)

    def start_span(
        self,
        incident_id: str,
        name: str,
        agent_id: str = "",
        parent_span_id: str | None = None,
    ) -> Span:
        trace = self._traces.get(incident_id)
        if not trace:
            trace = self.start_trace(incident_id)
        return trace.start_span(name, agent_id, parent_span_id)

    def list_traces(self) -> list[dict[str, Any]]:
        return [
            {
                "trace_id": t.trace_id,
                "incident_id": t.incident_id,
                "total_spans": len(t.spans),
                "created_at": t.created_at.isoformat(),
            }
            for t in self._traces.values()
        ]


def export_audit_bundle(incident_id: str) -> dict[str, Any]:
    """Export a complete audit bundle: trace + gateway decisions + event history.

    This is the compliance export that proves end-to-end incident execution
    for the observability trace demo beat.
    """
    from src.core.agent_gateway import AgentGateway
    from src.core.event_bus import EventBus

    tracer = Tracer.get()
    trace = tracer.get_trace(incident_id)
    gateway = AgentGateway.get()
    bus = EventBus.get()

    events = bus.get_history(incident_id=incident_id, limit=200)
    decisions = gateway.get_decisions(incident_id=incident_id)

    return {
        "type": "AUDIT_BUNDLE",
        "incident_id": incident_id,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "trace": trace.to_dict() if trace else None,
        "gateway_decisions": [d.to_dict() for d in decisions],
        "gateway_denials": [d.to_dict() for d in decisions if not d.allowed],
        "event_log": [
            {
                "id": e.id,
                "type": str(e.type),
                "agent_id": e.agent_id,
                "timestamp": e.timestamp.isoformat(),
                "data": e.data,
            }
            for e in events
        ],
        "summary": {
            "total_spans": len(trace.spans) if trace else 0,
            "total_events": len(events),
            "total_gateway_checks": len(decisions),
            "gateway_denials": sum(1 for d in decisions if not d.allowed),
        },
    }

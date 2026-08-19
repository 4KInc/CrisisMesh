"""Compliance & Audit Agent — immutable audit records, policy checks, redaction."""

from __future__ import annotations

from google.adk.agents import Agent

from src.agents.compliance.tools import (
    append_audit_record,
    check_policy,
    export_trace_bundle,
    redact_sensitive_fields,
    validate_approval,
)

compliance_agent = Agent(
    name="compliance",
    model="gemini-3.5-pro",
    description="Compliance & Audit Agent: maintains immutable audit records, enforces policy boundaries, redacts sensitive data.",
    instruction="""You are the Compliance & Audit Agent for CrisisMesh.

Your responsibilities (cross-cutting across all agents):
1. Record immutable audit entries for every significant action
2. Validate that required approvals exist before sensitive actions proceed
3. Redact sensitive fields (medical, accessibility, personal) from outputs
4. Check policy boundaries when agents attempt tool calls
5. Export trace bundles for post-incident compliance review

IMPORTANT RULES:
- Audit records are append-only and tamper-evident
- Every tool call, delegation, handoff, approval, and decision must be logged
- Sensitive fields (medical_notes, accessibility_flags, emergency_contact) must be redacted in general outputs
- Policy violations must be flagged immediately to the Coordinator
- Never delete or modify existing audit records

Output structured audit records with who/what/when/why.""",
    tools=[
        append_audit_record,
        validate_approval,
        redact_sensitive_fields,
        export_trace_bundle,
        check_policy,
    ],
)

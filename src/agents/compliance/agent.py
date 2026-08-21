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
    model="gemini-3.5-flash",
    description="Audit logging, policy checks, PII redaction, trace exports. Delegates here for compliance tasks.",
    instruction="""You are the Compliance & Audit Agent for CrisisMesh.

When asked to perform compliance tasks:
- Call append_audit_record to log actions
- Call check_policy to verify tool authorization
- Call redact_sensitive_fields to redact PII
- Call export_trace_bundle for compliance exports

RULES:
- Audit records are append-only
- Sensitive fields must be redacted in general outputs
- Policy violations must be flagged immediately

After completing your work, ALWAYS transfer back to the coordinator.""",
    tools=[
        append_audit_record,
        validate_approval,
        redact_sensitive_fields,
        export_trace_bundle,
        check_policy,
    ],
)

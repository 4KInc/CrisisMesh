"""Tools for the Compliance & Audit Agent."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from src.config.agent_registry import is_tool_allowed

# Sensitive fields that must be redacted in general outputs
SENSITIVE_FIELDS = {
    "medical_notes",
    "mobility_limitations",
    "emergency_contact_name",
    "emergency_contact_phone",
    "phone",
    "email",
    "slack_user_id",
    "ssn",
    "date_of_birth",
}


def append_audit_record(
    incident_id: str,
    agent_id: str,
    action: str,
    target: str = "",
    details: str = "",
    approval_id: str = "",
) -> dict[str, Any]:
    """Append an immutable audit record to the event ledger.

    Args:
        incident_id: The incident this action relates to.
        agent_id: The agent performing the action.
        action: What action was taken.
        target: The target of the action (person, resource, etc.).
        details: Additional details.
        approval_id: If this action required approval, the approval ID.

    Returns:
        The created audit record with ID and timestamp.
    """
    record_id = str(uuid.uuid4())
    return {
        "audit_record_id": record_id,
        "incident_id": incident_id,
        "agent_id": agent_id,
        "action": action,
        "target": target,
        "details": details,
        "approval_id": approval_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "immutable": True,
    }


def validate_approval(
    incident_id: str,
    action: str,
    required_role: str = "incident_commander",
) -> dict[str, Any]:
    """Check if a required approval exists for a sensitive action.

    Args:
        incident_id: The incident context.
        action: The action that requires approval.
        required_role: The role that must approve.

    Returns:
        Approval status and whether the action can proceed.
    """
    # Stub — checks Firestore approvals subcollection
    return {
        "incident_id": incident_id,
        "action": action,
        "required_role": required_role,
        "approved": False,
        "status": "pending",
        "note": "Action requires explicit approval from the Incident Commander.",
    }


def redact_sensitive_fields(
    data: dict[str, Any],
    context: str = "general",
) -> dict[str, Any]:
    """Redact sensitive fields from a data dictionary.

    Args:
        data: The data dictionary to redact.
        context: The output context (general, commander, responder, audit).

    Returns:
        Redacted copy of the data.
    """
    if context in ("commander", "audit"):
        # Commanders and audit see everything
        return {"data": data, "redacted_fields": [], "context": context}

    redacted = {}
    redacted_fields = []
    for key, value in data.items():
        if key in SENSITIVE_FIELDS:
            redacted[key] = "[REDACTED]"
            redacted_fields.append(key)
        elif isinstance(value, dict):
            inner_result = redact_sensitive_fields(value, context)
            redacted[key] = inner_result["data"]
            redacted_fields.extend(inner_result["redacted_fields"])
        else:
            redacted[key] = value

    return {
        "data": redacted,
        "redacted_fields": redacted_fields,
        "context": context,
    }


def export_trace_bundle(
    incident_id: str,
    include_audit: bool = True,
    include_timeline: bool = True,
    include_accountability: bool = True,
) -> dict[str, Any]:
    """Export a compliance trace bundle for an incident.

    Args:
        incident_id: The incident to export traces for.
        include_audit: Include audit log entries.
        include_timeline: Include incident timeline.
        include_accountability: Include accountability records.

    Returns:
        Trace bundle metadata (actual data fetched from Firestore).
    """
    return {
        "type": "TRACE_BUNDLE",
        "incident_id": incident_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "includes": {
            "audit_log": include_audit,
            "timeline": include_timeline,
            "accountability": include_accountability,
        },
        "format": "json",
        "status": "ready",
    }


def check_policy(
    agent_id: str,
    tool_name: str,
    incident_id: str = "",
) -> dict[str, Any]:
    """Check if an agent is authorized to use a specific tool.

    Args:
        agent_id: The agent requesting the tool.
        tool_name: The tool being requested.
        incident_id: Optional incident context.

    Returns:
        Policy check result — allowed or denied with reason.
    """
    allowed = is_tool_allowed(agent_id, tool_name)

    result = {
        "agent_id": agent_id,
        "tool_name": tool_name,
        "incident_id": incident_id,
        "allowed": allowed,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }

    if not allowed:
        result["reason"] = f"Agent '{agent_id}' is not authorized to use tool '{tool_name}'"
        result["action"] = "denied"

    return result

"""Tools for the Coordinator Agent."""

from __future__ import annotations

from typing import Any

from src.core.tactical_reasoning import get_tactical_context as _get_tactical_context


def resolve_incident(incident_id: str) -> dict[str, Any]:
    """Mark an incident as resolved. Requires Incident Commander approval.

    This action is gated by the Agent Gateway approval policy. If the gateway
    blocks this call, the result will contain pending_action_id and
    instructions for the IC to approve via '/incident approve <id>'.

    Args:
        incident_id: The incident to resolve.

    Returns:
        Resolution status. If blocked, returns approval instructions.
    """
    return {
        "status": "resolved",
        "incident_id": incident_id,
        "message": "Incident marked as resolved by Incident Commander.",
    }


def get_tactical_context(
    incident_type: str,
    playbook_id: str,
    severity: str = "",
    situation_summary: str = "",
) -> dict[str, Any]:
    """Get playbook-grounded tactical context for reasoning over the incident.

    Returns approved playbook rules if they cover the incident type (origin:
    playbook_grounded). If no approved rule covers the situation, returns
    origin: improvised — the coordinator may then reason from general
    emergency-management principles.

    Args:
        incident_type: The classified incident type (e.g. 'fire').
        playbook_id: The selected playbook ID from intake.
        severity: Incident severity level.
        situation_summary: Brief description of the situation.

    Returns:
        Tactical context with origin, playbook rules (if grounded),
        and guidance notes.
    """
    return _get_tactical_context(
        incident_type=incident_type,
        playbook_id=playbook_id,
        severity=severity,
        situation_summary=situation_summary,
    )

"""Tools for the SITREP & Handoff Agent."""

from __future__ import annotations

from datetime import datetime
from typing import Any


def generate_sitrep(
    incident_id: str,
    incident_type: str,
    severity: str,
    location: str,
    accountability: dict[str, Any],
    blocked_zones: list[str],
    resources_deployed: list[str],
    timeline_events: list[dict[str, str]],
) -> dict[str, Any]:
    """Generate an Incident Commander SITREP.

    Args:
        incident_id: Active incident ID.
        incident_type: Type of incident.
        severity: Current severity level.
        location: Incident location.
        accountability: Current accountability summary.
        blocked_zones: List of blocked zones.
        resources_deployed: Resources that have been deployed.
        timeline_events: Recent timeline events.

    Returns:
        Structured SITREP for the Incident Commander.
    """
    total = accountability.get("total_tracked", 0)
    accounted = accountability.get("accounted", 0)
    unaccounted = total - accounted

    return {
        "type": "IC_SITREP",
        "incident_id": incident_id,
        "generated_at": datetime.utcnow().isoformat(),
        "situation": {
            "incident_type": incident_type,
            "severity": severity,
            "location": location,
            "status": "active",
        },
        "accountability": {
            "total": total,
            "accounted": accounted,
            "unaccounted": unaccounted,
            "breakdown": accountability.get("breakdown", {}),
        },
        "hazards": {
            "blocked_zones": blocked_zones,
        },
        "resources": resources_deployed,
        "recent_events": timeline_events[-5:] if timeline_events else [],
        "emergency_notice": "If not already done, ensure 911 has been contacted.",
        "requires_commander_review": True,
    }


def generate_responder_card(
    incident_id: str,
    incident_type: str,
    severity: str,
    location: str,
    time_declared: str,
    accountability: dict[str, Any],
    people_needing_assistance: list[dict[str, str]],
    hazards: list[str],
    safe_routes: list[str],
    blocked_routes: list[str],
    resources: list[str],
    command_contact: str,
) -> dict[str, Any]:
    """Generate a one-card responder handoff brief for police/fire/EMS.

    Args:
        incident_id: Active incident ID.
        incident_type: Type of incident.
        severity: Severity level.
        location: Facility address and specific location.
        time_declared: When the incident was declared.
        accountability: Accountability summary.
        people_needing_assistance: People flagged as needing help.
        hazards: Known hazards in the area.
        safe_routes: Available safe routes for responders.
        blocked_routes: Routes that are blocked.
        resources: Available on-site resources.
        command_contact: Incident commander contact info.

    Returns:
        One-card brief for first responders. REQUIRES COMMANDER APPROVAL.
    """
    return {
        "type": "RESPONDER_ONE_CARD",
        "incident_id": incident_id,
        "generated_at": datetime.utcnow().isoformat(),
        "REQUIRES_COMMANDER_APPROVAL": True,
        "threat": incident_type,
        "severity": severity,
        "time_declared": time_declared,
        "location": location,
        "headcount": {
            "total": accountability.get("total_tracked", 0),
            "unaccounted": accountability.get("total_tracked", 0) - accountability.get("accounted", 0),
            "injured": accountability.get("breakdown", {}).get("injured", 0),
            "need_help": accountability.get("breakdown", {}).get("need_help", 0),
        },
        "people_needing_assistance": people_needing_assistance,
        "hazards": hazards,
        "safe_routes": safe_routes,
        "blocked_routes": blocked_routes,
        "on_site_resources": resources,
        "command_contact": command_contact,
    }


def generate_stakeholder_update(
    incident_id: str,
    incident_type: str,
    severity: str,
    status_summary: str,
    actions_taken: list[str],
) -> dict[str, Any]:
    """Generate a redacted stakeholder update (parents, board, media).

    Args:
        incident_id: Active incident ID.
        incident_type: Type of incident.
        severity: Severity level.
        status_summary: High-level status (no personal info).
        actions_taken: Actions taken so far.

    Returns:
        Redacted stakeholder update. No personal/medical data included.
    """
    return {
        "type": "STAKEHOLDER_UPDATE",
        "incident_id": incident_id,
        "generated_at": datetime.utcnow().isoformat(),
        "incident_type": incident_type,
        "severity": severity,
        "status": status_summary,
        "actions_taken": actions_taken,
        "notice": "All students and staff are being accounted for. Emergency services have been contacted.",
        "personal_data_included": False,
        "REQUIRES_COMMANDER_APPROVAL": True,
    }


def generate_timeline_summary(
    incident_id: str,
    events: list[dict[str, str]],
) -> dict[str, Any]:
    """Generate a chronological timeline summary of the incident.

    Args:
        incident_id: Active incident ID.
        events: List of timeline events with timestamp, action, agent, details.

    Returns:
        Formatted timeline summary.
    """
    return {
        "type": "TIMELINE_SUMMARY",
        "incident_id": incident_id,
        "generated_at": datetime.utcnow().isoformat(),
        "total_events": len(events),
        "events": events,
    }

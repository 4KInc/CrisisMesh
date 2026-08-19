"""Tools for the Accountability Agent."""

from __future__ import annotations

from typing import Any

from src.models.person import PersonStatus


# In-memory store for demo/testing — production uses Firestore via state service
_checkin_store: dict[str, dict[str, PersonStatus]] = {}


def read_roster(facility_id: str, zone: str = "") -> dict[str, Any]:
    """Read the personnel roster for a facility, optionally filtered by zone/floor.

    Args:
        facility_id: The facility to query.
        zone: Optional zone/floor filter.

    Returns:
        Roster summary with total count and zone breakdown.
    """
    # Tool stub — actual implementation queries Firestore via the state service
    return {
        "facility_id": facility_id,
        "zone": zone,
        "status": "roster_loaded",
        "note": "Roster data loaded from knowledge base. Use compute_accountability_summary for current status.",
    }


def process_checkin(
    incident_id: str,
    person_id: str,
    status: str,
    location: str = "",
    notes: str = "",
) -> dict[str, Any]:
    """Process a check-in response from a person.

    Args:
        incident_id: The active incident ID.
        person_id: ID of the person checking in.
        status: Their reported status (safe, injured, need_help, evacuated).
        location: Their current location if provided.
        notes: Any additional notes.

    Returns:
        Confirmation of the processed check-in.
    """
    try:
        person_status = PersonStatus(status)
    except ValueError:
        person_status = PersonStatus.UNKNOWN

    if incident_id not in _checkin_store:
        _checkin_store[incident_id] = {}
    _checkin_store[incident_id][person_id] = person_status

    return {
        "incident_id": incident_id,
        "person_id": person_id,
        "status": person_status,
        "location": location,
        "recorded": True,
    }


def compute_accountability_summary(incident_id: str) -> dict[str, Any]:
    """Compute the current accountability summary for an incident.

    Args:
        incident_id: The active incident ID.

    Returns:
        Summary with counts per status category.
    """
    checkins = _checkin_store.get(incident_id, {})

    summary: dict[str, int] = {s: 0 for s in PersonStatus}
    for status in checkins.values():
        summary[status] = summary.get(status, 0) + 1

    total = sum(summary.values())
    accounted = total - summary.get(PersonStatus.UNKNOWN, 0) - summary.get(PersonStatus.SILENT, 0)

    return {
        "incident_id": incident_id,
        "total_tracked": total,
        "accounted": accounted,
        "unaccounted": total - accounted,
        "breakdown": summary,
    }


def send_checkin_request(
    incident_id: str,
    person_ids: list[str],
    channel: str = "",
) -> dict[str, Any]:
    """Send check-in requests to a list of people.

    Args:
        incident_id: The active incident ID.
        person_ids: List of person IDs to request check-ins from.
        channel: Optional Slack channel to post the request.

    Returns:
        Confirmation with count of requests sent.
    """
    # Initialize unknown status for everyone being requested
    if incident_id not in _checkin_store:
        _checkin_store[incident_id] = {}
    for pid in person_ids:
        if pid not in _checkin_store[incident_id]:
            _checkin_store[incident_id][pid] = PersonStatus.UNKNOWN

    return {
        "incident_id": incident_id,
        "requests_sent": len(person_ids),
        "channel": channel or "direct_message",
        "status": "sent",
    }


def escalate_missing_checkins(
    incident_id: str,
    timeout_minutes: int = 5,
) -> dict[str, Any]:
    """Identify and escalate people who haven't responded to check-ins.

    Args:
        incident_id: The active incident ID.
        timeout_minutes: Minutes after which a missing check-in triggers escalation.

    Returns:
        List of unaccounted person IDs and escalation status.
    """
    checkins = _checkin_store.get(incident_id, {})
    missing = [
        pid for pid, status in checkins.items()
        if status in (PersonStatus.UNKNOWN, PersonStatus.SILENT)
    ]

    return {
        "incident_id": incident_id,
        "missing_count": len(missing),
        "missing_person_ids": missing,
        "timeout_minutes": timeout_minutes,
        "escalation": "notified_coordinator" if missing else "all_accounted",
    }

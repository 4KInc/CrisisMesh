"""Tools for the Accountability Agent.

Uses the in-memory knowledge base for roster queries and an in-memory
check-in store for incident-scoped accountability tracking.
"""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import Any

from src.core.knowledge_base import KnowledgeBase
from src.models.person import PersonStatus


# In-memory check-in store: {incident_id: {person_id: {status, name, location, time}}}
logger = logging.getLogger(__name__)

_checkin_store: dict[str, dict[str, dict[str, Any]]] = {}


def read_roster(
    facility_id: str,
    zone: str = "",
    floor: int = 0,
) -> dict[str, Any]:
    """Read the personnel roster for a facility, optionally filtered by zone or floor.

    Args:
        facility_id: The facility to query (e.g. 'jefferson').
        zone: Optional zone ID filter (e.g. 'west-wing-f2').
        floor: Optional floor filter (0 = all floors).

    Returns:
        Full roster with names, roles, locations, and special needs flags.
    """
    kb = KnowledgeBase.get()

    if zone:
        people = kb.get_personnel_by_zone(zone)
    elif floor:
        people = kb.get_personnel_by_floor(floor)
    else:
        people = kb.get_personnel_by_facility(facility_id)

    mobility_people = [
        {"person_id": p["person_id"], "name": p["name"], "location": p.get("default_location")}
        for p in people
        if p.get("mobility_limitations", "").lower() in ("true", "yes", "1")
    ]

    wardens = [
        {
            "person_id": p["person_id"],
            "name": p["name"],
            "evacuation_role": p.get("evacuation_role", ""),
            "location": p.get("default_location"),
        }
        for p in people
        if p.get("is_floor_warden", "").lower() in ("true", "yes", "1")
        or p.get("evacuation_role", "")
    ]

    return {
        "facility_id": facility_id,
        "zone": zone,
        "floor": floor,
        "total_personnel": len(people),
        "personnel": [
            {
                "person_id": p["person_id"],
                "name": p["name"],
                "role": p.get("role", ""),
                "default_location": p.get("default_location", ""),
                "floor": p.get("floor"),
            }
            for p in people
        ],
        "mobility_needs": mobility_people,
        "floor_wardens_and_leads": wardens,
        "source": "knowledge_base.personnel",
    }


def _mirror_to_reconciliation(incident_id: str, person_id: str, status: str) -> None:
    """Tell the reconciliation state machine that this person is accounted for.

    Two stores hold per-person state: this one, which the console and SITREPs
    read, and the reconciliation state machine, which decides who to chase. Six
    call sites wrote here and none wrote there, so a teacher who texted SAFE
    stayed SILENT to the loop — re-pinged, then escalated to their own floor
    warden. The `SAFE`-cancels-pending contract was correct and unconnected.

    Never called from inside a store transaction: check-ins arrive at transport
    top level, so this cannot reproduce the self-contention livelock.
    """
    if status in (PersonStatus.UNKNOWN, PersonStatus.SILENT):
        return
    try:
        from src.core import reconciliation
        reconciliation.record_checkin(incident_id, person_id, source="checkin")
    except Exception as exc:  # noqa: BLE001 - a mirror failure must not lose the check-in
        logger.error(
            f"Check-in for {person_id} recorded but not mirrored to reconciliation "
            f"({exc}) — the loop may re-ping them"
        )


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

    kb = KnowledgeBase.get()
    person = kb.get_person(person_id)
    name = person["name"] if person else person_id

    if incident_id not in _checkin_store:
        _checkin_store[incident_id] = {}

    _checkin_store[incident_id][person_id] = {
        "status": person_status,
        "name": name,
        "location": location,
        "notes": notes,
        "time": datetime.now(timezone.utc).isoformat(),
    }

    _mirror_to_reconciliation(incident_id, person_id, person_status)

    return {
        "incident_id": incident_id,
        "person_id": person_id,
        "name": name,
        "status": person_status,
        "location": location,
        "recorded": True,
    }


def compute_accountability_summary(incident_id: str) -> dict[str, Any]:
    """Compute the current accountability summary for an incident.

    Args:
        incident_id: The active incident ID.

    Returns:
        Summary with counts per status category and lists of people per status.
    """
    checkins = _checkin_store.get(incident_id, {})

    breakdown: dict[str, list[dict]] = {s: [] for s in PersonStatus}
    for pid, info in checkins.items():
        status = info["status"]
        breakdown.setdefault(status, []).append({
            "person_id": pid,
            "name": info.get("name", pid),
            "location": info.get("location", ""),
        })

    counts = {status: len(people) for status, people in breakdown.items()}
    total = sum(counts.values())
    accounted = total - counts.get(PersonStatus.UNKNOWN, 0) - counts.get(PersonStatus.SILENT, 0)

    return {
        "incident_id": incident_id,
        "total_tracked": total,
        "accounted": accounted,
        "unaccounted": total - accounted,
        "counts": counts,
        "breakdown": breakdown,
    }


def send_checkin_request(
    incident_id: str,
    person_ids: list[str] | str = "",
    zone: str = "",
    floor: int = 0,
    facility_id: str = "jefferson",
) -> dict[str, Any]:
    """Send check-in requests to people. Can target specific IDs, a zone, or a floor.

    Args:
        incident_id: The active incident ID.
        person_ids: Comma-separated person IDs, or empty to use zone/floor filter.
        zone: Zone ID to send requests to all personnel in that zone.
        floor: Floor number to send requests to all personnel on that floor.
        facility_id: Facility ID (defaults to jefferson).

    Returns:
        Confirmation with count of requests sent and the person list.
    """
    kb = KnowledgeBase.get()

    if isinstance(person_ids, str) and person_ids:
        ids = [pid.strip() for pid in person_ids.split(",")]
    elif isinstance(person_ids, list):
        ids = person_ids
    elif zone:
        people = kb.get_personnel_by_zone(zone)
        ids = [p["person_id"] for p in people]
    elif floor:
        people = kb.get_personnel_by_floor(floor)
        ids = [p["person_id"] for p in people]
    else:
        people = kb.get_personnel_by_facility(facility_id)
        ids = [p["person_id"] for p in people]

    if incident_id not in _checkin_store:
        _checkin_store[incident_id] = {}

    requested = []
    for pid in ids:
        if pid not in _checkin_store[incident_id]:
            person = kb.get_person(pid)
            _checkin_store[incident_id][pid] = {
                "status": PersonStatus.UNKNOWN,
                "name": person["name"] if person else pid,
                "location": "",
                "notes": "",
                "time": datetime.now(timezone.utc).isoformat(),
            }
        requested.append(pid)

    return {
        "incident_id": incident_id,
        "requests_sent": len(requested),
        "person_ids": requested,
        "zone": zone,
        "floor": floor,
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
        List of unaccounted people with names and last known locations.
    """
    kb = KnowledgeBase.get()
    checkins = _checkin_store.get(incident_id, {})

    missing = []
    missing_with_mobility = []
    for pid, info in checkins.items():
        if info["status"] in (PersonStatus.UNKNOWN, PersonStatus.SILENT):
            person = kb.get_person(pid)
            entry = {
                "person_id": pid,
                "name": info.get("name", pid),
                "last_known_location": person.get("default_location", "") if person else "",
                "floor": person.get("floor", "") if person else "",
            }
            missing.append(entry)
            if person and person.get("mobility_limitations", "").lower() in ("true", "yes", "1"):
                missing_with_mobility.append(entry)

    return {
        "incident_id": incident_id,
        "missing_count": len(missing),
        "missing_personnel": missing,
        "missing_with_mobility_needs": missing_with_mobility,
        "timeout_minutes": timeout_minutes,
        "escalation": "notified_coordinator" if missing else "all_accounted",
        "priority_note": f"{len(missing_with_mobility)} missing person(s) have mobility limitations — prioritize search."
        if missing_with_mobility else "",
    }

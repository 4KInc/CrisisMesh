"""Tools for the SITREP & Handoff Agent.

Generates structured briefs using real data from the knowledge base
and accountability tracking.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from src.core.knowledge_base import KnowledgeBase


def generate_sitrep(
    incident_id: str,
    incident_type: str,
    severity: str,
    location: str,
    accountability: dict[str, Any],
    blocked_zones: list[str] | str = "",
    resources_deployed: list[str] | str = "",
    timeline_events: list[dict[str, str]] | str = "",
) -> dict[str, Any]:
    """Generate an Incident Commander SITREP with full operational picture.

    Args:
        incident_id: Active incident ID.
        incident_type: Type of incident.
        severity: Current severity level.
        location: Incident location description.
        accountability: Current accountability summary from the Accountability Agent.
        blocked_zones: Comma-separated blocked zone IDs or list.
        resources_deployed: Comma-separated deployed resources or list.
        timeline_events: Recent timeline events.

    Returns:
        Structured SITREP for the Incident Commander.
    """
    kb = KnowledgeBase.get()

    # Normalize inputs
    if isinstance(blocked_zones, str):
        blocked_zones = [z.strip() for z in blocked_zones.split(",") if z.strip()]
    if isinstance(resources_deployed, str):
        resources_deployed = [r.strip() for r in resources_deployed.split(",") if r.strip()]
    if isinstance(timeline_events, str):
        timeline_events = []

    total = accountability.get("total_tracked", 0)
    accounted = accountability.get("accounted", 0)
    unaccounted = total - accounted

    # Enrich with nearby services info
    fire_stations = kb.get_nearby_services("fire_station")
    nearest_fire = fire_stations[0] if fire_stations else {}

    hospitals = kb.get_nearby_services("hospital")
    nearest_hospital = hospitals[0] if hospitals else {}

    return {
        "type": "IC_SITREP",
        "incident_id": incident_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
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
            "breakdown": accountability.get("counts", accountability.get("breakdown", {})),
        },
        "hazards": {
            "blocked_zones": blocked_zones,
        },
        "resources": resources_deployed,
        "nearby_services": {
            "nearest_fire_station": {
                "name": nearest_fire.get("name", ""),
                "eta_minutes": nearest_fire.get("eta_minutes", ""),
                "phone": nearest_fire.get("phone", ""),
            } if nearest_fire else None,
            "nearest_hospital": {
                "name": nearest_hospital.get("name", ""),
                "eta_minutes": nearest_hospital.get("eta_minutes", ""),
                "trauma_level": nearest_hospital.get("trauma_level", ""),
            } if nearest_hospital else None,
        },
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
    incident_zone: str = "",
    facility_id: str = "jefferson",
) -> dict[str, Any]:
    """Generate a one-card responder handoff brief for police/fire/EMS.

    Automatically enriches with safe/blocked routes, resources, people needing
    assistance, and command contact from the knowledge base.

    Args:
        incident_id: Active incident ID.
        incident_type: Type of incident.
        severity: Severity level.
        location: Facility address and specific location.
        time_declared: When the incident was declared.
        accountability: Accountability summary.
        incident_zone: Zone where the incident is occurring.
        facility_id: Facility ID.

    Returns:
        One-card brief for first responders. REQUIRES COMMANDER APPROVAL.
    """
    kb = KnowledgeBase.get()

    # Get facility address
    facility = kb.get_facility(facility_id)
    full_location = f"{facility['address']} — {location}" if facility else location

    # Find blocked routes
    affected = [incident_zone] if incident_zone else []
    blocked_routes = kb.get_blocked_routes(facility_id, affected)
    blocked_names = [r["name"] for r in blocked_routes]

    # Find safe routes from affected zone
    safe_routes_data = kb.get_routes_from_zone(facility_id, incident_zone, affected) if incident_zone else []
    safe_route_names = [f"{r['name']} -> {r['to_exit']}" for r in safe_routes_data]

    # Find people needing assistance (mobility limitations)
    mobility_people = kb.get_personnel_with_mobility_limitations()
    people_needing_assistance = [
        {"name": p["name"], "location": p.get("default_location", ""), "notes": p.get("medical_notes", "")}
        for p in mobility_people
    ]

    # Find hazards near incident zone
    hazards = []
    if incident_zone:
        hazmat = kb.get_resources(facility_id, "fire_extinguisher", zone_id=incident_zone)
        # Check for science lab chemicals
        zone = kb.get_zone(incident_zone)
        if zone and "science lab" in zone.get("notes", "").lower():
            hazards.append("Science lab chemical storage in Room 215 (locked cabinet — acids/bases)")

    # Get available resources
    resources = []
    aeds = kb.get_resources(facility_id, "aed")
    resources.extend([f"AED: {r['location_description']}" for r in aeds])
    kits = kb.get_resources(facility_id, "first_aid_kit")
    resources.extend([f"First Aid: {r['location_description']}" for r in kits[:3]])

    # Assembly points
    assembly = kb.get_assembly_points(facility_id, primary_only=True)
    assembly_info = assembly[0]["name"] if assembly else "Athletic Field"

    # Command contact
    ic = next((p for p in kb.personnel if p.get("evacuation_role") == "Incident Commander"), None)
    command_contact = f"{ic['name']} — {ic.get('phone', '')}" if ic else "Main Office"

    headcount = accountability
    total = headcount.get("total_tracked", 0)
    accounted_n = headcount.get("accounted", 0)

    return {
        "type": "RESPONDER_ONE_CARD",
        "incident_id": incident_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "REQUIRES_COMMANDER_APPROVAL": True,
        "threat": incident_type,
        "severity": severity,
        "time_declared": time_declared,
        "location": full_location,
        "headcount": {
            "total": total,
            "unaccounted": total - accounted_n,
            "injured": headcount.get("counts", {}).get("injured", 0),
            "need_help": headcount.get("counts", {}).get("need_help", 0),
        },
        "people_needing_assistance": people_needing_assistance,
        "hazards": hazards,
        "safe_routes": safe_route_names,
        "blocked_routes": blocked_names,
        "on_site_resources": resources,
        "assembly_point": assembly_info,
        "command_contact": command_contact,
    }


def generate_stakeholder_update(
    incident_id: str,
    incident_type: str,
    severity: str,
    status_summary: str,
    actions_taken: list[str] | str = "",
) -> dict[str, Any]:
    """Generate a redacted stakeholder update (parents, board, media).

    Args:
        incident_id: Active incident ID.
        incident_type: Type of incident.
        severity: Severity level.
        status_summary: High-level status (no personal info).
        actions_taken: Actions taken so far (comma-separated or list).

    Returns:
        Redacted stakeholder update. No personal/medical data included.
    """
    if isinstance(actions_taken, str):
        actions_taken = [a.strip() for a in actions_taken.split(",") if a.strip()]

    return {
        "type": "STAKEHOLDER_UPDATE",
        "incident_id": incident_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
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
    events: list[dict[str, str]] | str = "",
) -> dict[str, Any]:
    """Generate a chronological timeline summary of the incident.

    Args:
        incident_id: Active incident ID.
        events: List of timeline events with timestamp, action, agent, details.

    Returns:
        Formatted timeline summary.
    """
    if isinstance(events, str):
        events = []

    return {
        "type": "TIMELINE_SUMMARY",
        "incident_id": incident_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_events": len(events),
        "events": events,
    }

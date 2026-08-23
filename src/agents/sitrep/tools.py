"""Tools for the SITREP & Handoff Agent.

Generates structured briefs using real data from the knowledge base
and accountability tracking.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from src.core.knowledge_base import KnowledgeBase

_THREAT_PATTERNS = [
    re.compile(
        r"last\s+(?:seen|spotted|reported)\s+(?:heading\s+)?(?:toward|towards|near|in|at|by)\s+(?:the\s+)?(.+?)(?:\.|,|$)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:shooter|suspect|intruder|threat|gunman)\s+(?:seen|spotted|reported|observed)\s+(?:near|in|at|by|heading\s+(?:toward|towards))\s+(?:the\s+)?(.+?)(?:\.|,|$)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:gunshots?|shots?\s+fired)\s+(?:heard|reported)\s+(?:from|in|near)\s+(?:the\s+)?(.+?)(?:\.|,|$)",
        re.IGNORECASE,
    ),
]


def extract_threat_observation(report: str) -> str:
    """Extract reported threat location from incident report text.

    Returns the raw reported observation or empty string. This extracts
    what was REPORTED by witnesses — it does not infer or generate positions.
    """
    for pattern in _THREAT_PATTERNS:
        m = pattern.search(report)
        if m:
            return m.group(1).strip()
    return ""


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


def generate_arrival_brief(
    incident_id: str,
    incident_type: str,
    severity: str,
    location: str,
    time_declared: str,
    accountability: dict[str, Any],
    incident_zone: str = "",
    facility_id: str = "jefferson",
    reported_threat_location: str = "",
    threat_last_seen_time: str = "",
) -> dict[str, Any]:
    """Generate a Law Enforcement Arrival Brief for police/fire/EMS on-scene handoff.

    Read-only, point-in-time SITREP. REPORTS observed state only — no tactical
    directives, no movement/entry instructions, no targeting instructions.

    Args:
        incident_id: Active incident ID.
        incident_type: Type of incident.
        severity: Severity level.
        location: Facility address and specific location.
        time_declared: When the incident was declared.
        accountability: Accountability summary.
        incident_zone: Zone where the incident is occurring.
        facility_id: Facility ID.
        reported_threat_location: Observed/reported threat location (if any).
        threat_last_seen_time: Timestamp of last threat observation (if any).

    Returns:
        Arrival brief for first responders. REQUIRES COMMANDER APPROVAL.
    """
    kb = KnowledgeBase.get()
    now = datetime.now(timezone.utc).isoformat()

    facility = kb.get_facility(facility_id)
    full_location = f"{facility['address']} — {location}" if facility else location

    affected = [incident_zone] if incident_zone else []
    blocked_routes = kb.get_blocked_routes(facility_id, affected)
    blocked_names = [r["name"] for r in blocked_routes]

    safe_routes_data = kb.get_routes_from_zone(facility_id, incident_zone, affected) if incident_zone else []
    safe_route_names = [f"{r['name']} -> {r['to_exit']}" for r in safe_routes_data]

    accessible_routes = kb.get_accessible_routes(facility_id, incident_zone) if incident_zone else []
    accessible_names = [f"{r['name']} -> {r['to_exit']}" for r in accessible_routes]

    mobility_people = kb.get_personnel_with_mobility_limitations()
    people_needing_assistance = [
        {"name": p["name"], "last_known_location": p.get("default_location", ""), "has_mobility_limitation": True}
        for p in mobility_people
    ]

    hazards = []
    if incident_zone:
        zone = kb.get_zone(incident_zone)
        if zone and "science lab" in zone.get("notes", "").lower():
            hazards.append("Science lab chemical storage in Room 215 (locked cabinet — acids/bases)")

    resources = []
    aeds = kb.get_resources(facility_id, "aed")
    resources.extend([f"AED: {r['location_description']}" for r in aeds])
    kits = kb.get_resources(facility_id, "first_aid_kit")
    resources.extend([f"First Aid: {r['location_description']}" for r in kits[:3]])
    extinguishers = kb.get_resources(facility_id, "fire_extinguisher")
    resources.extend([f"Fire Ext: {r['location_description']}" for r in extinguishers[:3]])

    assembly = kb.get_assembly_points(facility_id, primary_only=True)
    assembly_info = assembly[0]["name"] if assembly else "Athletic Field"

    ic = next((p for p in kb.personnel if p.get("evacuation_role") == "Incident Commander"), None)
    command_contact = f"{ic['name']} — {ic.get('phone', '')}" if ic else "Main Office"

    wardens = kb.get_floor_wardens()
    warden_info = [
        {"name": w["name"], "floor": int(w.get("floor", 0)), "location": w.get("default_location", "")}
        for w in wardens
    ]

    floor_set = {int(z.get("floor", 0)) for z in kb.zones if z.get("facility_id") == facility_id}
    floor_summary = []
    for fl in sorted(floor_set):
        personnel_on_floor = kb.get_personnel_by_floor(fl)
        zones_on_floor = kb.get_zones_by_floor(facility_id, fl)
        floor_summary.append({
            "floor": fl,
            "zones": len(zones_on_floor),
            "personnel_assigned": len(personnel_on_floor),
        })

    fire_stations = kb.get_nearby_services("fire_station")
    nearest_fire = fire_stations[0] if fire_stations else {}
    hospitals = kb.get_nearby_services("hospital")
    nearest_hospital = hospitals[0] if hospitals else {}
    police_stations = kb.get_nearby_services("police_station")
    nearest_police = police_stations[0] if police_stations else {}

    headcount = accountability
    total = headcount.get("total_tracked", 0)
    accounted_n = headcount.get("accounted", 0)

    threat_observation = None
    if reported_threat_location:
        threat_observation = {
            "status": "UNCONFIRMED — reported observation only",
            "last_reported_location": reported_threat_location,
            "last_reported_time": threat_last_seen_time or "unknown",
            "caveat": "This is a reported observation, not a confirmed position. Treat as unverified.",
        }

    return {
        "type": "ARRIVAL_BRIEF",
        "scope_notice": (
            "This brief REPORTS observed state at generation time. "
            "It contains NO tactical directives, NO movement/entry instructions, "
            "and NO targeting guidance."
        ),
        "incident_id": incident_id,
        "generated_at": now,
        "REQUIRES_COMMANDER_APPROVAL": True,
        "incident": {
            "type": incident_type,
            "severity": severity,
            "time_declared": time_declared,
            "location": full_location,
            "incident_zone": incident_zone or "not specified",
        },
        "headcount": {
            "total": total,
            "accounted": accounted_n,
            "unaccounted": total - accounted_n,
            "injured": headcount.get("counts", {}).get("injured", 0),
            "need_help": headcount.get("counts", {}).get("need_help", 0),
        },
        "people_needing_assistance": people_needing_assistance,
        "threat_observation": threat_observation,
        "floor_summary": floor_summary,
        "floor_wardens": warden_info,
        "hazards": hazards,
        "egress": {
            "safe_routes": safe_route_names,
            "blocked_routes": blocked_names,
            "accessible_routes": accessible_names,
        },
        "on_site_resources": resources,
        "assembly_point": assembly_info,
        "command_contact": command_contact,
        "nearby_services": {
            "nearest_police_station": {
                "name": nearest_police.get("name", ""),
                "eta_minutes": nearest_police.get("eta_minutes", ""),
                "phone": nearest_police.get("phone", ""),
            } if nearest_police else None,
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
        "emergency_notice": "If not already done, ensure 911 has been contacted.",
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

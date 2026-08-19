"""Tools for the Safety & Resource Intelligence Agent.

Queries the in-memory knowledge base (loaded from CSVs) or Firestore in production.
"""

from __future__ import annotations

from typing import Any

from src.core.knowledge_base import KnowledgeBase


def find_safe_routes(
    facility_id: str,
    from_zone: str,
    blocked_zones: str = "",
) -> dict[str, Any]:
    """Find safe evacuation routes from a zone, excluding routes blocked by affected zones.

    Args:
        facility_id: The facility ID (e.g. 'jefferson').
        from_zone: Starting zone ID (e.g. 'east-wing-f2').
        blocked_zones: Comma-separated zone IDs that are currently blocked/affected.

    Returns:
        Available routes with exit info, descriptions, and accessibility.
    """
    kb = KnowledgeBase.get()
    blocked = [z.strip() for z in blocked_zones.split(",") if z.strip()] if blocked_zones else []
    routes = kb.get_routes_from_zone(facility_id, from_zone, blocked)

    return {
        "facility_id": facility_id,
        "from_zone": from_zone,
        "blocked_zones_excluded": blocked,
        "routes": [
            {
                "name": r["name"],
                "to_exit": r["to_exit"],
                "description": r.get("route_description", ""),
                "accessibility": r.get("accessibility", "standard"),
            }
            for r in routes
        ],
        "total_routes": len(routes),
        "source": "knowledge_base.evacuation_routes",
        "note": "Only pre-approved, organization-authored routes. Blocked routes excluded.",
    }


def find_zone_info(
    facility_id: str,
    zone_id: str,
) -> dict[str, Any]:
    """Get full zone details including exits, shelter location, and rooms.

    Args:
        facility_id: The facility ID.
        zone_id: The zone to query (e.g. 'west-wing-f2').

    Returns:
        Zone details: primary/alternate exits, shelter location, capacity, rooms, notes.
    """
    kb = KnowledgeBase.get()
    zone = kb.get_zone(zone_id)
    if not zone:
        return {"error": f"Zone '{zone_id}' not found", "source": "knowledge_base.zones"}

    rooms = kb.get_rooms_by_zone(zone_id)
    personnel = kb.get_personnel_by_zone(zone_id)

    return {
        "zone_id": zone_id,
        "name": zone["name"],
        "floor": zone.get("floor"),
        "zone_type": zone.get("zone_type"),
        "primary_exit": zone.get("primary_exit"),
        "alternate_exit": zone.get("alternate_exit"),
        "shelter_location": zone.get("shelter_location"),
        "capacity": zone.get("capacity"),
        "notes": zone.get("notes", ""),
        "rooms": [{"room_id": r["room_id"], "name": r["name"], "type": r.get("room_type")} for r in rooms],
        "personnel_count": len(personnel),
        "source": "knowledge_base.zones",
    }


def find_blocked_zones(
    facility_id: str,
    incident_zone: str = "",
) -> dict[str, Any]:
    """Determine which routes are blocked based on the incident zone.

    Args:
        facility_id: The facility ID.
        incident_zone: Zone ID where the incident is occurring.

    Returns:
        List of blocked routes and alternative routes from affected zones.
    """
    kb = KnowledgeBase.get()
    affected_zones = [incident_zone] if incident_zone else []

    blocked_routes = kb.get_blocked_routes(facility_id, affected_zones) if affected_zones else []

    # For each blocked route, find alternatives from the same origin zone
    alternatives = {}
    for br in blocked_routes:
        origin = br["from_zone"]
        if origin not in alternatives:
            alt_routes = kb.get_routes_from_zone(facility_id, origin, affected_zones)
            alternatives[origin] = [
                {"name": r["name"], "to_exit": r["to_exit"], "accessibility": r.get("accessibility")}
                for r in alt_routes
            ]

    return {
        "facility_id": facility_id,
        "incident_zone": incident_zone,
        "blocked_routes": [
            {
                "name": r["name"],
                "from_zone": r["from_zone"],
                "to_exit": r["to_exit"],
                "reason": f"Blocked by incident in zone: {incident_zone}",
            }
            for r in blocked_routes
        ],
        "alternative_routes": alternatives,
        "source": "knowledge_base.evacuation_routes.blocked_by_zones",
    }


def locate_resource(
    facility_id: str,
    resource_type: str,
    near_zone: str = "",
    floor: int = 0,
) -> dict[str, Any]:
    """Locate emergency resources (AED, first aid kit, fire extinguisher, trauma kit, emergency phone).

    Args:
        facility_id: The facility ID.
        resource_type: Type of resource (aed, first_aid_kit, fire_extinguisher, trauma_kit, emergency_phone).
        near_zone: Optional zone ID to prioritize closest resources.
        floor: Optional floor filter (0 = all floors).

    Returns:
        List of matching resources with location descriptions and zone IDs.
    """
    kb = KnowledgeBase.get()

    # First try filtered by zone, then by floor, then all
    resources = kb.get_resources(facility_id, resource_type, zone_id=near_zone)
    if not resources and floor:
        resources = kb.get_resources(facility_id, resource_type, floor=floor)
    if not resources:
        resources = kb.get_resources(facility_id, resource_type)

    return {
        "facility_id": facility_id,
        "resource_type": resource_type,
        "near_zone": near_zone,
        "resources": [
            {
                "type": r["resource_type"],
                "location": r["location_description"],
                "floor": r.get("floor"),
                "zone_id": r.get("zone_id"),
                "notes": r.get("notes", ""),
            }
            for r in resources
        ],
        "total_found": len(resources),
        "source": "knowledge_base.emergency_resources",
    }


def find_assembly_point(
    facility_id: str,
    primary_only: bool = False,
) -> dict[str, Any]:
    """Find designated assembly/rally points for evacuees.

    Args:
        facility_id: The facility ID.
        primary_only: If true, only return the primary assembly point.

    Returns:
        Assembly point details: location, capacity, accessibility, notes.
    """
    kb = KnowledgeBase.get()
    points = kb.get_assembly_points(facility_id, primary_only)

    return {
        "facility_id": facility_id,
        "assembly_points": [
            {
                "id": ap.get("point_id"),
                "name": ap["name"],
                "location": ap.get("location_description", ""),
                "capacity": ap.get("capacity"),
                "is_primary": ap.get("is_primary"),
                "accessibility": ap.get("accessibility", "standard"),
                "notes": ap.get("notes", ""),
            }
            for ap in points
        ],
        "total_found": len(points),
        "source": "knowledge_base.assembly_points",
    }


def find_nearby_services(
    service_type: str = "",
) -> dict[str, Any]:
    """Find nearby emergency services (hospitals, fire stations, police, trauma centers).

    Args:
        service_type: Filter by type (hospital, trauma_center, police_station, fire_station, urgent_care). Empty for all.

    Returns:
        Nearby services with address, phone, distance, ETA, and capabilities.
    """
    kb = KnowledgeBase.get()
    services = kb.get_nearby_services(service_type)

    return {
        "service_type": service_type or "all",
        "services": [
            {
                "type": s["service_type"],
                "name": s["name"],
                "address": s.get("address", ""),
                "phone": s.get("phone", ""),
                "distance_miles": s.get("distance_miles"),
                "eta_minutes": s.get("eta_minutes"),
                "trauma_level": s.get("trauma_level", ""),
                "helipad": s.get("helipad", ""),
            }
            for s in services
        ],
        "total_found": len(services),
        "source": "knowledge_base.nearby_services",
    }


def find_accessible_routes(
    facility_id: str,
    from_zone: str,
) -> dict[str, Any]:
    """Find wheelchair-accessible evacuation routes from a zone.

    Args:
        facility_id: The facility ID.
        from_zone: Starting zone ID.

    Returns:
        Accessible routes (elevator routes, accessible exits).
    """
    kb = KnowledgeBase.get()
    routes = kb.get_accessible_routes(facility_id, from_zone)

    return {
        "facility_id": facility_id,
        "from_zone": from_zone,
        "accessible_routes": [
            {
                "name": r["name"],
                "to_exit": r["to_exit"],
                "description": r.get("route_description", ""),
            }
            for r in routes
        ],
        "total_found": len(routes),
        "source": "knowledge_base.evacuation_routes[accessibility=wheelchair_accessible]",
        "note": "Elevator routes require key from main office." if routes else "No accessible routes found from this zone.",
    }

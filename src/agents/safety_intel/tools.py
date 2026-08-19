"""Tools for the Safety & Resource Intelligence Agent.

These tools query the Firestore knowledge base populated from CSVs:
- zones, rooms, evacuation_routes, emergency_resources, assembly_points, nearby_services
"""

from __future__ import annotations

from typing import Any


def find_safe_routes(
    facility_id: str,
    from_zone: str,
    incident_type: str = "",
) -> dict[str, Any]:
    """Find safe evacuation routes from a zone, considering blocked zones.

    Args:
        facility_id: The facility ID (e.g. 'jefferson').
        from_zone: Starting zone ID (e.g. 'east-wing-f2').
        incident_type: The incident type to assess route suitability.

    Returns:
        Available routes with exit info, descriptions, and accessibility.
    """
    # Stub — queries Firestore evacuation_routes collection
    # Filters out routes whose blocked_by_zones overlap with affected zones
    return {
        "facility_id": facility_id,
        "from_zone": from_zone,
        "routes": [],
        "source": "knowledge_base.evacuation_routes",
        "note": "Only pre-approved, organization-authored routes are returned. Routes blocked by affected zones are excluded.",
    }


def find_zone_info(
    facility_id: str,
    zone_id: str,
) -> dict[str, Any]:
    """Get full zone details including exits and shelter location.

    Args:
        facility_id: The facility ID.
        zone_id: The zone to query (e.g. 'west-wing-f2').

    Returns:
        Zone details: primary/alternate exits, shelter location, capacity, notes.
    """
    return {
        "facility_id": facility_id,
        "zone_id": zone_id,
        "zone": None,
        "source": "knowledge_base.zones",
    }


def find_blocked_zones(
    facility_id: str,
    incident_location: str = "",
) -> dict[str, Any]:
    """Determine which zones and routes are blocked based on incident location.

    Args:
        facility_id: The facility ID.
        incident_location: Zone or room where the incident is occurring.

    Returns:
        List of blocked zones and affected routes.
    """
    return {
        "facility_id": facility_id,
        "incident_location": incident_location,
        "blocked_zones": [],
        "affected_routes": [],
        "source": "knowledge_base.zones + evacuation_routes.blocked_by_zones",
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
        near_zone: Optional zone to prioritize closest resource.
        floor: Optional floor filter.

    Returns:
        List of matching resources with location descriptions and zone IDs.
    """
    return {
        "facility_id": facility_id,
        "resource_type": resource_type,
        "near_zone": near_zone,
        "floor": floor,
        "resources": [],
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
    return {
        "facility_id": facility_id,
        "primary_only": primary_only,
        "assembly_points": [],
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
    return {
        "service_type": service_type or "all",
        "services": [],
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
    return {
        "facility_id": facility_id,
        "from_zone": from_zone,
        "accessible_routes": [],
        "source": "knowledge_base.evacuation_routes[accessibility=wheelchair_accessible]",
        "note": "Elevator routes require key from main office.",
    }

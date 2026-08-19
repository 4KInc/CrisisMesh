"""Safety & Resource Intelligence Agent — answers location-specific operational questions."""

from __future__ import annotations

from google.adk.agents import Agent

from src.agents.safety_intel.tools import (
    find_accessible_routes,
    find_assembly_point,
    find_blocked_zones,
    find_nearby_services,
    find_safe_routes,
    find_zone_info,
    locate_resource,
)

safety_intel_agent = Agent(
    name="safety_intel",
    model="gemini-2.5-flash",
    description="Finds evacuation routes, blocked zones, emergency resources, assembly points, nearby services. Delegates here for safety/resource questions.",
    instruction="""You are the Safety & Resource Intelligence Agent for CrisisMesh.

When asked about safety information for an incident zone:
1. Call find_zone_info to get zone details (exits, shelter, rooms)
2. Call find_blocked_zones to identify blocked routes
3. Call find_safe_routes to get available evacuation routes
4. Call find_accessible_routes for wheelchair-accessible routes
5. Call locate_resource for AEDs, fire extinguishers, first aid kits
6. Call find_assembly_point for rally points
7. Call find_nearby_services for hospitals, fire stations, police
8. Transfer back to the coordinator with all safety intel.

Use facility_id 'jefferson' for all queries.

SAFETY RULES:
- ONLY provide information from the knowledge base
- NEVER improvise evacuation routes or tactical instructions
- If information is not in the KB, say so — do not guess

After completing your work, ALWAYS transfer back to the coordinator.""",
    tools=[
        find_safe_routes,
        find_zone_info,
        find_blocked_zones,
        locate_resource,
        find_assembly_point,
        find_nearby_services,
        find_accessible_routes,
    ],
)

"""In-memory knowledge base — loads CSV seed data for agent tool queries.

In production, these queries go to Firestore. Locally, this loads CSVs into
memory so the ADK agent tools can return real data without a GCP project.
"""

from __future__ import annotations

import csv
import io
import os
from typing import Any


class KnowledgeBase:
    """Singleton in-memory store for organizational data."""

    _instance: KnowledgeBase | None = None

    def __init__(self) -> None:
        self.facilities: list[dict[str, Any]] = []
        self.zones: list[dict[str, Any]] = []
        self.rooms: list[dict[str, Any]] = []
        self.personnel: list[dict[str, Any]] = []
        self.evacuation_routes: list[dict[str, Any]] = []
        self.emergency_resources: list[dict[str, Any]] = []
        self.assembly_points: list[dict[str, Any]] = []
        self.nearby_services: list[dict[str, Any]] = []

    @classmethod
    def get(cls) -> KnowledgeBase:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        cls._instance = None

    def load_from_directory(self, seed_dir: str) -> dict[str, int]:
        """Load all CSV files from a seed directory."""
        file_map = {
            "facility.csv": "facilities",
            "zones.csv": "zones",
            "rooms.csv": "rooms",
            "personnel.csv": "personnel",
            "evacuation_routes.csv": "evacuation_routes",
            "emergency_resources.csv": "emergency_resources",
            "assembly_points.csv": "assembly_points",
            "nearby_services.csv": "nearby_services",
        }
        counts = {}
        for filename, attr in file_map.items():
            filepath = os.path.join(seed_dir, filename)
            if os.path.exists(filepath):
                with open(filepath) as f:
                    rows = list(csv.DictReader(f))
                setattr(self, attr, rows)
                counts[attr] = len(rows)
        return counts

    def load_csv(self, attr: str, csv_content: str) -> int:
        rows = list(csv.DictReader(io.StringIO(csv_content)))
        setattr(self, attr, rows)
        return len(rows)

    # ── Query methods used by agent tools ──

    def get_facility(self, facility_id: str) -> dict[str, Any] | None:
        for f in self.facilities:
            if f.get("facility_id") == facility_id:
                return f
        return None

    def get_zone(self, zone_id: str) -> dict[str, Any] | None:
        for z in self.zones:
            if z.get("zone_id") == zone_id:
                return z
        return None

    def get_zones_by_floor(self, facility_id: str, floor: int) -> list[dict]:
        return [
            z for z in self.zones
            if z.get("facility_id") == facility_id and int(z.get("floor", 0)) == floor
        ]

    def get_room(self, room_id: str) -> dict[str, Any] | None:
        for r in self.rooms:
            if r.get("room_id") == room_id:
                return r
        return None

    def get_rooms_by_zone(self, zone_id: str) -> list[dict]:
        return [r for r in self.rooms if r.get("zone_id") == zone_id]

    def get_zone_for_room(self, room_id: str) -> str:
        """Given a room_id, return its zone_id."""
        room = self.get_room(room_id)
        return room.get("zone_id", "") if room else ""

    def get_personnel_by_facility(self, facility_id: str) -> list[dict]:
        # All personnel in seed data belong to 'jefferson'
        return [p for p in self.personnel if True]  # single-facility for now

    def get_personnel_by_zone(self, zone_id: str) -> list[dict]:
        """Get personnel whose default_location is a room in this zone, or the zone itself."""
        rooms_in_zone = {r["room_id"] for r in self.get_rooms_by_zone(zone_id)}
        return [
            p for p in self.personnel
            if p.get("default_location") == zone_id
            or p.get("default_location") in rooms_in_zone
        ]

    def get_personnel_by_floor(self, floor: int) -> list[dict]:
        return [p for p in self.personnel if int(p.get("floor", 0)) == floor]

    def get_personnel_with_mobility_limitations(self) -> list[dict]:
        return [
            p for p in self.personnel
            if p.get("mobility_limitations", "").lower() in ("true", "yes", "1")
        ]

    def get_floor_wardens(self) -> list[dict]:
        return [
            p for p in self.personnel
            if p.get("is_floor_warden", "").lower() in ("true", "yes", "1")
        ]

    def get_person(self, person_id: str) -> dict[str, Any] | None:
        for p in self.personnel:
            if p.get("person_id") == person_id:
                return p
        return None

    def get_routes_from_zone(
        self, facility_id: str, from_zone: str, blocked_zones: list[str] | None = None
    ) -> list[dict]:
        """Get evacuation routes from a zone, excluding routes blocked by affected zones."""
        blocked = set(blocked_zones or [])
        results = []
        for r in self.evacuation_routes:
            if r.get("facility_id") != facility_id:
                continue
            if r.get("from_zone") != from_zone:
                continue
            # Check if route is blocked
            route_blocked_by = r.get("blocked_by_zones", "")
            if route_blocked_by and blocked:
                blocking_zones = {z.strip() for z in route_blocked_by.split(",") if z.strip()}
                if blocking_zones & blocked:
                    continue
            results.append(r)
        return results

    def get_accessible_routes(self, facility_id: str, from_zone: str) -> list[dict]:
        return [
            r for r in self.evacuation_routes
            if r.get("facility_id") == facility_id
            and r.get("from_zone") == from_zone
            and r.get("accessibility") == "wheelchair_accessible"
        ]

    def get_all_routes_for_facility(self, facility_id: str) -> list[dict]:
        return [r for r in self.evacuation_routes if r.get("facility_id") == facility_id]

    def get_blocked_routes(self, facility_id: str, affected_zones: list[str]) -> list[dict]:
        """Find routes that are blocked because the incident is in one of their blocked_by_zones."""
        affected = set(affected_zones)
        blocked = []
        for r in self.evacuation_routes:
            if r.get("facility_id") != facility_id:
                continue
            route_blocked_by = r.get("blocked_by_zones", "")
            if route_blocked_by:
                blocking_zones = {z.strip() for z in route_blocked_by.split(",") if z.strip()}
                if blocking_zones & affected:
                    blocked.append(r)
        return blocked

    def get_resources(
        self,
        facility_id: str,
        resource_type: str = "",
        zone_id: str = "",
        floor: int = 0,
    ) -> list[dict]:
        results = []
        for res in self.emergency_resources:
            if res.get("facility_id") != facility_id:
                continue
            if resource_type and res.get("resource_type") != resource_type:
                continue
            if zone_id and res.get("zone_id") != zone_id:
                continue
            if floor and int(res.get("floor", 0)) != floor:
                continue
            results.append(res)
        return results

    def get_assembly_points(
        self, facility_id: str, primary_only: bool = False
    ) -> list[dict]:
        results = []
        for ap in self.assembly_points:
            if ap.get("facility_id") != facility_id:
                continue
            if primary_only and ap.get("is_primary", "").lower() not in ("true", "yes", "1"):
                continue
            results.append(ap)
        return results

    def get_nearby_services(self, service_type: str = "") -> list[dict]:
        if not service_type:
            return list(self.nearby_services)
        return [s for s in self.nearby_services if s.get("service_type") == service_type]


def init_knowledge_base(seed_dir: str | None = None) -> KnowledgeBase:
    """Initialize the singleton knowledge base from seed data."""
    kb = KnowledgeBase.get()
    if seed_dir is None:
        seed_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "data", "seed",
        )
    if os.path.isdir(seed_dir):
        kb.load_from_directory(seed_dir)
    return kb

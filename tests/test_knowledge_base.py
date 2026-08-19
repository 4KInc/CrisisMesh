"""Tests for the in-memory knowledge base — verifies queries against real seed data."""

import os

import pytest

from src.core.knowledge_base import KnowledgeBase, init_knowledge_base

SEED_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "seed")


@pytest.fixture(autouse=True)
def fresh_kb():
    KnowledgeBase.reset()
    init_knowledge_base(SEED_DIR)
    yield
    KnowledgeBase.reset()


class TestDataLoading:
    def test_load_counts(self):
        kb = KnowledgeBase.get()
        assert len(kb.facilities) == 1
        assert len(kb.zones) == 8
        assert len(kb.rooms) == 22
        assert len(kb.personnel) == 34
        assert len(kb.evacuation_routes) == 13
        assert len(kb.emergency_resources) == 17
        assert len(kb.assembly_points) == 3
        assert len(kb.nearby_services) == 6


class TestFacilityQueries:
    def test_get_facility(self):
        kb = KnowledgeBase.get()
        f = kb.get_facility("jefferson")
        assert f is not None
        assert f["name"] == "Jefferson Elementary School"

    def test_get_zone(self):
        kb = KnowledgeBase.get()
        z = kb.get_zone("west-wing-f2")
        assert z is not None
        assert z["name"] == "West Wing Floor 2"
        assert "science lab" in z["notes"].lower()

    def test_get_zones_by_floor(self):
        kb = KnowledgeBase.get()
        floor2 = kb.get_zones_by_floor("jefferson", 2)
        assert len(floor2) == 3  # east-wing-f2, west-wing-f2, library

    def test_get_rooms_by_zone(self):
        kb = KnowledgeBase.get()
        rooms = kb.get_rooms_by_zone("east-wing-f1")
        assert len(rooms) == 8  # rooms 101-108

    def test_get_zone_for_room(self):
        kb = KnowledgeBase.get()
        zone = kb.get_zone_for_room("215")
        assert zone == "west-wing-f2"


class TestPersonnelQueries:
    def test_get_person(self):
        kb = KnowledgeBase.get()
        p = kb.get_person("p001")
        assert p is not None
        assert p["name"] == "Principal Johnson"
        assert p["evacuation_role"] == "Incident Commander"

    def test_personnel_by_zone(self):
        kb = KnowledgeBase.get()
        people = kb.get_personnel_by_zone("east-wing-f1")
        names = [p["name"] for p in people]
        assert "Mrs. Rodriguez" in names  # room 101
        assert "Nurse Sarah" in names  # room 108

    def test_personnel_by_floor(self):
        kb = KnowledgeBase.get()
        floor2 = kb.get_personnel_by_floor(2)
        assert len(floor2) >= 10  # all floor 2 teachers + librarian + IT + special ed aide

    def test_mobility_limitations(self):
        kb = KnowledgeBase.get()
        mobility = kb.get_personnel_with_mobility_limitations()
        names = [p["name"] for p in mobility]
        assert "Mrs. Davis" in names  # wheelchair
        assert "Mrs. Thompson" in names  # knee replacement

    def test_floor_wardens(self):
        kb = KnowledgeBase.get()
        wardens = kb.get_floor_wardens()
        assert len(wardens) >= 4  # one per wing per floor


class TestRouteQueries:
    def test_routes_from_zone(self):
        kb = KnowledgeBase.get()
        routes = kb.get_routes_from_zone("jefferson", "east-wing-f2")
        assert len(routes) >= 2  # primary + alternate + elevator

    def test_routes_with_blocked_zones(self):
        kb = KnowledgeBase.get()
        # east-wing-f1 primary route is blocked by "east-entrance"
        all_routes = kb.get_routes_from_zone("jefferson", "east-wing-f1")
        filtered = kb.get_routes_from_zone("jefferson", "east-wing-f1", ["east-entrance"])
        assert len(filtered) < len(all_routes)

    def test_accessible_routes(self):
        kb = KnowledgeBase.get()
        accessible = kb.get_accessible_routes("jefferson", "east-wing-f2")
        assert len(accessible) >= 1
        assert all(r["accessibility"] == "wheelchair_accessible" for r in accessible)

    def test_blocked_routes_detection(self):
        kb = KnowledgeBase.get()
        blocked = kb.get_blocked_routes("jefferson", ["east-entrance"])
        assert len(blocked) >= 1
        # Should block the east wing routes that have blocked_by_zones=east-entrance
        for r in blocked:
            assert "east-entrance" in r.get("blocked_by_zones", "")


class TestResourceQueries:
    def test_find_aeds(self):
        kb = KnowledgeBase.get()
        aeds = kb.get_resources("jefferson", "aed")
        assert len(aeds) == 3  # hallway B, main office, gym

    def test_find_resources_by_zone(self):
        kb = KnowledgeBase.get()
        admin_resources = kb.get_resources("jefferson", zone_id="admin-f1")
        types = [r["resource_type"] for r in admin_resources]
        assert "aed" in types
        assert "first_aid_kit" in types
        assert "trauma_kit" in types

    def test_find_fire_extinguishers(self):
        kb = KnowledgeBase.get()
        extinguishers = kb.get_resources("jefferson", "fire_extinguisher")
        assert len(extinguishers) == 6

    def test_find_resources_by_floor(self):
        kb = KnowledgeBase.get()
        floor2 = kb.get_resources("jefferson", "fire_extinguisher", floor=2)
        assert len(floor2) == 2  # east hallway F2 + west hallway F2


class TestAssemblyPoints:
    def test_all_points(self):
        kb = KnowledgeBase.get()
        points = kb.get_assembly_points("jefferson")
        assert len(points) == 3

    def test_primary_only(self):
        kb = KnowledgeBase.get()
        primary = kb.get_assembly_points("jefferson", primary_only=True)
        assert len(primary) == 1
        assert "Athletic Field" in primary[0]["name"]


class TestNearbyServices:
    def test_all_services(self):
        kb = KnowledgeBase.get()
        all_svcs = kb.get_nearby_services()
        assert len(all_svcs) == 6

    def test_fire_station(self):
        kb = KnowledgeBase.get()
        fire = kb.get_nearby_services("fire_station")
        assert len(fire) == 1
        assert int(fire[0]["eta_minutes"]) == 3

    def test_hospitals(self):
        kb = KnowledgeBase.get()
        hospitals = kb.get_nearby_services("hospital")
        assert len(hospitals) == 2

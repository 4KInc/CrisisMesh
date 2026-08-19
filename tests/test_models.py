"""Tests for data models — validates they match the real CSV schemas."""

from src.models.facility import (
    AssemblyPoint,
    EmergencyResource,
    EvacuationRoute,
    Facility,
    NearbyService,
    ResourceType,
    Room,
    Zone,
)
from src.models.person import Person, PersonStatus


class TestFacilityModels:
    def test_facility(self):
        f = Facility(id="jefferson", name="Jefferson Elementary School", floors=2, capacity=450)
        assert f.id == "jefferson"
        assert f.floors == 2

    def test_zone(self):
        z = Zone(
            id="east-wing-f1",
            facility_id="jefferson",
            name="East Wing Floor 1",
            floor=1,
            zone_type="classrooms",
            primary_exit="Door 3 (East Exit)",
            alternate_exit="Door 7 (Gym Exit)",
            shelter_location="Interior hallway A",
            capacity=120,
        )
        assert z.primary_exit == "Door 3 (East Exit)"
        assert z.zone_type == "classrooms"

    def test_room(self):
        r = Room(
            id="215",
            facility_id="jefferson",
            name="Room 215 - Science Lab",
            floor=2,
            zone_id="west-wing-f2",
            room_type="laboratory",
            capacity=25,
        )
        assert r.zone_id == "west-wing-f2"
        assert r.room_type == "laboratory"

    def test_evacuation_route(self):
        route = EvacuationRoute(
            facility_id="jefferson",
            name="East Wing F2 Elevator",
            from_zone="east-wing-f2",
            to_exit="Door 2 (Main Entrance)",
            accessibility="wheelchair_accessible",
            blocked_by_zones="",
        )
        assert route.accessibility == "wheelchair_accessible"

    def test_evacuation_route_with_blocked_zones(self):
        route = EvacuationRoute(
            facility_id="jefferson",
            name="East Wing F1 Primary",
            from_zone="east-wing-f1",
            to_exit="Door 3 (East Exit)",
            blocked_by_zones="east-entrance",
        )
        assert route.blocked_by_zones == "east-entrance"

    def test_emergency_resource(self):
        res = EmergencyResource(
            facility_id="jefferson",
            resource_type=ResourceType.AED,
            location_description="Main office lobby next to front desk",
            floor=1,
            zone_id="admin-f1",
        )
        assert res.resource_type == "aed"
        assert res.zone_id == "admin-f1"

    def test_assembly_point(self):
        ap = AssemblyPoint(
            id="ap-field",
            facility_id="jefferson",
            name="Athletic Field (Primary)",
            location_description="Behind the gym past the track 500ft from building",
            capacity=500,
            is_primary=True,
        )
        assert ap.is_primary is True
        assert ap.capacity == 500

    def test_nearby_service(self):
        svc = NearbyService(
            service_type="hospital",
            name="TriStar Centennial Medical Center",
            distance_miles=1.8,
            eta_minutes=5,
            trauma_level="Level II",
            helipad=False,
        )
        assert svc.eta_minutes == 5
        assert svc.helipad is False


class TestPersonModel:
    def test_person_with_mobility(self):
        p = Person(
            id="p008",
            name="Mrs. Davis",
            slack_user_id="U_DAVIS",
            role="4th Grade Teacher",
            department="Teaching",
            default_location="104",
            floor=1,
            medical_notes="Uses wheelchair — elevator required for evacuation",
            mobility_limitations=True,
        )
        assert p.mobility_limitations is True
        assert "wheelchair" in p.medical_notes

    def test_person_with_evacuation_role(self):
        p = Person(
            id="p001",
            name="Principal Johnson",
            evacuation_role="Incident Commander",
            is_floor_warden=True,
            trained_first_aid=True,
        )
        assert p.evacuation_role == "Incident Commander"
        assert p.is_floor_warden is True

    def test_person_default_status(self):
        p = Person(id="p006", name="Mr. Chen")
        assert p.status == PersonStatus.UNKNOWN

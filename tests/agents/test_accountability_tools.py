"""Tests for Accountability Agent tools."""

import os

import pytest

from src.core.knowledge_base import KnowledgeBase, init_knowledge_base
from src.agents.accountability.tools import (
    _checkin_store,
    compute_accountability_summary,
    escalate_missing_checkins,
    process_checkin,
    read_roster,
    send_checkin_request,
)

SEED_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "seed",
)


@pytest.fixture(autouse=True)
def fresh_kb():
    KnowledgeBase.reset()
    init_knowledge_base(SEED_DIR)
    _checkin_store.clear()
    yield
    KnowledgeBase.reset()
    _checkin_store.clear()


class TestReadRoster:
    def test_full_roster(self):
        result = read_roster("jefferson")
        assert result["total_personnel"] == 34

    def test_roster_by_zone(self):
        result = read_roster("jefferson", zone="east-wing-f1")
        assert result["total_personnel"] >= 7  # rooms 101-108
        names = [p["name"] for p in result["personnel"]]
        assert "Mrs. Rodriguez" in names

    def test_roster_by_floor(self):
        result = read_roster("jefferson", floor=2)
        assert result["total_personnel"] >= 10

    def test_mobility_needs_flagged(self):
        result = read_roster("jefferson")
        mobility_names = [p["name"] for p in result["mobility_needs"]]
        assert "Mrs. Davis" in mobility_names

    def test_wardens_listed(self):
        result = read_roster("jefferson")
        warden_names = [w["name"] for w in result["floor_wardens_and_leads"]]
        assert "Principal Johnson" in warden_names  # Incident Commander
        assert "Mrs. Rodriguez" in warden_names  # Floor Warden - East F1


class TestAccountability:
    def test_send_checkin_by_zone(self):
        result = send_checkin_request("INC-001", zone="east-wing-f1")
        assert result["requests_sent"] >= 7

    def test_send_checkin_by_ids(self):
        result = send_checkin_request("INC-001", person_ids="p001,p002,p003")
        assert result["requests_sent"] == 3

    def test_process_checkin_with_name(self):
        result = process_checkin("INC-002", "p001", "safe", "Assembly Point A")
        assert result["recorded"] is True
        assert result["name"] == "Principal Johnson"

    def test_compute_summary(self):
        send_checkin_request("INC-003", person_ids="p001,p002,p003")
        process_checkin("INC-003", "p001", "safe")
        process_checkin("INC-003", "p002", "evacuated")
        result = compute_accountability_summary("INC-003")
        assert result["total_tracked"] == 3
        assert result["accounted"] == 2
        assert result["unaccounted"] == 1

    def test_escalate_missing(self):
        send_checkin_request("INC-004", person_ids="p008,p021")
        process_checkin("INC-004", "p008", "safe")
        result = escalate_missing_checkins("INC-004")
        assert result["missing_count"] == 1
        missing_names = [p["name"] for p in result["missing_personnel"]]
        assert "Mrs. Thompson" in missing_names

    def test_escalate_flags_mobility(self):
        send_checkin_request("INC-005", person_ids="p008,p021")
        # p008 = Mrs. Davis (wheelchair), p021 = Mrs. Thompson (knee)
        # Neither checks in
        result = escalate_missing_checkins("INC-005")
        assert result["missing_count"] == 2
        assert len(result["missing_with_mobility_needs"]) == 2


class TestSafetyIntelTools:
    def test_find_safe_routes(self):
        from src.agents.safety_intel.tools import find_safe_routes

        result = find_safe_routes("jefferson", "east-wing-f2")
        assert result["total_routes"] >= 2

    def test_routes_exclude_blocked(self):
        from src.agents.safety_intel.tools import find_safe_routes

        all_routes = find_safe_routes("jefferson", "east-wing-f1")
        filtered = find_safe_routes("jefferson", "east-wing-f1", blocked_zones="east-entrance")
        assert filtered["total_routes"] < all_routes["total_routes"]

    def test_find_zone_info(self):
        from src.agents.safety_intel.tools import find_zone_info

        result = find_zone_info("jefferson", "west-wing-f2")
        assert result["name"] == "West Wing Floor 2"
        assert result["primary_exit"] == "West Stairwell to Door 1"
        assert result["personnel_count"] >= 3

    def test_locate_aed(self):
        from src.agents.safety_intel.tools import locate_resource

        result = locate_resource("jefferson", "aed")
        assert result["total_found"] == 3

    def test_locate_resource_by_zone(self):
        from src.agents.safety_intel.tools import locate_resource

        result = locate_resource("jefferson", "fire_extinguisher", near_zone="west-wing-f2")
        assert result["total_found"] >= 1

    def test_find_assembly_points(self):
        from src.agents.safety_intel.tools import find_assembly_point

        result = find_assembly_point("jefferson")
        assert result["total_found"] == 3
        primary = find_assembly_point("jefferson", primary_only=True)
        assert primary["total_found"] == 1

    def test_find_nearby_fire_station(self):
        from src.agents.safety_intel.tools import find_nearby_services

        result = find_nearby_services("fire_station")
        assert result["total_found"] == 1
        assert int(result["services"][0]["eta_minutes"]) == 3

    def test_find_accessible_routes(self):
        from src.agents.safety_intel.tools import find_accessible_routes

        result = find_accessible_routes("jefferson", "east-wing-f2")
        assert result["total_found"] >= 1

    def test_find_blocked_zones(self):
        from src.agents.safety_intel.tools import find_blocked_zones

        result = find_blocked_zones("jefferson", "east-entrance")
        assert len(result["blocked_routes"]) >= 1


class TestComplianceRedaction:
    def test_redact_sensitive(self):
        from src.agents.compliance.tools import redact_sensitive_fields

        data = {
            "name": "Mrs. Davis",
            "medical_notes": "Uses wheelchair",
            "phone": "615-555-0116",
            "role": "Teacher",
            "emergency_contact_name": "Robert Davis",
        }
        result = redact_sensitive_fields(data, context="general")
        assert result["data"]["medical_notes"] == "[REDACTED]"
        assert result["data"]["phone"] == "[REDACTED]"
        assert result["data"]["emergency_contact_name"] == "[REDACTED]"
        assert result["data"]["name"] == "Mrs. Davis"
        assert result["data"]["role"] == "Teacher"

    def test_commander_sees_all(self):
        from src.agents.compliance.tools import redact_sensitive_fields

        data = {"medical_notes": "Uses wheelchair", "name": "Mrs. Davis"}
        result = redact_sensitive_fields(data, context="commander")
        assert result["data"]["medical_notes"] == "Uses wheelchair"


class TestPolicyCheck:
    def test_allowed_tool(self):
        from src.agents.compliance.tools import check_policy

        result = check_policy("intake", "classify_incident")
        assert result["allowed"] is True

    def test_denied_tool(self):
        from src.agents.compliance.tools import check_policy

        result = check_policy("accountability", "send_external_message")
        assert result["allowed"] is False

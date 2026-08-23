"""Tests for SITREP & Handoff Agent tools."""

import os

import pytest

from src.core.knowledge_base import KnowledgeBase, init_knowledge_base

SEED_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "seed",
)


@pytest.fixture(autouse=True)
def fresh_kb():
    KnowledgeBase.reset()
    init_knowledge_base(SEED_DIR)
    yield
    KnowledgeBase.reset()


class TestGenerateSitrep:
    def test_sitrep_structure(self):
        from src.agents.sitrep.tools import generate_sitrep

        result = generate_sitrep(
            incident_id="FIRE-2026-001",
            incident_type="fire",
            severity="high",
            location="West Wing Floor 2 — Science Lab",
            accountability={"total_tracked": 34, "accounted": 30, "counts": {"safe": 28, "evacuated": 2}},
            blocked_zones="east-entrance",
        )
        assert result["type"] == "IC_SITREP"
        assert result["accountability"]["unaccounted"] == 4
        assert result["requires_commander_review"] is True

    def test_sitrep_includes_nearby_services(self):
        from src.agents.sitrep.tools import generate_sitrep

        result = generate_sitrep(
            incident_id="FIRE-2026-001",
            incident_type="fire",
            severity="high",
            location="Science Lab",
            accountability={"total_tracked": 34, "accounted": 34},
        )
        services = result["nearby_services"]
        assert services["nearest_fire_station"]["name"]
        assert services["nearest_hospital"]["name"]


class TestResponderCard:
    def test_responder_card_enrichment(self):
        from src.agents.sitrep.tools import generate_responder_card

        result = generate_responder_card(
            incident_id="FIRE-2026-001",
            incident_type="fire",
            severity="high",
            location="West Wing Floor 2 — Science Lab",
            time_declared="2026-08-19T14:30:00Z",
            accountability={"total_tracked": 34, "accounted": 30, "counts": {"injured": 1, "need_help": 2}},
            incident_zone="west-wing-f2",
        )
        assert result["REQUIRES_COMMANDER_APPROVAL"] is True
        # Should include facility address
        assert "1200 Oak Street" in result["location"]
        # Should include people needing assistance
        assert len(result["people_needing_assistance"]) >= 1
        # Should have command contact
        assert "Principal Johnson" in result["command_contact"]
        # Should include assembly point
        assert result["assembly_point"]
        # Should have resources
        assert len(result["on_site_resources"]) >= 3

    def test_responder_card_has_routes(self):
        from src.agents.sitrep.tools import generate_responder_card

        result = generate_responder_card(
            incident_id="FIRE-2026-001",
            incident_type="fire",
            severity="high",
            location="Science Lab",
            time_declared="2026-08-19T14:30:00Z",
            accountability={"total_tracked": 34, "accounted": 34, "counts": {}},
            incident_zone="west-wing-f2",
        )
        assert len(result["safe_routes"]) >= 1


class TestArrivalBrief:
    def test_arrival_brief_structure(self):
        from src.agents.sitrep.tools import generate_arrival_brief

        result = generate_arrival_brief(
            incident_id="FIRE-2026-001",
            incident_type="fire",
            severity="high",
            location="West Wing Floor 2 — Science Lab",
            time_declared="2026-08-19T14:30:00Z",
            accountability={"total_tracked": 34, "accounted": 30, "counts": {"injured": 1, "need_help": 2}},
            incident_zone="west-wing-f2",
        )
        assert result["type"] == "ARRIVAL_BRIEF"
        assert result["REQUIRES_COMMANDER_APPROVAL"] is True
        assert "NO tactical directives" in result["scope_notice"]
        assert "NO movement/entry instructions" in result["scope_notice"]
        assert result["incident"]["type"] == "fire"
        assert result["incident"]["severity"] == "high"
        assert result["headcount"]["unaccounted"] == 4
        assert result["headcount"]["injured"] == 1
        assert result["headcount"]["need_help"] == 2
        assert result["emergency_notice"]

    def test_arrival_brief_includes_facility_address(self):
        from src.agents.sitrep.tools import generate_arrival_brief

        result = generate_arrival_brief(
            incident_id="FIRE-2026-001",
            incident_type="fire",
            severity="high",
            location="Science Lab",
            time_declared="2026-08-19T14:30:00Z",
            accountability={"total_tracked": 34, "accounted": 34, "counts": {}},
        )
        assert "1200 Oak Street" in result["incident"]["location"]

    def test_arrival_brief_people_needing_assistance(self):
        from src.agents.sitrep.tools import generate_arrival_brief

        result = generate_arrival_brief(
            incident_id="FIRE-2026-001",
            incident_type="fire",
            severity="high",
            location="Science Lab",
            time_declared="2026-08-19T14:30:00Z",
            accountability={"total_tracked": 34, "accounted": 34, "counts": {}},
            incident_zone="west-wing-f2",
        )
        assert len(result["people_needing_assistance"]) >= 1
        for person in result["people_needing_assistance"]:
            assert person["has_mobility_limitation"] is True
            assert "name" in person
            assert "last_known_location" in person

    def test_arrival_brief_no_threat_observation_by_default(self):
        from src.agents.sitrep.tools import generate_arrival_brief

        result = generate_arrival_brief(
            incident_id="FIRE-2026-001",
            incident_type="fire",
            severity="high",
            location="Science Lab",
            time_declared="2026-08-19T14:30:00Z",
            accountability={"total_tracked": 34, "accounted": 34, "counts": {}},
        )
        assert result["threat_observation"] is None

    def test_arrival_brief_threat_observation_unconfirmed(self):
        from src.agents.sitrep.tools import generate_arrival_brief

        result = generate_arrival_brief(
            incident_id="THREAT-2026-001",
            incident_type="active_threat",
            severity="critical",
            location="Main Building",
            time_declared="2026-08-19T14:30:00Z",
            accountability={"total_tracked": 34, "accounted": 30, "counts": {}},
            reported_threat_location="Room 204",
            threat_last_seen_time="14:32",
        )
        obs = result["threat_observation"]
        assert obs is not None
        assert "UNCONFIRMED" in obs["status"]
        assert obs["last_reported_location"] == "Room 204"
        assert obs["last_reported_time"] == "14:32"
        assert "unverified" in obs["caveat"].lower()

    def test_arrival_brief_has_floor_summary(self):
        from src.agents.sitrep.tools import generate_arrival_brief

        result = generate_arrival_brief(
            incident_id="FIRE-2026-001",
            incident_type="fire",
            severity="high",
            location="Science Lab",
            time_declared="2026-08-19T14:30:00Z",
            accountability={"total_tracked": 34, "accounted": 34, "counts": {}},
        )
        assert len(result["floor_summary"]) >= 1
        for floor in result["floor_summary"]:
            assert "floor" in floor
            assert "zones" in floor
            assert "personnel_assigned" in floor

    def test_arrival_brief_has_floor_wardens(self):
        from src.agents.sitrep.tools import generate_arrival_brief

        result = generate_arrival_brief(
            incident_id="FIRE-2026-001",
            incident_type="fire",
            severity="high",
            location="Science Lab",
            time_declared="2026-08-19T14:30:00Z",
            accountability={"total_tracked": 34, "accounted": 34, "counts": {}},
        )
        assert isinstance(result["floor_wardens"], list)

    def test_arrival_brief_egress_structure(self):
        from src.agents.sitrep.tools import generate_arrival_brief

        result = generate_arrival_brief(
            incident_id="FIRE-2026-001",
            incident_type="fire",
            severity="high",
            location="Science Lab",
            time_declared="2026-08-19T14:30:00Z",
            accountability={"total_tracked": 34, "accounted": 34, "counts": {}},
            incident_zone="west-wing-f2",
        )
        assert "safe_routes" in result["egress"]
        assert "blocked_routes" in result["egress"]
        assert "accessible_routes" in result["egress"]

    def test_arrival_brief_has_resources(self):
        from src.agents.sitrep.tools import generate_arrival_brief

        result = generate_arrival_brief(
            incident_id="FIRE-2026-001",
            incident_type="fire",
            severity="high",
            location="Science Lab",
            time_declared="2026-08-19T14:30:00Z",
            accountability={"total_tracked": 34, "accounted": 34, "counts": {}},
        )
        assert len(result["on_site_resources"]) >= 3
        assert result["command_contact"]
        assert result["assembly_point"]
        assert result["nearby_services"]["nearest_fire_station"]["name"]
        assert result["nearby_services"]["nearest_hospital"]["name"]

    def test_arrival_brief_no_medical_details(self):
        """Arrival brief must NOT leak medical notes — only mobility-limitation flag."""
        from src.agents.sitrep.tools import generate_arrival_brief

        result = generate_arrival_brief(
            incident_id="FIRE-2026-001",
            incident_type="fire",
            severity="high",
            location="Science Lab",
            time_declared="2026-08-19T14:30:00Z",
            accountability={"total_tracked": 34, "accounted": 34, "counts": {}},
        )
        brief_str = str(result)
        assert "medical_notes" not in brief_str


class TestExtractThreatObservation:
    def test_last_seen_heading_toward(self):
        from src.agents.sitrep.tools import extract_threat_observation

        result = extract_threat_observation(
            "Active shooter in west wing. Last seen heading toward east hallway."
        )
        assert "east hallway" in result

    def test_last_seen_near(self):
        from src.agents.sitrep.tools import extract_threat_observation

        result = extract_threat_observation(
            "Gunman reported. Last seen near the cafeteria entrance."
        )
        assert "cafeteria" in result

    def test_shooter_spotted_in(self):
        from src.agents.sitrep.tools import extract_threat_observation

        result = extract_threat_observation(
            "Shooter spotted in Room 204, students sheltering."
        )
        assert "Room 204" in result

    def test_gunshots_heard_from(self):
        from src.agents.sitrep.tools import extract_threat_observation

        result = extract_threat_observation(
            "Multiple gunshots heard from the east wing hallway."
        )
        assert "east wing hallway" in result

    def test_no_threat_in_fire_report(self):
        from src.agents.sitrep.tools import extract_threat_observation

        result = extract_threat_observation(
            "Fire in the cafeteria, smoke visible from east wing."
        )
        assert result == ""

    def test_suspect_reported_at(self):
        from src.agents.sitrep.tools import extract_threat_observation

        result = extract_threat_observation(
            "Suspect reported near the main entrance, wearing dark clothing."
        )
        assert "main entrance" in result


class TestStakeholderUpdate:
    def test_no_personal_data(self):
        from src.agents.sitrep.tools import generate_stakeholder_update

        result = generate_stakeholder_update(
            incident_id="FIRE-2026-001",
            incident_type="fire",
            severity="high",
            status_summary="All personnel are being accounted for",
            actions_taken="Fire department contacted,Building evacuation in progress",
        )
        assert result["personal_data_included"] is False
        assert result["REQUIRES_COMMANDER_APPROVAL"] is True
        assert len(result["actions_taken"]) == 2

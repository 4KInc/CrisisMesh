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

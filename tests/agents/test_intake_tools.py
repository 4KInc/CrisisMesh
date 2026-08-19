"""Tests for Intake & Classification Agent tools."""

import os

import pytest

from src.core.knowledge_base import KnowledgeBase, init_knowledge_base
from src.agents.intake.tools import classify_incident, extract_location, select_playbook

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


class TestClassifyIncident:
    def test_fire_classification(self):
        result = classify_incident("Smoke detected near the science lab on floor 2")
        assert result["incident_type"] == "fire"
        assert result["keywords_matched"] >= 1
        assert "emergency_notice" in result

    def test_active_threat_classification(self):
        result = classify_incident("Armed intruder reported near the main entrance")
        assert result["incident_type"] == "active_threat"

    def test_medical_classification(self):
        result = classify_incident("Student unconscious in the gymnasium, not breathing")
        assert result["incident_type"] == "medical"

    def test_cyber_classification(self):
        result = classify_incident("All computers locked, ransomware message on screens")
        assert result["incident_type"] == "cyber_ransomware"

    def test_severity_critical(self):
        result = classify_incident("Uncontrolled fire spreading to multiple rooms")
        assert result["severity"] == "critical"

    def test_severity_high(self):
        result = classify_incident("Serious fire, students trapped in classroom")
        assert result["severity"] == "high"

    def test_incident_id_format(self):
        result = classify_incident("Smoke in the hallway")
        assert result["incident_id"].startswith("FIRE-")


class TestExtractLocation:
    def test_floor_extraction(self):
        result = extract_location("Fire on floor 2 near the lab")
        assert result["floor"] == "2"

    def test_room_extraction(self):
        result = extract_location("Smoke coming from room 215")
        assert result["room_id"] == "215"
        assert result["zone_id"] == "west-wing-f2"
        assert result["resolved"] is True

    def test_room_name_resolved(self):
        result = extract_location("Smoke coming from room 215")
        assert "Science Lab" in result["room_name"]

    def test_science_lab_keyword(self):
        result = extract_location("Smoke near the science lab on floor 2")
        assert result["zone_id"] == "west-wing-f2"
        assert result["resolved"] is True

    def test_gym_keyword(self):
        result = extract_location("Medical emergency in the gym")
        assert result["zone_id"] == "gym"
        assert result["resolved"] is True

    def test_cafeteria_keyword(self):
        result = extract_location("Water leak in the cafeteria")
        assert result["zone_id"] == "cafeteria"

    def test_library_keyword(self):
        result = extract_location("Suspicious package found in the library")
        assert result["zone_id"] == "library"

    def test_unresolved_location(self):
        result = extract_location("Something happened outside the building")
        assert result["resolved"] is False


class TestSelectPlaybook:
    def test_fire_playbook(self):
        result = select_playbook("fire")
        assert result["playbook_id"] == "playbook-fire-v1"
        assert result["status"] == "approved"

    def test_unknown_type_fallback(self):
        result = select_playbook("unknown_type")
        assert result["playbook_id"] == "playbook-general-v1"

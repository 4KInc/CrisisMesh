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


class TestSeverityIsNotConfidence:
    """Severity must come from what the reporter said, never from how many
    type keywords happened to match. `keywords_matched` measures how
    confidently the report was CLASSIFIED; a report nobody can categorise is
    not a mild one."""

    def test_occupancy_escalates_a_single_keyword_report(self):
        """The regression: this exact message came back "low" over WhatsApp."""
        result = classify_incident(
            "Smoke coming from the science lab on floor 2, students still inside"
        )
        assert result["keywords_matched"] == 1
        assert result["severity"] == "high"

    @pytest.mark.parametrize("phrase", [
        "students still inside",
        "kids inside",
        "children inside",
        "people inside",
        "still in the building",
        "can't get out",
        "unable to evacuate",
        "3 students unaccounted",
    ])
    def test_every_occupancy_signal_escalates(self, phrase):
        assert classify_incident(f"Smoke in the gym, {phrase}")["severity"] in (
            "high", "critical",
        )

    def test_low_confidence_is_never_low_severity(self):
        """One keyword, no de-escalating language — moderate, not low."""
        result = classify_incident("Smoke in the hallway")
        assert result["keywords_matched"] == 1
        assert result["severity"] == "moderate"

    @pytest.mark.parametrize("text,expected", [
        ("Fire drill scheduled for 10am, building will be evacuated", "low"),
        ("Small smoke smell, contained, no injuries", "low"),
        ("Alarm went off, false alarm confirmed", "low"),
    ])
    def test_low_requires_explicit_de_escalation(self, text, expected):
        assert classify_incident(text)["severity"] == expected

    def test_medical_arrest_is_critical(self):
        result = classify_incident("Student unconscious in the gymnasium, not breathing")
        assert result["severity"] == "critical"

    def test_de_escalation_beats_occupancy(self):
        """A drill says people are inside on purpose — do not page as high."""
        assert classify_incident(
            "Fire drill, students still inside until the bell"
        )["severity"] == "low"


class TestUnclassifiedIsNotMedical:
    """MEDICAL used to be both a real category and the fallback, so anything
    the keyword tables missed was reported as a medical incident."""

    @pytest.mark.parametrize("text", [
        "hi",
        "wrong number sorry",
        "there is no active CrisisMesh incident, nothing was recorded",
        "asdfgh",
    ])
    def test_unrecognised_text_is_other(self, text):
        result = classify_incident(text)
        assert result["incident_type"] == "other"
        assert result["unclassified"] is True
        assert result["keywords_matched"] == 0

    def test_real_medical_still_classifies_as_medical(self):
        result = classify_incident("Student unconscious in the gym, not breathing")
        assert result["incident_type"] == "medical"
        assert result["unclassified"] is False

    def test_unclassified_keeps_its_severity(self):
        """Failing to name the emergency is not deciding there isn't one."""
        result = classify_incident("Something is very wrong, students still inside")
        assert result["incident_type"] == "other"
        assert result["severity"] == "high"

    def test_noise_is_not_urgent(self):
        assert classify_incident("hi")["severity"] == "moderate"

    def test_other_falls_back_to_the_general_playbook(self):
        assert select_playbook("other")["playbook_id"] == "playbook-general-v1"

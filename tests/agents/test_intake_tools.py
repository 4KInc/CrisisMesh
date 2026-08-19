"""Tests for Intake & Classification Agent tools."""

from src.agents.intake.tools import classify_incident, extract_location, select_playbook


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
        result = extract_location("Smoke coming from room 203")
        assert result["room"] == "203"

    def test_near_extraction(self):
        result = extract_location("Incident near the science lab")
        assert "near the science lab" in result["raw_location"]


class TestSelectPlaybook:
    def test_fire_playbook(self):
        result = select_playbook("fire")
        assert result["playbook_id"] == "playbook-fire-v1"
        assert result["status"] == "approved"

    def test_unknown_type_fallback(self):
        result = select_playbook("unknown_type")
        assert result["playbook_id"] == "playbook-general-v1"

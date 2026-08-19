"""Tests for Accountability Agent tools."""

from src.agents.accountability.tools import (
    compute_accountability_summary,
    escalate_missing_checkins,
    process_checkin,
    send_checkin_request,
)


class TestAccountability:
    def test_send_checkin_request(self):
        result = send_checkin_request("INC-001", ["p-001", "p-002", "p-003"])
        assert result["requests_sent"] == 3
        assert result["status"] == "sent"

    def test_process_checkin(self):
        result = process_checkin("INC-002", "p-001", "safe", "Room 101")
        assert result["recorded"] is True
        assert result["status"] == "safe"

    def test_compute_summary(self):
        send_checkin_request("INC-003", ["p-001", "p-002", "p-003"])
        process_checkin("INC-003", "p-001", "safe")
        process_checkin("INC-003", "p-002", "evacuated")
        result = compute_accountability_summary("INC-003")
        assert result["total_tracked"] == 3
        assert result["unaccounted"] == 1  # p-003 still unknown

    def test_escalate_missing(self):
        send_checkin_request("INC-004", ["p-010", "p-011"])
        process_checkin("INC-004", "p-010", "safe")
        result = escalate_missing_checkins("INC-004")
        assert result["missing_count"] == 1
        assert "p-011" in result["missing_person_ids"]


class TestComplianceRedaction:
    def test_redact_sensitive(self):
        from src.agents.compliance.tools import redact_sensitive_fields

        data = {
            "name": "John Doe",
            "medical_notes": "Diabetic",
            "phone": "555-1234",
            "role": "Teacher",
        }
        result = redact_sensitive_fields(data, context="general")
        assert result["data"]["medical_notes"] == "[REDACTED]"
        assert result["data"]["phone"] == "[REDACTED]"
        assert result["data"]["name"] == "John Doe"
        assert result["data"]["role"] == "Teacher"

    def test_commander_sees_all(self):
        from src.agents.compliance.tools import redact_sensitive_fields

        data = {"medical_notes": "Diabetic", "name": "John"}
        result = redact_sensitive_fields(data, context="commander")
        assert result["data"]["medical_notes"] == "Diabetic"


class TestPolicyCheck:
    def test_allowed_tool(self):
        from src.agents.compliance.tools import check_policy

        result = check_policy("intake", "classify_incident")
        assert result["allowed"] is True

    def test_denied_tool(self):
        from src.agents.compliance.tools import check_policy

        result = check_policy("accountability", "send_external_message")
        assert result["allowed"] is False

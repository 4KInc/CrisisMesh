"""Tests for the channel-neutral active-incident owner."""

import threading

import pytest

from src.core import incident_state


@pytest.fixture(autouse=True)
def fresh():
    incident_state.reset()
    yield
    incident_state.reset()


class TestLifecycle:
    def test_nothing_active_initially(self):
        assert incident_state.is_active() is False
        assert incident_state.get_active_incident_id() == ""
        assert incident_state.get_latest_incident() == {}

    def test_declare_makes_it_active(self):
        incident_state.declare("INC-1", {"incident_id": "INC-1"}, source="sms")
        assert incident_state.is_active() is True
        assert incident_state.get_active_incident_id() == "INC-1"
        assert incident_state.get_latest_incident()["source"] == "sms"

    def test_clear_returns_what_it_was(self):
        incident_state.declare("INC-1", {"incident_id": "INC-1"}, source="slack")
        previous = incident_state.clear()
        assert previous["incident_id"] == "INC-1"
        assert previous["source"] == "slack"
        assert incident_state.is_active() is False

    def test_latest_incident_is_a_copy(self):
        """A caller mutating the returned dict must not corrupt the state."""
        incident_state.declare("INC-1", {"incident_id": "INC-1"}, source="web")
        incident_state.get_latest_incident()["incident_id"] = "TAMPERED"
        assert incident_state.get_active_incident_id() == "INC-1"

    def test_redeclare_replaces(self):
        incident_state.declare("INC-1", {"incident_id": "INC-1"}, source="slack")
        incident_state.declare("INC-2", {"incident_id": "INC-2"}, source="whatsapp")
        assert incident_state.get_active_incident_id() == "INC-2"
        assert incident_state.get_origin()["source"] == "whatsapp"


class TestDurationAcrossChannels:
    """The clock used to be started only by the Slack command handler, so an
    incident declared by text reported 0 minutes for its entire life."""

    @pytest.mark.parametrize("source", ["slack", "sms", "whatsapp", "web"])
    def test_every_source_starts_the_clock(self, source):
        incident_state.declare("INC-1", {"incident_id": "INC-1"}, source=source)
        assert incident_state.get_origin()["started_at"] > 0

    def test_no_incident_reports_zero(self):
        assert incident_state.elapsed_minutes() == 0

    def test_clear_stops_the_clock(self):
        incident_state.declare("INC-1", {"incident_id": "INC-1"}, source="sms")
        incident_state.clear()
        assert incident_state.elapsed_minutes() == 0


class TestOrigin:
    def test_attach_origin_after_declare(self):
        incident_state.declare("INC-1", {"incident_id": "INC-1"}, source="slack")
        incident_state.attach_origin(declared_by="U_PRINCIPAL", origin_channel="C123")
        origin = incident_state.get_origin()
        assert origin["declared_by"] == "U_PRINCIPAL"
        assert origin["origin_channel"] == "C123"

    def test_attach_origin_does_not_restart_the_clock(self):
        incident_state.declare("INC-1", {"incident_id": "INC-1"}, source="slack")
        started = incident_state.get_origin()["started_at"]
        incident_state.attach_origin(declared_by="U_VP")
        assert incident_state.get_origin()["started_at"] == started

    def test_blank_values_do_not_erase(self):
        incident_state.declare("INC-1", {"incident_id": "INC-1"}, source="slack")
        incident_state.attach_origin(declared_by="U_PRINCIPAL", origin_channel="C123")
        incident_state.attach_origin(declared_by="")
        assert incident_state.get_origin()["declared_by"] == "U_PRINCIPAL"


class TestCrossChannelVisibility:
    """The point of the move: a check-in on one channel finds an incident
    declared on another."""

    def test_sms_checkin_sees_a_slack_declaration(self):
        from src.services import sms_transport
        incident_state.declare("INC-SLACK", {"incident_id": "INC-SLACK"}, source="slack")
        assert sms_transport.incident_state.get_active_incident_id() == "INC-SLACK"

    def test_whatsapp_checkin_sees_an_sms_declaration(self):
        from src.services import whatsapp_transport
        incident_state.declare("INC-SMS", {"incident_id": "INC-SMS"}, source="sms")
        assert whatsapp_transport.incident_state.get_active_incident_id() == "INC-SMS"

    def test_slack_accessors_delegate(self):
        from src.services import slack_transport
        incident_state.declare("INC-WA", {"incident_id": "INC-WA"}, source="whatsapp")
        assert slack_transport.get_active_incident_id() == "INC-WA"
        assert slack_transport.get_latest_incident()["source"] == "whatsapp"


class TestThreadSafety:
    def test_concurrent_declares_leave_consistent_state(self):
        """The server is threaded and pipelines run on background threads."""
        errors = []

        def declare(n):
            try:
                for _ in range(50):
                    incident_state.declare(f"INC-{n}", {"incident_id": f"INC-{n}"}, source="sms")
                    record = incident_state.get_latest_incident()
                    if record and record.get("incident_id") != record.get("incident_id"):
                        errors.append("torn read")
            except Exception as exc:
                errors.append(repr(exc))

        threads = [threading.Thread(target=declare, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []
        assert incident_state.is_active() is True

"""A demo handset resolves to a roster person without entering the repo."""

import os

import pytest

from src.core import demo_identity
from src.core.knowledge_base import KnowledgeBase, init_knowledge_base

SEED_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "seed",
)

DEMO_VARS = ("CRISISMESH_DEMO_PHONE", "CRISISMESH_DEMO_PERSON", "CRISISMESH_DEMO_PHONE_MAP")


@pytest.fixture(autouse=True)
def fresh(tmp_path, monkeypatch):
    monkeypatch.setenv("CRISISMESH_CONSENT_LOG", str(tmp_path / "consent.jsonl"))
    for var in DEMO_VARS:
        monkeypatch.delenv(var, raising=False)
    KnowledgeBase.reset()
    init_knowledge_base(SEED_DIR)
    from src.services.sms_transport import _phone_to_person
    from src.services.whatsapp_transport import _phone_to_person as wa
    _phone_to_person.clear()
    wa.clear()
    yield
    KnowledgeBase.reset()


class TestSeedDataIsClean:
    def test_no_real_number_in_the_committed_roster(self):
        """The seed roster is public. Every phone in it must be a placeholder."""
        import csv
        import pathlib

        rows = list(csv.DictReader(
            pathlib.Path(SEED_DIR, "personnel.csv").read_text().splitlines()))
        for row in rows:
            for field in ("phone", "emergency_contact_phone"):
                value = (row.get(field) or "").replace("-", "")
                if value:
                    assert value.startswith("615555"), (
                        f"{row['person_id']} {field}={row[field]} is not a 555 placeholder"
                    )


class TestOverrides:
    def test_no_overrides_when_unset(self):
        assert demo_identity.overrides() == {}

    def test_single_phone_defaults_to_p001(self, monkeypatch):
        monkeypatch.setenv("CRISISMESH_DEMO_PHONE", "+15550001111")
        assert demo_identity.overrides() == {"+15550001111": "p001"}

    def test_person_can_be_chosen(self, monkeypatch):
        monkeypatch.setenv("CRISISMESH_DEMO_PHONE", "555-000-1111")
        monkeypatch.setenv("CRISISMESH_DEMO_PERSON", "p004")
        assert demo_identity.overrides() == {"+15550001111": "p004"}

    def test_multiple_handsets(self, monkeypatch):
        monkeypatch.setenv("CRISISMESH_DEMO_PHONE_MAP",
                           "+15550001111=p001, 555-123-0000=p004")
        mapping = demo_identity.overrides()
        assert mapping["+15550001111"] == "p001"
        assert mapping["+15551230000"] == "p004"

    def test_malformed_entries_are_skipped(self, monkeypatch):
        monkeypatch.setenv("CRISISMESH_DEMO_PHONE_MAP", "garbage,+15550001111=p001,=p002")
        assert demo_identity.overrides() == {"+15550001111": "p001"}


class TestTransportResolution:
    def test_whatsapp_resolves_the_demo_handset(self, monkeypatch):
        monkeypatch.setenv("CRISISMESH_DEMO_PHONE", "+15550001111")
        from src.services.whatsapp_transport import _build_phone_map, _phone_to_person
        _build_phone_map()
        assert _phone_to_person["+15550001111"] == "p001"

    def test_sms_resolves_the_demo_handset(self, monkeypatch):
        monkeypatch.setenv("CRISISMESH_DEMO_PHONE", "+15550001111")
        from src.services.sms_transport import _build_phone_map, _phone_to_person
        _build_phone_map()
        assert _phone_to_person["+15550001111"] == "p001"

    def test_unknown_handset_stays_unknown_without_the_override(self):
        from src.services.whatsapp_transport import _build_phone_map, _phone_to_person
        _build_phone_map()
        assert "+15550001111" not in _phone_to_person

    def test_roster_numbers_still_resolve(self, monkeypatch):
        monkeypatch.setenv("CRISISMESH_DEMO_PHONE", "+15550001111")
        from src.services.whatsapp_transport import _build_phone_map, _phone_to_person
        _build_phone_map()
        assert _phone_to_person["+16155550103"] == "p002"

    def test_checkin_works_through_the_override(self, monkeypatch):
        monkeypatch.setenv("CRISISMESH_DEMO_PHONE", "+15550001111")
        monkeypatch.setenv("CRISISMESH_WHATSAPP_MODE", "off")
        from src.core import incident_state
        from src.services.whatsapp_transport import handle_inbound_message
        incident_state.declare("T-1", {"incident_id": "T-1",
                                       "classification": {"incident_type": "fire",
                                                          "severity": "high"}},
                               source="slack")
        result = handle_inbound_message("+15550001111", "SAFE")
        assert result["action"] == "checkin"
        assert result["person_id"] == "p001"
        incident_state.reset()


class TestSlackIdOverride:
    """Mapping a few real workspace ids makes those people genuinely reachable
    without putting workspace-specific ids in version control — and without
    making everyone reachable, so the unreachable list stays true."""

    def test_no_override_uses_the_roster_value(self, monkeypatch):
        monkeypatch.delenv("CRISISMESH_DEMO_SLACK_MAP", raising=False)
        assert demo_identity.slack_id_for("p001", "U_PRINCIPAL") == "U_PRINCIPAL"

    def test_an_override_replaces_the_placeholder(self, monkeypatch):
        monkeypatch.setenv("CRISISMESH_DEMO_SLACK_MAP", "p001=U0REAL001")
        assert demo_identity.slack_id_for("p001", "U_PRINCIPAL") == "U0REAL001"

    def test_unmapped_people_keep_their_placeholder(self, monkeypatch):
        """The point: four mapped, thirty unreachable, and that is the truth."""
        monkeypatch.setenv("CRISISMESH_DEMO_SLACK_MAP", "p001=U0REAL001")
        assert demo_identity.slack_id_for("p002", "U_VP") == "U_VP"

    def test_multiple_mappings(self, monkeypatch):
        monkeypatch.setenv("CRISISMESH_DEMO_SLACK_MAP",
                           "p001=U0REAL001, p005=U0REAL005")
        assert demo_identity.slack_id_for("p005", "U_RODRIGUEZ") == "U0REAL005"

    def test_malformed_entries_are_skipped(self, monkeypatch):
        monkeypatch.setenv("CRISISMESH_DEMO_SLACK_MAP", "garbage,p001=U0REAL001,=U0X")
        assert demo_identity.slack_overrides() == {"p001": "U0REAL001"}

    def test_the_loop_sees_a_mapped_person_as_reachable(self, monkeypatch):
        from src.core import notify

        monkeypatch.setenv("CRISISMESH_DEMO_SLACK_MAP", "p001=U0REAL001")
        monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
        monkeypatch.setattr(notify, "_slack_ready", lambda: True)
        monkeypatch.setattr(notify, "slack_id_resolves", lambda sid: sid == "U0REAL001")
        notify.reset_slack_id_cache()

        reach = notify.resolve_reach(
            {"person_id": "p001", "name": "Principal Johnson",
             "phone": "", "slack_user_id": "U_PRINCIPAL"})
        assert reach.channel == notify.CHANNEL_SLACK
        assert reach.address == "U0REAL001"
        notify.reset_slack_id_cache()

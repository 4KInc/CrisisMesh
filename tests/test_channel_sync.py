"""An incident declared on one channel has to reach the others.

Someone in a corridor during a lockdown reports it from a phone, because that
is the device in their hand. The people coordinating are in Slack. Until this
existed the report reached every individual handset and the room where the
response is actually run heard nothing — the sync ran one way only.
"""

import os
from unittest.mock import patch

import pytest

from src.core import channel_sync, incident_state


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    monkeypatch.setenv("CRISISMESH_DELIVERY", "on")
    monkeypatch.setenv("SLACK_INCIDENT_CHANNEL", "C0BGU1FTDCL")
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    incident_state.reset()
    yield
    incident_state.reset()


def _record(source="whatsapp", incident_type="active_threat"):
    return {
        "incident_id": "THREAT-2026-001",
        "source": source,
        "report": "active shooter in the east wing, gunshots heard",
        "classification": {"incident_type": incident_type, "severity": "critical"},
        "location": {"zone_name": "East Wing Floor 2"},
    }


class TestTheSlackRoomHearsAboutAPhoneDeclaration:
    def test_a_whatsapp_declaration_is_announced_in_slack(self):
        posted = []
        with patch.object(channel_sync, "_post", lambda ch, text: posted.append((ch, text)) or True):
            result = channel_sync.announce_declaration(_record())
        assert result["posted"] is True
        assert posted and posted[0][0] == "C0BGU1FTDCL"

    def test_the_announcement_carries_what_a_responder_needs(self):
        text = channel_sync.compose_declaration(_record())
        assert "THREAT-2026-001" in text
        assert "ACTIVE THREAT" in text.upper()
        assert "critical" in text.lower()
        assert "east wing" in text.lower()
        # It has to say where it came from, or the room cannot tell this from a
        # declaration one of them made.
        assert "whatsapp" in text.lower()

    def test_the_report_is_quoted_verbatim(self):
        """The room needs the sentence, not our summary of it."""
        text = channel_sync.compose_declaration(_record())
        assert "gunshots heard" in text

    def test_an_sms_declaration_is_announced_too(self):
        text = channel_sync.compose_declaration(_record(source="sms"))
        assert "sms" in text.lower()


class TestItDoesNotDoublePost:
    def test_a_slack_declaration_is_not_re_announced(self):
        """The Block Kit card is already in the room; a second copy is noise."""
        posted = []
        with patch.object(channel_sync, "_post", lambda ch, text: posted.append(ch) or True):
            result = channel_sync.announce_declaration(_record(source="slack"))
        assert result["posted"] is False
        assert "slack" in result["reason"].lower()
        assert posted == []


class TestItFailsClosed:
    def test_no_configured_channel_posts_nothing(self, monkeypatch):
        monkeypatch.delenv("SLACK_INCIDENT_CHANNEL", raising=False)
        posted = []
        with patch.object(channel_sync, "_post", lambda ch, text: posted.append(ch) or True):
            result = channel_sync.announce_declaration(_record())
        assert result["posted"] is False
        assert posted == []
        # Guessing a channel would announce a lockdown into whichever room the
        # bot happened to be in.
        assert "configured" in result["reason"].lower()

    def test_delivery_off_suppresses_the_post(self, monkeypatch):
        monkeypatch.setenv("CRISISMESH_DELIVERY", "off")
        posted = []
        with patch.object(channel_sync, "_post", lambda ch, text: posted.append(ch) or True):
            result = channel_sync.announce_declaration(_record())
        assert result["posted"] is False
        assert posted == []
        assert "delivery" in result["reason"].lower()

    def test_a_failed_post_is_reported_as_failed(self):
        with patch.object(channel_sync, "_post", lambda ch, text: False):
            result = channel_sync.announce_declaration(_record())
        assert result["posted"] is False


class TestItDoesNotLeakAPhoneNumber:
    def test_a_known_reporter_is_named(self):
        with patch.object(channel_sync, "_reporter_name", lambda addr: "Mrs. Rodriguez"):
            text = channel_sync.compose_declaration(_record(), reporter_address="+16155550101")
        assert "Mrs. Rodriguez" in text
        assert "6155550101" not in text

    def test_an_unknown_reporter_is_not_printed_as_a_number(self):
        """A channel is a wider audience than a DM. An unrecognised handset is
        described, never quoted."""
        with patch.object(channel_sync, "_reporter_name", lambda addr: ""):
            text = channel_sync.compose_declaration(_record(), reporter_address="+16155559999")
        assert "6155559999" not in text
        assert "unlisted" in text.lower() or "unrecognised" in text.lower()


class TestTheRoomAlsoHearsTheAllClear:
    def test_a_phone_resolution_is_announced(self):
        posted = []
        with patch.object(channel_sync, "_post", lambda ch, text: posted.append(text) or True):
            result = channel_sync.announce_resolution(_record())
        assert result["posted"] is True
        assert "all clear" in posted[0].lower() or "resolved" in posted[0].lower()
        assert "THREAT-2026-001" in posted[0]


class TestItIsWiredToDeclaration:
    def test_declaring_through_whatsapp_reaches_the_room(self):
        """The unit works; this pins that something calls it."""
        from src.core import notify

        seen = []
        with patch.object(channel_sync, "announce_declaration",
                          lambda record, reporter_address="": seen.append(record) or {"posted": True}), \
             patch.object(notify, "announce_incident", lambda *a, **k: None), \
             patch.object(notify, "_start_reconciliation", lambda *a: None):
            incident_state.declare("THREAT-2026-001", _record(), source="whatsapp")

            class _Event:
                incident_id = "THREAT-2026-001"
                data = {"reporter_address": "+16155550101"}

            notify._on_declared(_Event())
            for t in list(getattr(notify.threading, "enumerate", lambda: [])()):
                if t.name.startswith("Thread") and t.is_alive():
                    t.join(timeout=2)
        assert seen, "a WhatsApp declaration did not reach the Slack room"


class TestTheAllClearSaysHowItWasActuallyResolved:
    """An incident declared on WhatsApp and stood down from Slack was announced
    as "Resolved via WhatsApp." The resolution carried its own channel all the
    way to `resolve()`, which then dropped it, so every surface downstream
    described the resolution using the declaration's channel."""

    def test_it_names_the_resolving_channel_not_the_declaring_one(self):
        previous = {**_record(source="whatsapp"), "resolved_via": "slack"}
        text = channel_sync.compose_resolution(previous)
        assert "whatsapp" not in text.lower()

    def test_a_phone_resolution_still_says_whatsapp(self):
        previous = {**_record(source="slack"), "resolved_via": "whatsapp"}
        assert "whatsapp" in channel_sync.compose_resolution(previous).lower()

    def test_it_does_not_double_post_when_slack_resolved_it(self):
        """The room already has the RESOLVED card the command produced."""
        posted = []
        previous = {**_record(source="whatsapp"), "resolved_via": "slack"}
        with patch.object(channel_sync, "_post", lambda ch, t: posted.append(ch) or True):
            result = channel_sync.announce_resolution(previous)
        assert result["posted"] is False
        assert posted == []

    def test_an_http_resolution_still_reaches_the_room(self):
        """The console button is not Slack; nobody there has seen anything."""
        posted = []
        previous = {**_record(source="whatsapp"), "resolved_via": "http"}
        with patch.object(channel_sync, "_post", lambda ch, t: posted.append(ch) or True):
            result = channel_sync.announce_resolution(previous)
        assert result["posted"] is True

    def test_resolve_records_the_channel_it_was_asked_on(self):
        """The seam: resolve() takes `channel` and has to keep it."""
        from src.core import incident_resolve

        incident_state.declare("T-1", _record(), source="whatsapp")
        seen = {}
        with patch.object(incident_resolve, "_publish", lambda prev: seen.update(prev)):
            incident_resolve.resolve("T-1", resolved_by="U1", channel="slack")
        assert seen.get("resolved_via") == "slack"

"""Phone channels answer the same questions Slack does."""

import os

import pytest

from src.core import incident_queries, incident_state, observations, room_board
from src.core.knowledge_base import KnowledgeBase, init_knowledge_base
from src.agents.accountability.tools import _checkin_store, process_checkin, send_checkin_request

SEED_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "seed",
)


@pytest.fixture(autouse=True)
def fresh(tmp_path, monkeypatch):
    monkeypatch.setenv("CRISISMESH_CONSENT_LOG", str(tmp_path / "consent.jsonl"))
    monkeypatch.setenv("CRISISMESH_WHATSAPP_MODE", "off")
    monkeypatch.setenv("CRISISMESH_SMS_MODE", "off")
    KnowledgeBase.reset()
    init_knowledge_base(SEED_DIR)
    incident_state.reset()
    observations.reset()
    room_board.reset()
    _checkin_store.clear()
    from src.services.sms_transport import _phone_to_person
    from src.services.whatsapp_transport import _phone_to_person as wa
    _phone_to_person.clear()
    wa.clear()
    yield
    incident_state.reset()
    observations.reset()
    room_board.reset()
    _checkin_store.clear()
    KnowledgeBase.reset()


def _incident(itype="active_threat"):
    incident_state.declare(
        "T-1",
        {"incident_id": "T-1",
         "classification": {"incident_type": itype, "severity": "critical"}},
        source="slack",
    )
    send_checkin_request("T-1", facility_id="jefferson")


class TestRoomReports:
    def test_parses_counts_and_notes(self):
        parsed = room_board.parse("room 104: 23 students are safe, 2 are missing, last seen in hallway")
        assert parsed["room"] == "104"
        assert parsed["safe"] == 23
        assert parsed["missing"] == 2
        assert "last seen in hallway" in parsed["notes"]

    def test_parses_all_form(self):
        parsed = room_board.parse("room 101: all 25 students are safe")
        assert parsed["safe"] == 25
        assert parsed["missing"] == 0

    def test_recorded_and_confirmed(self):
        _incident()
        reply = incident_queries.answer("room 104: 23 students are safe, 2 are missing",
                                        source="whatsapp")
        assert "Room 104 recorded" in reply
        assert "2 MISSING" in reply
        assert room_board.get("T-1")["104"]["safe"] == 23

    def test_board_is_keyed_by_incident(self):
        """It used to be a flat dict that carried into the next incident."""
        _incident()
        incident_queries.answer("room 104: all 20 students are safe", source="sms")
        assert room_board.get("T-1")
        assert room_board.get("SOME-OTHER-INCIDENT") == {}


class TestQueries:
    def test_board_lists_reported_and_silent_rooms(self):
        _incident()
        incident_queries.answer("room 104: 23 students are safe, 2 are missing", source="sms")
        reply = incident_queries.answer("show the classroom board", source="sms")
        assert "Room 104" in reply
        assert "MISSING" in reply
        assert "have NOT reported" in reply

    def test_unaccounted_names_people(self):
        _incident()
        process_checkin("T-1", "p001", "safe")
        reply = incident_queries.answer("who is still unaccounted?", source="sms")
        assert "Unaccounted personnel" in reply
        assert "Principal Johnson" not in reply.split("PRIORITY")[0]

    def test_unaccounted_surfaces_mobility_needs_first_class(self):
        _incident()
        reply = incident_queries.answer("who is still unaccounted?", source="sms")
        assert "PRIORITY" in reply
        assert "mobility limitations" in reply

    def test_on_call_lists_commander_and_wardens(self):
        _incident()
        reply = incident_queries.answer("who is on call right now?", source="sms")
        assert "Incident Commander" in reply
        assert "Floor wardens" in reply

    def test_routes_name_a_real_exit(self):
        _incident("fire")
        reply = incident_queries.answer("whats the fastest route out of east wing", source="sms")
        assert "East Wing" in reply
        assert "Door" in reply

    def test_unknown_zone_asks_for_one_it_knows(self):
        _incident("fire")
        reply = incident_queries.answer("route out of narnia", source="sms")
        assert "Name a zone I know" in reply

    def test_not_a_query_returns_none(self):
        _incident()
        assert incident_queries.answer("he is moving toward the gym") is None

    def test_queries_need_an_active_incident(self):
        assert "no active incident" in incident_queries.answer("show the board")


class TestArrivalBriefWithheld:
    """It names where people with mobility limitations are, and any handset that
    texts the published number can reach these replies."""

    def test_refused_over_text(self):
        _incident()
        reply = incident_queries.answer("give me the law enforcement arrival brief", source="sms")
        assert "approval" in reply
        assert "Slack" in reply

    def test_no_names_leak_in_the_refusal(self):
        _incident()
        reply = incident_queries.answer("arrival brief", source="whatsapp")
        for name in ("Mrs. Davis", "Mrs. Thompson"):
            assert name not in reply


class TestChannelParity:
    def test_whatsapp_answers_a_board_query(self):
        from src.services.whatsapp_transport import handle_inbound_message
        _incident()
        result = handle_inbound_message("+16155550101", "who is still unaccounted?")
        assert result["action"] == "query"
        assert "Unaccounted personnel" in result["reply"]

    def test_sms_answers_a_room_report(self):
        from src.services.sms_transport import handle_inbound_sms
        _incident()
        result = handle_inbound_sms("+16155550101", "room 104: all 22 students are safe")
        assert result["action"] == "query"
        assert "Room 104 recorded" in result["twiml"]

    def test_a_query_never_becomes_an_observation(self):
        from src.services.whatsapp_transport import handle_inbound_message
        _incident()
        handle_inbound_message("+16155550101", "show the classroom board")
        assert observations.count("T-1") == 0

    def test_free_text_still_becomes_an_observation(self):
        from src.services.whatsapp_transport import handle_inbound_message
        _incident()
        result = handle_inbound_message("+16155550101", "he is moving toward the gym")
        assert result["action"] == "observation"
        assert observations.count("T-1") == 1

    def test_queries_never_replace_the_incident(self):
        from src.services.whatsapp_transport import handle_inbound_message
        _incident()
        for q in ["show the classroom board", "who is on call right now?",
                  "room 104: all 22 students are safe", "who is still unaccounted?"]:
            handle_inbound_message("+16155550101", q)
            assert incident_state.get_active_incident_id() == "T-1"


class TestNoteIsNotRedundant:
    """The parsed counts are already shown; repeating them in the note wastes
    the only line a reader may get on a lock screen."""

    def test_counts_are_stripped_from_the_note(self):
        parsed = room_board.parse(
            "room 104: 23 students are safe, 2 are missing, last seen in hallway")
        assert parsed["notes"] == "last seen in hallway"
        assert "23" not in parsed["notes"]

    def test_note_is_empty_when_only_counts_were_given(self):
        assert room_board.parse("room 101: all 25 students are safe")["notes"] == ""

    def test_qualitative_detail_survives(self):
        parsed = room_board.parse("room 210: 18 safe, 4 missing — door is jammed, need help")
        assert "door is jammed" in parsed["notes"]

    def test_raw_text_is_preserved(self):
        parsed = room_board.parse("room 104: 23 students are safe, 2 are missing, last seen in hallway")
        assert "23 students are safe" in parsed["raw"]

    def test_board_line_does_not_repeat_counts(self):
        _incident()
        incident_queries.answer(
            "room 104: 23 students are safe, 2 are missing, last seen in hallway", source="sms")
        line = [ln for ln in room_board.as_text("T-1").splitlines() if "Room 104" in ln][0]
        assert line.count("23") == 1
        assert "last seen in hallway" in line


class TestTheArrivalBriefSeesEveryChannel:
    """The law-enforcement handoff is the document responders act on fastest
    and with least questioning. It read `_room_checkins` (Slack-only) for
    silent rooms and re-parsed the *original* report for the threat position —
    so a teacher who reported her room by WhatsApp was still listed as silent,
    and "he is headed towards the gym" never reached the brief."""

    def _phone(self):
        from src.core.knowledge_base import KnowledgeBase
        return "+1" + KnowledgeBase.get().get_person("p005")["phone"].replace("-", "")

    def test_a_room_reported_by_whatsapp_is_not_listed_silent(self):
        from src.services.whatsapp_transport import handle_inbound_message
        from src.services.slack_transport import _reported_rooms

        _incident()
        handle_inbound_message(self._phone(), "room 101: all 25 students are safe")
        assert "101" in _reported_rooms(), "the brief would still call room 101 silent"

    def test_the_shared_board_and_the_slack_board_are_merged(self):
        from src.services import slack_transport
        from src.core import room_board

        _incident()
        slack_transport._room_checkins["210"] = {
            "room": "210", "safe": 20, "missing": 0, "status": "safe", "notes": ""}
        room_board.record("T-1", room_board.parse("room 104: 23 safe, 2 missing"),
                          source="whatsapp")
        merged = slack_transport._reported_rooms()
        assert {"210", "104"} <= set(merged)
        slack_transport._room_checkins.clear()

    def test_the_latest_witness_report_supersedes_the_original(self):
        """The freshest thing anyone knows about where the threat is."""
        from src.services.whatsapp_transport import handle_inbound_message

        _incident()
        handle_inbound_message(self._phone(), "he is headed towards the gym")
        assert "gym" in observations.latest_threat_location("T-1").lower()

    def test_with_no_witness_report_the_original_still_stands(self):
        from src.agents.sitrep.tools import extract_threat_observation

        _incident()
        assert observations.latest_threat_location("T-1") == ""
        assert extract_threat_observation("shooter in the east wing")


class TestThreatLocationPhrasing:
    """Measured against how a teacher types under stress, not the two forms
    originally handled. "he is headed towards the gym" matched nothing, so the
    freshest observation in the incident never reached the brief."""

    @pytest.mark.parametrize("text,expected", [
        ("he is headed towards the gym", "gym"),
        ("he is moving toward the gym", "gym"),
        ("shooter in the east wing", "east wing"),
        ("last seen in the cafeteria", "cafeteria"),
        ("they are running into the library", "library"),
        ("suspect now in the gymnasium", "gymnasium"),
        ("moved to the north stairwell", "north stairwell"),
        ("gunman spotted near the main office", "main office"),
    ])
    def test_common_phrasings_extract_a_location(self, text, expected):
        from src.agents.sitrep.tools import extract_threat_observation
        assert extract_threat_observation(text).lower() == expected

    def test_a_report_with_no_threat_movement_extracts_nothing(self):
        from src.agents.sitrep.tools import extract_threat_observation
        assert extract_threat_observation("smoke in the science lab") == ""

    def test_it_reaches_the_incident_through_whatsapp(self):
        from src.services.whatsapp_transport import handle_inbound_message
        from src.core.knowledge_base import KnowledgeBase

        _incident()
        phone = "+1" + KnowledgeBase.get().get_person("p005")["phone"].replace("-", "")
        handle_inbound_message(phone, "he is headed towards the gym")
        assert "gym" in observations.latest_threat_location("T-1").lower()

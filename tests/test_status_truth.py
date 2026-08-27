"""What the status surfaces are allowed to say.

Three things went wrong in one live run:

  * A teacher filed a room report from her phone. The reconciliation loop marked
    her accounted; `/incident status` still listed her as missing and reported
    0 check-ins. Two counts of the same people, disagreeing.
  * The missing list said "and 24 more". Those are the people someone has to go
    find; a count is the problem restated as a number.
  * The card said "Declared by: —" for an incident a named person had declared
    from a handset two minutes earlier.
"""

import os
from unittest.mock import patch

import pytest

from src.core import incident_state, incident_queries, room_board
from src.core.knowledge_base import KnowledgeBase, init_knowledge_base

SEED = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "seed")


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setenv("CRISISMESH_DELIVERY", "off")
    monkeypatch.setenv("CRISISMESH_AUTO_TICK", "off")
    monkeypatch.setenv("CRISISMESH_DEMO_PHONE", "+16692167706")
    monkeypatch.setenv("CRISISMESH_DEMO_PERSON", "p001")
    KnowledgeBase.reset()
    init_knowledge_base(SEED)
    incident_state.reset()
    room_board.reset()
    from src.core import reconciliation
    reconciliation.reset()
    from src.agents.accountability import tools as acct
    acct._checkin_store.clear()
    yield
    incident_state.reset()
    KnowledgeBase.reset()


def _declare(hydrate=True):
    if hydrate:
        from src.agents.accountability.tools import send_checkin_request
        send_checkin_request("T-1", facility_id="jefferson")
    rec = {"incident_id": "T-1", "source": "whatsapp",
           "report": "active shooter in the east wing",
           "classification": {"incident_type": "active_threat", "severity": "critical"}}
    incident_state.declare("T-1", rec, source="whatsapp")
    return rec


class TestARoomReportAccountsItsReporterEverywhere:
    def test_both_ledgers_agree(self):
        """The loop and the status card count the same person the same way."""
        from src.agents.accountability.tools import compute_accountability_summary
        from src.core import reconciliation

        _declare()
        incident_queries.answer("room 104: 23 students are safe, 1 unaccounted",
                                source="+16692167706")

        assert reconciliation.get_state("T-1", "p001").status == reconciliation.ACCOUNTED
        summary = compute_accountability_summary("T-1")
        assert summary["accounted"] >= 1, (
            "the reconciliation loop accounted for the reporter and the "
            "accountability ledger did not"
        )

    def test_the_reporter_is_not_listed_as_missing(self):
        from src.agents.accountability.tools import compute_accountability_summary

        _declare()
        incident_queries.answer("room 104: 23 students are safe, 1 unaccounted",
                                source="+16692167706")
        summary = compute_accountability_summary("T-1")
        missing = [p.get("name", "") for group in ("unknown", "silent")
                   for p in summary.get("breakdown", {}).get(group, [])]
        assert "Principal Johnson" not in missing

    def test_nobody_else_in_the_room_is_accounted(self):
        """"23 of 25 safe" never says which 23. A falsely accounted person is
        one nobody goes looking for."""
        from src.agents.accountability.tools import compute_accountability_summary

        _declare()
        incident_queries.answer("room 101: 25 students are safe", source="+16692167706")
        assert compute_accountability_summary("T-1")["accounted"] == 1


class TestTheMissingListNamesEveryone:
    def test_status_card_lists_all_names(self):
        from src.services import slack_transport

        _declare()
        result = slack_transport._handle_status("C1", "U1")
        text = result["text"]
        assert "more" not in text.split("Missing")[-1].split("\n")[0], text
        # Every tracked person by name — the last one on the roster included.
        assert "Ms. Garcia" in text and "Mr. Hughes" in text
        assert text.count(",") >= 30

    def test_checkin_confirmation_lists_all_names(self):
        """Same complaint, second surface."""
        import inspect
        from src.services import slack_transport

        src = inspect.getsource(slack_transport._post_checkin_confirmation)
        assert "missing_names[:5]" not in src


class TestTheCardNamesWhoDeclaredIt:
    def test_a_phone_declaration_names_the_reporter(self):
        from src.services import slack_transport

        _declare()
        incident_state.attach_reporter("+16692167706")
        text = slack_transport._handle_status("C1", "U1")["text"]
        assert "Principal Johnson" in text.split("Check-ins")[0]
        assert "—" not in text.split("Declared by:")[1].split("\n")[0]

    def test_it_says_which_channel(self):
        from src.services import slack_transport

        _declare()
        incident_state.attach_reporter("+16692167706")
        line = slack_transport._handle_status("C1", "U1")["text"].split("Declared by:")[1].split("\n")[0]
        assert "whatsapp" in line.lower()

    def test_an_unknown_handset_is_not_printed_as_a_number(self):
        from src.services import slack_transport

        _declare()
        incident_state.attach_reporter("+16155559999")
        line = slack_transport._handle_status("C1", "U1")["text"].split("Declared by:")[1].split("\n")[0]
        assert "6155559999" not in line


class TestTheBriefDoesNotPrintTwoContradictoryCounts:
    def test_the_two_headcounts_are_labelled(self):
        """The brief carried "34 total | 0 accounted" beside "48 safe". Both were
        true — 34 is the staff roster, 48 is students counted by their teachers —
        and nothing on the page said so."""
        from src.services import slack_transport

        _declare()
        incident_queries.answer("room 104: 23 students are safe, 1 unaccounted",
                                source="+16692167706")
        posted = []
        with patch.object(slack_transport, "_post_bot_message",
                          lambda ch, t, **kw: posted.append(t)):
            slack_transport._handle_arrival_brief("C1", "")
        text = "\n".join(posted)
        head = [ln for ln in text.split("\n") if "Headcount" in ln]
        assert head, text[:400]
        assert "staff" in head[0].lower() or "roster" in head[0].lower()


class TestTheBuildingLineNamesOnePopulation:
    def test_it_does_not_call_the_roster_staff_slash_students(self):
        """The header said "34 staff/students tracked" on a page that goes on to
        estimate ~525 people in the silent rooms. The roster is 34 staff; the
        students are counted by their teachers, room by room."""
        from src.services import slack_transport

        _declare()
        posted = []
        with patch.object(slack_transport, "_post_bot_message",
                          lambda ch, t, **kw: posted.append(t)):
            slack_transport._handle_arrival_brief("C1", "")
        text = "\n".join(posted)
        assert "staff/students tracked" not in text
        assert "34 staff tracked" in text

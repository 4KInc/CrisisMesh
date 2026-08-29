"""Nobody is accounted for because we lost their row.

A live brief, thirteen minutes into an active shooter, reported:

    Headcount (tracked staff roster): 1 total | 1 accounted | 0 unaccounted

The incident survives in Firestore; the accountability ledger is in memory, and
a redeploy between the declaration and the brief emptied it. The denominator was
however many rows happened to still be there, so losing 33 people's records
became a claim that there were no missing people at all.

"0 unaccounted" during an active shooter is the most dangerous sentence this
system can print. The roster is the denominator, and no record means nobody has
heard from them.
"""

import os

import pytest

from src.agents.accountability import tools as acct
from src.agents.accountability.tools import (
    compute_accountability_summary, process_checkin, send_checkin_request,
)
from src.core.knowledge_base import KnowledgeBase, init_knowledge_base

SEED = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "seed")


@pytest.fixture(autouse=True)
def fresh():
    KnowledgeBase.reset()
    init_knowledge_base(SEED)
    acct._checkin_store.clear()
    yield
    acct._checkin_store.clear()


class TestTheRosterIsTheDenominator:
    def test_an_empty_ledger_does_not_mean_an_empty_school(self):
        summary = compute_accountability_summary("T-1")
        assert summary["total_tracked"] == 34
        assert summary["accounted"] == 0
        assert summary["unaccounted"] == 34

    def test_a_wiped_ledger_with_one_survivor_still_counts_34(self):
        """Exactly the live failure: one row left after a redeploy."""
        process_checkin("T-1", "p001", "safe")
        summary = compute_accountability_summary("T-1")
        assert summary["total_tracked"] == 34
        assert summary["accounted"] == 1
        assert summary["unaccounted"] == 33, (
            "losing 33 records was reported as having no missing people"
        )

    def test_people_with_no_record_are_named_as_missing(self):
        """A commander reads names off this list. Someone whose row was lost
        must appear on it, not vanish from the incident."""
        process_checkin("T-1", "p001", "safe")
        summary = compute_accountability_summary("T-1")
        missing = [p["name"] for group in ("unknown", "silent")
                   for p in summary["breakdown"].get(group, [])]
        assert "VP Martinez" in missing
        assert "Principal Johnson" not in missing
        assert len(missing) == 33

    def test_a_seeded_ledger_is_unchanged(self):
        """The normal path already tracked all 34; this must not double-count."""
        send_checkin_request("T-1", facility_id="jefferson")
        process_checkin("T-1", "p001", "safe")
        summary = compute_accountability_summary("T-1")
        assert summary["total_tracked"] == 34
        assert summary["accounted"] == 1
        assert summary["unaccounted"] == 33

    def test_statuses_still_count(self):
        send_checkin_request("T-1", facility_id="jefferson")
        process_checkin("T-1", "p001", "safe")
        process_checkin("T-1", "p002", "injured")
        summary = compute_accountability_summary("T-1")
        assert summary["accounted"] == 2
        assert summary["counts"]["injured"] == 1

    def test_it_never_reports_fewer_people_than_it_has_records_for(self):
        """If the roster cannot be read, fall toward the ledger rather than
        claiming a smaller school than the one we have rows for."""
        KnowledgeBase.reset()
        process_checkin("T-1", "p001", "safe")
        summary = compute_accountability_summary("T-1")
        assert summary["total_tracked"] >= 1
        assert summary["unaccounted"] >= 0

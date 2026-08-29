"""Dropping the seed CSVs into the channel at the top of a demo.

Slack sends one file_shared event per file and the handler runs each in its own
thread. Fifteen files at once is fifteen threads each calling
KnowledgeBase.reset() and then re-reading the directory, and between those two
calls the knowledge base is empty. Anything reading it in that window sees a
school with no people in it — including the accountability denominator, which
now counts the roster, so a badly-timed drop reports everyone as accounted for.
"""

import os
import threading

import pytest

from src.core.knowledge_base import KnowledgeBase, init_knowledge_base

SEED = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "seed")


@pytest.fixture(autouse=True)
def fresh():
    KnowledgeBase.reset()
    init_knowledge_base(SEED)
    yield
    KnowledgeBase.reset()
    init_knowledge_base(SEED)


class TestConcurrentDropsDoNotEmptyTheRoster:
    def test_the_roster_is_never_observed_empty(self):
        """Read the roster hard while reloads run. It must never be empty."""
        from src.services import slack_transport

        observed: list[int] = []
        stop = threading.Event()

        def reader():
            while not stop.is_set():
                observed.append(len(KnowledgeBase.get().personnel))

        def reloader():
            for _ in range(12):
                slack_transport.reload_knowledge_base(SEED)

        r = threading.Thread(target=reader, daemon=True)
        r.start()
        loaders = [threading.Thread(target=reloader) for _ in range(4)]
        for t in loaders:
            t.start()
        for t in loaders:
            t.join()
        stop.set()
        r.join(timeout=2)

        assert observed, "the reader never ran"
        assert min(observed) == 34, (
            f"the roster was observed with {min(observed)} people during a reload"
        )

    def test_a_reload_invalidates_stale_reach(self):
        """New roster, new Slack ids. Keeping the old verdicts would report
        reachability for people who are no longer on it."""
        from src.core import notify
        from src.services import slack_transport

        notify._slack_id_cache["U_STALE"] = True
        slack_transport.reload_knowledge_base(SEED)
        assert "U_STALE" not in notify._slack_id_cache


class TestTheDropIsAnnouncedHonestly:
    def test_a_bad_csv_does_not_replace_a_good_roster(self):
        """A truncated or malformed file must not empty the school. Rejecting
        it leaves the previous data in place."""
        from src.services import slack_transport

        ok, detail = slack_transport.apply_csv_upload("personnel.csv", "not,a,valid\nroster")
        assert ok is False
        assert detail
        assert len(KnowledgeBase.get().personnel) == 34

    def test_a_good_csv_is_applied(self):
        from src.services import slack_transport

        content = open(os.path.join(SEED, "personnel.csv")).read()
        ok, detail = slack_transport.apply_csv_upload("personnel.csv", content)
        assert ok is True
        assert len(KnowledgeBase.get().personnel) == 34

    def test_an_unknown_filename_is_refused(self):
        """Only files the knowledge base actually reads. A stray CSV dropped in
        the channel is not seed data."""
        from src.services import slack_transport

        ok, detail = slack_transport.apply_csv_upload("budget_2026.csv", "a,b\n1,2")
        assert ok is False
        assert "budget_2026" in detail or "not" in detail.lower()

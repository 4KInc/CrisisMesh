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
def fresh(tmp_path, monkeypatch):
    # Applying an upload rewrites the seed directory. Pointed at a copy, because
    # a suite that writes into data/seed leaves the next run — and the next
    # deploy — reading whatever the last test happened to drop.
    import shutil
    for name in os.listdir(SEED):
        if name.endswith(".csv"):
            shutil.copy(os.path.join(SEED, name), tmp_path / name)
    monkeypatch.setenv("CRISISMESH_SEED_DIR", str(tmp_path))
    KnowledgeBase.reset()
    init_knowledge_base(str(tmp_path))
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
                slack_transport.reload_knowledge_base()

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
        slack_transport.reload_knowledge_base()
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


class TestFilesThatAreNotOurs:
    """The demo folder carries seven CSVs CrisisMesh has never read — runbooks,
    network assets, on-call schedules — because it is shared with another
    project. Dropping the folder posted a red refusal for each one. Somebody
    sharing a spreadsheet in a channel is not an error; a seed file that failed
    to load is."""

    def test_an_unrelated_csv_is_ignored_quietly(self):
        from src.services import slack_transport

        outcome = slack_transport.classify_upload("runbooks.csv", "a,b\n1,2")
        assert outcome["announce"] is False
        assert outcome["applied"] is False

    def test_a_seed_file_that_fails_is_announced(self):
        """This one has to be loud — someone tried to update seed data and it
        did not take."""
        from src.services import slack_transport

        outcome = slack_transport.classify_upload("personnel.csv", "wrong,columns\n1,2")
        assert outcome["announce"] is True
        assert outcome["applied"] is False
        assert "person_id" in outcome["detail"]

    def test_a_seed_file_that_loads_is_announced(self):
        import os
        from src.services import slack_transport

        content = open(os.path.join(SEED, "personnel.csv")).read()
        outcome = slack_transport.classify_upload("personnel.csv", content)
        assert outcome["announce"] is True
        assert outcome["applied"] is True

    def test_the_whole_demo_folder_produces_eight_messages(self):
        """Fifteen files in, eight confirmations out, no refusals."""
        import os
        from src.services import slack_transport

        folder = "/Users/heartlin/Projects/firstresponder-slack/templates/demo"
        if not os.path.isdir(folder):
            pytest.skip("demo folder not present")
        announced, applied = 0, 0
        for name in sorted(os.listdir(folder)):
            if not name.endswith(".csv"):
                continue
            outcome = slack_transport.classify_upload(
                name, open(os.path.join(folder, name)).read())
            announced += outcome["announce"]
            applied += outcome["applied"]
        assert applied == 8
        assert announced == 8

"""The demo identity map is chosen, not arbitrary.

The reconciliation loop only acts on people it can reach, and the seed roster's
Slack ids are placeholders that address nobody. So which handful of real
identities get mapped decides whether the ping -> re-ping -> handoff arc happens
to anyone an audience can watch.

Two properties the seeding has to hold, both of which were wrong on the first
attempt: p001 is the incident commander, the floor-1 warden and the handset the
demo declares from, so it must be the operator's own account rather than
whichever member users.list returns first — anchoring it elsewhere sends the
IC's own messages to somebody who is not in the room. And the escalation target
must be a *different* identity from the person running the demo, or the handoff
lands back on them and proves nothing.
"""

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = (ROOT / "scripts" / "seed_demo_identities.py").read_text()


def _roles() -> list[str]:
    block = SCRIPT.split("ROLES = [", 1)[1].split("]", 1)[0]
    return re.findall(r'\("(p\d+)"', block)


class TestTheMapProducesAVisibleHandoff:
    def test_the_escalation_target_is_a_warden(self):
        from src.core.knowledge_base import KnowledgeBase, init_knowledge_base

        KnowledgeBase.reset()
        init_knowledge_base(str(ROOT / "data" / "seed"))
        wardens = {w["person_id"] for w in KnowledgeBase.get().get_floor_wardens()}
        mapped = _roles()
        assert set(mapped) & wardens, "no warden is mapped; no handoff can be received"

    def test_the_silent_people_and_their_warden_share_a_floor(self):
        """_warden_for prefers the same floor. Map people whose warden is on a
        different floor and the handoff goes to whoever happens to be first."""
        from src.core.knowledge_base import KnowledgeBase, init_knowledge_base
        from src.core.reconciliation_loop import _warden_for

        KnowledgeBase.reset()
        init_knowledge_base(str(ROOT / "data" / "seed"))
        kb = KnowledgeBase.get()
        mapped = set(_roles())
        wardens = {w["person_id"] for w in kb.get_floor_wardens()}
        non_wardens = [p for p in mapped - wardens if p != "p001"]
        assert non_wardens, "everyone mapped is a warden; nobody escalates"
        for person_id in non_wardens:
            warden = _warden_for(kb.get_person(person_id))
            assert warden is not None
            assert warden["person_id"] in mapped, (
                f"{person_id} escalates to {warden['person_id']}, which is not mapped "
                "— the handoff would be flagged to the IC instead of delivered")

    def test_the_handoff_does_not_land_on_the_incident_commander(self):
        """If every escalation returns to p001, the demo shows the loop paging
        the person already running it."""
        from src.core.knowledge_base import KnowledgeBase, init_knowledge_base
        from src.core.reconciliation_loop import _warden_for

        KnowledgeBase.reset()
        init_knowledge_base(str(ROOT / "data" / "seed"))
        kb = KnowledgeBase.get()
        mapped = set(_roles())
        wardens = {w["person_id"] for w in kb.get_floor_wardens()}
        targets = {_warden_for(kb.get_person(p))["person_id"]
                   for p in mapped - wardens if p != "p001"}
        assert targets - {"p001"}, "every mapped escalation lands on the IC"


class TestNothingIsCommitted:
    def test_no_workspace_id_or_phone_is_in_the_script(self):
        """The whole point of the env map is that these stay out of the repo."""
        assert not re.search(r"\bU0[A-Z0-9]{8,}\b", SCRIPT), "a real Slack id is committed"
        assert not re.search(r"\+1\d{10}", SCRIPT), "a real phone number is committed"

    def test_the_repo_carries_no_real_slack_ids(self):
        for path in [ROOT / "data" / "seed" / "personnel.csv",
                     ROOT / "README.md", ROOT / "docs" / "DEMO_SEQUENCE.md"]:
            if path.exists():
                assert not re.search(r"\bU0[A-Z0-9]{8,}\b", path.read_text()), path

    def test_the_script_uses_the_caret_delimiter(self):
        """gcloud reserves the comma in --update-env-vars: set with one and the
        whole string becomes a single id matching nobody."""
        assert '"^".join(pairs)' in SCRIPT

"""Tests for Slack transport — validates message formatting and reaction mapping."""

import os

import pytest

from src.core.knowledge_base import KnowledgeBase, init_knowledge_base

SEED_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "seed",
)


@pytest.fixture(autouse=True)
def fresh_state():
    KnowledgeBase.reset()
    init_knowledge_base(SEED_DIR)
    # Reset the slack map
    from src.services.slack_transport import _slack_to_person
    _slack_to_person.clear()
    yield
    KnowledgeBase.reset()
    _slack_to_person.clear()


class TestReactionMapping:
    def test_safe_reactions(self):
        from src.services.slack_transport import REACTION_STATUS_MAP
        assert REACTION_STATUS_MAP["white_check_mark"] == "safe"
        assert REACTION_STATUS_MAP["heavy_check_mark"] == "safe"

    def test_injured_reaction(self):
        from src.services.slack_transport import REACTION_STATUS_MAP
        assert REACTION_STATUS_MAP["ambulance"] == "injured"

    def test_need_help_reactions(self):
        from src.services.slack_transport import REACTION_STATUS_MAP
        assert REACTION_STATUS_MAP["warning"] == "need_help"
        assert REACTION_STATUS_MAP["sos"] == "need_help"

    def test_evacuated_reaction(self):
        from src.services.slack_transport import REACTION_STATUS_MAP
        assert REACTION_STATUS_MAP["runner"] == "evacuated"


class TestSlackUserMapping:
    def test_build_slack_map(self):
        from src.services.slack_transport import _build_slack_map, _slack_to_person
        _build_slack_map()
        assert _slack_to_person["U_PRINCIPAL"] == "p001"
        assert _slack_to_person["U_NURSE"] == "p004"
        assert _slack_to_person["U_FRANKLIN"] == "p025"

    def test_all_personnel_mapped(self):
        from src.services.slack_transport import _build_slack_map, _slack_to_person
        _build_slack_map()
        assert len(_slack_to_person) == 34

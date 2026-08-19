"""Tests for Memory Bank — persistent cross-session lesson storage."""

import pytest

from src.core.memory_bank import MemoryBank, init_memory_bank


@pytest.fixture(autouse=True)
def fresh_mb():
    MemoryBank.reset()
    yield
    MemoryBank.reset()


class TestMemoryBankInit:
    def test_pre_seeded_lessons(self):
        init_memory_bank()
        mb = MemoryBank.get()
        assert len(mb.lessons) == 5

    def test_pre_seeded_outcomes(self):
        init_memory_bank()
        mb = MemoryBank.get()
        assert len(mb.incident_outcomes) == 2


class TestLessonStorage:
    def test_store_and_retrieve(self):
        mb = MemoryBank.get()
        lid = mb.store_lesson(
            incident_id="INC-001",
            incident_type="fire",
            facility_id="jefferson",
            title="Test lesson",
            body="This is a test lesson body.",
            category="evacuation",
            tags=["test", "fire"],
        )
        assert lid
        lessons = mb.find_lessons(incident_type="fire")
        assert len(lessons) == 1
        assert lessons[0]["title"] == "Test lesson"

    def test_find_by_category(self):
        init_memory_bank()
        mb = MemoryBank.get()
        evac = mb.find_lessons(category="evacuation")
        assert len(evac) >= 1
        assert all(l["category"] == "evacuation" for l in evac)

    def test_find_by_tags(self):
        init_memory_bank()
        mb = MemoryBank.get()
        results = mb.find_lessons(tags=["science_lab"])
        assert len(results) >= 1

    def test_find_fire_lessons(self):
        init_memory_bank()
        mb = MemoryBank.get()
        fire = mb.find_lessons(incident_type="fire")
        assert len(fire) == 3  # stairwell bottleneck, elevator key, gas shutoff


class TestOutcomeStats:
    def test_stats(self):
        init_memory_bank()
        mb = MemoryBank.get()
        stats = mb.get_outcome_stats("fire")
        assert stats["total_incidents"] == 2
        assert stats["accountability_rate"] == 100.0
        assert stats["avg_response_time_seconds"] == 255  # avg of 270 and 240

    def test_no_outcomes(self):
        mb = MemoryBank.get()
        stats = mb.get_outcome_stats("flood")
        assert stats["total_incidents"] == 0


class TestLearningTools:
    def test_find_similar_incidents(self):
        init_memory_bank()
        from src.agents.learning.tools import find_similar_incidents

        result = find_similar_incidents("fire", "jefferson")
        assert result["lessons_found"] == 3
        assert result["historical_stats"]["total_incidents"] == 2

    def test_find_similar_with_no_history(self):
        init_memory_bank()
        from src.agents.learning.tools import find_similar_incidents

        result = find_similar_incidents("flood")
        assert result["lessons_found"] == 0

    def test_store_lesson_via_tool(self):
        init_memory_bank()
        from src.agents.learning.tools import store_lesson

        result = store_lesson(
            incident_id="FIRE-2026-001",
            incident_type="fire",
            lesson_title="New lesson",
            lesson_body="A new lesson from the latest incident.",
            tags="fire,new",
        )
        assert result["status"] == "stored"

        mb = MemoryBank.get()
        fire = mb.find_lessons(incident_type="fire")
        assert len(fire) == 4  # 3 seeded + 1 new

    def test_produce_aar(self):
        init_memory_bank()
        from src.agents.learning.tools import produce_after_action_review

        result = produce_after_action_review(
            incident_id="FIRE-2026-001",
            incident_type="fire",
            total_personnel=34,
            accounted=32,
            response_time_seconds=300,
            issues_identified="Two staff unaccounted for 5 minutes",
            what_worked="Check-in system worked well",
            what_to_improve="Faster escalation of missing personnel",
        )
        assert result["type"] == "AFTER_ACTION_REVIEW"
        assert result["response_metrics"]["accountability_rate"] == 94.1
        assert result["historical_comparison"]["total_incidents"] == 3  # 2 seeded + this one

    def test_propose_playbook_change(self):
        init_memory_bank()
        from src.agents.learning.tools import propose_playbook_change

        result = propose_playbook_change(
            playbook_id="playbook-fire-v1",
            incident_id="FIRE-2026-001",
            change_description="Add gas shutoff verification step",
            rationale="Gas shutoff was not verified in the last drill",
            affected_sections="evacuation,floor_2",
        )
        assert result["REQUIRES_HUMAN_APPROVAL"] is True
        assert result["status"] == "pending_approval"

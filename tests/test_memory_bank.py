"""Tests for Memory Bank — persistent cross-session lesson storage.

These exercise search, confidence scoring, citation and outcome stats, all of
which need a corpus to search. That corpus used to be the five demo lessons
`init_memory_bank()` wrote into every deployment; it now lives here, where
fiction belongs. Production starts empty — see test_memory_starts_empty.py.
"""

import pytest

from src.core.memory_bank import MemoryBank, init_memory_bank


def corpus() -> MemoryBank:
    """Three fire lessons, one weather, one medical, two fire outcomes."""
    mb = MemoryBank.get()
    mb.store_lesson(
        incident_id="FIRE-2025-DRILL-001", incident_type="fire",
        facility_id="jefferson",
        title="Floor 2 west stairwell bottleneck during fire drill",
        body="The west stairwell backed up when Room 215 and Room 210 evacuated together.",
        category="evacuation",
        tags=["fire", "floor2", "stairwell", "bottleneck", "science_lab", "accessibility"])
    mb.store_lesson(
        incident_id="FIRE-2025-DRILL-001", incident_type="fire",
        facility_id="jefferson",
        title="Elevator key should be pre-staged on Floor 2 for mobility evacuations",
        body="Retrieving the key from the Floor 1 office cost two minutes.",
        category="accessibility",
        tags=["fire", "elevator", "accessibility", "mobility", "key"])
    mb.store_lesson(
        incident_id="FIRE-2025-DRILL-002", incident_type="fire",
        facility_id="jefferson",
        title="Science lab gas shutoff must be verified before evacuation clearance",
        body="The Room 215 gas valve was not confirmed closed before the all-clear.",
        category="playbook",
        tags=["fire", "gas_shutoff", "science_lab", "floor2", "hazmat"])
    mb.store_lesson(
        incident_id="WEATHER-2025-001", incident_type="severe_weather",
        facility_id="jefferson",
        title="Shelter-in-place locations should be marked with signage",
        body="Staff were unsure which interior hallway was the designated shelter.",
        category="facilities",
        tags=["severe_weather", "shelter", "signage", "floor2"])
    mb.store_lesson(
        incident_id="MEDICAL-2025-001", incident_type="medical",
        facility_id="jefferson",
        title="AED response time from west wing was over 3 minutes",
        body="The responding teacher did not know where the nearest AED was.",
        category="resources",
        tags=["medical", "aed", "west_wing", "training"])
    for iid, seconds in (("FIRE-2025-DRILL-001", 270), ("FIRE-2025-DRILL-002", 240)):
        mb.store_incident_outcome(
            incident_id=iid, incident_type="fire", facility_id="jefferson",
            total_personnel=34, accounted=34, response_time_seconds=seconds,
            resolved=True, summary=f"Drill completed in {seconds}s, all 34 accounted for.")
    return mb


@pytest.fixture(autouse=True)
def fresh_mb():
    MemoryBank.reset()
    yield
    MemoryBank.reset()


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
        mb = corpus()
        evac = mb.find_lessons(category="evacuation")
        assert len(evac) >= 1
        assert all(l["category"] == "evacuation" for l in evac)

    def test_find_by_tags(self):
        mb = corpus()
        results = mb.find_lessons(tags=["science_lab"])
        assert len(results) >= 1

    def test_find_fire_lessons(self):
        mb = corpus()
        fire = mb.find_lessons(incident_type="fire")
        assert len(fire) == 3  # stairwell bottleneck, elevator key, gas shutoff


class TestOutcomeStats:
    def test_stats(self):
        mb = corpus()
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
        corpus()
        from src.agents.learning.tools import find_similar_incidents

        result = find_similar_incidents("fire", "jefferson")
        assert result["lessons_found"] == 3
        assert result["historical_stats"]["total_incidents"] == 2

    def test_find_similar_with_no_history(self):
        corpus()
        from src.agents.learning.tools import find_similar_incidents

        result = find_similar_incidents("flood")
        assert result["lessons_found"] == 0

    def test_store_lesson_via_tool(self):
        corpus()
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
        assert len(fire) == 4  # 3 in the corpus + 1 new

    def test_produce_aar(self):
        corpus()
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
        assert result["historical_comparison"]["total_incidents"] == 3  # 2 in the corpus + this one

    def test_propose_playbook_change(self):
        corpus()
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


class TestJaccardConfidence:
    """Batch D: Jaccard tag-overlap confidence scores."""

    def test_confidence_present_on_every_lesson(self):
        corpus()
        from src.agents.learning.tools import find_similar_incidents

        result = find_similar_incidents("fire", "jefferson")
        for lesson in result["lessons"]:
            assert "confidence" in lesson
            assert isinstance(lesson["confidence"], float)
            assert 0.0 <= lesson["confidence"] <= 1.0

    def test_results_sorted_by_confidence_descending(self):
        corpus()
        from src.agents.learning.tools import find_similar_incidents

        result = find_similar_incidents("fire", "jefferson")
        confidences = [l["confidence"] for l in result["lessons"]]
        assert confidences == sorted(confidences, reverse=True)

    def test_query_tags_included_in_response(self):
        corpus()
        from src.agents.learning.tools import find_similar_incidents

        result = find_similar_incidents("fire", "jefferson", tags="floor2,science_lab")
        assert "query_tags" in result
        assert "fire" in result["query_tags"]
        assert "jefferson" in result["query_tags"]
        assert "floor2" in result["query_tags"]
        assert "science_lab" in result["query_tags"]

    def test_extra_tags_boost_confidence(self):
        corpus()
        from src.agents.learning.tools import find_similar_incidents

        broad = find_similar_incidents("fire", "jefferson")
        narrow = find_similar_incidents("fire", "jefferson", tags="floor2,science_lab,stairwell")

        broad_top = broad["lessons"][0]["confidence"]
        narrow_top = narrow["lessons"][0]["confidence"]
        assert narrow_top >= broad_top

    def test_jaccard_math_exact(self):
        from src.agents.learning.tools import _jaccard

        assert _jaccard({"a", "b", "c"}, {"b", "c", "d"}) == 2 / 4
        assert _jaccard({"a"}, {"a"}) == 1.0
        assert _jaccard({"a"}, {"b"}) == 0.0
        assert _jaccard(set(), set()) == 0.0


class TestSourceCitation:
    """Batch D: Source citations on every recalled lesson."""

    def test_citation_on_every_lesson(self):
        corpus()
        from src.agents.learning.tools import find_similar_incidents

        result = find_similar_incidents("fire", "jefferson")
        for lesson in result["lessons"]:
            assert "source" in lesson
            src = lesson["source"]
            assert "incident_id" in src
            assert "lesson_id" in src

    def test_citation_includes_outcome_when_available(self):
        corpus()
        from src.agents.learning.tools import find_similar_incidents

        result = find_similar_incidents("fire", "jefferson")
        has_outcome = False
        for lesson in result["lessons"]:
            src = lesson["source"]
            if "outcome_summary" in src:
                has_outcome = True
                assert src["outcome_summary"]
                assert "response_time_seconds" in src
        assert has_outcome, "At least one fire lesson should have a linked outcome"

    def test_citation_without_outcome(self):
        corpus()
        from src.agents.learning.tools import find_similar_incidents

        result = find_similar_incidents("severe_weather", "jefferson")
        assert result["lessons_found"] >= 1
        lesson = result["lessons"][0]
        assert "incident_id" in lesson["source"]
        assert "outcome_summary" not in lesson["source"]


class TestCrossSessionRecall:
    """Batch D / GAP-08: Lessons stored during incident A surface during incident B."""

    def test_cross_incident_recall_with_citation_and_confidence(self):
        corpus()
        from src.agents.learning.tools import find_similar_incidents, store_lesson

        store_lesson(
            incident_id="FIRE-2026-INCIDENT-A",
            incident_type="fire",
            lesson_title="Cafeteria exit was blocked by delivery truck",
            lesson_body=(
                "During FIRE-2026-INCIDENT-A, the cafeteria emergency exit on the "
                "south side was blocked by a parked delivery truck. The delivery "
                "schedule overlapped with the incident window. Recommend: no "
                "deliveries during peak occupancy hours."
            ),
            facility_id="jefferson",
            category="evacuation",
            tags="fire,cafeteria,blocked_exit,delivery,floor1",
        )

        result = find_similar_incidents(
            "fire", "jefferson", tags="cafeteria,blocked_exit"
        )

        lesson_titles = [l["title"] for l in result["lessons"]]
        assert "Cafeteria exit was blocked by delivery truck" in lesson_titles

        cross_lesson = next(
            l for l in result["lessons"]
            if l["title"] == "Cafeteria exit was blocked by delivery truck"
        )

        assert cross_lesson["source"]["incident_id"] == "FIRE-2026-INCIDENT-A"
        assert cross_lesson["source"]["lesson_id"]
        assert cross_lesson["confidence"] > 0.0

    def test_cross_incident_different_facility(self):
        """Lessons from facility X surface when querying facility Y if type matches."""
        corpus()
        from src.agents.learning.tools import find_similar_incidents, store_lesson

        store_lesson(
            incident_id="FIRE-2026-LINCOLN-001",
            incident_type="fire",
            lesson_title="Lincoln gym had no working fire extinguisher",
            lesson_body="The gym fire extinguisher was expired during the drill.",
            facility_id="lincoln",
            category="resources",
            tags="fire,gym,extinguisher",
        )

        result = find_similar_incidents("fire", "lincoln")
        titles = [l["title"] for l in result["lessons"]]
        assert "Lincoln gym had no working fire extinguisher" in titles

    def test_existing_lessons_survive_a_new_one(self):
        """Storing a lesson doesn't disturb the ones already there."""
        corpus()
        from src.agents.learning.tools import find_similar_incidents, store_lesson

        store_lesson(
            incident_id="FIRE-2026-NEW",
            incident_type="fire",
            lesson_title="New lesson from new incident",
            lesson_body="A newly stored lesson.",
            facility_id="jefferson",
            category="general",
            tags="fire,new",
        )

        result = find_similar_incidents("fire", "jefferson")
        assert result["lessons_found"] >= 4  # 3 in the corpus + 1 new
        titles = [l["title"] for l in result["lessons"]]
        assert "Floor 2 west stairwell bottleneck during fire drill" in titles
        assert "New lesson from new incident" in titles

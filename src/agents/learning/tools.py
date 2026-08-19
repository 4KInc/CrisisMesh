"""Tools for the Learning & After-Action Agent."""

from __future__ import annotations

from datetime import datetime
from typing import Any


def find_similar_incidents(
    incident_type: str,
    facility_id: str = "",
    limit: int = 5,
) -> dict[str, Any]:
    """Find similar past incidents from the Memory Bank.

    Args:
        incident_type: Type of incident to match.
        facility_id: Optional facility filter.
        limit: Max number of results.

    Returns:
        List of similar past incidents with lessons.
    """
    # Stub — queries Firestore lessons collection
    return {
        "incident_type": incident_type,
        "facility_id": facility_id,
        "similar_incidents": [],
        "lessons_found": 0,
        "source": "memory_bank.lessons",
    }


def produce_after_action_review(
    incident_id: str,
    incident_type: str,
    timeline: list[dict[str, str]],
    accountability_summary: dict[str, Any],
    response_time_seconds: int,
    issues_identified: list[str],
    what_worked: list[str],
    what_to_improve: list[str],
) -> dict[str, Any]:
    """Produce a structured After-Action Review for a resolved incident.

    Args:
        incident_id: The resolved incident ID.
        incident_type: Type of incident.
        timeline: Full incident timeline.
        accountability_summary: Final accountability numbers.
        response_time_seconds: Time from declaration to full accountability.
        issues_identified: Issues found during the response.
        what_worked: Things that worked well.
        what_to_improve: Areas for improvement.

    Returns:
        Structured AAR document.
    """
    return {
        "type": "AFTER_ACTION_REVIEW",
        "incident_id": incident_id,
        "incident_type": incident_type,
        "generated_at": datetime.utcnow().isoformat(),
        "timeline_events": len(timeline),
        "response_metrics": {
            "response_time_seconds": response_time_seconds,
            "total_tracked": accountability_summary.get("total_tracked", 0),
            "accounted": accountability_summary.get("accounted", 0),
        },
        "analysis": {
            "issues_identified": issues_identified,
            "what_worked": what_worked,
            "what_to_improve": what_to_improve,
        },
        "requires_team_confirmation": True,
    }


def store_lesson(
    incident_id: str,
    incident_type: str,
    lesson_title: str,
    lesson_body: str,
    category: str = "general",
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """Store a lesson learned from an incident into the Memory Bank.

    Args:
        incident_id: Source incident ID.
        incident_type: Type of incident.
        lesson_title: Short title for the lesson.
        lesson_body: Detailed lesson description.
        category: Lesson category (general, accountability, communication, resources, playbook).
        tags: Optional tags for search.

    Returns:
        Confirmation of stored lesson.
    """
    return {
        "status": "stored",
        "incident_id": incident_id,
        "incident_type": incident_type,
        "lesson_title": lesson_title,
        "category": category,
        "tags": tags or [],
        "stored_at": datetime.utcnow().isoformat(),
        "source": "memory_bank.lessons",
    }


def propose_playbook_change(
    playbook_id: str,
    incident_id: str,
    change_description: str,
    rationale: str,
    affected_sections: list[str],
) -> dict[str, Any]:
    """Propose a change to an approved playbook (requires human approval).

    Args:
        playbook_id: The playbook to modify.
        incident_id: The incident that prompted this proposal.
        change_description: What should change.
        rationale: Why this change is needed.
        affected_sections: Which sections of the playbook are affected.

    Returns:
        Change proposal requiring human approval.
    """
    return {
        "type": "PLAYBOOK_CHANGE_PROPOSAL",
        "playbook_id": playbook_id,
        "incident_id": incident_id,
        "change_description": change_description,
        "rationale": rationale,
        "affected_sections": affected_sections,
        "proposed_at": datetime.utcnow().isoformat(),
        "status": "pending_approval",
        "REQUIRES_HUMAN_APPROVAL": True,
    }

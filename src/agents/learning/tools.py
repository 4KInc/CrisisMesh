"""Tools for the Learning & After-Action Agent.

Uses an in-memory lesson store for local operation.
In production, lessons persist in Firestore Memory Bank.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any


# In-memory lesson store: list of lesson dicts
_lesson_store: list[dict[str, Any]] = []


def find_similar_incidents(
    incident_type: str,
    facility_id: str = "",
    limit: int = 5,
) -> dict[str, Any]:
    """Find similar past incidents and their lessons from the Memory Bank.

    Args:
        incident_type: Type of incident to match (e.g. 'fire').
        facility_id: Optional facility filter.
        limit: Max number of results.

    Returns:
        List of similar past incidents with lessons learned.
    """
    matches = [
        lesson for lesson in _lesson_store
        if lesson.get("incident_type") == incident_type
    ]
    if facility_id:
        facility_matches = [l for l in matches if l.get("facility_id") == facility_id]
        if facility_matches:
            matches = facility_matches

    matches = sorted(matches, key=lambda x: x.get("stored_at", ""), reverse=True)[:limit]

    return {
        "incident_type": incident_type,
        "facility_id": facility_id,
        "similar_incidents": matches,
        "lessons_found": len(matches),
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
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "timeline_events": len(timeline),
        "response_metrics": {
            "response_time_seconds": response_time_seconds,
            "total_tracked": accountability_summary.get("total_tracked", 0),
            "accounted": accountability_summary.get("accounted", 0),
            "unaccounted": accountability_summary.get("unaccounted", 0),
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
    facility_id: str = "jefferson",
    category: str = "general",
    tags: str = "",
) -> dict[str, Any]:
    """Store a lesson learned from an incident into the Memory Bank.

    Args:
        incident_id: Source incident ID.
        incident_type: Type of incident.
        lesson_title: Short title for the lesson.
        lesson_body: Detailed lesson description.
        facility_id: Facility this lesson applies to.
        category: Lesson category (general, accountability, communication, resources, playbook).
        tags: Comma-separated tags for search.

    Returns:
        Confirmation of stored lesson.
    """
    lesson_id = str(uuid.uuid4())
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []

    lesson = {
        "id": lesson_id,
        "incident_id": incident_id,
        "incident_type": incident_type,
        "facility_id": facility_id,
        "lesson_title": lesson_title,
        "lesson_body": lesson_body,
        "category": category,
        "tags": tag_list,
        "stored_at": datetime.now(timezone.utc).isoformat(),
        "approved": True,
    }
    _lesson_store.append(lesson)

    return {
        "status": "stored",
        "lesson_id": lesson_id,
        "incident_id": incident_id,
        "lesson_title": lesson_title,
        "source": "memory_bank.lessons",
    }


def propose_playbook_change(
    playbook_id: str,
    incident_id: str,
    change_description: str,
    rationale: str,
    affected_sections: list[str] | str = "",
) -> dict[str, Any]:
    """Propose a change to an approved playbook (requires human approval).

    Args:
        playbook_id: The playbook to modify.
        incident_id: The incident that prompted this proposal.
        change_description: What should change.
        rationale: Why this change is needed.
        affected_sections: Which sections of the playbook are affected (comma-separated).

    Returns:
        Change proposal requiring human approval.
    """
    if isinstance(affected_sections, str):
        sections = [s.strip() for s in affected_sections.split(",") if s.strip()]
    else:
        sections = affected_sections

    return {
        "type": "PLAYBOOK_CHANGE_PROPOSAL",
        "playbook_id": playbook_id,
        "incident_id": incident_id,
        "change_description": change_description,
        "rationale": rationale,
        "affected_sections": sections,
        "proposed_at": datetime.now(timezone.utc).isoformat(),
        "status": "pending_approval",
        "REQUIRES_HUMAN_APPROVAL": True,
    }

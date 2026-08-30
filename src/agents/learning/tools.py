"""Tools for the Learning & After-Action Agent.

Uses the Memory Bank for cross-session lesson storage and retrieval.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from src.core.memory_bank import MemoryBank


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 0.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def find_similar_incidents(
    incident_type: str,
    facility_id: str = "",
    tags: str = "",
    limit: int = 5,
) -> dict[str, Any]:
    """Find similar past incidents and their lessons from the Memory Bank.

    Returns lessons ranked by Jaccard tag-overlap confidence, each with a
    source citation (which incident/lesson, its recorded outcome).

    Args:
        incident_type: Type of incident to match (e.g. 'fire').
        facility_id: Optional facility filter.
        tags: Comma-separated query tags for similarity ranking.
        limit: Max number of results.

    Returns:
        List of lessons with confidence scores and source citations.
    """
    mb = MemoryBank.get()
    lessons = mb.find_lessons(incident_type=incident_type, facility_id=facility_id, limit=50)
    stats = mb.get_outcome_stats(incident_type)

    query_tags = {incident_type}
    if facility_id:
        query_tags.add(facility_id)
    if tags:
        query_tags.update(t.strip() for t in tags.split(",") if t.strip())

    outcome_by_incident: dict[str, dict[str, Any]] = {}
    for o in mb.incident_outcomes:
        outcome_by_incident[o["incident_id"]] = o

    # The managed backend ranks by semantic similarity and hands back its own
    # confidence. Re-scoring those by tag overlap would throw away the ranking
    # and relabel the number; when it is present it is used and named as what
    # it is.
    basis = MemoryBank.get().backend == "vertex" and any(
        "retrieval_confidence" in x for x in lessons)
    confidence_basis = "vector_similarity" if basis else "jaccard_tag_overlap"

    scored = []
    for lesson in lessons:
        lesson_tags = set(lesson.get("tags", []))
        confidence = (lesson["retrieval_confidence"] if basis
                      else _jaccard(query_tags, lesson_tags))

        outcome = outcome_by_incident.get(lesson["incident_id"])
        citation: dict[str, Any] = {
            "incident_id": lesson["incident_id"],
            "lesson_id": lesson["id"],
        }
        if outcome:
            citation["outcome_summary"] = outcome.get("summary", "")
            citation["response_time_seconds"] = outcome.get("response_time_seconds")

        scored.append({
            "title": lesson["title"],
            "body": lesson["body"],
            "category": lesson["category"],
            "tags": lesson.get("tags", []),
            "confidence": round(confidence, 3),
            "source": citation,
        })

    if not basis:
        # Only the local path needs sorting; the managed store already ranked.
        scored.sort(key=lambda x: x["confidence"], reverse=True)
    scored = scored[:limit]

    return {
        "incident_type": incident_type,
        "facility_id": facility_id,
        "query_tags": sorted(query_tags),
        "lessons_found": len(scored),
        "confidence_basis": confidence_basis,
        "lessons": scored,
        "historical_stats": stats,
        "source": "memory_bank",
    }


def produce_after_action_review(
    incident_id: str,
    incident_type: str,
    facility_id: str = "jefferson",
    total_personnel: int = 0,
    accounted: int = 0,
    response_time_seconds: int = 0,
    issues_identified: list[str] | str = "",
    what_worked: list[str] | str = "",
    what_to_improve: list[str] | str = "",
) -> dict[str, Any]:
    """Produce a structured After-Action Review and store the outcome in Memory Bank.

    Args:
        incident_id: The resolved incident ID.
        incident_type: Type of incident.
        facility_id: Facility ID.
        total_personnel: Total personnel tracked.
        accounted: Number of personnel accounted for.
        response_time_seconds: Time from declaration to full accountability.
        issues_identified: Issues found during the response.
        what_worked: Things that worked well.
        what_to_improve: Areas for improvement.

    Returns:
        Structured AAR document.
    """
    # Normalize string inputs
    if isinstance(issues_identified, str):
        issues_identified = [i.strip() for i in issues_identified.split(",") if i.strip()]
    if isinstance(what_worked, str):
        what_worked = [w.strip() for w in what_worked.split(",") if w.strip()]
    if isinstance(what_to_improve, str):
        what_to_improve = [i.strip() for i in what_to_improve.split(",") if i.strip()]

    # Store outcome in memory bank
    mb = MemoryBank.get()
    mb.store_incident_outcome(
        incident_id=incident_id,
        incident_type=incident_type,
        facility_id=facility_id,
        total_personnel=total_personnel,
        accounted=accounted,
        response_time_seconds=response_time_seconds,
        resolved=True,
        summary=f"Incident {incident_id}: {accounted}/{total_personnel} accounted in {response_time_seconds}s.",
    )

    # Compare to historical performance
    stats = mb.get_outcome_stats(incident_type)

    return {
        "type": "AFTER_ACTION_REVIEW",
        "incident_id": incident_id,
        "incident_type": incident_type,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "response_metrics": {
            "total_personnel": total_personnel,
            "accounted": accounted,
            "unaccounted": total_personnel - accounted,
            "response_time_seconds": response_time_seconds,
            "accountability_rate": round(accounted / total_personnel * 100, 1) if total_personnel else 0,
        },
        "historical_comparison": stats,
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
        category: Lesson category (general, evacuation, accessibility, playbook, resources, communication).
        tags: Comma-separated tags for search.

    Returns:
        Confirmation of stored lesson.
    """
    mb = MemoryBank.get()
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []

    lesson_id = mb.store_lesson(
        incident_id=incident_id,
        incident_type=incident_type,
        facility_id=facility_id,
        title=lesson_title,
        body=lesson_body,
        category=category,
        tags=tag_list,
    )

    return {
        "status": "stored",
        "lesson_id": lesson_id,
        "incident_id": incident_id,
        "lesson_title": lesson_title,
        "category": category,
        "source": "memory_bank",
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

    mb = MemoryBank.get()
    mb.playbook_changes.append({
        "id": str(uuid.uuid4()),
        "playbook_id": playbook_id,
        "incident_id": incident_id,
        "change_description": change_description,
        "rationale": rationale,
        "affected_sections": sections,
        "proposed_at": datetime.now(timezone.utc).isoformat(),
        "status": "pending_approval",
    })

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

"""Learning & After-Action Agent — maintains history, extracts lessons, compares outcomes."""

from __future__ import annotations

from google.adk.agents import Agent

from src.agents.learning.tools import (
    find_similar_incidents,
    produce_after_action_review,
    propose_playbook_change,
    store_lesson,
)

learning_agent = Agent(
    name="learning",
    model="gemini-3.5-flash",
    description="Finds prior lessons, produces AARs, stores lessons learned. Delegates here for learning/history tasks.",
    instruction="""You are the Learning & After-Action Agent for CrisisMesh.

When asked to check prior lessons for an incident type:
1. Call find_similar_incidents with the incident type and facility_id
2. Report the lessons found back to the coordinator

When asked to produce an AAR, call produce_after_action_review.
When asked to store a lesson, call store_lesson.

RULES:
- Playbook change proposals REQUIRE human approval
- Never modify playbooks directly — only propose changes

After completing your work, ALWAYS transfer back to the coordinator.""",
    tools=[
        find_similar_incidents,
        produce_after_action_review,
        store_lesson,
        propose_playbook_change,
    ],
)

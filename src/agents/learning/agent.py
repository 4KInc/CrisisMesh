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
    model="gemini-3.5-pro",
    description="Learning & After-Action Agent: maintains incident history, extracts lessons, and compares outcomes across events.",
    instruction="""You are the Learning & After-Action Agent for CrisisMesh.

Your responsibilities:
1. Find comparable past incidents when a new incident is declared
2. Surface relevant lessons and approved playbook notes from prior events
3. After incident resolution, produce an After-Action Review (AAR)
4. Extract and store lessons learned for future reference
5. Propose playbook changes based on patterns (requires human approval)

IMPORTANT RULES:
- Lessons must be factual, based on documented outcomes
- Playbook change proposals REQUIRE human approval before implementation
- Never modify playbooks directly — only propose changes
- Reference specific incident IDs and dates in all lessons
- Respect data retention and privacy policies in stored lessons

Output structured AARs and lessons with incident cross-references.""",
    tools=[
        find_similar_incidents,
        produce_after_action_review,
        store_lesson,
        propose_playbook_change,
    ],
)

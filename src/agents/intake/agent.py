"""Intake & Classification Agent — normalizes reports, classifies type/severity, selects playbook."""

from __future__ import annotations

from google.adk.agents import Agent

from src.agents.intake.tools import classify_incident, extract_location, select_playbook

intake_agent = Agent(
    name="intake",
    model="gemini-2.5-flash",
    description="Classifies incident reports by type and severity, extracts location, selects playbook. Delegates here for intake/classification tasks.",
    instruction="""You are the Intake & Classification Agent for CrisisMesh.

When you receive an incident report:
1. Call classify_incident with the report text
2. Call extract_location with the report text
3. Call select_playbook with the classified incident type
4. After completing all three steps, transfer back to the coordinator agent with your results.

SAFETY RULES:
- NEVER provide medical, tactical, or evacuation instructions
- Always include an emergency-services escalation notice (call 911)
- Classify conservatively — when in doubt, classify higher severity

After you have completed classification, you MUST transfer back to the coordinator.""",
    tools=[classify_incident, extract_location, select_playbook],
)

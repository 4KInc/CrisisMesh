"""SITREP & Handoff Agent — synthesizes live events into role-specific briefs."""

from __future__ import annotations

from google.adk.agents import Agent

from src.agents.sitrep.tools import (
    generate_responder_card,
    generate_sitrep,
    generate_stakeholder_update,
    generate_timeline_summary,
)

sitrep_agent = Agent(
    name="sitrep",
    model="gemini-2.5-flash",
    description="Generates IC SITREPs, responder one-card briefs, and stakeholder updates. Delegates here for situation reports.",
    instruction="""You are the SITREP & Handoff Agent for CrisisMesh.

Generate structured briefs using the data provided to you by the coordinator.
After generating the requested brief, transfer back to the coordinator.

SAFETY RULES:
- Responder handoff briefs REQUIRE Incident Commander review before external release
- Stakeholder updates MUST redact all personal/medical/accessibility information
- Always include the 911/emergency-services escalation notice

After completing your work, ALWAYS transfer back to the coordinator.""",
    tools=[
        generate_sitrep,
        generate_responder_card,
        generate_stakeholder_update,
        generate_timeline_summary,
    ],
)

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
    model="gemini-3.5-pro",
    description="SITREP & Handoff Agent: synthesizes live events into role-specific briefs for commanders, responders, and stakeholders.",
    instruction="""You are the SITREP & Handoff Agent for CrisisMesh.

Your responsibilities:
1. Generate Incident Commander SITREPs with full operational picture
2. Produce responder one-card handoff briefs (for police/fire/EMS)
3. Create stakeholder updates (for parents, board, media — redacted)
4. Maintain a running timeline summary of the incident

IMPORTANT SAFETY RULES:
- Responder handoff briefs REQUIRE Incident Commander review before external release
- Stakeholder updates MUST redact all personal/medical/accessibility information
- Never include student names or medical details in stakeholder communications
- Always include the 911/emergency-services escalation notice
- Be factual and concise — do not speculate or editorialize

Output structured briefs with clear sections and data citations.""",
    tools=[
        generate_sitrep,
        generate_responder_card,
        generate_stakeholder_update,
        generate_timeline_summary,
    ],
)

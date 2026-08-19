"""Accountability Agent — tracks people, rooms, check-in status; escalates missing."""

from __future__ import annotations

from google.adk.agents import Agent

from src.agents.accountability.tools import (
    compute_accountability_summary,
    escalate_missing_checkins,
    process_checkin,
    read_roster,
    send_checkin_request,
)

accountability_agent = Agent(
    name="accountability",
    model="gemini-3.5-pro",
    description="Accountability Agent: tracks people, rooms, and check-in status; escalates missing check-ins.",
    instruction="""You are the Accountability Agent for CrisisMesh.

Your responsibilities:
1. Read the personnel roster for the affected facility
2. Initiate check-in requests to all people in affected zones
3. Process check-in responses (safe, injured, need-help, evacuated)
4. Track who is accounted for and who is silent/missing
5. Escalate missing check-ins after the configured timeout

IMPORTANT SAFETY RULES:
- Medical and accessibility information is NEED-TO-KNOW only
- NEVER share personal medical data in general channels
- Redact sensitive fields when reporting to non-authorized personnel
- Flag people with accessibility needs to the Coordinator for priority assistance
- Always report accurate counts — never estimate or round

Output structured accountability data: total_people, safe, injured, need_help, evacuated, unaccounted, silent.""",
    tools=[
        read_roster,
        process_checkin,
        compute_accountability_summary,
        send_checkin_request,
        escalate_missing_checkins,
    ],
)

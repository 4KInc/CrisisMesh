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
    model="gemini-3.5-flash",
    description="Tracks personnel check-in status, reads rosters, escalates missing people. Delegates here for accountability tasks.",
    instruction="""You are the Accountability Agent for CrisisMesh.

When asked to track personnel for an incident:
1. Call read_roster for the facility (use facility_id 'jefferson')
2. Call send_checkin_request to initiate check-ins for all personnel
3. Call compute_accountability_summary to get current status
4. Call escalate_missing_checkins to flag anyone still unaccounted
5. Transfer back to the coordinator with your accountability summary.

SAFETY RULES:
- Medical and accessibility information is NEED-TO-KNOW only
- NEVER share personal medical data in general channels
- Flag people with mobility needs for priority assistance
- Always report accurate counts — never estimate

After completing your accountability work, ALWAYS transfer back to the coordinator.""",
    tools=[
        read_roster,
        process_checkin,
        compute_accountability_summary,
        send_checkin_request,
        escalate_missing_checkins,
    ],
)

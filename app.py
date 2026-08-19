"""CrisisMesh — main entry point for the multi-agent fleet."""

from __future__ import annotations

import asyncio
import os

from dotenv import load_dotenv
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai.types import Content, Part

from src.agents.coordinator.agent import coordinator_agent
from src.core.knowledge_base import init_knowledge_base

load_dotenv()

# Initialize the knowledge base with seed data at startup
init_knowledge_base()

APP_NAME = "crisismesh"
USER_ID = "commander"


async def run_incident(report: str) -> None:
    """Run the CrisisMesh fleet against an incident report."""
    session_service = InMemorySessionService()
    session = await session_service.create_session(
        app_name=APP_NAME,
        user_id=USER_ID,
    )

    runner = Runner(
        agent=coordinator_agent,
        app_name=APP_NAME,
        session_service=session_service,
    )

    user_message = Content(
        role="user",
        parts=[Part(text=report)],
    )

    print(f"\n{'='*60}")
    print(f"CRISISMESH INCIDENT REPORT")
    print(f"{'='*60}")
    print(f"Report: {report}")
    print(f"{'='*60}\n")

    async for event in runner.run_async(
        user_id=USER_ID,
        session_id=session.id,
        new_message=user_message,
    ):
        if event.is_final_response():
            response_text = event.content.parts[0].text if event.content and event.content.parts else ""
            print(f"\n{'='*60}")
            print("COORDINATOR RESPONSE:")
            print(f"{'='*60}")
            print(response_text)
            print(f"{'='*60}\n")
        elif event.actions and event.actions.escalations:
            for esc in event.actions.escalations:
                print(f"[ESCALATION] {esc}")


async def main() -> None:
    """Demo: run a fire drill scenario."""
    report = (
        "Smoke detected near the science lab on floor 2 of the main building. "
        "Fire alarm has been triggered. Students and staff need to evacuate. "
        "This appears to be a serious fire situation."
    )
    await run_incident(report)


if __name__ == "__main__":
    asyncio.run(main())

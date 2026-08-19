#!/usr/bin/env python3
"""
CrisisMesh — Gemini-driven entrypoint.

Runs the Coordinator Agent through Vertex AI Gemini via Google ADK.
Gemini decides which sub-agents to delegate to and which tools to call.

Usage:
    # Default fire drill scenario
    python scripts/run_gemini.py

    # Custom report
    python scripts/run_gemini.py "Smoke near the science lab, floor 2 — kids still inside"

Requires:
    GOOGLE_CLOUD_PROJECT set in .env or environment
    GOOGLE_GENAI_USE_VERTEXAI=TRUE in .env or environment
    gcloud auth application-default login
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai.types import Content, Part

from src.agents.coordinator.agent import coordinator_agent
from src.core.knowledge_base import init_knowledge_base
from src.core.memory_bank import init_memory_bank

# Initialize data layers
init_knowledge_base()
init_memory_bank()

APP_NAME = "crisismesh"
USER_ID = "commander"

# ── Formatting ──
CYAN = "\033[96m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
MAGENTA = "\033[95m"
DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"


async def run_with_gemini(report: str) -> list[dict]:
    """Send an incident report through the ADK Coordinator via Gemini.

    Returns a log of all events (model calls, tool calls, delegations, responses).
    """
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

    user_message = Content(role="user", parts=[Part(text=report)])

    print(f"\n{BOLD}{CYAN}{'='*70}")
    print(f"  CRISISMESH — Gemini-Driven Incident Coordination")
    print(f"{'='*70}{RESET}")
    print(f"\n  {BOLD}Report:{RESET} {report}")
    print(f"  {BOLD}Model:{RESET} {coordinator_agent.model}")
    print(f"  {BOLD}Project:{RESET} {os.environ.get('GOOGLE_CLOUD_PROJECT', 'NOT SET')}")
    print(f"  {BOLD}Vertex AI:{RESET} {os.environ.get('GOOGLE_GENAI_USE_VERTEXAI', 'NOT SET')}")
    print(f"  {BOLD}Timestamp:{RESET} {datetime.now(timezone.utc).isoformat()}")
    print(f"\n{DIM}  Sending to Coordinator Agent via Vertex AI Gemini...{RESET}\n")

    event_log: list[dict] = []
    event_count = 0

    async for event in runner.run_async(
        user_id=USER_ID,
        session_id=session.id,
        new_message=user_message,
    ):
        event_count += 1
        author = getattr(event, "author", "")
        entry: dict = {
            "event_num": event_count,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "author": author,
        }

        # ── Agent transfer / delegation ──
        if event.actions and event.actions.transfer_to_agent:
            target = event.actions.transfer_to_agent
            entry["type"] = "delegation"
            entry["target_agent"] = target
            print(f"  {MAGENTA}[DELEGATE]{RESET} {BOLD}{author}{RESET} -> "
                  f"{BOLD}{CYAN}{target}{RESET}")

        # ── Escalation ──
        elif event.actions and event.actions.escalate:
            entry["type"] = "escalation"
            entry["escalation"] = str(event.actions.escalate)
            print(f"  {YELLOW}[ESCALATION]{RESET} {event.actions.escalate}")

        # ── Content with function calls (tool invocations by the model) ──
        elif event.content and event.content.parts:
            has_function_call = False
            has_function_response = False
            has_text = False
            text_parts = []

            for part in event.content.parts:
                # Model chose to call a tool
                if hasattr(part, "function_call") and part.function_call:
                    has_function_call = True
                    fc = part.function_call
                    tool_name = fc.name if hasattr(fc, "name") else str(fc)
                    tool_args = fc.args if hasattr(fc, "args") else {}
                    entry["type"] = "tool_call"
                    entry["tool_name"] = tool_name
                    entry["tool_args"] = _safe_serialize(tool_args)

                    print(f"  {YELLOW}[TOOL CALL]{RESET} {BOLD}{author}{RESET} -> "
                          f"{GREEN}{tool_name}{RESET}")
                    if isinstance(tool_args, dict):
                        for k, v in tool_args.items():
                            v_str = str(v)[:120]
                            print(f"    {DIM}{k}: {v_str}{RESET}")

                # Tool returned a result
                elif hasattr(part, "function_response") and part.function_response:
                    has_function_response = True
                    fr = part.function_response
                    name = fr.name if hasattr(fr, "name") else ""
                    entry["type"] = "tool_result"
                    entry["tool_name"] = name
                    print(f"  {DIM}[TOOL RESULT] {name}{RESET}")

                # Model produced text
                elif hasattr(part, "text") and part.text:
                    has_text = True
                    text_parts.append(part.text)

            if has_text and not has_function_call and not has_function_response:
                text = "".join(text_parts)
                if event.is_final_response():
                    entry["type"] = "final_response"
                    entry["text"] = text
                    print(f"\n{BOLD}{CYAN}{'─'*70}")
                    print(f"  FINAL RESPONSE ({author})")
                    print(f"{'─'*70}{RESET}")
                    print(f"\n{text}\n")
                else:
                    entry["type"] = "model_text"
                    entry["text"] = text
                    # Show intermediate reasoning
                    preview = text[:300].replace("\n", " ")
                    print(f"  {DIM}[{author}] {preview}{RESET}")

        if entry.get("type"):
            event_log.append(entry)

    print(f"\n{BOLD}{CYAN}{'='*70}")
    print(f"  TRACE COMPLETE — {len(event_log)} logged events from {event_count} raw events")
    print(f"{'='*70}{RESET}")

    # Summary
    delegations = [e for e in event_log if e.get("type") == "delegation"]
    tool_calls = [e for e in event_log if e.get("type") == "tool_call"]
    tool_results = [e for e in event_log if e.get("type") == "tool_result"]
    model_texts = [e for e in event_log if e.get("type") == "model_text"]

    print(f"\n  {BOLD}Summary:{RESET}")
    print(f"    Delegations:    {len(delegations)}")
    print(f"    Tool calls:     {len(tool_calls)}")
    print(f"    Tool results:   {len(tool_results)}")
    print(f"    Model texts:    {len(model_texts)}")
    if delegations:
        print(f"    Delegation path: {' -> '.join(e['target_agent'] for e in delegations)}")
    if tool_calls:
        print(f"    Tools invoked:   {', '.join(e['tool_name'] for e in tool_calls)}")
    print()

    return event_log


def _safe_serialize(obj):
    if isinstance(obj, dict):
        return {k: _safe_serialize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_safe_serialize(v) for v in obj]
    if isinstance(obj, (str, int, float, bool, type(None))):
        return obj
    return str(obj)


async def main() -> None:
    args = sys.argv[1:]

    if args and not args[0].startswith("--"):
        report = " ".join(args)
    else:
        report = "Smoke near the science lab, floor 2 — kids still inside"

    event_log = await run_with_gemini(report)

    # Write transcript to docs/
    docs_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs")
    os.makedirs(docs_dir, exist_ok=True)
    transcript_path = os.path.join(docs_dir, "gemini_transcript.json")
    with open(transcript_path, "w") as f:
        json.dump(event_log, f, indent=2, default=str)
    print(f"  Transcript saved to {transcript_path}")


if __name__ == "__main__":
    asyncio.run(main())

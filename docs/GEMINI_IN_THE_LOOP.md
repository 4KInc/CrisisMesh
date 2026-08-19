# Gemini In The Loop — Live Vertex AI Transcript

**Proof that Gemini 3.5 drives orchestration, delegation, and tool selection.**

This document records a live run of CrisisMesh against **Vertex AI Gemini 3.5 Flash**
(mandatory hackathon model floor: Gemini 3.5+).
The Coordinator Agent receives a natural-language incident report. Gemini decides
which sub-agents to delegate to and which tools each agent should call. The
deterministic KB/tools serve as the guarantee layer — they return real data —
but the **orchestration is model-driven**.

## Run Configuration

| Key | Value |
|-----|-------|
| Model | `gemini-3.5-flash` |
| GCP Project | `quick-catcher-470218-b0` |
| Backend | Vertex AI (`GOOGLE_GENAI_USE_VERTEXAI=TRUE`) |
| ADK Version | 2.7.1 |
| genai Version | 2.18.1 |
| Timestamp | 2026-08-19T14:42:37Z |
| Command | `python scripts/run_gemini.py "Smoke near the science lab, floor 2 — kids still inside"` |

## Model-Driven vs Code-Driven Map

| Step | Who decides | Evidence |
|------|-----------|---------|
| Delegate to intake | **Gemini 3.5** (Coordinator) | `transfer_to_agent(agent_name: intake)` |
| Call classify_incident + extract_location (parallel) | **Gemini 3.5** (Intake) | Model issued both calls simultaneously |
| Call select_playbook | **Gemini 3.5** (Intake) | Model chose tool + args from classify result |
| Transfer back to coordinator | **Gemini 3.5** (Intake) | `transfer_to_agent(agent_name: coordinator)` |
| Delegate to safety_intel | **Gemini 3.5** (Coordinator) | Model chose next delegation target |
| Call 13 safety tools | **Gemini 3.5** (Safety Intel) | zone_info, blocked_zones, 2x safe_routes, accessible_routes, 5x locate_resource, assembly_point, nearby_services |
| Transfer back to coordinator | **Gemini 3.5** (Safety Intel) | `transfer_to_agent(agent_name: coordinator)` |
| Delegate to accountability | **Gemini 3.5** (Coordinator) | Model chose next delegation target |
| Call 4 accountability tools | **Gemini 3.5** (Accountability) | read_roster, send_checkin_request, compute_summary, escalate_missing |
| Transfer back to coordinator | **Gemini 3.5** (Accountability) | `transfer_to_agent(agent_name: coordinator)` |
| Delegate to learning | **Gemini 3.5** (Coordinator) | Model chose next delegation target |
| Call find_similar_incidents | **Gemini 3.5** (Learning) | Model chose tool + args |
| Transfer back to coordinator | **Gemini 3.5** (Learning) | `transfer_to_agent(agent_name: coordinator)` |
| Synthesize final response | **Gemini 3.5** (Coordinator) | Model combines all agent results |

**Tools are code-driven** (deterministic, KB-backed). **Orchestration is model-driven** (Gemini decides delegation order, tool selection, argument construction, and final synthesis).

## Delegation Chain

```
coordinator
  -> intake (classify_incident, extract_location [parallel], select_playbook)
  -> coordinator
  -> safety_intel (find_zone_info, find_blocked_zones, 2x find_safe_routes,
                   find_accessible_routes, 5x locate_resource,
                   find_assembly_point, find_nearby_services)
  -> coordinator
  -> accountability (read_roster, send_checkin_request,
                     compute_accountability_summary, escalate_missing_checkins)
  -> coordinator
  -> learning (find_similar_incidents)
  -> coordinator
  -> FINAL RESPONSE
```

## Event Summary

| Metric | Count |
|--------|-------|
| Total events | 55 |
| Delegations | 8 |
| Tool calls (model-selected) | 27 |
| Tool results (KB-backed) | 19 |

## Behavior Comparison: gemini-3.5-flash vs gemini-2.5-flash

| Metric | 2.5-flash | 3.5-flash | Notes |
|--------|-----------|-----------|-------|
| Delegations | 8 | 8 | Identical chain |
| Tool calls | 27 | 27 | Same count |
| Transfer-back | All 4 agents | All 4 agents | No issues |
| Parallel tool calls | No | Yes (classify + extract_location) | 3.5 is smarter about parallelism |
| Safety tools | 11 | 13 (2x safe_routes, 5x locate_resource) | 3.5 probes more resource types (trauma_kit, emergency_phone) |
| Final response quality | Structured JSON/bullet | Rich formatted markdown with sections | 3.5 produces production-quality output |
| Mobility escalation | Named 2 people | Named 2 people + cited elevator key holder | 3.5 cross-references lesson with roster |
| Floor warden info | Not mentioned | Listed Ms. Johnson, Mrs. Nguyen, Tech Jordan | 3.5 highlights on-scene critical roles |

**Conclusion:** gemini-3.5-flash reproduces the full delegation chain with no regressions and produces a significantly richer final briefing.

## Gemini 3.5's Final Response (verbatim, abbreviated)

> **CRITICAL EMERGENCY NOTICE**
> If this is a life-threatening emergency, call 911 immediately.
>
> **Incident ID:** FIRE-2026-144242
> **Type:** Fire | **Location:** Room 215, West Wing Floor 2
> **Playbook:** playbook-fire-v1
>
> **Accountability:** 34 tracked, 0 accounted (awaiting check-ins), 34 missing
> **Mobility Escalations:** Mrs. Thompson (Room 204, Floor 2), Mrs. Davis (Room 104, Floor 1)
> **Critical On-Scene Roles:** Ms. Johnson (Floor Warden West F2), Mrs. Nguyen (Floor Warden East F2, holds elevator key), Tech Jordan (PA system + cameras)
>
> **Evacuation Routes:** Standard via West Stairwell to Door 1; Wheelchair via Elevator to Door 1 (key in Room 201)
> **Fire Extinguisher:** West hallway F2 near Room 215
> **Assembly:** Athletic Field (primary), Staff Parking (alt), First Baptist Church (off-site)
> **Fire Station:** Nashville Fire Station 9 (0.8mi, 3min ETA)
>
> **Prior Lessons Applied:**
> 1. Gas shutoff valve (Room 215 east wall) must be confirmed CLOSED
> 2. Stagger Room 215 evacuation 30s before Room 210 (stairwell bottleneck)
> 3. Retrieve duplicate elevator key from Room 201 for Mrs. Thompson

## Raw Transcript

See `docs/gemini_transcript.json` for the full machine-readable event log.

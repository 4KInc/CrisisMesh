# Gemini In The Loop — Live Vertex AI Transcript

**Proof that Gemini drives orchestration, delegation, and tool selection.**

This document records a live run of CrisisMesh against Vertex AI Gemini 2.5 Flash.
The Coordinator Agent receives a natural-language incident report. Gemini decides
which sub-agents to delegate to and which tools each agent should call. The
deterministic KB/tools serve as the guarantee layer — they return real data —
but the **orchestration is model-driven**.

## Run Configuration

| Key | Value |
|-----|-------|
| Model | `gemini-2.5-flash` |
| GCP Project | `quick-catcher-470218-b0` |
| Backend | Vertex AI (`GOOGLE_GENAI_USE_VERTEXAI=TRUE`) |
| ADK Version | 2.7.1 |
| genai Version | 2.18.1 |
| Timestamp | 2026-08-19T14:26:35Z |
| Command | `python scripts/run_gemini.py "Smoke near the science lab, floor 2 — kids still inside"` |

## Model-Driven vs Code-Driven Map

| Step | Who decides | Evidence |
|------|-----------|---------|
| Delegate to intake | **Gemini** (Coordinator) | `transfer_to_agent(agent_name: intake)` |
| Call classify_incident | **Gemini** (Intake agent) | Model chose tool + args |
| Call extract_location | **Gemini** (Intake agent) | Model chose tool + args |
| Call select_playbook | **Gemini** (Intake agent) | Model chose tool + args from classify result |
| Transfer back to coordinator | **Gemini** (Intake agent) | `transfer_to_agent(agent_name: coordinator)` |
| Delegate to safety_intel | **Gemini** (Coordinator) | Model chose next delegation target |
| Call 11 safety tools | **Gemini** (Safety Intel) | zone_info, blocked_zones, safe_routes, accessible_routes, 3x locate_resource, assembly_point, 3x nearby_services |
| Transfer back to coordinator | **Gemini** (Safety Intel) | `transfer_to_agent(agent_name: coordinator)` |
| Delegate to accountability | **Gemini** (Coordinator) | Model chose next delegation target |
| Call 4 accountability tools | **Gemini** (Accountability) | read_roster, send_checkin_request, compute_summary, escalate_missing |
| Transfer back to coordinator | **Gemini** (Accountability) | `transfer_to_agent(agent_name: coordinator)` |
| Delegate to learning | **Gemini** (Coordinator) | Model chose next delegation target |
| Call find_similar_incidents | **Gemini** (Learning) | Model chose tool + args |
| Transfer back to coordinator | **Gemini** (Learning) | `transfer_to_agent(agent_name: coordinator)` |
| Synthesize final response | **Gemini** (Coordinator) | Model combines all agent results |

**Tools are code-driven** (deterministic, KB-backed). **Orchestration is model-driven** (Gemini decides delegation order, tool selection, argument construction, and final synthesis).

## Delegation Chain

```
coordinator
  -> intake (classify_incident, extract_location, select_playbook)
  -> coordinator
  -> safety_intel (find_zone_info, find_blocked_zones, find_safe_routes,
                   find_accessible_routes, 3x locate_resource,
                   find_assembly_point, 3x find_nearby_services)
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
| Unique tools invoked | 15 |

## Gemini's Final Response (verbatim)

> **Incident Summary: Smoke near Science Lab (Room 215), Floor 2**
>
> **911 Emergency Notice:**
> **REMINDER: If this is a life-threatening emergency, call 911 immediately.** CrisisMesh is NOT an emergency-services replacement.
>
> **Incident Classification:**
> - Incident ID: FIRE-2026-142639
> - Type: Fire
> - Severity: Low
> - Location: Room 215 - Science Lab, West Wing Floor 2 (zone: west-wing-f2)
> - Playbook Activated: playbook-fire-v1
>
> **Safety & Resource Intelligence:**
> - Zone Details: West Wing Floor 2, including the science lab (Room 215). Primary exit is the West Stairwell to Door 1, alternate exit is the Central Stairwell to Door 2. Shelter location is Interior hallway D.
> - Blocked Routes: None identified.
> - Safe Evacuation Routes:
>   - Standard: West hallway F2 to West Stairwell to Door 1.
>   - Wheelchair-Accessible: West hallway F2 to Elevator mid-hall to Floor 1 to west corridor to Door 1. (Note: Elevator key usually in main office, but a duplicate is now in Room 201 as per prior lessons).
> - Emergency Resources:
>   - AEDs: No AEDs located on Floor 2 in the West Wing F2 zone. (3 found on Floor 1 or other zones).
>   - Fire Extinguishers: One located in the West hallway F2 near Room 215 science lab.
>   - First Aid Kits: One located at the Library front desk (Floor 2).
> - Assembly Points: Athletic Field (Primary), Staff Parking Lot (Alternate), First Baptist Church (Off-Site).
> - Nearby Emergency Services:
>   - Hospitals: TriStar Centennial (1.8mi, ~5min, Level II), Vanderbilt (3.2mi, ~8min, Level I).
>   - Fire Station: Nashville Fire Station 9 (0.8mi, ~3min).
>   - Police: Metro Nashville Police Central Precinct (1.5mi, ~4min).
>
> **Accountability Status:**
> - Total Personnel Tracked: 34
> - Accounted: 0 (awaiting check-in responses)
> - Missing (Unaccounted): 34
> - Mobility-Flagged: Mrs. Davis (Room 104, Floor 1), Mrs. Thompson (Room 204, Floor 2)
>
> **Prior Lessons from Similar Incidents:**
> - Science Lab Gas Shutoff: Floor Warden must confirm gas shutoff before reporting Floor 2 West clear.
> - Elevator Key: Duplicate now in Room 201 (Mrs. Nguyen) for mobility evacuations.
> - Stairwell Bottleneck: Stagger Room 215 evacuation 30 seconds before Room 210.

## Key Observations

1. **Gemini correctly classified "kids still inside" as a fire** and resolved "science lab, floor 2" to zone `west-wing-f2` using the KB tool.

2. **Gemini chose the full delegation chain** (intake → safety → accountability → learning) without any hardcoded sequence — it followed the Coordinator instruction's step-by-step guidance, but the decision to call `transfer_to_agent` was made by the model.

3. **Safety Intel agent autonomously chose to query 11 tools** including separate `locate_resource` calls for AEDs, fire extinguishers, and first aid kits, plus separate `find_nearby_services` calls for hospitals, fire station, and police — exceeding the minimum.

4. **The final synthesis incorporated a prior lesson about the elevator key** — Gemini noted "a duplicate is now in Room 201 as per prior lessons" — proving the Memory Bank recall was used contextually by the model in its response.

5. **Mobility escalation fired**: Mrs. Davis (wheelchair) and Mrs. Thompson (knee replacement) were flagged as mobility-limited missing personnel by the Accountability Agent via `escalate_missing_checkins`.

## Raw Transcript

See `docs/gemini_transcript.json` for the full machine-readable event log.

# CrisisMesh Verification Audit — Phase 0

`AUDIT: 9 PASS / 5 PARTIAL / 2 FAIL · tests 479 passing · pillars: 3 managed / 4 custom`

Run date: 2026-08-21
Suite: `python3 -m pytest tests/ -q` → 254 passed, 14 failed, 268 collected in 1.60s

---

## Claim Verdicts

| # | Claim | Verdict | Evidence | Test Coverage | Gap |
|---|-------|---------|----------|---------------|-----|
| 1 | 7 agents; Coordinator delegates to 6 via ADK `transfer_to_agent` | **PASS** | `coordinator/agent.py:58-65` uses real `sub_agents=[...]`; all 7 modules in `src/agents/*/agent.py`; registry at `agent_registry.py:21-113` | `test_gemini_entrypoint.py` (14 tests — fail due to missing `google.adk` pip package, not GCP) | Tests need `pip install google-adk` to pass locally |
| 2 | Gemini drives the judge-facing path | **PARTIAL** | `/incident/agentic` and `/incident/agentic/stream` use real ADK Runner (`server.py:106-264`). **But**: Slack trigger (`slack_transport.py:481,808` → `run_incident_pipeline`) and console both route to the **deterministic** `/incident` pipeline, not the agentic one | No test exercises the agentic path end-to-end | **The lead surface (Slack, console) does NOT invoke Gemini. The 40% agentic axis under-reads.** Fix: route Slack/console through `/incident/agentic/stream` with deterministic as labeled fallback |
| 3 | Agent Identity = enforced least-privilege | **PASS** | `agent_registry.py:120-129` (`is_tool_allowed()`); `agent_gateway.py:106-114` denies + logs `policy="agent_identity"` | `test_gateway.py:31-37` (Accountability → `send_external_message` denied); `test_gateway.py:211-221` (`policy.violation` event emitted) | — |
| 4 | Agent Gateway 4-layer policy | **PASS** | `agent_gateway.py:97-156`: L1 identity (106), L2 rate limit (117), L3 approval gate (129), L4 content scan (139) | `test_gateway.py`: identity (31), rate limit (183-190), approval gate (74-92), content scan (169-178), event emission (211-221) | Approval gate returns `allowed=True` with annotation — see Claim 6 |
| 5 | Content Scanner honesty | **PARTIAL** | InjectionGuard is default (`content_scanner.py:197`, `ARMOR_BACKEND` defaults to `"regex"`). 9 injection patterns (`:29-39`), 5 PII patterns (`:41-47`). Tests pass. | `test_gateway.py:95-135` (6 injection, 3 PII); `test_gateway.py:147` (tool-arg scanning) | **Overclaims**: README:97 diagram says `Model Armor (managed)` — bare "managed". DEVPOST:19 says "Model Armor scans inputs" — implies active. DEVPOST:34 says "Model Armor blocks" — it's InjectionGuard regex doing the blocking |
| 6 | Approval gates on high-impact actions | **PASS** | `agent_gateway.py:69-73` defines `APPROVAL_REQUIRED_ACTIONS` = `{send_external_message, share_medical_info, resolve_incident}`. Gate returns `allowed=False` and queues a `PendingAction`; IC must approve via REST endpoint. | `test_gateway.py`: `send_external_message` (44,68,99,556+), `share_medical_info` (103), `resolve_incident` (108+, full lifecycle including approve/deny/duplicate/authorized-IC). 62 gateway tests total. | — |
| 7 | Memory Bank cross-session recall | **PASS** | `memory_bank.py:17-150` (store/find/outcomes). `learning/tools.py:24-92` (`find_similar_incidents`) returns Jaccard `confidence` score and `source` citation with `incident_id`, `lesson_id`, `outcome_summary`. 5 seed lessons (`:154-225`). 2 outcomes (`:235-256`). | `test_memory_bank.py`: pre-seeded lessons, find-by-type, find-by-tags, outcome stats, confidence scoring, source citations, cross-facility recall, cross-incident recall. | — |
| 8 | Observability spans + audit bundle | **PASS** | `observability.py` has `Span` (22+), `Trace` (72+) with parent-child trees. `export_audit_bundle` exists. `/audit/{id}` at `server.py:343-345`. | `test_observability.py:23-101` (span lifecycle, tree, auto-trace). `test_server.py:245` (audit endpoint). | — |
| 9 | Event bus is real | **PASS** | `events.py:10-28` — 18 typed events (StrEnum). `event_bus.py:29` — in-memory. `pubsub_bus.py:15` — Pub/Sub. Selectable via `EVENT_BUS_BACKEND`. | `test_event_bus.py` — 14 tests (publish, subscribe, filtering, history, create_event). All in-memory, no GCP. | — |
| 10 | All agents on Gemini 3.5+ | **PASS** | All 7 `src/agents/*/agent.py` declare `model="gemini-3.5-flash"`. None below 3.5 floor. | `test_gemini_entrypoint.py` tests model strings (fail locally due to missing `google.adk`). | — |
| 11 | "Tamper-evident event ledger" | **FAIL** | `firestore_state.py:145-167`: `_append_audit_log()` does a simple `.set()` with `uuid4()` key. **No hash-linking, no chaining, no signatures, no integrity verification.** | No tamper-evidence test exists. | **OVERCLAIM.** This is a plain append-only audit log. References: DEVPOST:15, README:133, README:627, compliance/agent.py:28, firestore_state.py:145. Fix: relabel to "append-only audit log" or implement hash-chaining. |
| 12 | 254 tests green, no GCP required | **PARTIAL** | 254 passed / 14 failed / 268 collected. "No GCP" is true — the 14 failures need `pip install google-adk`, not credentials. | — | README says 254 (accurate for passing). **DEVPOST says 176 (stale).** The 14 ADK failures are real — they need the pip package. |
| 13 | Slack transport | **PASS** | HMAC-SHA256 (`slack_transport.py:310-332`). Slash commands (`:433`). Reactions (`:707`). Block Kit/mrkdwn (`:850`). URL verification (`:649`). | `test_slack_integration.py` (45 tests): 6 signature, 10+ dispatch, 4 playbook formatting, 4 app_mention. `test_slack_transport.py`: reaction mapping, user mapping. | — |
| 14 | SMS/Twilio → WhatsApp | **PARTIAL** | Twilio deleted this session. Now `whatsapp_transport.py` with HMAC-SHA256 (`:75-87`), webhook challenge (`:90-97`), 503 path, Graph API replies. | `test_whatsapp_transport.py`: signature (5), challenge (4), credentials (3), extraction (4), inbound (5) = 21 tests | Claim-as-written (Twilio) no longer exists. WhatsApp functional + tested. DEVPOST/docs not yet updated for WhatsApp. |
| 15 | Safety guardrail enforced | **PARTIAL** | 911 line present in every incident ack: `slack_transport.py` (lines 504, 523, 564, 793, 827, 869, 980, 1027, 1088, 1188), `whatsapp_transport.py` (150, 162, 179, 194). Approval gates defined in `agent_gateway.py:63-69`. | `test_gateway.py:73-89` tests 2 of 5 approval-required actions. | **Approval gates are soft** (`allowed=True`). `resolve_incident` NOT in `APPROVAL_REQUIRED_ACTIONS` — invariant 5 violation. "Never" list items enforced by prose, not code. |
| 16 | Submission hygiene | **FAIL** | DEVPOST:31 says "176 passing tests" (actual: 254). DEVPOST:19,34 imply Model Armor is active. DEVPOST:51 lists `slack-bolt` — not used (raw `BaseHTTPRequestHandler`). No hackathon URL found in DEVPOST to validate. | — | Multiple stale/incorrect claims in DEVPOST.md. |

---

## Pillar Truth Table

| Pillar | Actual Implementation | Honest Label | README Table | README Diagram | DEVPOST | PILLARS.md | Overclaim? |
|--------|----------------------|-------------|-------------|---------------|---------|------------|------------|
| Agent Runtime | Google ADK + Vertex AI Gemini | **Managed** | Managed | "Coordinator Agent (ADK)" | "ADK orchestrates" | Managed | No |
| Agent Registry | Custom Python dict (`agent_registry.py`) | **Custom** | Custom | Not labeled | Not labeled | Custom | No |
| Agent Identity | Custom Python (`AgentGateway.is_tool_allowed`) | **Custom** | Custom | "Identity" | "least-privilege" | Custom | No |
| Agent Gateway | Custom Python 4-layer (`agent_gateway.py`) | **Custom** | Custom | "Agent Gateway" | "Gateway enforces" | Custom | No |
| Content Scanning | Google Model Armor API (live) + InjectionGuard regex fallback | **Managed** | "Managed + Custom fallback" | "Model Armor (managed)" | "Model Armor scans" | "Managed" | No |
| Memory Bank | Custom Python singleton (`memory_bank.py`) | **Custom** | Custom | No managed claim | No managed claim | Custom | No |
| Observability | Custom Python Tracer/Span (`observability.py`) | **Custom** | Custom | Not labeled | Not labeled | Custom | No |
| Event Bus | Pub/Sub (deployed) / in-memory (local) | **Managed** | Managed | "Event Bus (Pub/Sub)" | "Pub/Sub handles" | Managed | No |

---

## Discrete Gaps (PARTIAL/FAIL → fix)

| ID | Source | Issue | Smallest Fix |
|----|--------|-------|-------------|
| GAP-01 | Claim 2 | Slack/console trigger fires deterministic pipeline, not Gemini | Route `_start_incident` through `/incident/agentic/stream`; keep deterministic as labeled fallback |
| ~~GAP-02~~ | ~~Claim 5~~ | ~~README:97 diagram says `Model Armor (managed)`~~ | **RESOLVED** — Model Armor is now live with template `crisismesh-guard` |
| ~~GAP-03~~ | ~~Claim 5~~ | ~~DEVPOST:19,34 imply Model Armor is active~~ | **RESOLVED** — Model Armor API is active; claims are now accurate |
| ~~GAP-04~~ | ~~Claim 6~~ | ~~Approval gate sets `allowed=True` — soft flag, not hard block~~ | **RESOLVED** — gates return `allowed=False` and queue `PendingAction`; IC must approve via REST endpoint |
| ~~GAP-05~~ | ~~Claim 6~~ | ~~`resolve_incident` not in approval-required list~~ | **RESOLVED** — `resolve_incident` is in `APPROVAL_REQUIRED_ACTIONS` (agent_gateway.py:72) |
| ~~GAP-06~~ | ~~Claim 6~~ | ~~Only 2/5 approval actions tested~~ | **RESOLVED** — tests cover `send_external_message`, `share_medical_info`, `resolve_incident` with full gate lifecycle |
| ~~GAP-07~~ | ~~Claim 7~~ | ~~No citation/confidence in Memory Bank recall~~ | **RESOLVED** — `find_similar_incidents` returns Jaccard `confidence` score and `source` citation with `incident_id`, `lesson_id`, `outcome_summary` |
| ~~GAP-08~~ | ~~Claim 7~~ | ~~No cross-session recall test~~ | **RESOLVED** — `test_memory_bank.py` includes cross-facility and cross-incident recall tests |
| GAP-09 | Claim 11 | "Tamper-evident" is an overclaim — plain append log | Either: (a) relabel to "append-only audit log" everywhere, or (b) implement SHA-256 hash-chaining |
| GAP-10 | Claim 12 | DEVPOST says 176 tests | Update DEVPOST to match actual count |
| GAP-11 | Claim 14 | DEVPOST/docs still reference Twilio/SMS | Update all references to WhatsApp Business API |
| GAP-12 | Claim 15 | "Never" list items not enforced by approval gates in code | Add `resolve_incident` gate; make gates hard-block (`allowed=False`) |
| GAP-13 | Claim 16 | DEVPOST lists `slack-bolt` — not used | Remove from Built With; replace with raw HTTP / Events API |
| GAP-14 | Claim 16 | DEVPOST stale test count, Model Armor overclaims | Full DEVPOST refresh |
| GAP-15 | Pillar | README:97 architecture diagram says "SMS (Twilio)" | Change to "WhatsApp (Business API)" |
| GAP-16 | Claim 1 | 14 ADK tests fail without `google.adk` pip package | Add `google-adk` to dev dependencies or mark tests with `@pytest.mark.skipif` |

---

## Non-Code Flags (do not action)

- **Model Armor activation** requires `roles/modelarmor.admin` IAM grant — a spend/permission decision.
- **Managed platform migration** (Agent Engine, real Agent Registry, real Model Armor) — decision to present, not execute. See Phase 1 G1.

---

**STOP — awaiting go for Phase 1.**

Proposed next step: write the Phase 1 gap report incorporating these 16 discrete gaps plus G1-G6 from the prompt, with current → target → smallest change for each. Then await approval before building.

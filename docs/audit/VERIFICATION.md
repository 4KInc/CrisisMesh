# CrisisMesh Verification Audit — Phase 0

`AUDIT: 14 PASS / 2 PARTIAL / 0 FAIL · tests 495 passing · pillars: 3 managed / 4 custom`

Run date: 2026-08-23 (updated)
Suite: `python3 -m pytest tests/ -q` → 495 passed, 0 failed

---

## Claim Verdicts

| # | Claim | Verdict | Evidence | Test Coverage | Gap |
|---|-------|---------|----------|---------------|-----|
| 1 | 7 agents; Coordinator delegates to 6 via ADK `transfer_to_agent` | **PASS** | `coordinator/agent.py:58-65` uses real `sub_agents=[...]`; all 7 modules in `src/agents/*/agent.py`; registry at `agent_registry.py:21-113` | `test_gemini_entrypoint.py` (28 tests, all passing with `google-adk` installed) | — |
| 2 | Gemini drives the judge-facing path | **PASS** | `/incident/agentic` and `/incident/agentic/stream` use real ADK Runner (`server.py`). Slack `/incident` fires both deterministic fast-ack AND Gemini fleet in background (`slack_transport.py:345-349` → `_run_agentic_and_post`). Demo `--live` flag streams agentic endpoint. | — | — |
| 3 | Agent Identity = enforced least-privilege | **PASS** | `agent_registry.py:120-129` (`is_tool_allowed()`); `agent_gateway.py` denies + logs `policy="agent_identity"` | `test_gateway.py:31-37` (deny + event emission) | — |
| 4 | Agent Gateway 4-layer policy | **PASS** | `agent_gateway.py`: L1 identity, L2 rate limit, L3 approval gate (hard block), L4 content scan (Model Armor) | `test_gateway.py`: 62 tests covering all 4 layers | — |
| 5 | Content Scanner — Model Armor live | **PASS** | `ARMOR_BACKEND=model_armor` deployed default. Template `crisismesh-guard` live in `us-central1`. IAM `roles/modelarmor.user` on service account. Health endpoint confirms `scanner_backend: model_armor`. | `test_gateway.py:95-135` (injection, PII, tool-arg scanning) | — |
| 6 | Approval gates on high-impact actions | **PASS** | `APPROVAL_REQUIRED_ACTIONS` = `{send_external_message, share_medical_info, resolve_incident}`. Gate returns `allowed=False` and queues `PendingAction`; IC must approve via REST endpoint. | `test_gateway.py`: full lifecycle for all 3 gated actions, approve/deny/duplicate/authorized-IC. 62 gateway tests. | — |
| 7 | Memory Bank cross-session recall | **PASS** | `find_similar_incidents` returns Jaccard `confidence` score and `source` citation with `incident_id`, `lesson_id`, `outcome_summary`. 5 seed lessons, 2 outcomes. | `test_memory_bank.py`: confidence scoring, source citations, cross-facility recall, cross-incident recall. | — |
| 8 | Observability spans + audit bundle | **PASS** | `observability.py` has `Span`, `Trace` with parent-child trees. `export_audit_bundle` exists. `/audit/{id}` endpoint. | `test_observability.py` (span lifecycle, tree, auto-trace). `test_server.py` (audit endpoint). | — |
| 9 | Event bus is real | **PASS** | 18 typed events (StrEnum). In-memory + Pub/Sub. Selectable via `EVENT_BUS_BACKEND`. | `test_event_bus.py` — 14 tests. | — |
| 10 | All agents on Gemini 3.5+ | **PASS** | All 7 `src/agents/*/agent.py` declare `model="gemini-3.5-flash"`. | `test_gemini_entrypoint.py` verifies model strings. | — |
| 11 | Append-only audit log | **PASS** | `firestore_state.py`: `_append_audit_log()` appends with `uuid4()` key. Labeled "append-only" throughout README, DEVPOST, PILLARS — no "tamper-evident" overclaim remains. | — | — |
| 12 | 495 tests green, no GCP required | **PASS** | 495 passed / 0 failed. `google-adk` is a dev dependency. No GCP credentials needed. | — | — |
| 13 | Slack transport | **PASS** | HMAC-SHA256. Slash commands. Reactions. Block Kit/mrkdwn. URL verification. Threaded replies. CSV upload. Check-in board. | `test_slack_integration.py` (45+ tests), `test_slack_transport.py` | — |
| 14 | SMS transport (Twilio) + WhatsApp | **PARTIAL** | SMS via Twilio webhooks with TwiML, signature verification, consent flow. WhatsApp via Business API with HMAC-SHA256, Graph API replies. Both functional and tested. | `test_sms_transport.py`, `test_sms_consent.py`, `test_whatsapp_transport.py` | WhatsApp is deterministic-only (no agentic fleet) |
| 15 | Safety guardrail enforced | **PASS** | 911 line in every incident ack across all channels. Approval gates hard-block 3 actions. `resolve_incident` gated. | `test_gateway.py` covers all gated actions. | — |
| 16 | Submission hygiene | **PARTIAL** | DEVPOST refreshed: 495 tests, Model Armor accurate, `slack-bolt` removed from Built With. Demo fire drill has `--live` mode. | — | Video not yet submitted |

---

## Pillar Truth Table

| Pillar | Actual Implementation | Honest Label | Overclaim? |
|--------|----------------------|-------------|------------|
| Agent Runtime | Google ADK 2.7.1 + Vertex AI Gemini 3.5 Flash | **Managed** | No |
| Agent Registry | Custom Python dict (`agent_registry.py`) | **Custom** | No |
| Agent Identity | Custom Python (`AgentGateway.is_tool_allowed`) | **Custom** | No |
| Agent Gateway | Custom Python 4-layer (`agent_gateway.py`) | **Custom** | No |
| Content Scanning | Google Model Armor API (live) + InjectionGuard regex fallback | **Managed** | No |
| Memory Bank | Custom Python singleton (`memory_bank.py`) with Jaccard confidence + citations | **Custom** | No |
| Observability | Custom Python Tracer/Span (`observability.py`) | **Custom** | No |
| Event Bus | Pub/Sub (deployed) / in-memory (local) | **Managed** | No |

---

## Discrete Gaps — All Resolved

| ID | Source | Issue | Resolution |
|----|--------|-------|------------|
| ~~GAP-01~~ | Claim 2 | Slack trigger fires deterministic, not Gemini | **RESOLVED** — Slack fires both: deterministic fast-ack + `_run_agentic_and_post` in background thread (slack_transport.py:345-349). Demo `--live` mode streams agentic endpoint. |
| ~~GAP-02~~ | Claim 5 | Model Armor not live | **RESOLVED** — Template `crisismesh-guard` created, IAM granted, deployed as default |
| ~~GAP-03~~ | Claim 5 | DEVPOST implies Model Armor active | **RESOLVED** — Model Armor IS active; claims now accurate |
| ~~GAP-04~~ | Claim 6 | Approval gate soft flag | **RESOLVED** — gates return `allowed=False`, queue `PendingAction` |
| ~~GAP-05~~ | Claim 6 | `resolve_incident` ungated | **RESOLVED** — in `APPROVAL_REQUIRED_ACTIONS` |
| ~~GAP-06~~ | Claim 6 | Only 2/5 approval actions tested | **RESOLVED** — all 3 gated actions tested with full lifecycle |
| ~~GAP-07~~ | Claim 7 | No confidence/citation | **RESOLVED** — Jaccard `confidence` + `source` citation |
| ~~GAP-08~~ | Claim 7 | No cross-session test | **RESOLVED** — cross-facility and cross-incident tests |
| ~~GAP-09~~ | Claim 11 | "Tamper-evident" overclaim | **RESOLVED** — relabeled to "append-only audit log" everywhere |
| ~~GAP-10~~ | Claim 12 | Stale test count | **RESOLVED** — 495 everywhere |
| ~~GAP-11~~ | Claim 14 | SMS/Twilio references | **NOT A GAP** — CrisisMesh uses Slack + SMS + WhatsApp (all three channels); Twilio references are accurate |
| ~~GAP-12~~ | Claim 15 | "Never" list unenforced | **RESOLVED** — `resolve_incident` gated, hard-block confirmed |
| ~~GAP-13~~ | Claim 16 | `slack-bolt` in Built With | **RESOLVED** — removed, replaced with `slack-events-api` |
| ~~GAP-14~~ | Claim 16 | DEVPOST stale | **RESOLVED** — full DEVPOST refresh |
| ~~GAP-15~~ | Pillar | Diagram says "SMS (Twilio)" | **NOT A GAP** — SMS uses Twilio, diagram is accurate |
| ~~GAP-16~~ | Claim 1 | ADK tests fail | **RESOLVED** — `google-adk` in dev dependencies, 28 tests pass |

---

## Non-Code Flags

- **Model Armor** — LIVE. Template `crisismesh-guard`, IAM `roles/modelarmor.user` on service account.
- **Video** — not yet submitted. Demo `--live` mode ready.

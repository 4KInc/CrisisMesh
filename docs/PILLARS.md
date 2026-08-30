# Fortified Enterprise Fleet — Pillar Implementation Status

Per-pillar disclosure: what is a real Google managed product vs a custom implementation, and why.

| Pillar | Implementation | Managed Product | Status | Notes |
|--------|---------------|----------------|--------|-------|
| **Agent Registry** | Custom `AgentRegistryEntry` catalog in `src/config/agent_registry.py` | Google Agent Registry (Gemini Enterprise Agent Platform) | **Custom** | Agent Registry is part of the Gemini Enterprise Agent Platform governance suite. IAM permissions for provisioning were not available in this project during the build window. The custom registry catalogs all 7 agents with version, owner, data_class, approved/denied tools. |
| **Agent Runtime** | Google ADK `Runner` + `Agent` with `sub_agents` delegation | Google ADK 2.7.1 on Vertex AI | **Managed** | Coordinator + 6 specialist agents run on gemini-3.5-flash via Vertex AI. ADK handles session management, agent transfer, tool dispatch, and model invocation. |
| **Memory Bank** | `MemoryBank` facade with two backends, selected by `MEMORY_BACKEND` | Vertex AI Agent Engine Memory Bank (`reasoningEngines/*/memories`) | **Managed** | An Agent Engine instance backs the store: `crisismesh-memory-bank`, `projects/1031148889398/locations/us-central1/reasoningEngines/7390518588945203200`. A lesson is one `Memory` — `fact` carries the sentence, `description` the structured record so the citation survives the round trip, `scope` partitions CrisisMesh's memories. Retrieval is `RetrieveMemories` similarity search rather than tag matching, so recall crosses processes because the store is outside all of them. The local Jaccard store remains as the offline/test backend (`MEMORY_BACKEND=local`, the default) and as the fallback when the managed path is unavailable — an empty result would read as "no prior lessons", which a backend that is down has not established. The two backends do not compute the same number, so every result carries `confidence_basis`: `vector_similarity` or `jaccard_tag_overlap`. |
| **Agent Identity** | Custom least-privilege enforcement in `AgentGateway` using `AgentRegistryEntry.approved_tools` / `denied_tools` | Google Agent Identity (Gemini Enterprise Agent Platform) | **Custom** | Agent Identity is part of the governance suite not provisionable in this project. The custom implementation enforces the same principle: each agent has a scoped tool allowlist, denied tool calls are logged as `policy.violation` events, and a deny log is available for audit. |
| **Agent Gateway** | Custom `AgentGateway` in `src/core/agent_gateway.py` with 4 policy layers | Google Agent Gateway (Gemini Enterprise Agent Platform) | **Custom** | Same IAM constraint as Registry/Identity. Custom gateway enforces: (1) agent identity, (2) rate limiting, (3) approval gates for high-impact actions, (4) content scanning. All decisions logged to event bus. |
| **Content Scanning** | `ContentScanner` facade with two backends, selected by `ARMOR_BACKEND` env var | Google Cloud Model Armor (`modelarmor.googleapis.com`) | **Managed** | Model Armor API is enabled with template `crisismesh-guard` in `us-central1`. The deployed Cloud Run service uses `ARMOR_BACKEND=model_armor` as default. Template filters: prompt injection & jailbreak (`LOW_AND_ABOVE`), malicious URI, RAI (hate speech, harassment, dangerous, sexually explicit at `MEDIUM_AND_ABOVE`). The `InjectionGuard` regex scanner (9 injection + 5 PII patterns) serves as offline/local-dev fallback (`ARMOR_BACKEND=regex`). |
| **Event Bus** | `EventBus` with two backends, selected by `EVENT_BUS_BACKEND` env var | Google Cloud Pub/Sub | **Managed** | Real Pub/Sub is the deployed default (`EVENT_BUS_BACKEND=pubsub`). 4 topics + subscriptions created: `crisismesh-incidents`, `crisismesh-checkins`, `crisismesh-tasks`, `crisismesh-events`. Event round-trip proven: publish → pull → acknowledge. In-memory bus remains as local cache and offline/test fallback (`EVENT_BUS_BACKEND=memory`). |
| **Observability** | Custom `Tracer` / `Span` / `Trace` in `src/core/observability.py` | Google Cloud Observability / OpenTelemetry | **Custom** | ADK 2.7.1 natively emits OpenTelemetry spans for every model call and tool invocation. The custom tracer provides application-level incident traces (span trees, audit bundles) without requiring an OTel Collector setup. In production, the ADK OTel spans and the custom traces would both feed into Cloud Trace. |

## Configuration Flags

| Env Var | Values | Default | Effect |
|---------|--------|---------|--------|
| `EVENT_BUS_BACKEND` | `memory`, `pubsub` | `memory` | Selects event transport. `pubsub` publishes to real Google Cloud Pub/Sub. |
| `ARMOR_BACKEND` | `regex`, `model_armor` | `model_armor` | Selects content scanner. `model_armor` calls the real Google Cloud Model Armor API. `regex` for offline/local dev. |
| `ARMOR_TEMPLATE` | template ID | `crisismesh-guard` | Model Armor template name (only used when `ARMOR_BACKEND=model_armor`). |
| `GOOGLE_CLOUD_PROJECT` | project ID | — | Required for all managed backends. |
| `GOOGLE_CLOUD_REGION` | region | `us-central1` | Region for Vertex AI and Model Armor. |
| `GOOGLE_GENAI_USE_VERTEXAI` | `TRUE` | — | Required for ADK to use Vertex AI. |

## What's Managed vs Custom — Summary

- **Fully managed:** Agent Runtime (ADK + Vertex AI Gemini 3.5), Event Bus (Pub/Sub), Content Scanning (Model Armor API), Memory Bank (Vertex AI Agent Engine)
- **Custom with honest disclosure:** Agent Registry, Agent Identity, Agent Gateway, Observability
- **Reason for custom:** The Gemini Enterprise Agent Platform governance products (Registry, Identity, Gateway) require IAM roles that could not be granted in this project during the build window. Observability supplements ADK's native OTel with application-level incident traces.

## Model Armor Configuration

The deployed template `crisismesh-guard` was created with:

```bash
# Template: prompt injection + jailbreak + malicious URI + RAI
curl -X POST \
  "https://modelarmor.us-central1.rep.googleapis.com/v1/projects/$PROJECT_ID/locations/us-central1/templates?template_id=crisismesh-guard" \
  -H "Authorization: Bearer $(gcloud auth print-access-token)" \
  -H "Content-Type: application/json" \
  -d '{"filterConfig":{"piAndJailbreakFilterSettings":{"filterEnforcement":"ENABLED","confidenceLevel":"LOW_AND_ABOVE"},"maliciousUriFilterSettings":{"filterEnforcement":"ENABLED"},"raiSettings":{"raiFilters":[{"filterType":"SEXUALLY_EXPLICIT","confidenceLevel":"MEDIUM_AND_ABOVE"},{"filterType":"HATE_SPEECH","confidenceLevel":"MEDIUM_AND_ABOVE"},{"filterType":"HARASSMENT","confidenceLevel":"MEDIUM_AND_ABOVE"},{"filterType":"DANGEROUS","confidenceLevel":"MEDIUM_AND_ABOVE"}]}}}'

# Cloud Run env vars (already set)
ARMOR_BACKEND=model_armor
ARMOR_TEMPLATE=crisismesh-guard
```

## Memory Bank — Managed Setup

The Agent Engine instance backing the Memory Bank was created with an explicit
`MemoryBankConfig`:

```python
from google.cloud import aiplatform_v1beta1 as v1beta1

client = v1beta1.ReasoningEngineServiceClient(
    client_options={"api_endpoint": "us-central1-aiplatform.googleapis.com"})
client.create_reasoning_engine(
    parent="projects/quick-catcher-470218-b0/locations/us-central1",
    reasoning_engine=v1beta1.ReasoningEngine(
        display_name="crisismesh-memory-bank",
        context_spec=v1beta1.ReasoningEngineContextSpec(
            memory_bank_config=v1beta1.ReasoningEngineContextSpec.MemoryBankConfig()),
    ),
).result()
```

### One outstanding grant

Writing a memory makes the Agent Engine embed the text, and its service agent
carries only `roles/aiplatform.reasoningEngineServiceAgent`, which does not
include `aiplatform.endpoints.predict`. Until this is granted, `create_memory`
returns:

```
403 Permission denied to projects/…/publishers/google/models/text-embedding-005.
Please ensure the Reasoning Engine service account has aiplatform.endpoints.predict permission.
```

The facade treats that as a backend outage and falls back to the local store, so
the feature keeps working — but the managed path is not exercised until:

```bash
gcloud projects add-iam-policy-binding quick-catcher-470218-b0 \
  --member="serviceAccount:service-1031148889398@gcp-sa-aiplatform-re.iam.gserviceaccount.com" \
  --role="roles/aiplatform.user"
```

Then, to prove cross-process recall against the real API rather than a mock:

```bash
MEMORY_BACKEND=vertex \
VERTEX_MEMORY_ENGINE=projects/1031148889398/locations/us-central1/reasoningEngines/7390518588945203200 \
python scripts/verify_memory_bank.py
```

The script writes a lesson tagged `mobility, elevator, evacuation` and then
queries *"someone who cannot use stairs is stuck on an upper floor during a
fire"* — deliberately sharing no tag vocabulary with it, so a tag-overlap store
scores it zero and a hit can only come from the managed semantic search.

**Status: verified live.** The grant was made and `scripts/verify_memory_bank.py`
was run against the real service:

```
  wrote lesson 4eccae83-7354-4007-924d-7b2ac18c867a to the managed store
  recalled  : 'Pre-stage the Floor 2 elevator key'
  incident  : FIRE-2025-011
  confidence: 0.156 (basis: vector_similarity)
  Cross-session recall through the managed path: verified.
```

### Two things the live run corrected

**Vertex Memory Bank persists `fact` and `scope` and nothing else.** `display_name`
and `description` come back empty, so the first implementation — which put the
structured record in `description` — retrieved memories it could not read. The
record now rides at the end of the `fact` behind a marker, after the sentence, so
the text the embedding is built from still leads with what the lesson says.

**Scope matching is exact on the whole map, not a subset.** A memory stored with
`{app, incident_id, facility_id}` is invisible to a query for `{app}` — verified
by storing one of each and querying both ways. Metadata in scope would therefore
make every lesson retrievable only by someone who already knew its incident id,
which is the opposite of recall. Scope is one fixed key.

### What the confidence number is, and is not

Measured against the live API: the closest match to a well-aimed query returns
distance **0.8345**, an unrelated lesson **1.0489**. Rendered as `1 - distance`,
a correct top hit reads **0.166** and a miss goes negative.

The ordering is real; the magnitude is not comparable to a Jaccard overlap. So
each result carries `retrieval_distance` (raw, from the API) alongside
`retrieval_confidence`, and `find_similar_incidents` returns a `confidence_note`
saying so. A reader comparing 0.166 against a Jaccard 0.75 would otherwise
conclude the managed store was less sure, when the two are not on one scale.

## Horizontal Scale

`--max-instances=1` was load-bearing until the per-incident stores moved off
process memory. What changed:

| Store | Was | Now | Write shape |
|---|---|---|---|
| Incident state | Firestore | Firestore | single document |
| Reconciliation state machine | Firestore (CAS) | Firestore (CAS) | compare-and-set, version checked |
| Witness log / threat trail | in-process dict | Firestore | append-only, one doc per sighting |
| Room board | in-process dict | Firestore | one doc per room, last writer wins |
| Check-in ledger | in-process dict | Firestore | one doc per person, last writer wins |
| WhatsApp session window | in-process dict | Firestore | one doc per handset |
| Tick guard | in-process dict | Firestore lease | create-if-absent |

None of the new stores uses compare-and-set. Observations are append-only, a
room report replaces that room's entry, and a session window is one timestamp —
there is no state machine to serialise, and adding CAS would be machinery for
the look of it. The reconciliation state machine keeps its CAS because it has
ordering constraints the others do not.

The tick lease is the piece that makes more than one instance safe: a scheduler
runs in every container, and without a lease outside the process each one runs
its own tick N.

Verified against real Firestore with `scripts/verify_durable_stores.py`. Two
things the live run corrected that a mock had not: `where(...) order_by(...)`
requires a composite index, so ordering is done in process to keep the setup
reproducible without provisioning one; and the test double had no `create()`,
so the lease primitive was silently falling into its own except-path and
reporting success to every caller.

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

**Status: the managed backend is implemented and unit-tested against the real
API shape; the live round trip is pending that one IAM grant.** This line will
be updated when the script has been run, and not before.

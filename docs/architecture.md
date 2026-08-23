# CrisisMesh Architecture

## Agentic Loop

```mermaid
flowchart LR
    Report(["Report"]) --> Coord["Coordinator<br/>Gemini 3.5 Flash"]
    Coord -->|delegate| Scan["Gateway"]
    Scan -->|allowed| Agent["Specialist"]
    Agent -->|result| Coord
    Coord -.->|hold| IC{{"IC"}}
    IC -.->|approve/deny| Coord
    Coord -->|learn| MB[(Memory Bank)]
    MB -.->|recall| Coord

    linkStyle 3 stroke:#ff6b35,stroke-width:2px
    linkStyle 7 stroke:#ff6b35,stroke-width:2px
```

## Full System

```mermaid
flowchart LR
    %% Edges verified against src/ — see the Edge provenance table below

    subgraph Transport["Transport"]
        direction TB
        Slack["Slack<br/>Events API · Block Kit"]
        SMS["SMS · Twilio"]
        WhatsApp["WhatsApp · Business API"]
        Console["Web Console<br/>Tailwind · SSE"]
    end

    subgraph Server["Cloud Run"]
        HTTP["HTTP Server"]
    end

    subgraph Governance["Governance"]
        direction TB
        Scanner["Content Scanner<br/>Model Armor · Regex"]
        Gateway["Agent Gateway<br/>GatewayPlugin"]
    end

    IC{{"Incident<br/>Commander"}}

    subgraph Orchestration["Vertex AI · ADK 2.7.1 · Gemini 3.5 Flash"]
        Coordinator["Coordinator"]
        subgraph Specialists["Specialist Agents"]
            direction TB
            Intake["Intake"]
            Accountability["Accountability"]
            SafetyIntel["Safety Intel"]
            SITREP["SITREP"]
            Learning["Learning"]
            Compliance["Compliance"]
        end
    end

    subgraph Data["Data"]
        direction TB
        Firestore["Firestore<br/>State · Audit Log"]
        PubSub["Pub/Sub<br/>Event Bus"]
        KB["Knowledge Base<br/>8 CSV Types"]
        MB["Memory Bank<br/>Cross-session"]
    end

    Transport -->|report| HTTP
    HTTP -->|scan| Scanner
    Scanner -->|clean| Coordinator
    Scanner -.->|block| HTTP
    Coordinator -->|delegate| Specialists
    Specialists -->|result| Coordinator
    Specialists -->|scan| Gateway
    Gateway -->|allowed| Specialists
    Gateway -.->|hold| IC
    IC -.->|approve/deny| HTTP
    Specialists --> Data
    Learning -->|learn| MB
    MB -.->|recall| Learning
    Coordinator -->|respond| HTTP
    HTTP -->|stream| Transport

    linkStyle 5 stroke:#ff6b35,stroke-width:2px
    linkStyle 7 stroke:#ff6b35,stroke-width:2px
    linkStyle 12 stroke:#ff6b35,stroke-width:2px
```

### Edge Provenance

| Edge | Meaning | Source |
|------|---------|--------|
| Transport → HTTP | Incident report enters via Slack, SMS, WhatsApp, or console | `server.py:do_POST` |
| HTTP → Scanner | Raw report scanned at ingress before ADK Runner | `content_scanner.py:ContentScanner.scan_message` |
| Scanner → Coordinator | Clean report enters the ADK Runner | `server.py:_run_agentic` |
| Scanner -.-> HTTP | Blocked report returns 403 | `server.py:do_POST` (blocked branch) |
| Coordinator → Specialists | Coordinator delegates via ADK transfer | `coordinator/agent.py:sub_agents` + `transfer_to_agent` instruction |
| Specialists → Coordinator | Specialist returns result; Coordinator decides next step | ADK Runner loop (implicit return after transfer) |
| Specialists → Gateway | Every tool call intercepted by plugin | `agent_gateway.py:GatewayPlugin.before_tool_callback` |
| Gateway → Specialists | Tool allowed; specialist continues | `agent_gateway.py:GatewayPlugin.before_tool_callback` (returns None) |
| Gateway -.-> IC | Gated action queued as PendingAction | `agent_gateway.py:AgentGateway.check_tool_call` (approval\_gate branch) |
| IC -.-> HTTP | IC approves or denies via REST | `server.py:do_POST /incident/{id}/approve`, `/deny` |
| Specialists → Data | Agents read KB, write Firestore, emit to Pub/Sub | `agents/*/tools.py` (various) |
| Learning → MB | store\_lesson writes at resolve | `learning/tools.py:store_lesson` |
| MB -.-> Learning | find\_similar\_incidents reads at next incident | `learning/tools.py:find_similar_incidents` |
| Coordinator → HTTP | Final response with safety post-processing | `server.py:_run_agentic` → `tactical_reasoning.py:apply_safety_backstop`, `validate_routing_directives` |
| HTTP → Transport | SSE stream, SITREP, Block Kit posted back | `server.py:_stream_agentic_sse`, `slack_transport.py:_run_agentic_and_post` |

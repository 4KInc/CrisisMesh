"""Agent Registry — catalog of all CrisisMesh agents with metadata and scopes."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class AgentRegistryEntry:
    agent_id: str
    name: str
    version: str
    description: str
    owner: str
    data_class: str  # public, internal, sensitive, restricted
    approved_tools: list[str] = field(default_factory=list)
    denied_tools: list[str] = field(default_factory=list)
    purpose: str = ""


AGENT_REGISTRY: dict[str, AgentRegistryEntry] = {
    "coordinator": AgentRegistryEntry(
        agent_id="coordinator",
        name="Coordinator Agent",
        version="0.1.0",
        description="Owns incident state machine; delegates to specialists; enforces human-approval gates",
        owner="crisismesh",
        data_class="internal",
        approved_tools=[
            "create_incident", "update_incident", "delegate_task",
            "monitor_deadlines", "request_approval", "resolve_incident",
        ],
        purpose="Incident command orchestration",
    ),
    "intake": AgentRegistryEntry(
        agent_id="intake",
        name="Intake & Classification Agent",
        version="0.1.0",
        description="Normalizes incident reports; classifies type and severity; selects approved playbook",
        owner="crisismesh",
        data_class="internal",
        approved_tools=["classify_incident", "select_playbook", "extract_location"],
        purpose="Incident intake and classification",
    ),
    "accountability": AgentRegistryEntry(
        agent_id="accountability",
        name="Accountability Agent",
        version="0.1.0",
        description="Tracks people, rooms, check-in status; escalates missing check-ins",
        owner="crisismesh",
        data_class="sensitive",
        approved_tools=[
            "read_roster", "process_checkin", "compute_accountability",
            "send_checkin_request", "escalate_missing",
        ],
        denied_tools=["send_external_message", "share_medical_info"],
        purpose="Personnel accountability tracking",
    ),
    "safety_intel": AgentRegistryEntry(
        agent_id="safety_intel",
        name="Safety & Resource Intelligence Agent",
        version="0.1.0",
        description="Answers location-specific operational questions from the knowledge base",
        owner="crisismesh",
        data_class="internal",
        approved_tools=[
            "find_safe_routes", "find_blocked_zones", "locate_resource",
            "find_assembly_point", "find_hazmat", "find_utility_shutoff",
        ],
        denied_tools=["send_external_message", "modify_playbook"],
        purpose="Safety and resource intelligence",
    ),
    "sitrep": AgentRegistryEntry(
        agent_id="sitrep",
        name="SITREP & Handoff Agent",
        version="0.1.0",
        description="Synthesizes live events into role-specific briefs",
        owner="crisismesh",
        data_class="internal",
        approved_tools=[
            "generate_sitrep", "generate_responder_card",
            "generate_stakeholder_update", "generate_timeline",
        ],
        purpose="Situation reports and handoff briefs",
    ),
    "learning": AgentRegistryEntry(
        agent_id="learning",
        name="Learning & After-Action Agent",
        version="0.1.0",
        description="Maintains incident history; extracts lessons; compares outcomes across events",
        owner="crisismesh",
        data_class="internal",
        approved_tools=[
            "find_similar_incidents", "produce_aar", "store_lesson",
            "propose_playbook_change",
        ],
        purpose="Institutional learning and after-action review",
    ),
    "compliance": AgentRegistryEntry(
        agent_id="compliance",
        name="Compliance & Audit Agent",
        version="0.1.0",
        description="Immutable-style audit records; policy-boundary checks; redaction",
        owner="crisismesh",
        data_class="restricted",
        approved_tools=[
            "append_audit_log", "validate_approval", "redact_sensitive",
            "export_trace_bundle", "check_policy",
        ],
        purpose="Compliance, audit, and policy enforcement",
    ),
}


def get_agent_entry(agent_id: str) -> AgentRegistryEntry | None:
    return AGENT_REGISTRY.get(agent_id)


def is_tool_allowed(agent_id: str, tool_name: str) -> bool:
    """Check if a tool is allowed for an agent (Agent Identity / least-privilege)."""
    entry = AGENT_REGISTRY.get(agent_id)
    if entry is None:
        return False
    if tool_name in entry.denied_tools:
        return False
    if entry.approved_tools and tool_name not in entry.approved_tools:
        return False
    return True

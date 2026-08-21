"""Tests for the Gemini-driven entrypoint — validates agent structure and tool wiring.

These tests validate that the ADK agent hierarchy is correctly configured
so that when Gemini drives the orchestration, it has the right sub-agents
and tools available. They do NOT call Vertex AI.
"""

import os

import pytest

from src.core.knowledge_base import KnowledgeBase, init_knowledge_base
from src.core.memory_bank import MemoryBank, init_memory_bank

SEED_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "seed",
)


@pytest.fixture(autouse=True)
def fresh_state():
    KnowledgeBase.reset()
    MemoryBank.reset()
    init_knowledge_base(SEED_DIR)
    init_memory_bank()
    yield
    KnowledgeBase.reset()
    MemoryBank.reset()


class TestCoordinatorAgentStructure:
    def test_coordinator_has_six_sub_agents(self):
        from src.agents.coordinator.agent import coordinator_agent

        assert len(coordinator_agent.sub_agents) == 6

    def test_coordinator_sub_agent_names(self):
        from src.agents.coordinator.agent import coordinator_agent

        names = {a.name for a in coordinator_agent.sub_agents}
        assert names == {"intake", "accountability", "safety_intel", "sitrep", "learning", "compliance"}

    def test_coordinator_model(self):
        from src.agents.coordinator.agent import coordinator_agent

        assert coordinator_agent.model == "gemini-3.5-flash"

    def test_all_agents_use_same_model(self):
        from src.agents.coordinator.agent import coordinator_agent

        for sub in coordinator_agent.sub_agents:
            assert sub.model == "gemini-3.5-flash", f"{sub.name} uses {sub.model}"


class TestIntakeAgentTools:
    def test_intake_has_tools(self):
        from src.agents.intake.agent import intake_agent

        tool_names = [t.__name__ for t in intake_agent.tools]
        assert "classify_incident" in tool_names
        assert "extract_location" in tool_names
        assert "select_playbook" in tool_names

    def test_classify_returns_fire(self):
        from src.agents.intake.tools import classify_incident

        result = classify_incident("Smoke near the science lab, floor 2 — kids still inside")
        assert result["incident_type"] == "fire"

    def test_extract_resolves_science_lab(self):
        from src.agents.intake.tools import extract_location

        result = extract_location("Smoke near the science lab, floor 2 — kids still inside")
        assert result["zone_id"] == "west-wing-f2"
        assert result["resolved"] is True


class TestSafetyIntelAgentTools:
    def test_safety_has_seven_tools(self):
        from src.agents.safety_intel.agent import safety_intel_agent

        assert len(safety_intel_agent.tools) == 7

    def test_zone_info_returns_real_data(self):
        from src.agents.safety_intel.tools import find_zone_info

        result = find_zone_info("jefferson", "west-wing-f2")
        assert result["name"] == "West Wing Floor 2"
        assert result["primary_exit"] == "West Stairwell to Door 1"

    def test_routes_return_real_data(self):
        from src.agents.safety_intel.tools import find_safe_routes

        result = find_safe_routes("jefferson", "west-wing-f2")
        assert result["total_routes"] >= 2


class TestAccountabilityAgentTools:
    def test_accountability_has_five_tools(self):
        from src.agents.accountability.agent import accountability_agent

        assert len(accountability_agent.tools) == 5

    def test_roster_returns_34_personnel(self):
        from src.agents.accountability.tools import read_roster

        result = read_roster("jefferson")
        assert result["total_personnel"] == 34

    def test_mobility_flagged(self):
        from src.agents.accountability.tools import read_roster

        result = read_roster("jefferson")
        names = [p["name"] for p in result["mobility_needs"]]
        assert "Mrs. Davis" in names
        assert "Mrs. Thompson" in names


class TestLearningAgentTools:
    def test_learning_has_four_tools(self):
        from src.agents.learning.agent import learning_agent

        assert len(learning_agent.tools) == 4

    def test_fire_lessons_found(self):
        from src.agents.learning.tools import find_similar_incidents

        result = find_similar_incidents("fire", "jefferson")
        assert result["lessons_found"] >= 3


class TestCoordinatorResolveToolAndGateway:
    """Batch C: Coordinator has resolve_incident tool wired through the gateway."""

    def test_coordinator_has_resolve_tool(self):
        from src.agents.coordinator.agent import coordinator_agent

        tool_names = [t.__name__ for t in coordinator_agent.tools]
        assert "resolve_incident" in tool_names

    def test_coordinator_instruction_includes_resolve_step(self):
        from src.agents.coordinator.agent import coordinator_agent

        assert "resolve_incident" in coordinator_agent.instruction
        assert "pending ic approval" in coordinator_agent.instruction.lower()

    def test_coordinator_instruction_forbids_retry(self):
        from src.agents.coordinator.agent import coordinator_agent

        assert "do not retry" in coordinator_agent.instruction.lower()

    def test_resolve_tool_returns_status(self):
        from src.agents.coordinator.tools import resolve_incident

        result = resolve_incident("INC-TEST")
        assert result["status"] == "resolved"
        assert result["incident_id"] == "INC-TEST"

    @pytest.mark.asyncio
    async def test_gateway_plugin_blocks_gated_tool(self):
        from src.core.agent_gateway import AgentGateway, GatewayPlugin
        from src.core.event_bus import EventBus

        AgentGateway.reset()
        EventBus.reset()

        class FakeTool:
            name = "resolve_incident"

        class FakeContext:
            agent_name = "coordinator"

        plugin = GatewayPlugin()
        result = await plugin.before_tool_callback(
            tool=FakeTool(),
            tool_args={"incident_id": "INC-001"},
            tool_context=FakeContext(),
        )
        assert result is not None
        assert result["blocked"] is True
        assert result["status"] == "pending_approval"
        assert "pending_action_id" in result
        assert "/incident approve" in result["instruction"]

        AgentGateway.reset()
        EventBus.reset()

    @pytest.mark.asyncio
    async def test_gateway_plugin_allows_ungated_tool(self):
        from src.core.agent_gateway import AgentGateway, GatewayPlugin
        from src.core.event_bus import EventBus

        AgentGateway.reset()
        EventBus.reset()

        class FakeTool:
            name = "classify_incident"

        class FakeContext:
            agent_name = "intake"

        plugin = GatewayPlugin()
        result = await plugin.before_tool_callback(
            tool=FakeTool(),
            tool_args={"report_text": "Fire in west wing"},
            tool_context=FakeContext(),
        )
        assert result is None

        AgentGateway.reset()
        EventBus.reset()

    @pytest.mark.asyncio
    async def test_gateway_plugin_auto_approve_demo(self, monkeypatch):
        from src.core.agent_gateway import AgentGateway, GatewayPlugin
        from src.core.event_bus import EventBus

        monkeypatch.setenv("DEMO_AUTO_APPROVE", "1")
        AgentGateway.reset()
        EventBus.reset()

        class FakeTool:
            name = "resolve_incident"

        class FakeContext:
            agent_name = "coordinator"

        plugin = GatewayPlugin()
        result = await plugin.before_tool_callback(
            tool=FakeTool(),
            tool_args={"incident_id": "INC-001"},
            tool_context=FakeContext(),
        )
        assert result is None

        AgentGateway.reset()
        EventBus.reset()
        monkeypatch.delenv("DEMO_AUTO_APPROVE", raising=False)


class TestAgentInstructionsRequireTransferBack:
    """Verify all sub-agents have 'transfer back to coordinator' in their instructions."""

    def test_intake_transfers_back(self):
        from src.agents.intake.agent import intake_agent
        assert "transfer back to the coordinator" in intake_agent.instruction.lower()

    def test_accountability_transfers_back(self):
        from src.agents.accountability.agent import accountability_agent
        assert "transfer back to the coordinator" in accountability_agent.instruction.lower()

    def test_safety_transfers_back(self):
        from src.agents.safety_intel.agent import safety_intel_agent
        assert "transfer back to the coordinator" in safety_intel_agent.instruction.lower()

    def test_sitrep_transfers_back(self):
        from src.agents.sitrep.agent import sitrep_agent
        assert "transfer back to the coordinator" in sitrep_agent.instruction.lower()

    def test_learning_transfers_back(self):
        from src.agents.learning.agent import learning_agent
        assert "transfer back to the coordinator" in learning_agent.instruction.lower()

    def test_compliance_transfers_back(self):
        from src.agents.compliance.agent import compliance_agent
        assert "transfer back to the coordinator" in compliance_agent.instruction.lower()

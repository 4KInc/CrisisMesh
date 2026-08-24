"""Tests for Batch G: tactical reasoning, improvisation, autonomy, safety floors."""

import pytest

from src.core.tactical_reasoning import (
    BACKSTOP_LINES,
    EVACUATION_TYPES,
    apply_safety_backstop,
    build_provenance_record,
    get_tactical_context,
    strip_origin_from_payload,
    validate_routing_directives,
)
from src.core.agent_gateway import APPROVAL_REQUIRED_ACTIONS, AgentGateway
from src.core.event_bus import EventBus


@pytest.fixture(autouse=True)
def reset_singletons():
    AgentGateway.reset()
    EventBus.reset()
    yield
    AgentGateway.reset()
    EventBus.reset()


# ── Grounded vs improvised origin ──


class TestGroundedOrigin:
    """Playbook-grounded context when an approved rule covers the incident."""

    def test_fire_returns_grounded(self):
        ctx = get_tactical_context("fire", "playbook-fire-v1")
        assert ctx["origin"] == "playbook_grounded"
        assert ctx["playbook_rule_id"] == "playbook-fire-v1"
        assert len(ctx["immediate_actions"]) > 0

    def test_active_threat_returns_grounded(self):
        ctx = get_tactical_context("active_threat", "playbook-active-threat-v1")
        assert ctx["origin"] == "playbook_grounded"
        assert "Active Threat" in ctx["playbook_title"]

    def test_medical_returns_grounded(self):
        ctx = get_tactical_context("medical", "playbook-medical-v1")
        assert ctx["origin"] == "playbook_grounded"
        assert ctx["grounding_facts"]["incident_type"] == "medical"

    def test_grounded_includes_roles_and_resources(self):
        ctx = get_tactical_context("fire", "playbook-fire-v1")
        assert len(ctx["roles"]) > 0
        assert len(ctx["resources"]) > 0

    def test_grounded_includes_severity_in_facts(self):
        ctx = get_tactical_context("fire", "playbook-fire-v1", severity="critical")
        assert ctx["grounding_facts"]["severity"] == "critical"


class TestImprovisedOrigin:
    """Improvised context when no approved rule covers the situation."""

    def test_unknown_type_returns_improvised(self):
        ctx = get_tactical_context("alien_invasion", "playbook-unknown-v1")
        assert ctx["origin"] == "improvised"
        assert ctx["playbook_rule_id"] is None
        assert "no approved playbook" in ctx["reason"].lower()

    def test_improvised_has_guidance_note(self):
        ctx = get_tactical_context("unknown_crisis", "playbook-none-v1")
        assert "guidance_note" in ctx
        assert "improvised" in ctx["guidance_note"].lower()

    def test_improvised_includes_grounding_facts(self):
        ctx = get_tactical_context("alien_invasion", "playbook-x-v1", severity="high")
        assert ctx["grounding_facts"]["incident_type"] == "alien_invasion"
        assert ctx["grounding_facts"]["severity"] == "high"


class TestImprovisedOnlyWhenNoRule:
    """Invariant: improvisation fires ONLY when no approved rule matches."""

    @pytest.mark.parametrize("incident_type", [
        "fire", "active_threat", "severe_weather", "medical",
        "flood", "cyber_ransomware", "data_breach", "utility_outage",
    ])
    def test_all_known_types_are_grounded(self, incident_type):
        ctx = get_tactical_context(incident_type, f"playbook-{incident_type}-v1")
        assert ctx["origin"] == "playbook_grounded", (
            f"Known type '{incident_type}' should be grounded, not improvised"
        )

    def test_only_unknown_types_are_improvised(self):
        ctx_known = get_tactical_context("fire", "playbook-fire-v1")
        ctx_unknown = get_tactical_context("zombie_outbreak", "playbook-zombie-v1")
        assert ctx_known["origin"] == "playbook_grounded"
        assert ctx_unknown["origin"] == "improvised"


# ── No fabricated grounding (Invariant 1) ──


class TestNoFabricatedGrounding:
    """Improvised output must NOT have a playbook_rule_id attached."""

    def test_improvised_has_null_rule_id(self):
        ctx = get_tactical_context("alien_invasion", "playbook-alien-v1")
        assert ctx["origin"] == "improvised"
        assert ctx["playbook_rule_id"] is None

    def test_grounded_has_real_rule_id(self):
        ctx = get_tactical_context("fire", "playbook-fire-v1")
        assert ctx["origin"] == "playbook_grounded"
        assert ctx["playbook_rule_id"] == "playbook-fire-v1"

    def test_improvised_cannot_have_fabricated_rule_id(self):
        """No code path sets a non-None rule_id on improvised output."""
        ctx = get_tactical_context("unknown_crisis", "playbook-fake-v1")
        assert ctx["origin"] == "improvised"
        assert ctx["playbook_rule_id"] is None


# ── Safety backstop (deterministic code floor) ──


class TestSafetyBackstop:
    """Non-negotiable backstop lines on active-threat/evacuation output."""

    def test_backstop_appended_to_fire(self):
        text = "Evacuate via east wing. Report to assembly point."
        result = apply_safety_backstop(text, "fire")
        assert "call 911" in result.lower()
        assert "search for missing" in result.lower()
        assert "mobility limitations" in result.lower()

    def test_backstop_appended_to_active_threat(self):
        text = "Lock doors and stay hidden."
        result = apply_safety_backstop(text, "active_threat")
        for line in BACKSTOP_LINES:
            assert any(phrase in result for phrase in [line, line.lower()])

    def test_backstop_not_duplicated_if_already_present(self):
        text = (
            "Call 911 immediately. Do NOT send untrained personnel to search "
            "for missing individuals. Do NOT task occupants with mobility "
            "limitations to search or evacuate unaided."
        )
        result = apply_safety_backstop(text, "fire")
        assert result == text

    def test_backstop_not_appended_to_non_evacuation_type(self):
        text = "Isolate affected systems from the network."
        result = apply_safety_backstop(text, "cyber_ransomware")
        assert result == text

    def test_backstop_covers_all_evacuation_types(self):
        for etype in EVACUATION_TYPES:
            result = apply_safety_backstop("Some guidance.", etype)
            assert "911" in result, f"Backstop missing for {etype}"

    def test_backstop_present_even_when_model_omits(self):
        """Backstop is code — model text doesn't matter."""
        model_text = "Everything is fine, no need to worry."
        result = apply_safety_backstop(model_text, "active_threat")
        assert "call 911" in result.lower()
        assert "search for missing" in result.lower()


# ── Route validation (deterministic code floor) ──


class TestRouteValidation:
    """Improvised routing into a known blocked zone is suppressed."""

    def test_directive_into_blocked_zone_suppressed(self):
        text = "Evacuate to east-wing-f2 immediately."
        result = validate_routing_directives(text, ["east-wing-f2"])
        assert "SUPPRESSED" in result
        assert "blocked zone" in result
        assert "east-wing-f2" in result

    def test_move_to_blocked_zone_suppressed(self):
        text = "Move to science-lab for shelter."
        result = validate_routing_directives(text, ["science-lab"])
        assert "SUPPRESSED" in result

    def test_safe_zone_not_suppressed(self):
        text = "Evacuate to the athletic field assembly point."
        result = validate_routing_directives(text, ["east-wing-f2"])
        assert "SUPPRESSED" not in result
        assert "athletic field" in result

    def test_multiple_blocked_zones_all_suppressed(self):
        text = "Go to east-wing-f2 first, then proceed to west-wing-f1."
        result = validate_routing_directives(text, ["east-wing-f2", "west-wing-f1"])
        assert result.count("SUPPRESSED") == 2

    def test_no_blocked_zones_noop(self):
        text = "Evacuate to east-wing-f2."
        result = validate_routing_directives(text, [])
        assert result == text

    def test_case_insensitive_matching(self):
        text = "Head to East-Wing-F2 for supplies."
        result = validate_routing_directives(text, ["east-wing-f2"])
        assert "SUPPRESSED" in result


# ── Origin stripping from UI/transport ──


class TestOriginStripping:
    """Origin is stored in DB/audit but ABSENT from UI/transport payloads."""

    def test_strip_origin_from_flat_payload(self):
        payload = {
            "incident_id": "INC-001",
            "classification": {"type": "fire"},
            "origin": "playbook_grounded",
            "playbook_rule_id": "playbook-fire-v1",
            "grounding_facts": {"incident_type": "fire"},
        }
        cleaned = strip_origin_from_payload(payload)
        assert "origin" not in cleaned
        assert "playbook_rule_id" not in cleaned
        assert "grounding_facts" not in cleaned
        assert cleaned["incident_id"] == "INC-001"

    def test_strip_origin_from_nested_payload(self):
        payload = {
            "incident_id": "INC-001",
            "tactical": {
                "origin": "improvised",
                "playbook_rule_id": None,
            },
        }
        cleaned = strip_origin_from_payload(payload)
        assert "origin" not in cleaned.get("tactical", {})
        assert "playbook_rule_id" not in cleaned.get("tactical", {})

    def test_strip_origin_from_list_payload(self):
        payload = {
            "events": [
                {"type": "tool_result", "origin": "playbook_grounded"},
                {"type": "delegation"},
            ],
        }
        cleaned = strip_origin_from_payload(payload)
        assert "origin" not in cleaned["events"][0]
        assert cleaned["events"][1]["type"] == "delegation"

    def test_strip_preserves_non_origin_fields(self):
        payload = {
            "incident_id": "INC-001",
            "report": "Smoke detected",
            "classification": {"type": "fire", "severity": "high"},
            "origin": "playbook_grounded",
        }
        cleaned = strip_origin_from_payload(payload)
        assert cleaned["incident_id"] == "INC-001"
        assert cleaned["report"] == "Smoke detected"
        assert cleaned["classification"]["type"] == "fire"

    def test_sitrep_payload_has_no_origin(self):
        """Simulated SITREP payload — origin must be absent."""
        sitrep = {
            "type": "IC_SITREP",
            "situation": {"type": "fire", "severity": "high"},
            "accountability": {"total": 34, "accounted": 30},
            "origin": "playbook_grounded",
        }
        cleaned = strip_origin_from_payload(sitrep)
        assert "origin" not in cleaned

    def test_slack_block_kit_has_no_origin(self):
        """Simulated Slack Block Kit payload — origin must be absent."""
        slack_payload = {
            "blocks": [{"type": "section", "text": "Fire alert"}],
            "origin": "improvised",
            "grounding_facts": {"incident_type": "fire"},
        }
        cleaned = strip_origin_from_payload(slack_payload)
        assert "origin" not in cleaned
        assert "grounding_facts" not in cleaned

    def test_console_sse_event_has_no_origin(self):
        """Simulated SSE event — origin must be absent."""
        sse_event = {
            "type": "final_response",
            "text": "Summary...",
            "origin": "playbook_grounded",
        }
        cleaned = strip_origin_from_payload(sse_event)
        assert "origin" not in cleaned


# ── Provenance record ──


class TestProvenanceRecord:
    """Origin is correctly stored in audit/DB provenance records."""

    def test_grounded_provenance(self):
        ctx = get_tactical_context("fire", "playbook-fire-v1", severity="high")
        record = build_provenance_record(ctx, "INC-001")
        assert record["origin"] == "playbook_grounded"
        assert record["playbook_rule_id"] == "playbook-fire-v1"
        assert record["grounding_facts"]["incident_type"] == "fire"
        assert record["incident_id"] == "INC-001"

    def test_improvised_provenance(self):
        ctx = get_tactical_context("alien_invasion", "playbook-alien-v1")
        record = build_provenance_record(ctx, "INC-002")
        assert record["origin"] == "improvised"
        assert record["reason"] == ctx["reason"]
        assert "playbook_rule_id" not in record
        assert record["incident_id"] == "INC-002"


# ── Authority-bounded autonomy ──


class TestAuthorityBoundedAutonomy:
    """Operationally autonomous but authority-bounded: humans retain
    the consequential, hard-to-reverse decisions (external comms,
    medical-data sharing, incident closure)."""

    def test_gated_actions_set(self):
        assert APPROVAL_REQUIRED_ACTIONS == {
            "send_external_message",
            "share_medical_info",
            "resolve_incident",
        }

    @pytest.mark.asyncio
    async def test_resolve_incident_gated(self):
        gw = AgentGateway.get()
        decision = await gw.check_tool_call(
            "coordinator", "resolve_incident", incident_id="INC-001"
        )
        assert decision.allowed is False
        assert decision.policy == "approval_gate"

    def test_send_external_message_gated(self):
        """External comms are hard to reverse once sent."""
        assert "send_external_message" in APPROVAL_REQUIRED_ACTIONS

    def test_share_medical_info_gated(self):
        """Medical-data sharing is sensitive and hard to retract."""
        assert "share_medical_info" in APPROVAL_REQUIRED_ACTIONS

    @pytest.mark.asyncio
    async def test_propose_playbook_change_autonomous(self):
        """A proposal is low-consequence — applying it is separately gated."""
        gw = AgentGateway.get()
        decision = await gw.check_tool_call(
            "learning", "propose_playbook_change", incident_id="INC-001"
        )
        assert decision.allowed is True

    @pytest.mark.asyncio
    async def test_improvised_guidance_no_approval_needed(self):
        """Improvised guidance releases without human approval step."""
        ctx = get_tactical_context("alien_invasion", "playbook-alien-v1")
        assert ctx["origin"] == "improvised"
        assert "guidance_note" in ctx


# ── Coordinator tool integration ──


class TestCoordinatorTool:
    """get_tactical_context tool on the coordinator agent."""

    def test_tool_accessible(self):
        from src.agents.coordinator.tools import get_tactical_context as tool_fn
        result = tool_fn(
            incident_type="fire",
            playbook_id="playbook-fire-v1",
            severity="high",
            situation_summary="Smoke in science lab",
        )
        assert result["origin"] == "playbook_grounded"

    def test_tool_in_registry_approved(self):
        from src.config.agent_registry import is_tool_allowed
        assert is_tool_allowed("coordinator", "get_tactical_context")

    def test_tool_returns_improvised_for_unknown(self):
        from src.agents.coordinator.tools import get_tactical_context as tool_fn
        result = tool_fn(
            incident_type="unknown_crisis",
            playbook_id="playbook-none-v1",
        )
        assert result["origin"] == "improvised"


class TestLockdownBackstop:
    """An active threat is not an evacuation, and the safety floor has to say so."""

    def test_lockdown_lines_appended_for_active_threat(self):
        from src.core.tactical_reasoning import apply_safety_backstop
        result = apply_safety_backstop("Move everyone to the assembly point.", "active_threat")
        assert "Do NOT direct a general evacuation" in result
        assert "Do NOT pull the fire alarm" in result
        assert "silence phones" in result.lower()

    def test_lockdown_lines_not_appended_for_fire(self):
        from src.core.tactical_reasoning import apply_safety_backstop
        result = apply_safety_backstop("Evacuate via stairwell B.", "fire")
        assert "Do NOT pull the fire alarm" not in result
        # the universal floor still applies
        assert "call 911" in result.lower()

    def test_bomb_threat_is_a_lockdown(self):
        from src.core.tactical_reasoning import apply_safety_backstop
        result = apply_safety_backstop("Guidance.", "bomb_threat")
        assert "Do NOT pull the fire alarm" in result

    def test_evacuation_types_alias_preserved(self):
        from src.core.tactical_reasoning import EVACUATION_TYPES, LIFE_SAFETY_TYPES
        assert EVACUATION_TYPES is LIFE_SAFETY_TYPES

    def test_lockdown_types_are_a_subset(self):
        from src.core.tactical_reasoning import LIFE_SAFETY_TYPES, LOCKDOWN_TYPES
        assert LOCKDOWN_TYPES <= LIFE_SAFETY_TYPES

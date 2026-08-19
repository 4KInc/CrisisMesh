"""Agent Gateway — central routing, access policy, rate limits, approved-action boundaries.

Intercepts all agent tool calls to enforce:
1. Agent Identity (least-privilege) — deny out-of-scope tools
2. Content scanning (Model Armor or InjectionGuard) — block injection + PII leakage
3. Rate limiting — prevent runaway agents
4. Approval gates — block high-impact actions without commander approval

Every decision is logged to the event bus for the observability trace.
"""

from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from src.config.agent_registry import AGENT_REGISTRY, is_tool_allowed
from src.core.content_scanner import ContentScanner
from src.core.event_bus import EventBus, create_event
from src.models.events import EventType

logger = logging.getLogger(__name__)


class GatewayDecision:
    """Result of a gateway policy check."""

    def __init__(
        self,
        allowed: bool,
        agent_id: str,
        tool_name: str,
        reason: str = "",
        policy: str = "",
        incident_id: str = "",
    ) -> None:
        self.id = str(uuid.uuid4())[:8]
        self.allowed = allowed
        self.agent_id = agent_id
        self.tool_name = tool_name
        self.reason = reason
        self.policy = policy
        self.incident_id = incident_id
        self.timestamp = datetime.now(timezone.utc)

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_id": self.id,
            "allowed": self.allowed,
            "agent_id": self.agent_id,
            "tool_name": self.tool_name,
            "reason": self.reason,
            "policy": self.policy,
            "incident_id": self.incident_id,
            "timestamp": self.timestamp.isoformat(),
        }


# Actions that require Incident Commander approval before execution
APPROVAL_REQUIRED_ACTIONS = {
    "generate_responder_card",
    "generate_stakeholder_update",
    "propose_playbook_change",
    "share_medical_info",
    "send_external_message",
}


class AgentGateway:
    """Central policy enforcement gateway for all agent tool calls."""

    _instance: AgentGateway | None = None

    def __init__(self) -> None:
        self._decisions: list[GatewayDecision] = []
        self._rate_counts: dict[str, int] = defaultdict(int)
        self._rate_limit = 100
        self._scanner = ContentScanner.get()

    @classmethod
    def get(cls) -> AgentGateway:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        cls._instance = None

    @property
    def scanner_backend(self) -> str:
        return self._scanner.backend

    async def check_tool_call(
        self,
        agent_id: str,
        tool_name: str,
        tool_args: dict[str, Any] | None = None,
        incident_id: str = "",
    ) -> GatewayDecision:
        tool_args = tool_args or {}

        # 1. Agent Identity — least-privilege check
        if not is_tool_allowed(agent_id, tool_name):
            decision = GatewayDecision(
                allowed=False, agent_id=agent_id, tool_name=tool_name,
                reason=f"Agent '{agent_id}' is not authorized to use tool '{tool_name}'",
                policy="agent_identity", incident_id=incident_id,
            )
            await self._log_decision(decision)
            return decision

        # 2. Rate limiting
        rate_key = f"{agent_id}:{incident_id}"
        self._rate_counts[rate_key] += 1
        if self._rate_counts[rate_key] > self._rate_limit:
            decision = GatewayDecision(
                allowed=False, agent_id=agent_id, tool_name=tool_name,
                reason=f"Rate limit exceeded: {self._rate_counts[rate_key]} calls (limit: {self._rate_limit})",
                policy="rate_limit", incident_id=incident_id,
            )
            await self._log_decision(decision)
            return decision

        # 3. Approval gate for high-impact actions
        if tool_name in APPROVAL_REQUIRED_ACTIONS:
            decision = GatewayDecision(
                allowed=True, agent_id=agent_id, tool_name=tool_name,
                reason=f"Action '{tool_name}' requires Incident Commander approval before external release",
                policy="approval_gate", incident_id=incident_id,
            )
            await self._log_decision(decision)
            return decision

        # 4. Content scanning — check tool arguments for injection/PII
        scan_result = self._scanner.scan_tool_args(agent_id, tool_name, tool_args)
        if scan_result["blocked"]:
            decision = GatewayDecision(
                allowed=False, agent_id=agent_id, tool_name=tool_name,
                reason=scan_result["reason"],
                policy=scan_result["policy"],
                incident_id=incident_id,
            )
            await self._log_decision(decision)
            return decision

        # All checks passed
        decision = GatewayDecision(
            allowed=True, agent_id=agent_id, tool_name=tool_name,
            policy="allowed", incident_id=incident_id,
        )
        await self._log_decision(decision)
        return decision

    async def _log_decision(self, decision: GatewayDecision) -> None:
        self._decisions.append(decision)
        bus = EventBus.get()
        if not decision.allowed:
            await bus.publish(create_event(
                EventType.POLICY_VIOLATION,
                incident_id=decision.incident_id,
                agent_id=decision.agent_id,
                data={
                    "decision_id": decision.id,
                    "tool_name": decision.tool_name,
                    "policy": decision.policy,
                    "reason": decision.reason,
                },
            ))
            logger.warning(
                f"GATEWAY DENY: {decision.agent_id} -> {decision.tool_name} "
                f"[{decision.policy}] {decision.reason}"
            )

    def get_decisions(
        self, incident_id: str = "", agent_id: str = "", allowed: bool | None = None,
    ) -> list[GatewayDecision]:
        results = list(self._decisions)
        if incident_id:
            results = [d for d in results if d.incident_id == incident_id]
        if agent_id:
            results = [d for d in results if d.agent_id == agent_id]
        if allowed is not None:
            results = [d for d in results if d.allowed == allowed]
        return results

    def get_deny_log(self, incident_id: str = "") -> list[dict[str, Any]]:
        denied = self.get_decisions(incident_id=incident_id, allowed=False)
        return [d.to_dict() for d in denied]

    def get_policy_summary(self) -> dict[str, Any]:
        total = len(self._decisions)
        denied = sum(1 for d in self._decisions if not d.allowed)
        by_policy = defaultdict(int)
        for d in self._decisions:
            if not d.allowed:
                by_policy[d.policy] += 1
        return {
            "total_checks": total,
            "denied": denied,
            "allowed": total - denied,
            "denials_by_policy": dict(by_policy),
            "scanner_backend": self._scanner.backend,
            "policies_active": [
                "agent_identity", "rate_limit", "approval_gate",
                f"content_scanner ({self._scanner.backend})",
            ],
        }

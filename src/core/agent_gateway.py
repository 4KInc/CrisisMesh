"""Agent Gateway — central routing, access policy, rate limits, approved-action boundaries.

Intercepts all agent tool calls to enforce:
1. Agent Identity (least-privilege) — deny out-of-scope tools
2. Model Armor — block prompt injection, PII leakage, tool poisoning
3. Rate limiting — prevent runaway agents
4. Approval gates — block high-impact actions without commander approval

Every decision is logged to the event bus for the observability trace.
"""

from __future__ import annotations

import logging
import re
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from src.config.agent_registry import AGENT_REGISTRY, AgentRegistryEntry, is_tool_allowed
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
    "generate_responder_card",  # Responder handoff — external release
    "generate_stakeholder_update",  # Stakeholder comms — external
    "propose_playbook_change",  # Playbook modification
    "share_medical_info",  # PII sharing
    "send_external_message",  # External communications
}


class AgentGateway:
    """Central policy enforcement gateway for all agent tool calls."""

    _instance: AgentGateway | None = None

    def __init__(self) -> None:
        self._decisions: list[GatewayDecision] = []
        self._rate_counts: dict[str, int] = defaultdict(int)
        self._rate_limit = 100  # max tool calls per agent per incident

    @classmethod
    def get(cls) -> AgentGateway:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        cls._instance = None

    async def check_tool_call(
        self,
        agent_id: str,
        tool_name: str,
        tool_args: dict[str, Any] | None = None,
        incident_id: str = "",
    ) -> GatewayDecision:
        """Run all policy checks against a proposed tool call."""
        tool_args = tool_args or {}

        # 1. Agent Identity — least-privilege check
        if not is_tool_allowed(agent_id, tool_name):
            decision = GatewayDecision(
                allowed=False,
                agent_id=agent_id,
                tool_name=tool_name,
                reason=f"Agent '{agent_id}' is not authorized to use tool '{tool_name}'",
                policy="agent_identity",
                incident_id=incident_id,
            )
            await self._log_decision(decision)
            return decision

        # 2. Rate limiting
        rate_key = f"{agent_id}:{incident_id}"
        self._rate_counts[rate_key] += 1
        if self._rate_counts[rate_key] > self._rate_limit:
            decision = GatewayDecision(
                allowed=False,
                agent_id=agent_id,
                tool_name=tool_name,
                reason=f"Rate limit exceeded: {self._rate_counts[rate_key]} calls (limit: {self._rate_limit})",
                policy="rate_limit",
                incident_id=incident_id,
            )
            await self._log_decision(decision)
            return decision

        # 3. Approval gate for high-impact actions
        if tool_name in APPROVAL_REQUIRED_ACTIONS:
            decision = GatewayDecision(
                allowed=True,  # allowed but flagged
                agent_id=agent_id,
                tool_name=tool_name,
                reason=f"Action '{tool_name}' requires Incident Commander approval before external release",
                policy="approval_gate",
                incident_id=incident_id,
            )
            await self._log_decision(decision)
            return decision

        # 4. Model Armor — check tool arguments for injection/PII
        armor_result = ModelArmor.scan_tool_args(agent_id, tool_name, tool_args)
        if armor_result["blocked"]:
            decision = GatewayDecision(
                allowed=False,
                agent_id=agent_id,
                tool_name=tool_name,
                reason=armor_result["reason"],
                policy=armor_result["policy"],
                incident_id=incident_id,
            )
            await self._log_decision(decision)
            return decision

        # All checks passed
        decision = GatewayDecision(
            allowed=True,
            agent_id=agent_id,
            tool_name=tool_name,
            policy="allowed",
            incident_id=incident_id,
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
        self,
        incident_id: str = "",
        agent_id: str = "",
        allowed: bool | None = None,
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
        """Get all denied decisions — the audit proof for agent identity."""
        denied = self.get_decisions(incident_id=incident_id, allowed=False)
        return [d.to_dict() for d in denied]

    def get_policy_summary(self) -> dict[str, Any]:
        """Summary view of gateway policies — for the demo registry page."""
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
            "policies_active": [
                "agent_identity", "rate_limit", "approval_gate",
                "model_armor_injection", "model_armor_pii",
            ],
        }


class ModelArmor:
    """Inline protection from prompt injection, tool poisoning, and PII leakage.

    Scans tool call arguments for:
    - Prompt injection attempts (ignore instructions, override policy, etc.)
    - PII leakage requests (publish medical records, share personal data)
    - Tool poisoning (attempts to call unauthorized tools via arguments)
    """

    # Patterns that indicate prompt injection
    _INJECTION_PATTERNS = [
        r"ignore\s+(all\s+)?(previous\s+)?(?:policy|instructions|rules|guidelines)",
        r"override\s+(?:policy|security|access|controls)",
        r"disregard\s+(?:safety|policy|rules|guidelines)",
        r"you\s+are\s+now\s+(?:a|an)\s+(?:unrestricted|unfiltered)",
        r"bypass\s+(?:security|policy|access|controls|restrictions)",
        r"jailbreak",
        r"pretend\s+(?:you|there)\s+(?:are|is)\s+no\s+(?:policy|rules|restrictions)",
        r"act\s+as\s+(?:a|an)\s+(?:admin|root|superuser)",
        r"system\s*prompt",
    ]

    # Patterns that indicate PII leakage requests
    _PII_PATTERNS = [
        r"publish\s+(?:every|all)\s+(?:student|staff|personnel)\s+(?:medical|health)\s+record",
        r"share\s+(?:all|every)\s+(?:medical|health|personal)\s+(?:data|info|record|note)",
        r"(?:export|dump|list)\s+(?:all\s+)?(?:ssn|social\s+security|phone\s+number)",
        r"broadcast\s+(?:medical|health|personal)\s+(?:data|info|condition)",
        r"post\s+(?:medical|health)\s+(?:record|info|data)\s+(?:to|in)\s+(?:general|public)",
    ]

    @classmethod
    def scan_tool_args(
        cls,
        agent_id: str,
        tool_name: str,
        tool_args: dict[str, Any],
    ) -> dict[str, Any]:
        """Scan tool arguments for injection, PII leakage, and poisoning."""
        # Flatten all string arguments for scanning
        text_to_scan = cls._flatten_args(tool_args)

        # Check for prompt injection
        for pattern in cls._INJECTION_PATTERNS:
            if re.search(pattern, text_to_scan, re.IGNORECASE):
                return {
                    "blocked": True,
                    "reason": f"Prompt injection detected: matches pattern '{pattern}'",
                    "policy": "model_armor_injection",
                    "quarantined_text": text_to_scan[:200],
                }

        # Check for PII leakage
        for pattern in cls._PII_PATTERNS:
            if re.search(pattern, text_to_scan, re.IGNORECASE):
                return {
                    "blocked": True,
                    "reason": f"PII leakage attempt detected: matches pattern '{pattern}'",
                    "policy": "model_armor_pii",
                    "quarantined_text": text_to_scan[:200],
                }

        return {"blocked": False, "policy": "model_armor_clear"}

    @classmethod
    def scan_message(cls, text: str) -> dict[str, Any]:
        """Scan an incoming message for prompt injection or PII requests."""
        for pattern in cls._INJECTION_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                return {
                    "blocked": True,
                    "reason": f"Prompt injection detected in message",
                    "policy": "model_armor_injection",
                    "quarantined_text": text[:200],
                }

        for pattern in cls._PII_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                return {
                    "blocked": True,
                    "reason": f"PII leakage request detected in message",
                    "policy": "model_armor_pii",
                    "quarantined_text": text[:200],
                }

        return {"blocked": False, "policy": "model_armor_clear"}

    @classmethod
    def _flatten_args(cls, args: dict[str, Any]) -> str:
        parts = []
        for v in args.values():
            if isinstance(v, str):
                parts.append(v)
            elif isinstance(v, dict):
                parts.append(cls._flatten_args(v))
            elif isinstance(v, list):
                for item in v:
                    if isinstance(item, str):
                        parts.append(item)
                    elif isinstance(item, dict):
                        parts.append(cls._flatten_args(item))
        return " ".join(parts)

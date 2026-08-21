"""Agent Gateway — central routing, access policy, rate limits, approved-action boundaries.

Intercepts all agent tool calls to enforce:
1. Agent Identity (least-privilege) — deny out-of-scope tools
2. Content scanning (InjectionGuard regex / Model Armor IAM-blocked) — block injection + PII
3. Rate limiting — prevent runaway agents
4. Approval gates — block high-impact actions without commander approval

Every decision is logged to the event bus for the observability trace.
"""

from __future__ import annotations

import hmac
import logging
import os
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
        pending_action_id: str = "",
    ) -> None:
        self.id = str(uuid.uuid4())[:8]
        self.allowed = allowed
        self.agent_id = agent_id
        self.tool_name = tool_name
        self.reason = reason
        self.policy = policy
        self.incident_id = incident_id
        self.pending_action_id = pending_action_id
        self.timestamp = datetime.now(timezone.utc)

    def to_dict(self) -> dict[str, Any]:
        d = {
            "decision_id": self.id,
            "allowed": self.allowed,
            "agent_id": self.agent_id,
            "tool_name": self.tool_name,
            "reason": self.reason,
            "policy": self.policy,
            "incident_id": self.incident_id,
            "timestamp": self.timestamp.isoformat(),
        }
        if self.pending_action_id:
            d["pending_action_id"] = self.pending_action_id
        return d


APPROVAL_REQUIRED_ACTIONS = {
    "send_external_message",
    "share_medical_info",
    "resolve_incident",
}

AUTHORIZED_IC_IDS: set[str] = set()


def _load_authorized_ics() -> None:
    raw = os.environ.get("AUTHORIZED_IC_IDS", "")
    AUTHORIZED_IC_IDS.clear()
    if raw:
        AUTHORIZED_IC_IDS.update(id.strip() for id in raw.split(",") if id.strip())


class PendingAction:
    """An action queued for Incident Commander approval."""

    VALID_STATES = {"pending", "granted", "executed", "denied"}

    def __init__(
        self,
        incident_id: str,
        action: str,
        args: dict[str, Any],
        requesting_agent: str,
    ) -> None:
        self.id = str(uuid.uuid4())[:8]
        self.incident_id = incident_id
        self.action = action
        self.args = args
        self.requesting_agent = requesting_agent
        self.timestamp = datetime.now(timezone.utc)
        self.state = "pending"
        self.decided_by: str = ""
        self.decided_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        d = {
            "id": self.id,
            "incident_id": self.incident_id,
            "action": self.action,
            "requesting_agent": self.requesting_agent,
            "timestamp": self.timestamp.isoformat(),
            "state": self.state,
        }
        if self.decided_by:
            d["decided_by"] = self.decided_by
        if self.decided_at:
            d["decided_at"] = self.decided_at.isoformat()
        return d


class AgentGateway:
    """Central policy enforcement gateway for all agent tool calls."""

    _instance: AgentGateway | None = None

    def __init__(self) -> None:
        self._decisions: list[GatewayDecision] = []
        self._rate_counts: dict[str, int] = defaultdict(int)
        self._rate_limit = 100
        self._scanner = ContentScanner.get()
        self._pending_actions: dict[str, PendingAction] = {}
        _load_authorized_ics()

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

        # 3. Approval gate for high-impact actions — hard block
        if tool_name in APPROVAL_REQUIRED_ACTIONS:
            if os.environ.get("DEMO_AUTO_APPROVE") == "1":
                decision = GatewayDecision(
                    allowed=True, agent_id=agent_id, tool_name=tool_name,
                    reason=f"Action '{tool_name}' auto-approved (DEMO MODE)",
                    policy="approval_gate", incident_id=incident_id,
                )
                await self._log_decision(decision)
                bus = EventBus.get()
                await bus.publish(create_event(
                    EventType.APPROVAL_GRANTED,
                    incident_id=incident_id,
                    agent_id=agent_id,
                    data={
                        "action": tool_name,
                        "mode": "auto_granted (DEMO MODE)",
                    },
                ))
                return decision

            pending = PendingAction(
                incident_id=incident_id,
                action=tool_name,
                args=tool_args,
                requesting_agent=agent_id,
            )
            self._pending_actions[pending.id] = pending

            decision = GatewayDecision(
                allowed=False, agent_id=agent_id, tool_name=tool_name,
                reason=f"Action '{tool_name}' requires Incident Commander approval — queued as {pending.id}",
                policy="approval_gate", incident_id=incident_id,
                pending_action_id=pending.id,
            )
            await self._log_decision(decision)

            bus = EventBus.get()
            await bus.publish(create_event(
                EventType.APPROVAL_REQUESTED,
                incident_id=incident_id,
                agent_id=agent_id,
                data={
                    "pending_action_id": pending.id,
                    "action": tool_name,
                    "requesting_agent": agent_id,
                },
            ))
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

    async def approve_action(
        self, action_id: str, approver_id: str,
    ) -> dict[str, Any]:
        """Approve a pending action. Returns result dict."""
        pending = self._pending_actions.get(action_id)
        if not pending:
            return {"error": "Action not found", "status": 404}

        if pending.state != "pending":
            return {"error": f"Action already {pending.state}", "status": 409}

        if not self._is_authorized_ic(approver_id):
            return {"error": "Unauthorized — not a recognized Incident Commander", "status": 403}

        pending.state = "granted"
        pending.decided_by = approver_id
        pending.decided_at = datetime.now(timezone.utc)

        bus = EventBus.get()
        await bus.publish(create_event(
            EventType.APPROVAL_GRANTED,
            incident_id=pending.incident_id,
            agent_id=pending.requesting_agent,
            data={
                "pending_action_id": pending.id,
                "action": pending.action,
                "approved_by": approver_id,
            },
        ))

        pending.state = "executed"

        return {
            "status": "granted",
            "action_id": pending.id,
            "action": pending.action,
            "incident_id": pending.incident_id,
        }

    async def deny_action(
        self, action_id: str, approver_id: str,
    ) -> dict[str, Any]:
        """Deny a pending action. Returns result dict."""
        pending = self._pending_actions.get(action_id)
        if not pending:
            return {"error": "Action not found", "status": 404}

        if pending.state != "pending":
            return {"error": f"Action already {pending.state}", "status": 409}

        if not self._is_authorized_ic(approver_id):
            return {"error": "Unauthorized — not a recognized Incident Commander", "status": 403}

        pending.state = "denied"
        pending.decided_by = approver_id
        pending.decided_at = datetime.now(timezone.utc)

        bus = EventBus.get()
        await bus.publish(create_event(
            EventType.APPROVAL_DENIED,
            incident_id=pending.incident_id,
            agent_id=pending.requesting_agent,
            data={
                "pending_action_id": pending.id,
                "action": pending.action,
                "denied_by": approver_id,
            },
        ))

        return {
            "status": "denied",
            "action_id": pending.id,
            "action": pending.action,
            "incident_id": pending.incident_id,
        }

    def _is_authorized_ic(self, approver_id: str) -> bool:
        if not AUTHORIZED_IC_IDS:
            logger.warning(
                "WARN: no authorized ICs configured — approval gates are OPEN. "
                "Set AUTHORIZED_IC_IDS to restrict approvals."
            )
            return True
        return any(
            hmac.compare_digest(approver_id, ic_id)
            for ic_id in AUTHORIZED_IC_IDS
        )

    def get_pending_actions(
        self, incident_id: str = "",
    ) -> list[PendingAction]:
        actions = list(self._pending_actions.values())
        if incident_id:
            actions = [a for a in actions if a.incident_id == incident_id]
        return actions

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
        pending = [a for a in self._pending_actions.values() if a.state == "pending"]
        return {
            "total_checks": total,
            "denied": denied,
            "allowed": total - denied,
            "denials_by_policy": dict(by_policy),
            "pending_approvals": len(pending),
            "scanner_backend": self._scanner.backend,
            "policies_active": [
                "agent_identity", "rate_limit", "approval_gate",
                f"content_scanner ({self._scanner.backend})",
            ],
        }


try:
    from google.adk.plugins.base_plugin import BasePlugin as _BasePlugin
except ImportError:
    class _BasePlugin:
        def __init__(self, *, name: str = "") -> None:
            self.name = name


class GatewayPlugin(_BasePlugin):
    """ADK Runner plugin that routes tool calls through the Agent Gateway.

    Intercepts all tool calls in the agentic pipeline and enforces the same
    policy layer (identity, rate limit, approval gates, content scan) as the
    deterministic path.

    Usage:
        app = App(name="crisismesh", root_agent=agent, plugins=[GatewayPlugin()])
    """

    def __init__(self, incident_id: str = "") -> None:
        super().__init__(name="gateway")
        self.incident_id = incident_id

    async def before_tool_callback(
        self,
        *,
        tool: Any,
        tool_args: dict[str, Any],
        tool_context: Any,
    ) -> dict[str, Any] | None:
        gw = AgentGateway.get()
        tool_name = getattr(tool, "name", str(tool))
        agent_id = getattr(tool_context, "agent_name", "unknown")
        incident_id = tool_args.get("incident_id", self.incident_id)

        decision = await gw.check_tool_call(
            agent_id, tool_name, tool_args, incident_id=incident_id,
        )

        if not decision.allowed:
            if decision.policy == "approval_gate":
                return {
                    "blocked": True,
                    "status": "pending_approval",
                    "reason": decision.reason,
                    "pending_action_id": decision.pending_action_id,
                    "instruction": (
                        "This action requires Incident Commander approval. "
                        f"Use '/incident approve {decision.pending_action_id}' to proceed. "
                        "Do NOT retry this action — report its pending status in the SITREP."
                    ),
                }
            return {
                "blocked": True,
                "reason": decision.reason,
                "policy": decision.policy,
            }

        return None

"""Ending an incident — from any channel, not just Slack.

Resolution used to exist only inside the Slack slash-command handler, which
meant an incident declared by text message could be started by anyone and ended
by nobody without a Slack workspace. It also meant the report and the state
change were welded to Block Kit formatting.

This module does the state change and produces a structured record. Callers
render it however their channel renders things: Slack builds a message from it,
HTTP returns it as JSON.

Resolution is destructive — it ends the incident everyone is coordinating
around and triggers the all-clear fan-out — so `resolve` refuses to act on a
guess. The caller must name the incident it believes is active, and if that is
not the one actually running, nothing happens. That turns a stale tab or a
replayed request into a no-op instead of an unexplained all-clear.
"""

from __future__ import annotations

import logging
from typing import Any

from src.core import incident_state

logger = logging.getLogger(__name__)


class ResolveRefused(Exception):
    """The incident was not resolved, and why."""

    def __init__(self, reason: str, code: str):
        super().__init__(reason)
        self.reason = reason
        self.code = code


def build_report(resolved_by: str = "") -> dict[str, Any]:
    """Summarise the active incident without changing anything."""
    from src.agents.accountability.tools import compute_accountability_summary

    incident_id = incident_state.get_active_incident_id()
    record = incident_state.get_latest_incident()
    classification = record.get("classification", {}) or {}
    summary = compute_accountability_summary(incident_id)

    return {
        "incident_id": incident_id,
        "incident_type": classification.get("incident_type", "other"),
        "severity": classification.get("severity", ""),
        "duration_minutes": incident_state.elapsed_minutes(),
        "resolved_by": resolved_by,
        "source": record.get("source", ""),
        "accountability": {
            "total_tracked": summary.get("total_tracked", 0),
            "accounted": summary.get("accounted", 0),
            "unaccounted": summary.get("unaccounted", 0),
            "counts": summary.get("counts", {}),
        },
        "prior_lessons": record.get("prior_lessons", {}),
    }


def resolve(incident_id: str, resolved_by: str = "", channel: str = "") -> dict[str, Any]:
    """End the active incident and announce it. Raises ResolveRefused instead
    of guessing.

    `incident_id` must match what is actually running. Passing "" is only
    accepted from a caller that has just read the active id itself.
    """
    if not incident_state.is_active():
        raise ResolveRefused("There is no active incident to resolve.", "no_active_incident")

    active = incident_state.get_active_incident_id()
    if incident_id and incident_id != active:
        raise ResolveRefused(
            f"Incident {incident_id} is not the active incident ({active}); "
            "nothing was resolved.",
            "incident_mismatch",
        )

    report = build_report(resolved_by=resolved_by)
    previous = incident_state.clear()
    previous["incident_type"] = report["incident_type"]

    logger.info(
        f"Incident {active} resolved from {channel or 'unknown'} by "
        f"{resolved_by or 'unspecified'} after {report['duration_minutes']} min"
    )

    _publish(previous)
    report["resolved"] = True
    return report


def _publish(previous: dict[str, Any]) -> None:
    """Announce on the bus, which is what triggers the all-clear fan-out."""
    from src.services.slack_transport import _publish_resolved
    _publish_resolved(previous)

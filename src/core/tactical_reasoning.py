"""Tactical reasoning — playbook-grounded guidance with improvisation fallback.

Produces tactical context for the coordinator agent. Two deterministic safety
floors run as code post-processing (not prompt instructions):

1. Non-negotiable backstop lines for active-threat/evacuation output.
2. Route/movement validation against known blocked/threat zones.
"""

from __future__ import annotations

import re
from typing import Any

from src.config.playbooks import PLAYBOOKS, INCIDENT_TYPE_TO_PLAYBOOK_KEY
from src.core.knowledge_base import KnowledgeBase


EVACUATION_TYPES = {"fire", "active_threat", "bomb_threat", "hazmat", "flood", "severe_weather"}

BACKSTOP_LINES = [
    "EMERGENCY: If this is a life-threatening emergency, call 911 immediately.",
    "Do NOT send untrained personnel to search for missing individuals.",
    "Do NOT task occupants with mobility limitations to search or evacuate unaided.",
]


def get_tactical_context(
    incident_type: str,
    playbook_id: str,
    severity: str = "",
    situation_summary: str = "",
) -> dict[str, Any]:
    """Return playbook rules and origin determination for the coordinator.

    If an approved playbook covers the incident type, returns the rules with
    origin "playbook_grounded". Otherwise returns origin "improvised" so the
    model knows it may reason from general emergency-management principles.
    """
    playbook_key = INCIDENT_TYPE_TO_PLAYBOOK_KEY.get(incident_type, "")
    content = PLAYBOOKS.get(playbook_key) if playbook_key else None

    if content:
        return {
            "origin": "playbook_grounded",
            "playbook_rule_id": playbook_id,
            "playbook_title": content["title"],
            "immediate_actions": content["immediate_actions"],
            "roles": content["roles"],
            "resources": content["resources"],
            "grounding_facts": {
                "incident_type": incident_type,
                "severity": severity,
                "playbook_key": playbook_key,
            },
        }

    return {
        "origin": "improvised",
        "playbook_rule_id": None,
        "reason": f"No approved playbook covers incident type '{incident_type}'",
        "guidance_note": (
            "No pre-approved playbook rule covers this situation. "
            "Provide general emergency-management guidance based on the "
            "incident facts. This guidance will be recorded as improvised."
        ),
        "grounding_facts": {
            "incident_type": incident_type,
            "severity": severity,
        },
    }


def apply_safety_backstop(text: str, incident_type: str) -> str:
    """Append non-negotiable safety lines to active-threat/evacuation output.

    This is CODE post-processing — the model cannot suppress these lines.
    """
    if incident_type not in EVACUATION_TYPES:
        return text

    missing = []
    text_lower = text.lower()
    for line in BACKSTOP_LINES:
        if line.lower() not in text_lower:
            key_phrase = _extract_key_phrase(line)
            if key_phrase not in text_lower:
                missing.append(line)

    if not missing:
        return text

    return text.rstrip() + "\n\n---\n" + "\n".join(missing)


def _extract_key_phrase(line: str) -> str:
    if "911" in line:
        return "call 911"
    if "search for missing" in line.lower():
        return "search for missing"
    if "mobility limitations" in line.lower():
        return "mobility limitations"
    return line.lower()[:40]


def validate_routing_directives(text: str, blocked_zones: list[str]) -> str:
    """Suppress improvised routing directives that route into known blocked zones.

    Scans the text for zone references that match blocked/threat zones and
    replaces the directive with a safety warning. This is deterministic CODE
    validation — not a prompt instruction.
    """
    if not blocked_zones:
        return text

    result = text
    for zone in blocked_zones:
        zone_lower = zone.lower()
        pattern = re.compile(
            rf"(go\s+to|move\s+to|evacuate\s+(?:to|via|through)|proceed\s+to|head\s+to|route\s+(?:to|through|via))\s+[^.]*?\b{re.escape(zone_lower)}\b",
            re.IGNORECASE,
        )
        result = pattern.sub(
            f"[SUPPRESSED — directive routes into blocked zone '{zone}'. Use an alternative route.]",
            result,
        )
    return result


def strip_origin_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Remove origin provenance fields from a UI/transport payload.

    Origin is stored in the audit log / incident record only, never shown
    on occupant/responder-facing surfaces.
    """
    cleaned = dict(payload)
    cleaned.pop("origin", None)
    cleaned.pop("playbook_rule_id", None)
    cleaned.pop("grounding_facts", None)
    cleaned.pop("improvisation_reason", None)

    for key, value in cleaned.items():
        if isinstance(value, dict):
            cleaned[key] = strip_origin_from_payload(value)
        elif isinstance(value, list):
            cleaned[key] = [
                strip_origin_from_payload(item) if isinstance(item, dict) else item
                for item in value
            ]
    return cleaned


def build_provenance_record(
    tactical_context: dict[str, Any],
    incident_id: str,
) -> dict[str, Any]:
    """Build a provenance record for the audit log / incident metadata."""
    origin = tactical_context.get("origin", "unknown")
    record: dict[str, Any] = {
        "incident_id": incident_id,
        "origin": origin,
    }
    if origin == "playbook_grounded":
        record["playbook_rule_id"] = tactical_context.get("playbook_rule_id")
        record["grounding_facts"] = tactical_context.get("grounding_facts", {})
    else:
        record["reason"] = tactical_context.get("reason", "no covering rule")
    return record

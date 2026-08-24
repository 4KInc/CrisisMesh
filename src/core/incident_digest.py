"""A one-message summary of the running incident, for a text channel.

Slack has `/incident status` and the console has a whole panel. SMS and
WhatsApp had nothing: someone on a phone could send information in but could
never find out what was happening. That is the half of "sync" that was missing
— the channels carried reports up and alerts down, but no one on a phone could
ask a question.

Kept short on purpose. This is read on a lock screen, sometimes by someone who
should not be looking at a lit phone for long.
"""

from __future__ import annotations

from src.core import incident_state, observations


def status_line() -> str:
    """Summarise the active incident in one message, or say there isn't one."""
    if not incident_state.is_active():
        return (
            "CrisisMesh: no active incident right now. "
            "If this is an emergency, call 911."
        )

    from src.agents.accountability.tools import compute_accountability_summary

    incident_id = incident_state.get_active_incident_id()
    record = incident_state.get_latest_incident()
    classification = record.get("classification", {}) or {}
    incident_type = (classification.get("incident_type") or "incident").replace("_", " ")
    severity = classification.get("severity", "")
    zone = (record.get("location", {}) or {}).get("zone_name", "")
    summary = compute_accountability_summary(incident_id)

    parts = [f"CrisisMesh status — {incident_type.upper()}"]
    if severity:
        parts[0] += f" ({severity})"
    if zone:
        parts.append(f"Location: {zone}.")

    threat = observations.latest_threat_location(incident_id)
    if threat:
        parts.append(f"Last reported threat position: {threat}.")

    parts.append(
        f"Accounted {summary.get('accounted', 0)}/{summary.get('total_tracked', 0)}, "
        f"unaccounted {summary.get('unaccounted', 0)}."
    )
    parts.append(f"Running {incident_state.elapsed_minutes()} min.")
    parts.append("Reply SAFE, SOS, INJURED or EVACUATED to check in.")
    return " ".join(parts) + f" [{incident_id}]"

"""Witness reports attached to a running incident.

A message arriving during an active incident used to be treated as a brand new
incident declaration, which overwrote the one everyone was coordinating around.
A teacher texting "he's moving toward the gym" during a lockdown destroyed the
lockdown — orphaning every check-in recorded against it — and replaced it with
an unclassified incident whose whole content was that sentence.

That sentence is the most valuable data in the incident. It belongs to the
incident, not instead of it.

Observations are append-only. Nothing here edits or removes an earlier one:
during an incident the record of who said what, and when, is the thing an
after-action review is built from.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

_observations: dict[str, list[dict[str, Any]]] = {}
_lock = threading.Lock()

MAX_PER_INCIDENT = 500


def record(
    incident_id: str,
    text: str,
    source: str = "",
    from_address: str = "",
    person_id: str = "",
    person_name: str = "",
) -> dict[str, Any]:
    """Attach a witness report to an incident."""
    from src.agents.sitrep.tools import extract_threat_observation

    entry = {
        "incident_id": incident_id,
        "text": text.strip(),
        "source": source,
        "from_address": from_address,
        "person_id": person_id,
        "person_name": person_name,
        # Reported, never inferred: this is what a witness said, not where the
        # system believes the threat is.
        "threat_location_reported": extract_threat_observation(text),
        "at": datetime.now(timezone.utc).isoformat(),
    }

    with _lock:
        entries = _observations.setdefault(incident_id, [])
        entries.append(entry)
        if len(entries) > MAX_PER_INCIDENT:
            del entries[:-MAX_PER_INCIDENT]

    logger.info(
        f"Observation on {incident_id} from {person_name or from_address or source}: "
        f"{text[:120]}"
    )
    return entry


def get(incident_id: str) -> list[dict[str, Any]]:
    with _lock:
        return [dict(e) for e in _observations.get(incident_id, [])]


def count(incident_id: str) -> int:
    with _lock:
        return len(_observations.get(incident_id, []))


def latest_threat_location(incident_id: str) -> str:
    """The most recent reported threat position, if a witness gave one."""
    for entry in reversed(get(incident_id)):
        if entry.get("threat_location_reported"):
            return entry["threat_location_reported"]
    return ""


def threat_track(incident_id: str) -> list[dict[str, Any]]:
    """Every reported threat position, oldest first.

    A single "last known location" tells a responder where the threat was. Two
    tell them which way it is moving, which is the difference between arriving
    behind it and arriving in front of it. Reported positions only — never
    inferred, never interpolated between sightings.
    """
    track: list[dict[str, Any]] = []
    for entry in get(incident_id):
        where = entry.get("threat_location_reported")
        if not where:
            continue
        if track and track[-1]["location"].lower() == where.lower():
            continue  # same place reported twice is not movement
        track.append({
            "location": where,
            "at": entry.get("at", ""),
            "source": entry.get("source", ""),
            "reported_by": entry.get("person_name") or entry.get("from_address", ""),
        })
    return track


def reset() -> None:
    with _lock:
        _observations.clear()

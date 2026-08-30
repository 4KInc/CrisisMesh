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
import uuid
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

COLLECTION = "crisismesh_observations"

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

    # Append-only, one document per sighting. No compare-and-set: two witnesses
    # reporting at once are two facts, not a contended write.
    from src.core import durable_store

    if durable_store.backend_name() == durable_store.MEMORY:
        with _lock:
            entries = _observations.setdefault(incident_id, [])
            entries.append(entry)
            if len(entries) > MAX_PER_INCIDENT:
                del entries[:-MAX_PER_INCIDENT]
    else:
        durable_store.put(
            COLLECTION, f"{incident_id}:{entry['at']}:{uuid.uuid4().hex[:8]}", entry)

    logger.info(
        f"Observation on {incident_id} from {person_name or from_address or source}: "
        f"{text[:120]}"
    )
    return entry


def get(incident_id: str) -> list[dict[str, Any]]:
    """Every reported sighting, oldest first.

    Raises `StoreUnavailable` rather than returning [] when the store cannot
    answer. The egress assessment calls a corridor clear because no sighting
    lies on it — built on an empty list that only means "unreadable", that
    sentence points a responder at the threat.
    """
    from src.core import durable_store

    if durable_store.backend_name() == durable_store.MEMORY:
        with _lock:
            return [dict(e) for e in _observations.get(incident_id, [])]
    rows = durable_store.query(COLLECTION, "incident_id", incident_id, order_by="at")
    return rows[-MAX_PER_INCIDENT:]


def count(incident_id: str) -> int:
    return len(get(incident_id))


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

    # The declaration is a sighting too, and it is the first one. Without it a
    # trail reads "gym -> cafeteria" for an incident that opened with "shooter
    # in the east wing", losing the point the threat started from.
    from src.core import incident_state

    record = incident_state.get_latest_incident()
    if record.get("incident_id") == incident_id:
        from src.agents.sitrep.tools import extract_threat_observation

        first = extract_threat_observation(record.get("report", ""))
        if first:
            origin = incident_state.get_origin()
            track.append({
                "location": first,
                "at": _started_at_iso(origin.get("started_at", 0.0)),
                "source": origin.get("source", ""),
                "reported_by": "initial report",
            })

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


def _started_at_iso(started_at: float) -> str:
    if not started_at:
        return ""
    return datetime.fromtimestamp(started_at, tz=timezone.utc).isoformat()


def clear(incident_id: str) -> None:
    """Drop one incident's sightings. Used when an incident is stood down."""
    from src.core import durable_store

    if durable_store.backend_name() == durable_store.MEMORY:
        with _lock:
            _observations.pop(incident_id, None)
        return
    durable_store.delete_where(COLLECTION, "incident_id", incident_id)


def reset() -> None:
    with _lock:
        _observations.clear()

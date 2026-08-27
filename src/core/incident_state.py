"""The active incident — owned by the system, not by any one channel.

An incident can be declared from Slack, SMS, WhatsApp, or the command console,
and every channel needs to read the same answer to "what is happening right
now". This module holds that answer.

It previously lived as module globals inside `services/slack_transport.py`,
which meant the SMS and WhatsApp transports both reached into the Slack module
to find out which incident a check-in belonged to. The state was never Slack's;
only the Block Kit rendering was.

Two things this fixes beyond the move:

  * `started_at` is stamped for every source. It used to be set only by the
    Slack command handler, so an incident declared by text message reported a
    duration of 0 minutes for as long as it ran.
  * Access is serialised. The server is a ThreadingHTTPServer and the agentic
    pipelines run on background threads, so reads and writes genuinely race.

Scope limit worth knowing: this is per-process memory. A second Cloud Run
instance has its own copy, so a Slack declaration on one instance is invisible
to a WhatsApp check-in routed to another. Surviving that needs shared storage
(Firestore is already a dependency), not a different in-process shape.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

_lock = threading.RLock()

_incident_id: str = ""
_record: dict[str, Any] = {}
_source: str = ""
_declared_by: str = ""
_origin_channel: str = ""
_reporter_address: str = ""
_started_at: float = 0.0


ACTIVE_DOC = "active"


def as_document() -> dict[str, Any]:
    """The persisted shape. `active` is an explicit boolean, never inferred.

    The proof pivots on one post-redeploy read: is there a live incident? A
    status derived from "is incident_id truthy" would read ambiguously through
    a store that coerces empty strings, so activeness is stated as its own
    field and asserted to round-trip.
    """
    with _lock:
        return {
            "active": bool(_incident_id and _record),
            "incident_id": _incident_id,
            "record": dict(_record),
            "source": _source,
            "declared_by": _declared_by,
            "origin_channel": _origin_channel,
            "reporter_address": _reporter_address,
            "started_at": float(_started_at),
        }


def from_document(doc: dict[str, Any]) -> None:
    """Restore a persisted incident, or leave nothing active."""
    global _incident_id, _record, _source, _declared_by, _origin_channel, _started_at
    global _reporter_address
    if not doc or not doc.get("active"):
        return
    with _lock:
        _incident_id = doc.get("incident_id", "") or ""
        _record = dict(doc.get("record") or {})
        _source = doc.get("source", "") or ""
        _declared_by = doc.get("declared_by", "") or ""
        _origin_channel = doc.get("origin_channel", "") or ""
        _reporter_address = doc.get("reporter_address", "") or ""
        _started_at = float(doc.get("started_at") or 0.0)


def _persist() -> None:
    """Mirror to the durable backing. Never raises into a declare or a resolve."""
    try:
        from src.core import incident_store
        incident_store.save(as_document())
    except Exception as exc:  # noqa: BLE001 - a persistence failure must not
        # lose the incident in memory; the running response still coordinates.
        logger.error(f"Incident state not persisted ({exc}) — in-memory only")


def rehydrate() -> bool:
    """Pull the active incident back after a restart. True if one was restored."""
    try:
        from src.core import incident_store
        doc = incident_store.load()
    except Exception as exc:  # noqa: BLE001
        logger.error(f"Could not rehydrate incident state ({exc})")
        return False
    if not doc:
        return False
    from_document(doc)
    restored = is_active()
    if restored:
        logger.info(f"Rehydrated incident {get_active_incident_id()} after restart")
    return restored


def declare(
    incident_id: str,
    record: dict[str, Any],
    source: str,
    declared_by: str = "",
    origin_channel: str = "",
    reporter_address: str = "",
) -> None:
    """Make this the active incident, whichever channel it arrived on."""
    global _incident_id, _record, _source, _declared_by, _origin_channel, _started_at
    global _reporter_address
    with _lock:
        _incident_id = incident_id
        _record = {**record, "source": source}
        _source = source
        _declared_by = declared_by
        _origin_channel = origin_channel
        _reporter_address = reporter_address
        _started_at = time.time()
    _persist()


def attach_origin(declared_by: str = "", origin_channel: str = "") -> None:
    """Record who declared it and where, once the channel knows.

    Separate from `declare` because the pipeline runs before the transport has
    finished unpacking its own request. Does not restart the clock.
    """
    global _declared_by, _origin_channel, _reporter_address
    with _lock:
        if declared_by:
            _declared_by = declared_by
        if origin_channel:
            _origin_channel = origin_channel
    _persist()


def attach_reporter(address: str) -> None:
    """Record the handset an incident was reported from.

    Kept beside the origin rather than in the record because it is routing
    information, not part of the report: the fan-out uses it to avoid alerting
    the person who just typed the alert, and the status card uses it to name a
    declarer that has no Slack account to mention. It is never rendered as a
    number.
    """
    global _reporter_address
    if not address:
        return
    with _lock:
        _reporter_address = address
    _persist()


def set_latest_incident(result: dict[str, Any], source: str = "web") -> None:
    """Record a pipeline result as the active incident.

    An identity-less result never becomes the active incident. The agentic
    background run finishes ~40s after the deterministic one and hands back a
    dict with no `incident_id`, which used to be declared anyway — wiping the
    live incident. In memory that lost the console's incident silently; against
    a durable store it persisted `active: False` over a running emergency.

    A result that names the incident already active enriches it instead of
    restarting its clock.
    """
    incident_id = (result.get("incident_id") or "").strip()
    if not incident_id:
        current = get_active_incident_id()
        if current:
            logger.info(
                f"Ignoring an identity-less {source} result while {current} is "
                "active — it cannot replace a live incident"
            )
            return
        logger.info(f"Ignoring an identity-less {source} result; nothing to declare")
        return

    if incident_id == get_active_incident_id():
        enrich(result)
        return

    declare(incident_id, result, source)


def enrich(result: dict[str, Any]) -> None:
    """Merge a later result into the running incident without restarting it."""
    global _record
    with _lock:
        if not _incident_id:
            return
        _record = {**_record, **result, "source": _source}
    _persist()


def get_active_incident_id() -> str:
    with _lock:
        return _incident_id


def get_latest_incident() -> dict[str, Any]:
    with _lock:
        return dict(_record)


def is_active() -> bool:
    """True when there is an incident for a check-in to belong to."""
    with _lock:
        return bool(_incident_id and _record)


def get_origin() -> dict[str, Any]:
    with _lock:
        return {
            "source": _source,
            "declared_by": _declared_by,
            "origin_channel": _origin_channel,
            "reporter_address": _reporter_address,
            "started_at": _started_at,
        }


def elapsed_minutes() -> int:
    """Whole minutes since declaration. 0 when nothing is active."""
    with _lock:
        return int((time.time() - _started_at) / 60) if _started_at else 0


def clear() -> dict[str, Any]:
    """End the incident. Returns what it was, so the caller can report on it."""
    global _incident_id, _record, _source, _declared_by, _origin_channel, _started_at
    global _reporter_address
    with _lock:
        previous = {
            "incident_id": _incident_id,
            "record": dict(_record),
            "source": _source,
            "declared_by": _declared_by,
            "origin_channel": _origin_channel,
            "reporter_address": _reporter_address,
            "started_at": _started_at,
            "elapsed_minutes": int((time.time() - _started_at) / 60) if _started_at else 0,
        }
        _incident_id = ""
        _record = {}
        _source = ""
        _declared_by = ""
        _origin_channel = ""
        _reporter_address = ""
        _started_at = 0.0
    _persist()
    return previous


def reset() -> None:
    """Forget in memory, without touching the durable record.

    This is what a restart does: the process loses its globals, the store still
    holds the incident. `clear()` is the opposite — resolving an incident must
    erase the durable record too, or a redeploy resurrects it. Conflating them
    meant a simulated restart also deleted what it was meant to recover from.
    """
    global _incident_id, _record, _source, _declared_by, _origin_channel, _started_at
    global _reporter_address
    with _lock:
        _incident_id = ""
        _record = {}
        _source = ""
        _declared_by = ""
        _origin_channel = ""
        _reporter_address = ""
        _started_at = 0.0

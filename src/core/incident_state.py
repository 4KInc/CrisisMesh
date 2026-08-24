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

import threading
import time
from typing import Any

_lock = threading.RLock()

_incident_id: str = ""
_record: dict[str, Any] = {}
_source: str = ""
_declared_by: str = ""
_origin_channel: str = ""
_started_at: float = 0.0


def declare(
    incident_id: str,
    record: dict[str, Any],
    source: str,
    declared_by: str = "",
    origin_channel: str = "",
) -> None:
    """Make this the active incident, whichever channel it arrived on."""
    global _incident_id, _record, _source, _declared_by, _origin_channel, _started_at
    with _lock:
        _incident_id = incident_id
        _record = {**record, "source": source}
        _source = source
        _declared_by = declared_by
        _origin_channel = origin_channel
        _started_at = time.time()


def attach_origin(declared_by: str = "", origin_channel: str = "") -> None:
    """Record who declared it and where, once the channel knows.

    Separate from `declare` because the pipeline runs before the transport has
    finished unpacking its own request. Does not restart the clock.
    """
    global _declared_by, _origin_channel
    with _lock:
        if declared_by:
            _declared_by = declared_by
        if origin_channel:
            _origin_channel = origin_channel


def set_latest_incident(result: dict[str, Any], source: str = "web") -> None:
    """Compatibility entry point for callers that hand over a whole result."""
    declare(result.get("incident_id", ""), result, source)


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
            "started_at": _started_at,
        }


def elapsed_minutes() -> int:
    """Whole minutes since declaration. 0 when nothing is active."""
    with _lock:
        return int((time.time() - _started_at) / 60) if _started_at else 0


def clear() -> dict[str, Any]:
    """End the incident. Returns what it was, so the caller can report on it."""
    global _incident_id, _record, _source, _declared_by, _origin_channel, _started_at
    with _lock:
        previous = {
            "incident_id": _incident_id,
            "record": dict(_record),
            "source": _source,
            "declared_by": _declared_by,
            "origin_channel": _origin_channel,
            "started_at": _started_at,
            "elapsed_minutes": int((time.time() - _started_at) / 60) if _started_at else 0,
        }
        _incident_id = ""
        _record = {}
        _source = ""
        _declared_by = ""
        _origin_channel = ""
        _started_at = 0.0
        return previous


def reset() -> None:
    """Clear without reporting (tests, and process start)."""
    clear()

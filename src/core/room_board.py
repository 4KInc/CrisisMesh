"""The classroom board — per-room headcounts reported by teachers.

Distinct from the per-person check-in store in `accountability/tools.py`, and
deliberately so: a teacher reports "room 104: 23 safe, 2 missing" without
knowing which 23. Both numbers matter — the board tells an incident commander
which rooms have not answered at all, which is the fastest way to find where
people still are.

This lived in `slack_transport` as a module-level dict that was never keyed by
incident and never cleared on resolve, so one incident's board carried into the
next. It is keyed by incident here, and the store is shared, so a teacher can
report from WhatsApp and the incident commander can read it in Slack.
"""

from __future__ import annotations

import csv
import io
import logging
import pathlib
import re
import threading
from typing import Any

logger = logging.getLogger(__name__)

_boards: dict[str, dict[str, dict[str, Any]]] = {}
_lock = threading.Lock()

# Rooms with no report at all. A silent room is not an empty room, and the
# estimate below is explicitly an estimate — it must never be presented as a
# headcount someone confirmed.
ASSUMED_ROOM_SIZE = 25

_ROOM_PATTERN = re.compile(r"room\s+(\w+)\s*[:\-–—]\s*(.+)", re.IGNORECASE)


def parse(text: str) -> dict[str, Any] | None:
    """Detect 'room 104: 23 students are safe, 2 are missing, last seen ...'."""
    m = _ROOM_PATTERN.search(text)
    if not m:
        return None

    room = m.group(1)
    body = m.group(2).lower()
    notes = m.group(2).strip()

    safe = 0
    missing = 0
    status = "reported"

    safe_m = re.search(r"(?:all\s+)?(\d+)\s*(?:students?\s+)?(?:are\s+)?safe", body)
    if safe_m:
        safe = int(safe_m.group(1))
        status = "safe"

    miss_m = re.search(r"(\d+)\s*(?:are\s+)?(?:missing|unaccounted)", body)
    if miss_m:
        missing = int(miss_m.group(1))
        status = "partial" if safe > 0 else "missing"

    if "all" in body and "safe" in body and safe == 0:
        all_m = re.search(r"all\s+(\d+)", body)
        if all_m:
            safe = int(all_m.group(1))
            status = "safe"

    return {
        "room": room,
        "safe": safe,
        "missing": missing,
        "status": status,
        "notes": _residual_note(notes),
        "raw": notes,
    }


# Count phrases that have already been parsed into `safe` / `missing`. Repeating
# them in the note wastes the only line a reader may get on a lock screen.
_COUNTED_PHRASES = re.compile(
    r"\b(?:all\s+)?\d+\s*(?:students?\s+)?(?:are\s+)?(?:safe|missing|unaccounted)\b",
    re.IGNORECASE,
)


def _residual_note(notes: str) -> str:
    """What the teacher said beyond the numbers — "last seen in hallway"."""
    residual = _COUNTED_PHRASES.sub("", notes)
    residual = re.sub(r"\s*[,;]\s*", ", ", residual).strip(" ,;.-—")
    return re.sub(r"\s{2,}", " ", residual)


def record(incident_id: str, entry: dict[str, Any], source: str = "") -> dict[str, Any]:
    """Store one room's report against an incident."""
    stored = {**entry, "source": source}
    with _lock:
        _boards.setdefault(incident_id, {})[entry["room"]] = stored
    logger.info(
        f"Room board {incident_id}: room {entry['room']} — "
        f"{entry['safe']} safe, {entry['missing']} missing (via {source or 'unknown'})"
    )
    return stored


def get(incident_id: str) -> dict[str, dict[str, Any]]:
    with _lock:
        return {k: dict(v) for k, v in _boards.get(incident_id, {}).items()}


def clear(incident_id: str) -> None:
    with _lock:
        _boards.pop(incident_id, None)


def reset() -> None:
    with _lock:
        _boards.clear()


def _all_rooms() -> dict[str, str]:
    """room_id -> teacher name, from the seed roster."""
    seed = pathlib.Path(__file__).resolve().parent.parent.parent / "data" / "seed" / "rooms.csv"
    rooms: dict[str, str] = {}
    if not seed.exists():
        return rooms
    for row in csv.DictReader(io.StringIO(seed.read_text())):
        rid = row.get("room_id", "")
        notes = row.get("notes", "")
        rooms[rid] = notes.split(" - ")[-1] if " - " in notes else ""
    return rooms


def summarise(incident_id: str) -> dict[str, Any]:
    """Totals plus the silent-room list, with no formatting attached."""
    reported = get(incident_id)
    all_rooms = _all_rooms()
    silent = [r for r in sorted(all_rooms) if r not in reported]

    return {
        "reported_count": len(reported),
        "total_rooms": len(all_rooms),
        "total_safe": sum(i["safe"] for i in reported.values()),
        "total_missing": sum(i["missing"] for i in reported.values()),
        "silent_rooms": silent,
        "estimated_unaccounted_in_silent_rooms": len(silent) * ASSUMED_ROOM_SIZE,
        "rooms": reported,
        "teachers": all_rooms,
    }


def as_text(incident_id: str, limit: int = 12) -> str:
    """Plain-text board, sized for a phone screen.

    Rooms with people missing come first: on a truncated screen those are the
    lines that change what someone does next.
    """
    s = summarise(incident_id)
    if not s["reported_count"]:
        return (
            f"Classroom board: 0 of {s['total_rooms']} rooms have reported. "
            "Teachers, reply e.g. \"room 104: 23 safe, 2 missing\"."
        )

    rooms = s["rooms"]
    ordered = sorted(rooms.items(), key=lambda kv: (kv[1]["missing"] == 0, kv[0]))

    lines = [f"Board — {s['reported_count']}/{s['total_rooms']} rooms reported."]
    for room, info in ordered[:limit]:
        if info["missing"]:
            lines.append(f"Room {room}: {info['safe']} safe, {info['missing']} MISSING — {info['notes']}")
        else:
            lines.append(f"Room {room}: {info['safe']} safe")
    if len(ordered) > limit:
        lines.append(f"(+{len(ordered) - limit} more rooms reported)")

    lines.append(f"Totals: {s['total_safe']} safe, {s['total_missing']} missing.")
    if s["silent_rooms"]:
        lines.append(
            f"{len(s['silent_rooms'])} rooms have NOT reported "
            f"(~{s['estimated_unaccounted_in_silent_rooms']} students, estimated): "
            + ", ".join(s["silent_rooms"][:10])
            + ("…" if len(s["silent_rooms"]) > 10 else "")
        )
    return "\n".join(lines)

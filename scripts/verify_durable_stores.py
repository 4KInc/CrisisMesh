"""Prove the per-incident stores survive an instance, against real Firestore.

Not a test: it talks to Google. Everything a replaced container used to lose —
the witness log, the room board, the check-in ledger — is written, the
process-local copies are cleared as a container replacement would clear them,
and the data is read back.

    CRISISMESH_DURABLE_STORE=firestore GOOGLE_CLOUD_PROJECT=… \
    python scripts/verify_durable_stores.py
"""

from __future__ import annotations

import sys

from src.agents.accountability import tools as acct
from src.core import durable_store, observations, room_board
from src.core.knowledge_base import init_knowledge_base

INCIDENT = "VERIFY-DURABLE-1"


def main() -> int:
    if durable_store.backend_name() != durable_store.FIRESTORE:
        print("Set CRISISMESH_DURABLE_STORE=firestore to verify durability.")
        return 2

    # Without the roster the denominator falls back to the ledger, and "1 of 1"
    # would hide the thing worth showing.
    init_knowledge_base()

    _cleanup()
    for text in ["shooter spotted in the east wing",
                 "shooter last seen heading toward the gym",
                 "shooter now near the cafeteria"]:
        observations.record(INCIDENT, text, source="whatsapp", person_name="Mrs. Rodriguez")
    room_board.record(INCIDENT, {"room": "104", "safe": 23, "missing": 1, "notes": ""})
    room_board.record(INCIDENT, {"room": "104", "safe": 24, "missing": 0, "notes": ""})
    acct.process_checkin(INCIDENT, "p001", "safe")

    # The container is replaced: every process-local copy goes.
    observations.reset()
    room_board.reset()
    acct._checkin_store.clear()

    trail = [t["location"] for t in observations.threat_track(INCIDENT)]
    board = room_board.get(INCIDENT)
    summary = acct.compute_accountability_summary(INCIDENT)

    print(f"  threat trail    : {' -> '.join(trail)}")
    print(f"  room board      : room 104 = {board['104']['safe']} safe (last write wins)")
    print(f"  check-in ledger : {summary['accounted']} accounted of "
          f"{summary['total_tracked']} (readable: {summary['ledger_readable']})")

    ok = (trail == ["east wing", "gym", "cafeteria"]
          and board["104"]["safe"] == 24
          and summary["accounted"] == 1
          and summary["total_tracked"] == 34)
    _cleanup()
    if not ok:
        print("FAIL: something did not survive the instance boundary")
        return 1
    print("\n  Survives instance replacement: verified.")
    return 0


def _cleanup() -> None:
    observations.clear(INCIDENT)
    room_board.clear(INCIDENT)
    durable_store.delete_where(acct.COLLECTION, "incident_id", INCIDENT)


if __name__ == "__main__":
    sys.exit(main())

"""Durable backing for the active incident.

`incident_state` is authoritative and reconciliation state is rebuilt from it,
so this is one document, written on declare and on resolve. That shape is
deliberate: there is no cross-store transaction, and requiring incident state
and reconciliation state to be written atomically together would create a
partial-failure window needing a repair path. Making the incident authoritative
and reconciliation lazy turns that window into a case that is correct without
special handling — a restart between declare and first tick just means the
first tick builds reconciliation state fresh from the surviving incident.

Backend selection mirrors the reconciliation store: memory by default, an
unknown value degrades to memory and says so.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any

logger = logging.getLogger(__name__)

MEMORY = "memory"
FIRESTORE = "firestore"
COLLECTION = "crisismesh_incident"
DOCUMENT = "active"
CALL_DEADLINE_SECONDS = 5.0

_backend: str | None = None
_memory_doc: dict[str, Any] = {}
_lock = threading.RLock()


def backend_name() -> str:
    global _backend
    with _lock:
        if _backend is not None:
            return _backend
        raw = (os.environ.get("CRISISMESH_INCIDENT_STORE")
               or os.environ.get("CRISISMESH_RECONCILIATION_STORE")
               or MEMORY).strip().lower()
        if raw in (MEMORY, FIRESTORE):
            _backend = raw
        else:
            _backend = MEMORY
            logger.error(f"incident store: memory (unknown value {raw!r} -> memory)")
        return _backend


def reset_backend() -> None:
    global _backend
    with _lock:
        _backend = None


def reset() -> None:
    with _lock:
        _memory_doc.clear()


def save(doc: dict[str, Any]) -> None:
    """Write the active-incident document. Raises StoreUnavailable on failure."""
    if backend_name() != FIRESTORE:
        with _lock:
            _memory_doc.clear()
            _memory_doc.update(doc)
        return

    from src.core.reconciliation_store import StoreUnavailable

    try:
        from google.cloud import firestore

        firestore.Client().collection(COLLECTION).document(DOCUMENT).set(
            doc, timeout=CALL_DEADLINE_SECONDS)
    except Exception as exc:  # noqa: BLE001
        raise StoreUnavailable(f"Incident state write failed: {exc}") from exc


def load() -> dict[str, Any]:
    """Read the active-incident document, or {} when there is none."""
    if backend_name() != FIRESTORE:
        with _lock:
            return dict(_memory_doc)

    from src.core.reconciliation_store import StoreUnavailable

    try:
        from google.cloud import firestore

        snap = firestore.Client().collection(COLLECTION).document(DOCUMENT).get(
            timeout=CALL_DEADLINE_SECONDS)
    except Exception as exc:  # noqa: BLE001
        raise StoreUnavailable(f"Incident state read failed: {exc}") from exc
    return snap.to_dict() or {} if snap.exists else {}

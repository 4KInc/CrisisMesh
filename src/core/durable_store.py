"""Documents that outlive the instance that wrote them.

The incident and the reconciliation state machine were already durable. The
witness log, the room board and the WhatsApp session window were not — they were
process-local dictionaries, so replacing a container mid-incident reset the board
under a live emergency, and the deployment was pinned to `--max-instances=1`
because two instances would each have held half the truth.

None of these needs the compare-and-set `reconciliation_store` uses.
Observations are append-only, a room report replaces that room's entry, and a
session window is one timestamp per handset — there is no state machine to
serialise. Adding CAS here would be machinery for the look of it.

What they do need is for a read failure to stay a read failure. Returning `[]`
because Firestore was unreachable is not "nothing was reported", and the egress
assessment consumes exactly that list to decide which corridors carry no
sighting. So reads raise `StoreUnavailable` and the surfaces above say they
could not read rather than printing a confident answer built on an empty list.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any

logger = logging.getLogger(__name__)

MEMORY = "memory"
FIRESTORE = "firestore"
BACKEND_ENV = "CRISISMESH_DURABLE_STORE"

_backend: str | None = None
_client_handle: Any = None
_lock = threading.Lock()

# Memory backend storage: {collection: {doc_id: data}}
_memory: dict[str, dict[str, dict[str, Any]]] = {}


class StoreUnavailable(Exception):
    """The store could not answer. Not the same as answering with nothing."""


def backend_name() -> str:
    """Which backend is active. Raises on a value that is neither.

    A deploy that meant to say `firestore` and typed something else must not
    look identical to one that meant `memory` — that is how a durability claim
    quietly stops being true.
    """
    global _backend
    with _lock:
        if _backend is None:
            raw = (os.environ.get(BACKEND_ENV) or MEMORY).strip().lower()
            if raw not in (MEMORY, FIRESTORE):
                raise ValueError(
                    f"{BACKEND_ENV}={raw!r} is neither {MEMORY!r} nor {FIRESTORE!r}")
            _backend = raw
        return _backend


def reset_backend() -> None:
    """Tests, and after an env change."""
    global _backend, _client_handle
    with _lock:
        _backend = None
        _client_handle = None
        _memory.clear()


def _client() -> Any:
    global _client_handle
    if _client_handle is None:
        from google.cloud import firestore

        _client_handle = firestore.Client()
    return _client_handle


def put(collection: str, doc_id: str, data: dict[str, Any]) -> None:
    """Write one document. Last writer wins, which is what these stores mean."""
    if backend_name() == MEMORY:
        with _lock:
            _memory.setdefault(collection, {})[doc_id] = dict(data)
        return
    try:
        _client().collection(collection).document(doc_id).set(dict(data))
    except Exception as exc:  # noqa: BLE001
        raise StoreUnavailable(f"write to {collection}/{doc_id} failed: {exc}") from exc


def get_doc(collection: str, doc_id: str) -> dict[str, Any] | None:
    if backend_name() == MEMORY:
        with _lock:
            found = _memory.get(collection, {}).get(doc_id)
            return dict(found) if found else None
    try:
        snapshot = _client().collection(collection).document(doc_id).get()
    except Exception as exc:  # noqa: BLE001
        raise StoreUnavailable(f"read of {collection}/{doc_id} failed: {exc}") from exc
    return snapshot.to_dict() if getattr(snapshot, "exists", False) else None


def query(
    collection: str, field: str, value: Any, order_by: str = "",
) -> list[dict[str, Any]]:
    """Every document where `field == value`, optionally ordered.

    Raises rather than returning [] when the store cannot answer: a trail read
    as empty says nobody reported anything, which is a claim.
    """
    if backend_name() == MEMORY:
        with _lock:
            rows = [dict(v) for v in _memory.get(collection, {}).values()
                    if v.get(field) == value]
        if order_by:
            rows.sort(key=lambda r: r.get(order_by, ""))
        return rows
    try:
        from google.cloud.firestore_v1.base_query import FieldFilter

        # Equality filter only, ordered in process. Asking Firestore for
        # `where(...) order_by(...)` needs a composite index, which is one more
        # thing standing between someone cloning this and a working setup — and
        # the per-incident result set is small enough that the sort is free.
        ref = _client().collection(collection).where(
            filter=FieldFilter(field, "==", value))
        rows = [doc.to_dict() for doc in ref.stream()]
        if order_by:
            rows.sort(key=lambda r: r.get(order_by, ""))
        return rows
    except StoreUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001
        raise StoreUnavailable(f"query of {collection} failed: {exc}") from exc


def delete_where(collection: str, field: str, value: Any) -> int:
    if backend_name() == MEMORY:
        with _lock:
            docs = _memory.get(collection, {})
            doomed = [k for k, v in docs.items() if v.get(field) == value]
            for k in doomed:
                docs.pop(k, None)
            return len(doomed)
    try:
        from google.cloud.firestore_v1.base_query import FieldFilter

        ref = _client().collection(collection).where(
            filter=FieldFilter(field, "==", value))
        removed = 0
        for doc in ref.stream():
            _client().collection(collection).document(doc.id).delete()
            removed += 1
        return removed
    except Exception as exc:  # noqa: BLE001
        raise StoreUnavailable(f"delete in {collection} failed: {exc}") from exc


def claim(collection: str, doc_id: str, data: dict[str, Any] | None = None) -> bool:
    """Take a lease on `doc_id`. True for the caller that created it, False for
    everyone after.

    Mutual exclusion between instances, which is the whole reason it exists: a
    scheduler runs in every container, so without this each one runs its own
    tick N and the same person is pinged once per instance.

    Returns True when the store cannot answer. A duplicate ping beats a missed
    one, and the per-person `last_acted_tick` still bounds the blast radius —
    but that is a fallback, not the design.
    """
    if backend_name() == MEMORY:
        with _lock:
            docs = _memory.setdefault(collection, {})
            if doc_id in docs:
                return False
            docs[doc_id] = dict(data or {})
            return True
    try:
        from google.api_core.exceptions import AlreadyExists

        try:
            _client().collection(collection).document(doc_id).create(dict(data or {}))
            return True
        except AlreadyExists:
            return False
    except Exception as exc:  # noqa: BLE001
        logger.error(f"Lease on {collection}/{doc_id} unreadable ({exc}) — proceeding")
        return True

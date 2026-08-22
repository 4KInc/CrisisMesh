"""SMS consent store — A2P 10DLC opt-in / opt-out records.

Twilio's campaign vetting (error codes 30908 / 30909 / 30924 / 30925) requires
that every mobile number CrisisMesh messages has an auditable consent record,
and that STOP / HELP / START behave the way carriers mandate.

Consent lifecycle:
  1. A staff member submits the web opt-in form at GET /sms-optin
     (name + organization + mobile + an UNCHECKED consent checkbox).
  2. POST /sms/optin records a `pending` consent record with the exact
     disclosure text they agreed to, a UTC timestamp, and the source IP.
  3. CrisisMesh sends a double opt-in SMS asking them to reply YES.
  4. Their YES reply promotes the record to `confirmed`.
  5. STOP (or any carrier opt-out keyword) sets `opted_out` and suppresses
     all further outbound SMS to that number.

Records are appended to a JSONL audit file so consent survives a restart and
can be produced during a carrier audit. Path: $CRISISMESH_CONSENT_LOG,
default data/consent/sms_consent.jsonl.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# The exact language shown next to the (unchecked) consent checkbox. Twilio
# reviewers compare the campaign's message_flow against this string, so the
# opt-in page renders it from here rather than duplicating it in HTML.
CONSENT_DISCLOSURE = (
    "I agree to receive emergency coordination and safety check-in text messages "
    "from CrisisMesh at the mobile number provided. Message frequency varies by "
    "incident. Message and data rates may apply. Reply STOP to unsubscribe or "
    "HELP for help. Consent is not a condition of employment, enrollment, or "
    "any purchase."
)
CONSENT_VERSION = "2026-08-21.1"

# Carrier-mandated keywords. These are handled BEFORE incident check-in
# keywords so that a compliance reply is never misread as an emergency status.
OPT_OUT_KEYWORDS = frozenset({
    "stop", "stopall", "unsubscribe", "cancel", "end", "quit", "revoke", "optout",
})
OPT_IN_KEYWORDS = frozenset({"start", "unstop", "yes", "confirm", "join", "optin"})
INFO_KEYWORDS = frozenset({"help", "info"})

STATUS_PENDING = "pending"
STATUS_CONFIRMED = "confirmed"
STATUS_OPTED_OUT = "opted_out"

_records: dict[str, dict[str, Any]] = {}
_lock = threading.Lock()
_loaded = False

# Abuse throttle for the public opt-in form: a confirmation SMS costs money and
# an open endpoint is an SMS-pumping target. Keyed by phone and by source IP.
_recent: dict[str, list[float]] = {}
MAX_PER_PHONE_PER_HOUR = 3
MAX_PER_IP_PER_HOUR = 10
_WINDOW_SECONDS = 3600.0


def normalize_phone(number: str) -> str:
    """Normalize a phone number to E.164-ish form (+1 assumed for 10-digit US)."""
    cleaned = "".join(c for c in number if c.isdigit() or c == "+")
    if not cleaned:
        return ""
    if cleaned.startswith("+"):
        return cleaned
    if len(cleaned) == 10:
        return "+1" + cleaned
    if len(cleaned) == 11 and cleaned.startswith("1"):
        return "+" + cleaned
    return "+" + cleaned


def _consent_log_path() -> str:
    override = os.environ.get("CRISISMESH_CONSENT_LOG", "")
    if override:
        return override
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(root, "data", "consent", "sms_consent.jsonl")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load() -> None:
    """Replay the JSONL audit log into memory (last record per number wins)."""
    global _loaded
    if _loaded:
        return
    _loaded = True
    path = _consent_log_path()
    if not os.path.isfile(path):
        return
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                phone = rec.get("phone", "")
                if phone:
                    _records[phone] = rec
    except OSError as exc:
        logger.error(f"Could not read consent log {path}: {exc}")


def _append(record: dict[str, Any]) -> None:
    path = _consent_log_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    except OSError as exc:
        logger.error(f"Could not append to consent log {path}: {exc}")


def _write(record: dict[str, Any]) -> dict[str, Any]:
    with _lock:
        _load()
        _records[record["phone"]] = record
    _append(record)
    return record


def record_optin(
    phone: str,
    name: str = "",
    organization: str = "",
    source: str = "web_form",
    ip: str = "",
    user_agent: str = "",
) -> dict[str, Any]:
    """Record a web-form opt-in. Starts as `pending` until the SMS YES reply."""
    normalized = normalize_phone(phone)
    record = {
        "phone": normalized,
        "name": name,
        "organization": organization,
        "status": STATUS_PENDING,
        "source": source,
        "consent_text": CONSENT_DISCLOSURE,
        "consent_version": CONSENT_VERSION,
        "ip": ip,
        "user_agent": user_agent[:200],
        "opted_in_at": _now(),
        "confirmed_at": "",
        "opted_out_at": "",
    }
    return _write(record)


def confirm_optin(phone: str, source: str = "sms_reply") -> dict[str, Any]:
    """Promote a pending record to confirmed (the double opt-in YES reply).

    A confirmation from a number with no prior web opt-in still creates a
    confirmed record — an inbound START/YES is itself express consent.
    """
    normalized = normalize_phone(phone)
    with _lock:
        _load()
        existing = dict(_records.get(normalized, {}))
    if not existing:
        existing = {
            "phone": normalized,
            "name": "",
            "organization": "",
            "source": source,
            "consent_text": CONSENT_DISCLOSURE,
            "consent_version": CONSENT_VERSION,
            "ip": "",
            "user_agent": "",
            "opted_in_at": _now(),
        }
    existing["status"] = STATUS_CONFIRMED
    existing["confirmed_at"] = _now()
    existing["confirm_source"] = source
    existing["opted_out_at"] = ""
    return _write(existing)


def record_optout(phone: str, source: str = "sms_reply") -> dict[str, Any]:
    """Record a STOP. Suppresses all further outbound SMS to this number."""
    normalized = normalize_phone(phone)
    with _lock:
        _load()
        existing = dict(_records.get(normalized, {}))
    record = existing or {"phone": normalized, "name": "", "organization": ""}
    record["phone"] = normalized
    record["status"] = STATUS_OPTED_OUT
    record["opted_out_at"] = _now()
    record["optout_source"] = source
    return _write(record)


def get_record(phone: str) -> dict[str, Any]:
    normalized = normalize_phone(phone)
    with _lock:
        _load()
        return dict(_records.get(normalized, {}))


def is_opted_out(phone: str) -> bool:
    return get_record(phone).get("status") == STATUS_OPTED_OUT


def has_consent(phone: str) -> bool:
    """True only for a confirmed double opt-in.

    Required before any CrisisMesh-initiated (proactive) SMS. Replies to a
    message the person just sent us are conversational and do not need this.
    """
    return get_record(phone).get("status") == STATUS_CONFIRMED


def consent_summary() -> dict[str, int]:
    with _lock:
        _load()
        values = list(_records.values())
    return {
        "total": len(values),
        "pending": sum(1 for r in values if r.get("status") == STATUS_PENDING),
        "confirmed": sum(1 for r in values if r.get("status") == STATUS_CONFIRMED),
        "opted_out": sum(1 for r in values if r.get("status") == STATUS_OPTED_OUT),
    }


def allow_optin_attempt(phone: str, ip: str = "") -> bool:
    """Rate-limit public opt-in submissions per phone number and per source IP."""
    now = time.monotonic()
    keys = [(f"phone:{normalize_phone(phone)}", MAX_PER_PHONE_PER_HOUR)]
    if ip:
        keys.append((f"ip:{ip}", MAX_PER_IP_PER_HOUR))

    with _lock:
        for key, limit in keys:
            hits = [t for t in _recent.get(key, []) if now - t < _WINDOW_SECONDS]
            _recent[key] = hits
            if len(hits) >= limit:
                return False
        for key, _ in keys:
            _recent[key].append(now)
    return True


def reset() -> None:
    """Clear the in-memory store (tests)."""
    global _loaded
    with _lock:
        _records.clear()
        _recent.clear()
        _loaded = False

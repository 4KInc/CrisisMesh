"""SMS transport — wires Twilio inbound SMS to the CrisisMesh agent fleet.

Route: POST /sms — Twilio webhook for inbound messages.

Requires: TWILIO_AUTH_TOKEN and TWILIO_PHONE_NUMBER env vars.
Without credentials, the /sms endpoint exists but returns HTTP 503.

How it works:
  1. A human sends an SMS to the CrisisMesh Twilio number
  2. Twilio forwards the message to POST /sms
  3. CrisisMesh classifies it as an incident report or a check-in reply
  4. Incident reports fire the deterministic pipeline
  5. Check-in replies (SAFE, HELP, INJURED, EVACUATED) update accountability
  6. TwiML response acknowledges the message

This is a HUMAN sending a message they would already send.
CrisisMesh does NOT detect or sense incidents.
CrisisMesh coordinates ALONGSIDE 911, never replaces it.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
from base64 import b64encode
from typing import Any
from urllib.parse import urlencode

from src.agents.accountability.tools import process_checkin
from src.core.knowledge_base import KnowledgeBase

logger = logging.getLogger(__name__)

CHECKIN_KEYWORDS: dict[str, str] = {
    "safe": "safe",
    "ok": "safe",
    "evacuated": "evacuated",
    "out": "evacuated",
    "help": "need_help",
    "injured": "injured",
    "hurt": "injured",
}

_phone_to_person: dict[str, str] = {}


def _build_phone_map() -> None:
    """Build phone number → person_id mapping from the knowledge base."""
    if _phone_to_person:
        return
    kb = KnowledgeBase.get()
    for p in kb.personnel:
        phone = p.get("phone", "")
        if phone:
            normalized = phone.replace("-", "").replace(" ", "").replace("(", "").replace(")", "")
            if not normalized.startswith("+"):
                normalized = "+1" + normalized
            _phone_to_person[normalized] = p["person_id"]


def has_twilio_credentials() -> bool:
    return bool(os.environ.get("TWILIO_AUTH_TOKEN"))


def verify_twilio_signature(
    auth_token: str,
    url: str,
    params: dict[str, str],
    signature: str,
) -> bool:
    """Verify a Twilio webhook request signature (HMAC-SHA1).

    Twilio signs requests by sorting POST params, concatenating them to the URL,
    then computing HMAC-SHA1 with the auth token.
    """
    if not auth_token or not signature:
        return False
    data = url + "".join(f"{k}{v}" for k, v in sorted(params.items()))
    computed = b64encode(
        hmac.new(auth_token.encode(), data.encode(), hashlib.sha1).digest()
    ).decode()
    return hmac.compare_digest(computed, signature)


def handle_inbound_sms(
    from_number: str,
    body: str,
    request_url: str = "",
) -> dict[str, Any]:
    """Handle an inbound SMS. Returns a result dict with a TwiML response body.

    Classifies the message as:
      - Check-in reply (single keyword: SAFE, HELP, INJURED, etc.)
      - Incident report (anything else — fires the deterministic pipeline)
    """
    word = body.strip().lower()

    if word in CHECKIN_KEYWORDS:
        return _handle_sms_checkin(from_number, CHECKIN_KEYWORDS[word])

    return _handle_sms_incident(from_number, body)


def _handle_sms_checkin(from_number: str, status: str) -> dict[str, Any]:
    """Process a check-in SMS reply."""
    _build_phone_map()
    normalized = from_number.replace("-", "").replace(" ", "").replace("(", "").replace(")", "")
    if not normalized.startswith("+"):
        normalized = "+1" + normalized

    person_id = _phone_to_person.get(normalized, "")
    if not person_id:
        return {
            "twiml": _twiml_response(
                "You are not registered in CrisisMesh. "
                "If this is an emergency, call 911."
            ),
            "action": "unknown_person",
        }

    from src.services.slack_transport import get_active_incident_id
    incident_id = get_active_incident_id() or "active"
    result = process_checkin(incident_id, person_id, status)

    return {
        "twiml": _twiml_response(
            f"Check-in recorded: {result['name']} — {status}. "
            f"If this is a life-threatening emergency, call 911."
        ),
        "action": "checkin",
        "person_id": person_id,
        "status": status,
    }


def _handle_sms_incident(from_number: str, body: str) -> dict[str, Any]:
    """Process an incident report via SMS."""
    from src.services.slack_transport import run_incident_pipeline

    result = run_incident_pipeline(body, source="sms")

    if result.get("blocked"):
        return {
            "twiml": _twiml_response(
                "Your message was flagged by content safety filters. "
                "If this is a real emergency, call 911."
            ),
            "action": "blocked",
        }

    incident_id = result.get("incident_id", "")
    severity = result.get("classification", {}).get("severity", "")
    incident_type = result.get("classification", {}).get("incident_type", "")

    return {
        "twiml": _twiml_response(
            f"Incident reported: {incident_type} ({severity}). "
            f"ID: {incident_id}. "
            f"CrisisMesh agent fleet is coordinating response. "
            f"Reply SAFE, HELP, INJURED, or EVACUATED to check in. "
            f"If 911 has not been called, do so immediately."
        ),
        "action": "incident",
        "incident_id": incident_id,
    }


def _twiml_response(message: str) -> str:
    """Wrap a message in TwiML <Response><Message> XML."""
    safe_msg = (
        message
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f"<Response><Message>{safe_msg}</Message></Response>"
    )

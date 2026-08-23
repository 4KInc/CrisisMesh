"""SMS transport — wires Twilio inbound SMS to the CrisisMesh agent fleet.

Route: POST /sms — Twilio webhook for inbound messages.

Requires: TWILIO_AUTH_TOKEN and TWILIO_PHONE_NUMBER env vars.
Without credentials, the /sms endpoint exists but returns HTTP 503.

How it works:
  1. A human sends an SMS to the CrisisMesh Twilio number
  2. Twilio forwards the message to POST /sms
  3. CrisisMesh classifies it as an incident report or a check-in reply
  4. Incident reports fire the deterministic pipeline + background agentic fleet
  5. Check-in replies (SAFE, SOS, INJURED, EVACUATED) update accountability
  6. TwiML response acknowledges the message immediately
  7. When the agentic fleet finishes, a follow-up SMS delivers the Gemini SITREP

Carrier-mandated keywords (STOP / HELP / START) are handled first, before any
incident logic, per A2P 10DLC requirements. HELP returns program information —
it is NOT an emergency status. The emergency "I need assistance" check-in is
SOS / NEEDHELP, because HELP is reserved by the carriers.

Outbound SMS uses the Twilio REST API directly via `requests` (no SDK needed),
following the same pattern as anbu-care's transport.py.

This is a HUMAN sending a message they would already send.
CrisisMesh does NOT detect or sense incidents.
CrisisMesh coordinates ALONGSIDE 911, never replaces it.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import os
import threading
from base64 import b64encode
from typing import Any
from urllib.parse import urlencode

from src.agents.accountability.tools import process_checkin
from src.core.knowledge_base import KnowledgeBase
from src.services.sms_consent import (
    INFO_KEYWORDS,
    OPT_IN_KEYWORDS,
    OPT_OUT_KEYWORDS,
    confirm_optin,
    is_opted_out,
    record_optout,
)

logger = logging.getLogger(__name__)

# NOTE: "help" is deliberately absent — it is a carrier-reserved keyword that
# must return program info (see INFO_KEYWORDS). SOS / NEEDHELP carry the
# "I need assistance" check-in status instead.
CHECKIN_KEYWORDS: dict[str, str] = {
    "safe": "safe",
    "ok": "safe",
    "evacuated": "evacuated",
    "out": "evacuated",
    "sos": "need_help",
    "needhelp": "need_help",
    "need help": "need_help",
    "assist": "need_help",
    "injured": "injured",
    "hurt": "injured",
}

# Surfaced in the carrier-mandated HELP reply. Override per deployment.
PUBLIC_BASE_URL = os.environ.get(
    "CRISISMESH_PUBLIC_URL", "https://crisismesh-1031148889398.us-central1.run.app"
).rstrip("/")
SUPPORT_EMAIL = os.environ.get(
    "CRISISMESH_SUPPORT_EMAIL", "heartlinmachado@blockintelai.com"
).strip()
TERMS_URL = f"{PUBLIC_BASE_URL}/sms-terms"
PRIVACY_URL = f"{PUBLIC_BASE_URL}/privacy"

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


def can_send_sms() -> bool:
    """True when outbound SMS credentials are configured."""
    return bool(
        os.environ.get("TWILIO_ACCOUNT_SID")
        and os.environ.get("TWILIO_AUTH_TOKEN")
        and os.environ.get("TWILIO_PHONE_NUMBER")
    )


def send_sms(to_number: str, body: str) -> dict[str, Any]:
    """Send an outbound SMS via the Twilio REST API.

    Uses raw `requests.post` — no Twilio SDK needed. Same pattern as
    anbu-care's transport.py.

    Numbers that have replied STOP are suppressed before the request is made.
    Proactive (CrisisMesh-initiated) broadcasts must additionally check
    `sms_consent.has_consent` first; replies to an inbound message do not.
    """
    if is_opted_out(to_number):
        logger.info(f"Outbound SMS suppressed — {to_number} has opted out")
        return {
            "delivered": False,
            "suppressed": True,
            "detail": "Recipient has opted out of CrisisMesh SMS (replied STOP).",
        }

    account_sid = os.environ.get("TWILIO_ACCOUNT_SID", "")
    auth_token = os.environ.get("TWILIO_AUTH_TOKEN", "")
    from_number = os.environ.get("TWILIO_PHONE_NUMBER", "")

    if not (account_sid and auth_token and from_number):
        return {
            "delivered": False,
            "detail": "Outbound SMS not configured (need TWILIO_ACCOUNT_SID, "
                      "TWILIO_AUTH_TOKEN, TWILIO_PHONE_NUMBER).",
        }

    import requests

    try:
        response = requests.post(
            f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json",
            auth=(account_sid, auth_token),
            data={"From": from_number, "To": to_number, "Body": body},
            timeout=20,
        )
    except Exception as exc:
        logger.error(f"SMS send failed: {exc}")
        return {"delivered": False, "detail": f"transport error: {exc}"[:200]}

    if not response.ok:
        try:
            reason = response.json().get("message", response.text)[:200]
        except Exception:
            reason = response.text[:200]
        return {
            "delivered": False,
            "http_status": response.status_code,
            "detail": f"Twilio rejected: {reason}",
        }

    payload = response.json()
    return {
        "delivered": True,
        "provider_id": payload.get("sid"),
        "http_status": response.status_code,
        "provider_status": payload.get("status"),
        "detail": f"accepted by Twilio for delivery to {to_number}",
    }


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
      - Carrier compliance keyword (STOP / HELP / START) — always handled first
      - Check-in reply (single keyword: SAFE, SOS, INJURED, etc.)
      - Incident report (anything else — fires the deterministic pipeline)
    """
    word = body.strip().lower().strip(".!?")

    compliance = _handle_compliance_keyword(from_number, word)
    if compliance:
        return compliance

    if word in CHECKIN_KEYWORDS:
        return _handle_sms_checkin(from_number, CHECKIN_KEYWORDS[word])

    return _handle_sms_incident(from_number, body)


def _handle_compliance_keyword(from_number: str, word: str) -> dict[str, Any] | None:
    """Handle carrier-mandated STOP / HELP / START keywords.

    Returns None when the message is not a compliance keyword. These take
    precedence over every other classification — A2P 10DLC requires that STOP
    always unsubscribes and HELP always returns program info, even mid-incident.
    """
    if word in OPT_OUT_KEYWORDS:
        record_optout(from_number)
        return {
            "twiml": _twiml_response(
                "You have been unsubscribed from CrisisMesh emergency alerts and "
                "will receive no further messages. Reply START to resubscribe. "
                "In an emergency, call 911."
            ),
            "action": "opt_out",
        }

    if word in OPT_IN_KEYWORDS:
        confirm_optin(from_number)
        return {
            "twiml": _twiml_response(
                "You are subscribed to CrisisMesh emergency coordination alerts "
                "for your organization. Msg frequency varies by incident. "
                "Msg & data rates may apply. Reply HELP for help, STOP to cancel. "
                "In an emergency, call 911."
            ),
            "action": "opt_in",
        }

    if word in INFO_KEYWORDS:
        # Never emit a placeholder or invented support address in the
        # carrier-mandated HELP reply — omit the clause instead.
        # Kept under Twilio's 320-character cap for the registered HELP message,
        # so the campaign registration and the live reply stay identical.
        support = f"Support: {SUPPORT_EMAIL}. " if SUPPORT_EMAIL else ""
        return {
            "twiml": _twiml_response(
                "CrisisMesh emergency coordination alerts. Msg frequency varies "
                "by incident. Msg & data rates may apply. Reply STOP to cancel. "
                f"{support}Terms: {TERMS_URL} In an emergency, call 911."
            ),
            "action": "info",
        }

    return None


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
            f"CrisisMesh: check-in recorded: {result['name']} — {status}. "
            f"If this is a life-threatening emergency, call 911. "
            f"Reply STOP to unsubscribe."
        ),
        "action": "checkin",
        "person_id": person_id,
        "status": status,
    }


def _handle_sms_incident(from_number: str, body: str) -> dict[str, Any]:
    """Process an incident report via SMS.

    Fast ack via TwiML (deterministic), then background agentic fleet.
    When the Gemini fleet finishes, a follow-up SMS delivers the SITREP.
    """
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

    if can_send_sms():
        threading.Thread(
            target=_run_agentic_and_sms,
            args=(from_number, body, incident_id),
            daemon=True,
        ).start()

    return {
        "twiml": _twiml_response(
            f"Incident reported: {incident_type} ({severity}). "
            f"ID: {incident_id}. "
            f"CrisisMesh agent fleet is coordinating response. "
            f"Reply SAFE, SOS, INJURED, or EVACUATED to check in. "
            f"Reply STOP to unsubscribe, HELP for help. "
            f"If 911 has not been called, do so immediately."
        ),
        "action": "incident",
        "incident_id": incident_id,
    }


def _run_agentic_and_sms(to_number: str, report: str, incident_id: str) -> None:
    """Run the Gemini agentic pipeline and send the SITREP as a follow-up SMS."""
    try:
        from src.core.server import _run_agentic
        loop = asyncio.new_event_loop()
        result = loop.run_until_complete(_run_agentic(report))
        loop.close()
    except Exception as e:
        logger.error(f"SMS agentic pipeline failed (deterministic ack already sent): {e}")
        return

    final_text = result.get("final_response", "")
    if not final_text:
        logger.info("SMS agentic pipeline returned no final text — deterministic ack stands")
        return

    delegations = result.get("delegations", 0)
    tool_calls = result.get("tool_calls", 0)

    sms_body = (
        f"CrisisMesh SITREP — {incident_id}\n\n"
        f"{final_text[:1400]}\n\n"
        f"Gemini Fleet: {delegations} delegations, {tool_calls} tool calls.\n"
        f"If 911 has not been called, do so immediately."
    )

    delivery = send_sms(to_number, sms_body)
    if delivery["delivered"]:
        logger.info(f"SMS SITREP sent to {to_number}: {delivery.get('provider_id', '')}")
    else:
        logger.error(f"SMS SITREP delivery failed: {delivery.get('detail', '')}")


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

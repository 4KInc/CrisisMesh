"""WhatsApp transport — wires WhatsApp Business API to the CrisisMesh agent fleet.

Routes:
  GET  /whatsapp — Webhook verification (hub.mode, hub.verify_token, hub.challenge)
  POST /whatsapp — Inbound message webhook

Requires: WHATSAPP_VERIFY_TOKEN, WHATSAPP_APP_SECRET, WHATSAPP_ACCESS_TOKEN,
          WHATSAPP_PHONE_NUMBER_ID env vars.
Without credentials, the /whatsapp endpoint exists but returns HTTP 503.

How it works:
  1. A human sends a WhatsApp message to the CrisisMesh number
  2. Meta forwards the message to POST /whatsapp
  3. CrisisMesh classifies it as an incident report or a check-in reply
  4. Incident reports fire the deterministic pipeline
  5. Check-in replies (SAFE, HELP, INJURED, EVACUATED) update accountability
  6. A WhatsApp reply is sent back via the Cloud API

This is a HUMAN sending a message they would already send.
CrisisMesh does NOT detect or sense incidents.
CrisisMesh coordinates ALONGSIDE 911, never replaces it.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import threading
from typing import Any
from urllib.request import Request, urlopen

from src.agents.accountability.tools import process_checkin
from src.core.knowledge_base import KnowledgeBase

logger = logging.getLogger(__name__)

GRAPH_API_VERSION = "v21.0"

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


def has_whatsapp_credentials() -> bool:
    return bool(
        os.environ.get("WHATSAPP_ACCESS_TOKEN")
        and os.environ.get("WHATSAPP_PHONE_NUMBER_ID")
    )


def verify_webhook_signature(app_secret: str, payload: str, signature: str) -> bool:
    """Verify a WhatsApp webhook signature (HMAC-SHA256).

    Meta sends X-Hub-Signature-256: sha256=<hex_digest>
    """
    if not app_secret or not signature:
        return False
    if not signature.startswith("sha256="):
        return False
    expected = hmac.new(
        app_secret.encode(), payload.encode(), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature[7:])


def verify_webhook_challenge(
    mode: str, token: str, challenge: str,
) -> str | None:
    """Verify the webhook subscription. Returns the challenge if valid, else None."""
    verify_token = os.environ.get("WHATSAPP_VERIFY_TOKEN", "")
    if mode == "subscribe" and token == verify_token and verify_token:
        return challenge
    return None


def extract_messages(payload: dict[str, Any]) -> list[dict[str, str]]:
    """Extract inbound text messages from a WhatsApp webhook payload.

    Returns a list of dicts with 'from' (phone number) and 'body' (message text).
    """
    messages = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            for msg in value.get("messages", []):
                if msg.get("type") == "text":
                    messages.append({
                        "from": msg.get("from", ""),
                        "body": msg.get("text", {}).get("body", ""),
                        "msg_id": msg.get("id", ""),
                    })
    return messages


def handle_inbound_message(
    from_number: str,
    body: str,
) -> dict[str, Any]:
    """Handle an inbound WhatsApp message.

    Classifies the message as:
      - Check-in reply (single keyword: SAFE, HELP, INJURED, etc.)
      - Incident report (anything else — fires the deterministic pipeline)

    Returns a result dict with 'reply' text and 'action' type.
    """
    word = body.strip().lower()

    if word in CHECKIN_KEYWORDS:
        return _handle_checkin(from_number, CHECKIN_KEYWORDS[word])

    return _handle_incident(from_number, body)


def _handle_checkin(from_number: str, status: str) -> dict[str, Any]:
    _build_phone_map()
    normalized = from_number.replace("-", "").replace(" ", "")
    if not normalized.startswith("+"):
        normalized = "+" + normalized

    person_id = _phone_to_person.get(normalized, "")
    if not person_id:
        return {
            "reply": (
                "You are not registered in CrisisMesh. "
                "If this is an emergency, call 911."
            ),
            "action": "unknown_person",
        }

    from src.services.slack_transport import get_active_incident_id
    incident_id = get_active_incident_id() or "active"
    result = process_checkin(incident_id, person_id, status)

    return {
        "reply": (
            f"Check-in recorded: {result['name']} — {status}. "
            f"If this is a life-threatening emergency, call 911."
        ),
        "action": "checkin",
        "person_id": person_id,
        "status": status,
    }


def _handle_incident(from_number: str, body: str) -> dict[str, Any]:
    from src.services.slack_transport import run_incident_pipeline

    result = run_incident_pipeline(body, source="whatsapp")

    if result.get("blocked"):
        return {
            "reply": (
                "Your message was flagged by content safety filters. "
                "If this is a real emergency, call 911."
            ),
            "action": "blocked",
        }

    incident_id = result.get("incident_id", "")
    severity = result.get("classification", {}).get("severity", "")
    incident_type = result.get("classification", {}).get("incident_type", "")

    return {
        "reply": (
            f"Incident reported: {incident_type} ({severity}). "
            f"ID: {incident_id}. "
            f"CrisisMesh agent fleet is coordinating response. "
            f"Reply SAFE, HELP, INJURED, or EVACUATED to check in. "
            f"If 911 has not been called, do so immediately."
        ),
        "action": "incident",
        "incident_id": incident_id,
    }


def send_reply(to_number: str, text: str) -> bool:
    """Send a WhatsApp text reply via the Cloud API (background-safe)."""
    access_token = os.environ.get("WHATSAPP_ACCESS_TOKEN", "")
    phone_number_id = os.environ.get("WHATSAPP_PHONE_NUMBER_ID", "")
    if not access_token or not phone_number_id:
        logger.info("WhatsApp credentials not set — skipping reply")
        return False

    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{phone_number_id}/messages"
    payload = json.dumps({
        "messaging_product": "whatsapp",
        "to": to_number,
        "type": "text",
        "text": {"body": text},
    }).encode()

    req = Request(url, data=payload, method="POST")
    req.add_header("Authorization", f"Bearer {access_token}")
    req.add_header("Content-Type", "application/json")

    try:
        with urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception as e:
        logger.error(f"Failed to send WhatsApp reply: {e}")
        return False


def send_reply_async(to_number: str, text: str) -> None:
    """Send a WhatsApp reply in a background thread."""
    threading.Thread(
        target=send_reply,
        args=(to_number, text),
        daemon=True,
    ).start()

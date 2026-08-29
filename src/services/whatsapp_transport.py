"""WhatsApp transport — wires WhatsApp Business API to the CrisisMesh agent fleet.

Two providers, selected by CRISISMESH_WHATSAPP_MODE (same shape as anbu-care's
ANBU_WHATSAPP_MODE):

  meta    — Meta Cloud API direct. Inbound on POST /whatsapp as Meta's JSON
            envelope, signed with X-Hub-Signature-256. Outbound via
            graph.facebook.com/{version}/{WHATSAPP_PHONE_NUMBER_ID}/messages.
            Needs WHATSAPP_VERIFY_TOKEN, WHATSAPP_APP_SECRET,
            WHATSAPP_ACCESS_TOKEN, WHATSAPP_PHONE_NUMBER_ID.
  twilio  — Twilio-hosted WhatsApp. Inbound on POST /whatsapp/twilio as a
            form-encoded webhook signed with X-Twilio-Signature — the same shape
            as /sms, so it reuses that signature check. Outbound via the Twilio
            Messages API with a `whatsapp:` prefixed sender. Needs
            TWILIO_ACCOUNT_SID, TWILIO_WHATSAPP_FROM, and either an API key or
            TWILIO_AUTH_TOKEN.
  off     — no transport. The coordination still runs; nothing leaves.

The two inbound routes are deliberately separate. A provider's webhook is only
ever verified by that provider's own signature scheme, so a request can never be
accepted by the wrong check.

Routes:
  GET  /whatsapp        — Meta webhook verification (hub.mode/verify_token/challenge)
  POST /whatsapp        — Meta inbound message webhook
  POST /whatsapp/twilio — Twilio-hosted WhatsApp inbound webhook

Without credentials for the selected mode, the endpoints return HTTP 503.

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
import time
from typing import Any
from urllib.request import Request, urlopen

from src.agents.accountability.tools import process_checkin
from src.core import (
    checkin_policy,
    demo_identity,
    declaration_guard,
    incident_digest,
    incident_queries,
    inbound_router,
    incident_state,
    observations,
)
from src.core.knowledge_base import KnowledgeBase

logger = logging.getLogger(__name__)

GRAPH_API_VERSION = "v21.0"

# WhatsApp is not A2P 10DLC, so "help" is not carrier-reserved here and stays a
# check-in keyword. SOS and the rest are accepted too, so that staff trained on
# the SMS wording — where HELP had to be given up to the carriers — get the same
# result on whichever channel they actually reach for.
CHECKIN_KEYWORDS: dict[str, str] = {
    "safe": "safe",
    "ok": "safe",
    "evacuated": "evacuated",
    "out": "evacuated",
    "help": "need_help",
    "sos": "need_help",
    "needhelp": "need_help",
    "need help": "need_help",
    "assist": "need_help",
    "injured": "injured",
    "hurt": "injured",
}

OFF_MODES = frozenset({"off", "none", "false", ""})

# Meta only permits free-form business-initiated messages within 24 hours of the
# user's last inbound message. Outside it, an approved template is required.
# Tracking the last inbound per number is what lets the notifier tell the
# difference between "we may message this person" and "we may not".
SESSION_WINDOW_SECONDS = 24 * 60 * 60
_last_inbound: dict[str, float] = {}

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

    # A demo handset resolves to a roster person from the environment, so a
    # real phone number never has to be committed to the seed data.
    demo_identity.apply_to(_phone_to_person)


def whatsapp_mode() -> str:
    """The configured provider: "meta", "twilio", or "off".

    Defaults to meta, which is what this deployment used before the Twilio
    sender existed. An explicitly empty value means off rather than silently
    inheriting whatever the ambient environment happens to say.
    """
    raw = os.environ.get("CRISISMESH_WHATSAPP_MODE")
    if raw is None:
        return "meta"
    value = raw.strip().lower()
    return "off" if value in OFF_MODES else value


def has_whatsapp_credentials() -> bool:
    """True when the SELECTED provider is configured. Mode off is never ready."""
    mode = whatsapp_mode()
    if mode == "twilio":
        from src.services.sms_transport import _twilio_auth
        return bool(_twilio_auth() and os.environ.get("TWILIO_WHATSAPP_FROM"))
    if mode == "meta":
        return bool(
            os.environ.get("WHATSAPP_ACCESS_TOKEN")
            and os.environ.get("WHATSAPP_PHONE_NUMBER_ID")
        )
    return False


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


def note_inbound(from_number: str) -> None:
    """Open (or re-open) this number's 24-hour free-form window."""
    from src.services.sms_consent import normalize_phone
    normalized = normalize_phone(from_number)
    if normalized:
        _last_inbound[normalized] = time.time()


def in_session_window(phone: str) -> bool:
    """True when Meta still permits a free-form message to this number."""
    from src.services.sms_consent import normalize_phone
    last = _last_inbound.get(normalize_phone(phone), 0.0)
    return bool(last) and (time.time() - last) < SESSION_WINDOW_SECONDS


def reset_session_windows() -> None:
    """Tests."""
    _last_inbound.clear()


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
    body = declaration_guard.strip_command_prefix(body)
    note_inbound(from_number)
    word = body.strip().lower()

    if word in CHECKIN_KEYWORDS:
        return _handle_checkin(from_number, CHECKIN_KEYWORDS[word])

    action, payload = inbound_router.route(body)

    if action == inbound_router.ACTION_STATUS:
        return {"reply": incident_digest.status_line(), "action": "status"}

    if action == inbound_router.ACTION_OBSERVATION:
        # A question or a room report is answered, not filed as an observation.
        reply = incident_queries.answer(payload, source=from_number)
        if reply is not None:
            return {"reply": reply, "action": "query"}
        return _handle_observation(from_number, payload)

    return _handle_incident(from_number, payload)


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

    if not checkin_policy.can_accept():
        checkin_policy.log_refusal("whatsapp", status, from_number)
        return {
            "reply": checkin_policy.refusal_message(status),
            "action": "no_active_incident",
            "person_id": person_id,
            "status": status,
        }

    incident_id = incident_state.get_active_incident_id()
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


def _handle_observation(from_number: str, body: str) -> dict[str, Any]:
    # An off-topic message during an incident is noise in the log an after-action
    # review is built from, so it is declined here too — not just at declaration.
    allowed, reason = declaration_guard.is_plausible_report(body)
    if not allowed:
        declaration_guard.log_refusal("whatsapp", from_number, body, reason)
        return {
            "reply": "That was not logged against the incident — it "
                     f"{reason}. " + incident_digest.status_line(),
            "action": "not_logged",
        }

    """Attach a witness report to the running incident and answer with status.

    This used to declare a new incident, silently replacing the one in
    progress. Nothing a witness sends should be able to do that.
    """
    incident_id = incident_state.get_active_incident_id()
    _build_phone_map()
    normalized = from_number.replace("-", "").replace(" ", "")
    if not normalized.startswith("+"):
        normalized = "+" + normalized
    person_id = _phone_to_person.get(normalized, "")
    kb = KnowledgeBase.get()
    person = kb.get_person(person_id) if person_id else None

    observations.record(
        incident_id, body, source="whatsapp", from_address=from_number,
        person_id=person_id, person_name=person["name"] if person else "",
    )

    return {
        "reply": "Noted and added to the incident log. " + incident_digest.status_line(),
        "action": "observation",
        "incident_id": incident_id,
    }


def _handle_incident(from_number: str, body: str) -> dict[str, Any]:
    allowed, reason = declaration_guard.is_plausible_report(body)
    if not allowed:
        declaration_guard.log_refusal("whatsapp", from_number, body, reason)
        return {
            "reply": declaration_guard.refusal_message(reason),
            "action": "not_an_incident",
        }

    from src.services.slack_transport import run_incident_pipeline

    result = run_incident_pipeline(body, source="whatsapp", reporter_address=from_number)

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
            f"Reply SAFE, SOS, INJURED, or EVACUATED to check in. "
            f"If 911 has not been called, do so immediately."
        ),
        "action": "incident",
        "incident_id": incident_id,
    }


def send_whatsapp(to_number: str, text: str) -> dict[str, Any]:
    """Send a WhatsApp message via the configured provider.

    Same honesty rule as the SMS transport: `delivered` is True only when a
    provider actually accepted the message. Mode off, a missing credential, a
    timeout, a rejected request and a terminal provider status all report
    `delivered: False` with a reason.
    """
    mode = whatsapp_mode()
    if mode == "off":
        return {
            "delivered": False,
            "channel": "off",
            "outcome": "suppressed",
            "detail": "CRISISMESH_WHATSAPP_MODE is off, so no message left the "
                      "platform. The coordination above is real; the delivery is not.",
        }
    if mode == "twilio":
        return _send_via_twilio(to_number, text)
    return _send_via_meta(to_number, text)


def _send_via_meta(to_number: str, text: str) -> dict[str, Any]:
    access_token = os.environ.get("WHATSAPP_ACCESS_TOKEN", "")
    phone_number_id = os.environ.get("WHATSAPP_PHONE_NUMBER_ID", "")
    if not access_token or not phone_number_id:
        logger.info("WhatsApp Cloud API credentials not set — nothing sent")
        return {
            "delivered": False,
            "channel": "meta",
            "outcome": "suppressed",
            "detail": "WHATSAPP_ACCESS_TOKEN / WHATSAPP_PHONE_NUMBER_ID are not "
                      "set; nothing was sent.",
        }

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
            ok = resp.status == 200
            return {
                "delivered": ok,
                "outcome": "accepted" if ok else "rejected",
                "channel": "meta",
                "http_status": resp.status,
                "detail": (f"accepted by Meta for delivery to {to_number}. Handset "
                           "confirmation would arrive over a webhook this deployment "
                           "does not run — so this is acceptance, not receipt.")
                if ok else f"Meta returned HTTP {resp.status}; nothing was delivered.",
            }
    except Exception as e:
        logger.error(f"Failed to send WhatsApp reply: {e}")
        return {
            "delivered": False,
            "channel": "meta",
            "outcome": "unknown",
            "detail": f"send outcome unknown, the call did not complete: {type(e).__name__}: {e}"[:200],
        }


def _send_via_twilio(to_number: str, text: str) -> dict[str, Any]:
    """Send through Twilio-hosted WhatsApp.

    Reuses the SMS transport's auth resolution and terminal-state handling —
    it is the same REST endpoint, differing only in the `whatsapp:` prefix on
    both addresses.
    """
    from src.services.sms_transport import TERMINAL_FAILURE, _env, _twilio_auth

    account_sid = _env("TWILIO_ACCOUNT_SID")
    auth = _twilio_auth()
    sender = _env("TWILIO_WHATSAPP_FROM")

    if not (auth and sender):
        return {
            "delivered": False,
            "channel": "twilio",
            "outcome": "suppressed",
            "detail": "Twilio WhatsApp not configured (need TWILIO_ACCOUNT_SID plus "
                      "either TWILIO_API_KEY_SID/SECRET or TWILIO_AUTH_TOKEN, and "
                      "TWILIO_WHATSAPP_FROM); nothing was sent.",
        }

    import requests

    def _prefixed(number: str) -> str:
        return number if number.startswith("whatsapp:") else f"whatsapp:{number}"

    try:
        response = requests.post(
            f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json",
            auth=auth,
            data={
                "From": _prefixed(sender),
                "To": _prefixed(to_number),
                "Body": text,
            },
            timeout=20,
        )
    except Exception as exc:
        logger.error(f"WhatsApp send failed: {exc}")
        return {
            "delivered": False,
            "channel": "twilio",
            "outcome": "unknown",
            "detail": f"send outcome unknown, the call did not complete: "
                      f"{type(exc).__name__}: {exc}"[:200],
        }

    if not response.ok:
        try:
            reason = response.json().get("message", response.text)[:200]
        except Exception:
            reason = response.text[:200]
        return {
            "delivered": False,
            "channel": "twilio",
            "outcome": "rejected",
            "http_status": response.status_code,
            "detail": f"Twilio rejected the message, nothing was delivered: {reason}",
        }

    payload = response.json()
    status = payload.get("status")
    if status in TERMINAL_FAILURE:
        return {
            "delivered": False,
            "outcome": "rejected",
            "channel": "twilio",
            "provider_id": payload.get("sid"),
            "http_status": response.status_code,
            "provider_status": status,
            "detail": (f"Twilio accepted the request but the message is {status}; "
                       f"it did not reach {to_number}. "
                       f"{payload.get('error_message') or ''}").strip(),
        }

    return {
        "delivered": True,
        "outcome": "accepted",
        "channel": "twilio",
        "provider_id": payload.get("sid"),
        "http_status": response.status_code,
        "provider_status": status,
        "detail": (f"accepted by Twilio for delivery to {to_number} "
                   f"(status: {status}). Handset confirmation would arrive over a "
                   "status callback, which this deployment does not run — so this "
                   "is acceptance, not receipt."),
    }


def send_reply(to_number: str, text: str) -> bool:
    """Send a WhatsApp text reply (background-safe). True only on acceptance."""
    return bool(send_whatsapp(to_number, text).get("delivered"))


def send_reply_async(to_number: str, text: str) -> None:
    """Send a WhatsApp reply in a background thread."""
    threading.Thread(
        target=send_reply,
        args=(to_number, text),
        daemon=True,
    ).start()

def process_inbound_async(from_number: str, body: str) -> None:
    """Run the pipeline off the webhook thread and reply over the REST API.

    Twilio allows a webhook 15 seconds. Classifying a report, calling a model
    and writing Firestore inside that budget is a bet, and losing it is not a
    slow reply — Twilio records error 11200 and the message is gone. That is
    what happened to a witness reporting a shooter's position: Twilio had the
    message, the service had no request for it at all.

    Nothing about the reply needed the webhook response. Every other message
    this system sends already goes out through the REST API; only the inbound
    acknowledgement was riding on the work finishing first.
    """
    def _run() -> None:
        try:
            result = handle_inbound_message(from_number, body)
            reply = (result or {}).get("reply", "")
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Inbound processing failed for {from_number}: {exc}")
            # Silence would leave someone who just reported a shooter's position
            # believing nobody heard them.
            reply = ("CrisisMesh could not process that message. If this is an "
                     "emergency, call 911.")
        if reply:
            send_reply_async(from_number, reply)

    threading.Thread(target=_run, daemon=True).start()


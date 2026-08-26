"""SMS transport — wires Twilio inbound SMS to the CrisisMesh agent fleet.

Route: POST /sms — Twilio webhook for inbound messages.

Credentials (transport pattern ported from anbu-care's comms/transport.py):
  Inbound  — TWILIO_AUTH_TOKEN signs the webhook; without it /sms returns 503.
  Outbound — TWILIO_ACCOUNT_SID + TWILIO_PHONE_NUMBER, plus either
             TWILIO_API_KEY_SID/TWILIO_API_KEY_SECRET (preferred: revocable on
             its own) or TWILIO_AUTH_TOKEN.
  CRISISMESH_SMS_MODE=off is an explicit kill switch — having credentials is
  not consent to send, and a drill must not page a real roster.

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
following the same pattern as anbu-care's transport.py — including its honesty
rule: `delivered` is True only when Twilio accepted the message. A 2xx carrying
a terminal status, a missing credential, a timeout, and a rejected request all
report `delivered: False` with a reason.

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

# A 2xx from the Messages create call is not success on its own: Twilio can
# return one of these terminal states in the same body. Reporting a send that
# did not happen is the one failure a crisis system cannot afford.
TERMINAL_FAILURE = frozenset({"failed", "undelivered", "canceled"})

# What the caller is entitled to know. `delivered: False` collapsed six return
# paths into one flag, and the accompanying "nothing was sent" asserted more
# than the code knew: a request that left the process and then timed out may
# well have arrived.
#
#   ACCEPTED   the provider took it. The only outcome that counts as a ping.
#   REJECTED   we know it did not go — a refusal, or a 2xx carrying a terminal
#              status. The person is still owed a ping.
#   UNKNOWN    the call did not complete. It may or may not have arrived, and
#              from inside this process those are indistinguishable. Retry,
#              because missed is worse than duplicate — but record `unknown`,
#              not `failed`, or a later delivery receipt will contradict us.
#   SUPPRESSED a decision, not a failure. Opted out, switched off, or not
#              configured. Never retried: re-chasing would be the system
#              arguing with someone who said STOP.
OUTCOME_ACCEPTED = "accepted"
OUTCOME_REJECTED = "rejected"
OUTCOME_UNKNOWN = "unknown"
OUTCOME_SUPPRESSED = "suppressed"

RETRYABLE_OUTCOMES = frozenset({OUTCOME_REJECTED, OUTCOME_UNKNOWN})

# Explicit off switch. Credentials being present is not consent to send —
# a drill or a replayed incident must not page a real roster.
OFF_MODES = frozenset({"off", "none", "false", ""})

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

    # A demo handset resolves to a roster person from the environment, so a
    # real phone number never has to be committed to the seed data.
    demo_identity.apply_to(_phone_to_person)


def _env(name: str) -> str:
    """Secrets come from the environment, never from a literal in this file."""
    return (os.environ.get(name) or "").strip()


def _twilio_auth() -> tuple[str, str] | None:
    """HTTP Basic credentials for the REST API, preferring an API key.

    An API key can be revoked on its own; the account auth token cannot be
    rotated without breaking webhook signature verification at the same time.
    Either way the URL carries the Account SID — only the username changes.
    Returns None when nothing usable is configured.
    """
    account = _env("TWILIO_ACCOUNT_SID")
    if not account:
        return None
    key_sid, key_secret = _env("TWILIO_API_KEY_SID"), _env("TWILIO_API_KEY_SECRET")
    if key_sid and key_secret:
        return key_sid, key_secret
    token = _env("TWILIO_AUTH_TOKEN")
    return (account, token) if token else None


def sms_mode() -> str:
    """The configured outbound mode: "twilio" or "off".

    An explicitly empty CRISISMESH_SMS_MODE means off rather than silently
    inheriting whatever the ambient environment happens to say.
    """
    raw = os.environ.get("CRISISMESH_SMS_MODE")
    if raw is None:
        return "twilio"
    return "off" if raw.strip().lower() in OFF_MODES else raw.strip().lower()


def has_twilio_credentials() -> bool:
    return bool(_env("TWILIO_AUTH_TOKEN"))


def can_send_sms() -> bool:
    """True when outbound SMS is configured AND not switched off."""
    return bool(
        sms_mode() == "twilio"
        and _twilio_auth()
        and _env("TWILIO_PHONE_NUMBER")
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
            "outcome": OUTCOME_SUPPRESSED,
            "suppressed": True,
            "detail": "Recipient has opted out of CrisisMesh SMS (replied STOP).",
        }

    if sms_mode() == "off":
        return {
            "delivered": False,
            "outcome": OUTCOME_SUPPRESSED,
            "detail": "CRISISMESH_SMS_MODE is off, so no message left the platform. "
                      "The coordination above is real; the delivery is not.",
        }

    account_sid = _env("TWILIO_ACCOUNT_SID")
    auth = _twilio_auth()
    from_number = _env("TWILIO_PHONE_NUMBER")

    if not (auth and from_number):
        return {
            "delivered": False,
            "outcome": OUTCOME_SUPPRESSED,
            "detail": "Outbound SMS not configured (need TWILIO_ACCOUNT_SID plus "
                      "either TWILIO_API_KEY_SID/SECRET or TWILIO_AUTH_TOKEN, "
                      "and TWILIO_PHONE_NUMBER); nothing was sent.",
        }

    import requests

    try:
        response = requests.post(
            f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json",
            auth=auth,
            data={"From": from_number, "To": to_number, "Body": body},
            timeout=20,
        )
    except Exception as exc:
        logger.error(f"SMS send failed: {exc}")
        # Not "nothing was sent" — we do not know that. The request may have
        # reached the wire before the call failed.
        return {
            "delivered": False,
            "outcome": OUTCOME_UNKNOWN,
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
            "outcome": OUTCOME_REJECTED,
            "http_status": response.status_code,
            "detail": f"Twilio rejected the message, nothing was delivered: {reason}",
        }

    payload = response.json()
    status = payload.get("status")

    # A 2xx is not delivery. Twilio can return a terminal failure in the very
    # body that came back 201, and an incident commander reading "sent" for a
    # message that was never carried would stop chasing that person.
    if status in TERMINAL_FAILURE:
        return {
            "delivered": False,
            "outcome": OUTCOME_REJECTED,
            "provider_id": payload.get("sid"),
            "http_status": response.status_code,
            "provider_status": status,
            "detail": (f"Twilio accepted the request but the message is {status}; "
                       f"it did not reach {to_number}. "
                       f"{payload.get('error_message') or ''}").strip(),
        }

    return {
        "delivered": True,
        "outcome": OUTCOME_ACCEPTED,
        "provider_id": payload.get("sid"),
        "http_status": response.status_code,
        "provider_status": status,
        "detail": (f"accepted by Twilio for delivery to {to_number} "
                   f"(status: {status}). Handset confirmation would arrive over a "
                   "status callback, which this deployment does not run — so this "
                   "is acceptance, not receipt."),
    }


def public_url(headers: Any, path: str) -> str:
    """The URL Twilio actually signed, not the one this process received.

    Twilio signs the public HTTPS address it posted to. Behind Cloud Run the
    request arrives from a proxy, so the scheme this process sees is http and
    the signature never matches — the check fails closed, which is the safe
    direction, but it fails on every legitimate message too.

    Only the scheme and host are taken from the forwarded headers: the path
    comes from the request itself, so a spoofed header cannot redirect
    verification at a different endpoint.
    """
    scheme = headers.get("X-Forwarded-Proto") or "https"
    host = headers.get("X-Forwarded-Host") or headers.get("Host") or ""
    # Twilio signs the first proto when a chain of proxies appends several.
    scheme = scheme.split(",")[0].strip()
    host = host.split(",")[0].strip()
    return f"{scheme}://{host}{path}"


def verify_twilio_signature(
    auth_token: str,
    url: str,
    params: dict[str, str] | list[tuple[str, str]],
    signature: str,
) -> bool:
    """Verify a Twilio webhook request signature (HMAC-SHA1).

    Twilio signs the full URL concatenated with each POST parameter's name and
    value, sorted by name. This is not hardening in front of some other control
    — the webhook is an unauthenticated write path that can declare an incident,
    so this IS the control.

    Accepts the parameters as ordered pairs so a repeated field verifies the way
    it arrived; a dict is still accepted for callers that have already collapsed
    them. Missing, malformed and well-formed-but-wrong all return the same
    answer.
    """
    if not auth_token or not signature:
        return False
    pairs = list(params.items()) if isinstance(params, dict) else list(params)
    data = url + "".join(f"{k}{v}" for k, v in sorted(pairs, key=lambda kv: kv[0]))
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

    action, payload = inbound_router.route(body)

    if action == inbound_router.ACTION_STATUS:
        return {"twiml": _twiml_response(incident_digest.status_line()), "action": "status"}

    if action == inbound_router.ACTION_OBSERVATION:
        reply = incident_queries.answer(payload, source=from_number)
        if reply is not None:
            return {"twiml": _twiml_response(reply), "action": "query"}
        return _handle_sms_observation(from_number, payload)

    return _handle_sms_incident(from_number, payload)


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

    if not checkin_policy.can_accept():
        checkin_policy.log_refusal("sms", status, from_number)
        return {
            "twiml": _twiml_response(checkin_policy.refusal_message(status)),
            "action": "no_active_incident",
            "person_id": person_id,
            "status": status,
        }

    incident_id = incident_state.get_active_incident_id()
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


def _handle_sms_observation(from_number: str, body: str) -> dict[str, Any]:
    allowed, reason = declaration_guard.is_plausible_report(body)
    if not allowed:
        declaration_guard.log_refusal("sms", from_number, body, reason)
        return {
            "twiml": _twiml_response(
                "That was not logged against the incident — it "
                f"{reason}. " + incident_digest.status_line()
            ),
            "action": "not_logged",
        }

    """Attach a witness report to the running incident and answer with status."""
    incident_id = incident_state.get_active_incident_id()
    _build_phone_map()
    normalized = from_number.replace("-", "").replace(" ", "").replace("(", "").replace(")", "")
    if not normalized.startswith("+"):
        normalized = "+1" + normalized
    person_id = _phone_to_person.get(normalized, "")
    kb = KnowledgeBase.get()
    person = kb.get_person(person_id) if person_id else None

    observations.record(
        incident_id, body, source="sms", from_address=from_number,
        person_id=person_id, person_name=person["name"] if person else "",
    )

    return {
        "twiml": _twiml_response(
            "Noted and added to the incident log. " + incident_digest.status_line()
        ),
        "action": "observation",
        "incident_id": incident_id,
    }


def _handle_sms_incident(from_number: str, body: str) -> dict[str, Any]:
    """Process an incident report via SMS.

    Fast ack via TwiML (deterministic), then background agentic fleet.
    When the Gemini fleet finishes, a follow-up SMS delivers the SITREP.
    """
    allowed, reason = declaration_guard.is_plausible_report(body)
    if not allowed:
        declaration_guard.log_refusal("sms", from_number, body, reason)
        return {
            "twiml": _twiml_response(declaration_guard.refusal_message(reason)),
            "action": "not_an_incident",
        }

    from src.services.slack_transport import run_incident_pipeline

    result = run_incident_pipeline(body, source="sms", reporter_address=from_number)

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


def twiml_response(message: str) -> str:
    """Wrap a message in TwiML <Response><Message> XML.

    Shared with the Twilio-hosted WhatsApp route, which answers the same way.
    """
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


# Kept for the existing internal call sites and tests.
_twiml_response = twiml_response

"""Fan-out — telling everyone else, on whatever channel actually reaches them.

Every outbound message in CrisisMesh used to be a reply: SMS answered the SMS
sender, WhatsApp answered the WhatsApp sender, Slack answered in the channel
where the command was typed. Nobody who had not already spoken heard anything.
For a fire drill that is merely incomplete. For a lockdown it is the whole
point of the system going unmet — the active-threat playbook lists "Send
lockdown alerts" as the Communications Lead's job and "Lockdown notification
system" as a required resource.

This module closes that. It subscribes to INCIDENT_DECLARED and
INCIDENT_RESOLVED and pushes to the roster.

Reachability is decided per person, and it is not a free choice — two of the
three channels are gated by rules outside this codebase:

  SMS       Requires a confirmed double opt-in (sms_consent.has_consent).
            Replying to someone who just texted in is conversational and needs
            nothing; a broadcast is business-initiated and needs consent under
            A2P 10DLC. A number that replied STOP is suppressed by the
            transport regardless.
  WhatsApp  Requires the recipient to have messaged in within 24 hours, or an
            approved Meta template. Templates are not wired, so this reaches
            only people already in an open session window.
  Slack     No consent regime. A workspace member has already agreed to be
            messaged by the workspace's apps.

Anyone with no open channel is UNREACHABLE, and is counted and named as such.
That number is the operationally important one: it is the list of people the
incident commander must reach some other way.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Any

from src.core.knowledge_base import KnowledgeBase
# Imported rather than redefined so the notifier and the safety backstop
# cannot drift apart on which incidents count as a lockdown.
from src.core.tactical_reasoning import LOCKDOWN_TYPES
from src.models.events import EventType

logger = logging.getLogger(__name__)

CHANNEL_SMS = "sms"
CHANNEL_WHATSAPP = "whatsapp"
CHANNEL_SLACK = "slack"

# Order matters. SMS first because it needs no app, no data connection and no
# open session — it is the channel most likely to work in a building people are
# evacuating. WhatsApp second. Slack last: reliable, but a desktop app someone
# has already walked away from.
CHANNEL_PRIORITY = (CHANNEL_SMS, CHANNEL_WHATSAPP, CHANNEL_SLACK)

# During a lockdown every extra message is another buzz in a room where someone
# is hiding. Only these two fan out: the alert that starts it and the all-clear
# that ends it. Anything else waits.
LOCKDOWN_FANOUT_KINDS = frozenset({"declared", "resolved"})

# A report nothing matched. Held back unless the words themselves are urgent.
UNCLASSIFIED_TYPE = "other"
URGENT_SEVERITIES = frozenset({"high", "critical"})

_last_result: dict[str, Any] = {}
_lock = threading.Lock()


@dataclass(frozen=True)
class Reach:
    """How one person can be reached right now, or why they cannot be."""

    person_id: str
    name: str
    channel: str = ""
    address: str = ""
    reason: str = ""

    @property
    def reachable(self) -> bool:
        return bool(self.channel)


@dataclass
class FanOutResult:
    """What actually happened. `notified` counts provider acceptances only."""

    incident_id: str = ""
    kind: str = ""
    notified: int = 0
    failed: int = 0
    unreachable: int = 0
    skipped: int = 0
    by_channel: dict[str, int] = field(default_factory=dict)
    unreachable_people: list[dict[str, str]] = field(default_factory=list)
    failures: list[dict[str, str]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "incident_id": self.incident_id,
            "kind": self.kind,
            "notified": self.notified,
            "failed": self.failed,
            "unreachable": self.unreachable,
            "skipped": self.skipped,
            "by_channel": dict(self.by_channel),
            "unreachable_people": list(self.unreachable_people),
            "failures": list(self.failures),
        }


def _normalize(phone: str) -> str:
    from src.services.sms_consent import normalize_phone
    return normalize_phone(phone)


def resolve_reach(person: dict[str, Any]) -> Reach:
    """Pick the highest-priority channel this person can legitimately receive on."""
    from src.services import whatsapp_transport
    from src.services.sms_consent import has_consent, is_opted_out

    person_id = person.get("person_id", "")
    name = person.get("name", person_id)
    phone = _normalize(person.get("phone", ""))
    slack_id = person.get("slack_user_id", "")

    blockers: list[str] = []

    for channel in CHANNEL_PRIORITY:
        if channel == CHANNEL_SMS:
            if not phone:
                blockers.append("no phone number on the roster")
            elif is_opted_out(phone):
                blockers.append("SMS: opted out")
            elif not has_consent(phone):
                blockers.append("SMS: no confirmed opt-in")
            else:
                return Reach(person_id, name, CHANNEL_SMS, phone)

        elif channel == CHANNEL_WHATSAPP:
            if not phone:
                continue
            if whatsapp_transport.whatsapp_mode() == "off":
                blockers.append("WhatsApp: transport off")
            elif not whatsapp_transport.in_session_window(phone):
                blockers.append("WhatsApp: outside the 24h window, no template")
            else:
                return Reach(person_id, name, CHANNEL_WHATSAPP, phone)

        elif channel == CHANNEL_SLACK:
            if not slack_id:
                blockers.append("no Slack user id on the roster")
            elif not _slack_ready():
                blockers.append("Slack: no bot token")
            else:
                return Reach(person_id, name, CHANNEL_SLACK, slack_id)

    return Reach(person_id, name, reason="; ".join(blockers) or "no channel available")


def _slack_ready() -> bool:
    import os
    from src.services import slack_transport
    return bool(os.environ.get("SLACK_BOT_TOKEN") and slack_transport.WebClient)


def _send(reach: Reach, message: str) -> dict[str, Any]:
    """Deliver on the resolved channel. Never raises."""
    try:
        if reach.channel == CHANNEL_SMS:
            from src.services.sms_transport import send_sms
            return send_sms(reach.address, message)
        if reach.channel == CHANNEL_WHATSAPP:
            from src.services.whatsapp_transport import send_whatsapp
            return send_whatsapp(reach.address, message)
        if reach.channel == CHANNEL_SLACK:
            return _send_slack_dm(reach.address, message)
    except Exception as exc:
        logger.error(f"Fan-out send failed for {reach.person_id}: {exc}")
        return {"delivered": False, "detail": f"{type(exc).__name__}: {exc}"[:200]}
    return {"delivered": False, "detail": f"unknown channel {reach.channel!r}"}


def _send_slack_dm(user_id: str, message: str) -> dict[str, Any]:
    import os
    from src.services import slack_transport

    token = os.environ.get("SLACK_BOT_TOKEN", "")
    if not (token and slack_transport.WebClient):
        return {"delivered": False, "detail": "Slack not configured; nothing was sent."}
    client = slack_transport.WebClient(token=token)
    response = client.chat_postMessage(channel=user_id, text=message)
    ok = bool(response.get("ok"))
    return {
        "delivered": ok,
        "detail": "accepted by Slack" if ok else f"Slack refused: {response.get('error')}",
    }


def should_fan_out(kind: str, incident_type: str, severity: str = "") -> bool:
    """Whether this update is worth a buzz on every phone.

    Three rules, in order:

      * Inside a lockdown each message is a sound in a room where someone is
        hiding, so only the alert and the all-clear go out.
      * An unclassified report is one the keyword tables did not recognise. A
        wrong number or a typo must not page a roster — but "I cannot name this
        emergency" is not "there is no emergency", so an unclassified report
        that still reads as high or critical goes out anyway.
      * Everything else fans out; outside a lockdown more information is better.

    An operator who genuinely needs to push something else can call `fan_out`.
    """
    if incident_type in LOCKDOWN_TYPES:
        return kind in LOCKDOWN_FANOUT_KINDS
    if incident_type == UNCLASSIFIED_TYPE:
        return severity in URGENT_SEVERITIES
    return True


def fan_out(
    message: str,
    incident_id: str = "",
    kind: str = "alert",
    exclude: tuple[str, ...] = (),
) -> FanOutResult:
    """Push one message to every reachable person on the roster.

    `exclude` holds addresses that already know — normally the reporter, who
    has had a direct reply and does not need the broadcast as well.
    """
    result = FanOutResult(incident_id=incident_id, kind=kind)
    excluded = {_normalize(e) if e.startswith("+") or e.isdigit() else e for e in exclude}

    for person in KnowledgeBase.get().personnel:
        reach = resolve_reach(person)

        if reach.reachable and reach.address in excluded:
            result.skipped += 1
            continue

        if not reach.reachable:
            result.unreachable += 1
            result.unreachable_people.append({
                "person_id": reach.person_id,
                "name": reach.name,
                "reason": reach.reason,
            })
            continue

        delivery = _send(reach, message)
        if delivery.get("delivered"):
            result.notified += 1
            result.by_channel[reach.channel] = result.by_channel.get(reach.channel, 0) + 1
        else:
            result.failed += 1
            result.failures.append({
                "person_id": reach.person_id,
                "name": reach.name,
                "channel": reach.channel,
                "detail": str(delivery.get("detail", ""))[:200],
            })

    logger.info(
        f"Fan-out {kind} for {incident_id or 'n/a'}: "
        f"{result.notified} notified, {result.failed} failed, "
        f"{result.unreachable} unreachable, {result.skipped} skipped"
    )
    with _lock:
        _last_result.clear()
        _last_result.update(result.as_dict())
    return result


def compose_alert(record: dict[str, Any]) -> str:
    """The message sent when an incident is declared."""
    classification = record.get("classification", {}) or {}
    incident_type = classification.get("incident_type", "incident")
    if incident_type in LOCKDOWN_TYPES:
        return _compose_lockdown_alert(record)
    return _compose_evacuation_alert(record)


def _compose_evacuation_alert(record: dict[str, Any]) -> str:
    classification = record.get("classification", {}) or {}
    incident_type = classification.get("incident_type", "incident")
    severity = classification.get("severity", "")
    incident_id = record.get("incident_id", "")
    location = (record.get("location", {}) or {}).get("zone_name", "")
    assembly = (record.get("assembly", {}) or {}).get("name", "")

    if incident_type == UNCLASSIFIED_TYPE:
        # No category to name, so quote what was actually reported — a reader
        # can judge it faster than any label we could invent.
        report = (record.get("report", "") or "").strip()
        excerpt = f' Reported: "{report[:140]}".' if report else ""
        parts = ["CrisisMesh ALERT — UNCLASSIFIED INCIDENT"]
        if severity:
            parts[0] += f" ({severity})"
        parts.append(excerpt.strip())
        if location:
            parts.append(f"Location: {location}.")
        parts.append("Reply SAFE, SOS, INJURED or EVACUATED to check in.")
        parts.append("If 911 has not been called, do so immediately.")
        return " ".join(p for p in parts if p) + f" [{incident_id}]"

    parts = [f"CrisisMesh ALERT — {incident_type.replace('_', ' ').upper()}"]
    if severity:
        parts[0] += f" ({severity})"
    if location:
        parts.append(f"Location: {location}.")
    if assembly:
        parts.append(f"Assembly point: {assembly}.")
    parts.append("Reply SAFE, SOS, INJURED or EVACUATED to check in.")
    parts.append("If 911 has not been called, do so immediately.")
    return " ".join(parts) + f" [{incident_id}]"


def _compose_lockdown_alert(record: dict[str, Any]) -> str:
    """The message sent when the threat is a person rather than a hazard.

    Three things make this different from an evacuation alert, and each one is
    a decision rather than a wording preference:

      * It opens with SILENCE YOUR PHONE. This message will itself arrive with
        a buzz — that cannot be prevented, which is exactly why it has to be
        the last one that does. A phone that keeps announcing itself in a dark
        room is a beacon. Lock-screen previews truncate early, so the
        instruction goes where it will be read without unlocking.
      * It carries no assembly point. An assembly point is a named open space
        with a published location; broadcasting "go there" during a shooting
        directs people out of cover toward a predictable gathering.
      * It says reply, not call. Typing is silent; speaking is not.
    """
    classification = record.get("classification", {}) or {}
    incident_id = record.get("incident_id", "")
    location = (record.get("location", {}) or {}).get("zone_name", "")
    where = f" near {location}" if location else ""
    kind = "BOMB THREAT" if classification.get("incident_type") == "bomb_threat" else "LOCKDOWN"

    return (
        f"CrisisMesh {kind}. SILENCE YOUR PHONE NOW. "
        f"Active threat reported{where}. Follow your site's active-threat plan: "
        "move away only if you have a route confirmed clear, otherwise lock and "
        "barricade where you are. Do NOT pull the fire alarm. "
        "Call 911 when it is safe to speak. Reply SOS silently if you need help. "
        f"[{incident_id}]"
    )


def compose_all_clear(previous: dict[str, Any]) -> str:
    """The message sent when an incident is resolved."""
    incident_id = previous.get("incident_id", "")
    minutes = previous.get("elapsed_minutes", 0)
    tail = f" Duration: {minutes} min." if minutes else ""

    if previous.get("incident_type") in LOCKDOWN_TYPES:
        # A text must never be what gets someone to open a barricaded door.
        # It can be premature, it can be wrong, and it cannot be verified by
        # the person reading it.
        return (
            f"CrisisMesh ALL CLEAR — incident {incident_id} is resolved.{tail} "
            "Do NOT unlock, move or leave cover on the strength of this message. "
            "Wait for a law-enforcement officer or your incident commander in person."
        )

    return (
        f"CrisisMesh ALL CLEAR — incident {incident_id} is resolved.{tail} "
        "No further check-ins needed. Await instructions from your incident commander."
    )


def announce_incident(record: dict[str, Any], exclude: tuple[str, ...] = ()) -> FanOutResult:
    classification = record.get("classification", {}) or {}
    if not should_fan_out(
        "declared",
        classification.get("incident_type", ""),
        classification.get("severity", ""),
    ):
        return FanOutResult(incident_id=record.get("incident_id", ""), kind="suppressed")
    return fan_out(
        compose_alert(record),
        incident_id=record.get("incident_id", ""),
        kind="declared",
        exclude=exclude,
    )


def announce_resolution(previous: dict[str, Any]) -> FanOutResult:
    if not should_fan_out("resolved", previous.get("incident_type", "")):
        return FanOutResult(incident_id=previous.get("incident_id", ""), kind="suppressed")
    return fan_out(
        compose_all_clear(previous),
        incident_id=previous.get("incident_id", ""),
        kind="resolved",
    )


def get_last_result() -> dict[str, Any]:
    with _lock:
        return dict(_last_result)


def reset() -> None:
    with _lock:
        _last_result.clear()


# ── Event bus wiring ────────────────────────────────────────────────────────

_subscribed = False


def _on_declared(event: Any) -> None:
    """Fan out in the background — a Slack ack must not wait on 34 HTTP calls."""
    from src.core import incident_state

    record = incident_state.get_latest_incident()
    if not record:
        record = {"incident_id": event.incident_id, "classification": event.data or {}}
    reporter = (event.data or {}).get("reporter_address", "")
    threading.Thread(
        target=announce_incident,
        args=(record,),
        kwargs={"exclude": (reporter,) if reporter else ()},
        daemon=True,
    ).start()


def _on_resolved(event: Any) -> None:
    previous = dict(event.data or {})
    previous.setdefault("incident_id", event.incident_id)
    threading.Thread(target=announce_resolution, args=(previous,), daemon=True).start()


def subscribe() -> None:
    """Attach the fan-out to the event bus. Idempotent."""
    global _subscribed
    if _subscribed:
        return
    from src.core.event_bus import EventBus

    bus = EventBus.get()
    bus.subscribe(EventType.INCIDENT_DECLARED, _on_declared)
    bus.subscribe(EventType.INCIDENT_RESOLVED, _on_resolved)
    _subscribed = True
    logger.info("Fan-out subscribed to INCIDENT_DECLARED and INCIDENT_RESOLVED")


def unsubscribe_for_tests() -> None:
    global _subscribed
    _subscribed = False

"""Mapping a real handset to a roster person, without putting it in the repo.

SMS and WhatsApp have no login: the only thing an inbound message carries is a
phone number, so a check-in can only be attributed by looking that number up
against the roster. Demoing therefore means some real handset has to resolve to
a real person.

Editing the seed roster to do that publishes a personal phone number into
version control, permanently. This does the same job from the environment:

    CRISISMESH_DEMO_PHONE_MAP="+15550001111=p001,+15551230000=p004"

Slack ids work the same way:

    CRISISMESH_DEMO_SLACK_MAP="p001=U0BE0A0BWGH,p005=U0BDWBL9NCE"

The seed roster carries placeholders (`U_PRINCIPAL`), which look like ids and
address nobody — so the reachability check counts them unreachable, correctly.
Mapping a few real workspace ids here makes those people genuinely reachable
without putting workspace-specific ids in version control, and without making
*everyone* reachable: the unreachable list stays true, which is the half of the
loop's output an incident commander actually acts on.

or, for the common single-handset case:

    CRISISMESH_DEMO_PHONE=+15550001111
    CRISISMESH_DEMO_PERSON=p001          # defaults to p001

The overrides are applied on top of the roster, so a number present in both
resolves to the override. Nothing here is required in production, where staff
phone numbers legitimately belong on the roster the organisation maintains.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

DEFAULT_DEMO_PERSON = "p001"


def _normalize(phone: str) -> str:
    from src.services.sms_consent import normalize_phone
    return normalize_phone(phone)


def overrides() -> dict[str, str]:
    """phone (E.164) -> person_id, from the environment. Empty when unset."""
    mapping: dict[str, str] = {}

    raw_map = (os.environ.get("CRISISMESH_DEMO_PHONE_MAP") or "").strip()
    for pair in raw_map.replace("^", ",").split(","):
        if "=" not in pair:
            continue
        phone, _, person_id = pair.partition("=")
        phone, person_id = _normalize(phone.strip()), person_id.strip()
        if phone and person_id:
            mapping[phone] = person_id

    single = (os.environ.get("CRISISMESH_DEMO_PHONE") or "").strip()
    if single:
        person_id = (os.environ.get("CRISISMESH_DEMO_PERSON") or DEFAULT_DEMO_PERSON).strip()
        normalized = _normalize(single)
        if normalized:
            mapping[normalized] = person_id

    if mapping:
        # Logged by person, never by number: this file exists to keep a real
        # phone number out of places it does not belong, and a log is one.
        logger.info(
            f"Demo identity override active for {len(mapping)} handset(s) -> "
            f"{', '.join(sorted(set(mapping.values())))}"
        )
    return mapping


def slack_overrides() -> dict[str, str]:
    """person_id -> real Slack user id, from the environment."""
    mapping: dict[str, str] = {}
    raw = (os.environ.get("CRISISMESH_DEMO_SLACK_MAP") or "").strip()
    # `^` as well as `,`: gcloud reserves the comma as its own separator in
    # --update-env-vars, so a multi-pair value has to arrive delimited some
    # other way. Accepting both means the same string works from a shell, a
    # .env file and a Cloud Run flag.
    for pair in raw.replace("^", ",").split(","):
        if "=" not in pair:
            continue
        person_id, _, slack_id = pair.partition("=")
        person_id, slack_id = person_id.strip(), slack_id.strip()
        if person_id and slack_id:
            mapping[person_id] = slack_id
    if mapping:
        logger.info(f"Demo Slack ids mapped for {len(mapping)} roster person(s)")
    return mapping


def phone_for(person_id: str, roster_value: str) -> str:
    """The number to *reach* this person on — the demo handset when mapped.

    `overrides()` answers "which person is this inbound number?", which is what
    a check-in needs. Reaching outward is the same mapping read backwards, and
    it was missing: the fan-out looked up the roster's 555 placeholder, checked
    whether *that* number had an open WhatsApp window, found none, and fell
    through to Slack — so a demo handset that had just messaged in was still
    treated as unreachable on WhatsApp.
    """
    for phone, mapped_person in overrides().items():
        if mapped_person == person_id:
            return phone
    return roster_value


def slack_id_for(person_id: str, roster_value: str) -> str:
    """The id to use for this person — the override when one exists."""
    return slack_overrides().get(person_id, roster_value)


def apply_to(phone_map: dict[str, str]) -> None:
    """Layer the overrides over a roster-built phone map, in place."""
    phone_map.update(overrides())

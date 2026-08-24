"""Mapping a real handset to a roster person, without putting it in the repo.

SMS and WhatsApp have no login: the only thing an inbound message carries is a
phone number, so a check-in can only be attributed by looking that number up
against the roster. Demoing therefore means some real handset has to resolve to
a real person.

Editing the seed roster to do that publishes a personal phone number into
version control, permanently. This does the same job from the environment:

    CRISISMESH_DEMO_PHONE_MAP="+15550001111=p001,+15551230000=p004"

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
    for pair in raw_map.split(","):
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


def apply_to(phone_map: dict[str, str]) -> None:
    """Layer the overrides over a roster-built phone map, in place."""
    phone_map.update(overrides())

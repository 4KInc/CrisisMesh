"""Tools for the Intake & Classification Agent."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from src.core.knowledge_base import KnowledgeBase
from src.models.incident import IncidentType, Severity


PLAYBOOK_MAP: dict[str, str] = {
    IncidentType.FIRE: "playbook-fire-v1",
    IncidentType.ACTIVE_THREAT: "playbook-active-threat-v1",
    IncidentType.SEVERE_WEATHER: "playbook-severe-weather-v1",
    IncidentType.MEDICAL: "playbook-medical-v1",
    IncidentType.FLOOD: "playbook-flood-v1",
    IncidentType.CYBER_RANSOMWARE: "playbook-cyber-ransomware-v1",
    IncidentType.DATA_BREACH: "playbook-data-breach-v1",
    IncidentType.UTILITY_OUTAGE: "playbook-utility-outage-v1",
    IncidentType.HAZMAT: "playbook-hazmat-v1",
    IncidentType.BOMB_THREAT: "playbook-bomb-threat-v1",
}

_TYPE_KEYWORDS: dict[IncidentType, list[str]] = {
    IncidentType.FIRE: ["fire", "smoke", "flames", "burning", "alarm"],
    IncidentType.ACTIVE_THREAT: ["shooter", "weapon", "armed", "threat", "intruder", "gun"],
    IncidentType.SEVERE_WEATHER: ["tornado", "hurricane", "storm", "lightning", "hail"],
    IncidentType.MEDICAL: ["medical", "heart", "seizure", "unconscious", "breathing", "injury"],
    IncidentType.FLOOD: ["flood", "water", "leak", "pipe burst"],
    IncidentType.CYBER_RANSOMWARE: ["ransomware", "encrypted", "ransom", "malware", "locked out"],
    IncidentType.DATA_BREACH: ["breach", "data leak", "exposed", "unauthorized access"],
    IncidentType.UTILITY_OUTAGE: ["power", "outage", "electricity", "gas leak", "water shutoff"],
    IncidentType.HAZMAT: ["chemical", "spill", "hazmat", "toxic", "fumes", "gas"],
    IncidentType.BOMB_THREAT: ["bomb", "explosive", "suspicious package", "detonation"],
}


# Severity signals. Deliberately NOT derived from how many type keywords matched:
# that number measures how confidently the report was classified, and a report
# nobody can categorise is not a mild one. Treating low confidence as low
# severity is how "smoke, students still inside" — one keyword — came back LOW.
_CRITICAL_WORDS = [
    "critical", "multiple", "spreading", "uncontrolled", "mass", "armed",
    "not breathing", "unconscious", "collapsed",
]
_HIGH_WORDS = ["urgent", "serious", "large", "expanding", "trapped"]

# Who is still in harm's way. In a school this is the most decision-relevant
# fact in the message and it previously carried no weight at all.
_OCCUPANCY_WORDS = [
    "still inside", "still in the building", "still in there",
    "students inside", "kids inside", "children inside", "people inside",
    "occupants inside", "can't get out", "cannot get out", "unable to evacuate",
    "unaccounted",
]

# Explicit de-escalation. LOW must be something the reporter said, never
# something inferred from the classifier's own uncertainty.
_LOW_WORDS = [
    "drill", "test", "false alarm", "all clear", "contained", "minor",
    "no injuries", "no one inside", "building empty",
]


def _assess_severity(text_lower: str) -> str:
    """Grade a report by what it says, never by how well it parsed."""
    if any(w in text_lower for w in _LOW_WORDS):
        return Severity.LOW
    if any(w in text_lower for w in _CRITICAL_WORDS):
        return Severity.CRITICAL
    if any(w in text_lower for w in _HIGH_WORDS):
        return Severity.HIGH
    if any(w in text_lower for w in _OCCUPANCY_WORDS):
        return Severity.HIGH
    return Severity.MODERATE


def classify_incident(report_text: str) -> dict[str, Any]:
    """Classify an incident report into type and severity based on content analysis.

    Args:
        report_text: The raw incident report text.

    Returns:
        Classification with incident_type, severity, confidence, and keywords_matched.
    """
    text_lower = report_text.lower()

    # OTHER, not MEDICAL. The fallback used to be a real category, so anything
    # the keyword tables did not recognise — a wrong number, a typo, a pasted
    # sentence — came back as a medical incident with a straight face. Saying
    # "I could not classify this" is the only honest answer when nothing matched.
    best_type = IncidentType.OTHER
    best_score = 0

    for itype, keywords in _TYPE_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in text_lower)
        if score > best_score:
            best_score = score
            best_type = itype

    severity = _assess_severity(text_lower)

    now = datetime.now(timezone.utc)
    incident_id = f"{best_type.upper()}-{now.year}-{now.strftime('%H%M%S')}"

    return {
        "incident_id": incident_id,
        "incident_type": best_type,
        "severity": severity,
        "confidence": min(best_score / 3.0, 1.0),
        "keywords_matched": best_score,
        # True when no category matched at all. Severity is still assessed from
        # the words used: failing to name the type of emergency is not the same
        # as deciding there isn't one.
        "unclassified": best_score == 0,
        "emergency_notice": "REMINDER: If this is a life-threatening emergency, call 911 immediately.",
    }


def extract_location(report_text: str) -> dict[str, Any]:
    """Extract and resolve location details from an incident report against the knowledge base.

    Args:
        report_text: The raw incident report text.

    Returns:
        Extracted location with resolved zone, room, and floor from the KB.
    """
    kb = KnowledgeBase.get()
    text_lower = report_text.lower()

    location: dict[str, Any] = {
        "floor": "",
        "room_id": "",
        "room_name": "",
        "zone_id": "",
        "zone_name": "",
        "raw_location": "",
        "resolved": False,
    }

    # Floor extraction
    for marker in ["floor ", "level ", "fl "]:
        idx = text_lower.find(marker)
        if idx >= 0:
            rest = report_text[idx + len(marker):idx + len(marker) + 5].strip()
            floor_num = "".join(c for c in rest if c.isdigit())
            if floor_num:
                location["floor"] = floor_num
                break

    # Room extraction — match "room 215", "rm 215", "Room 215"
    room_match = re.search(r'(?:room|rm)\s*(\d{3})', text_lower)
    if room_match:
        room_id = room_match.group(1)
        room = kb.get_room(room_id)
        if room:
            location["room_id"] = room_id
            location["room_name"] = room["name"]
            location["zone_id"] = room.get("zone_id", "")
            location["floor"] = str(room.get("floor", ""))
            zone = kb.get_zone(room.get("zone_id", ""))
            if zone:
                location["zone_name"] = zone["name"]
            location["resolved"] = True
            return location

    # Zone name matching — check if any zone name appears in the text
    for z in kb.zones:
        zone_name_lower = z["name"].lower()
        if zone_name_lower in text_lower:
            location["zone_id"] = z["zone_id"]
            location["zone_name"] = z["name"]
            location["floor"] = str(z.get("floor", ""))
            location["resolved"] = True
            return location

    # Room name / keyword matching — "science lab", "gym", "cafeteria", "library"
    keyword_zone_map = {
        "science lab": "west-wing-f2",
        "lab": "west-wing-f2",
        "gymnasium": "gym",
        "gym": "gym",
        "cafeteria": "cafeteria",
        "library": "library",
        "media center": "library",
        "main office": "admin-f1",
        "front office": "admin-f1",
        "nurse": "east-wing-f1",
        "east wing": "east-wing-f1",
        "west wing": "west-wing-f1",
    }
    for keyword, zone_id in keyword_zone_map.items():
        if keyword in text_lower:
            zone = kb.get_zone(zone_id)
            if zone:
                location["zone_id"] = zone_id
                location["zone_name"] = zone["name"]
                location["floor"] = str(zone.get("floor", ""))
                location["resolved"] = True

            # Also try to find the specific room
            for r in kb.rooms:
                if keyword in r["name"].lower():
                    location["room_id"] = r["room_id"]
                    location["room_name"] = r["name"]
                    break
            return location

    # Fallback: capture raw location text
    for marker in ["near ", "by ", "at ", "in "]:
        idx = text_lower.find(marker)
        if idx >= 0:
            rest = report_text[idx:idx + 60].strip()
            end = rest.find(".")
            location["raw_location"] = rest[:end] if end > 0 else rest
            break

    return location


def select_playbook(incident_type: str) -> dict[str, str]:
    """Select the approved playbook for a given incident type.

    Args:
        incident_type: The classified incident type.

    Returns:
        Playbook selection with playbook_id and name.
    """
    playbook_id = PLAYBOOK_MAP.get(incident_type, "playbook-general-v1")
    return {
        "playbook_id": playbook_id,
        "incident_type": incident_type,
        "status": "approved",
        "note": "Only pre-approved, organization-specific playbooks are used. No improvised instructions.",
    }

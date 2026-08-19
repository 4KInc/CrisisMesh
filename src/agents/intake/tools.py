"""Tools for the Intake & Classification Agent."""

from __future__ import annotations

from datetime import datetime
from typing import Any

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

# Keywords for classification heuristics (supplements Gemini classification)
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


def classify_incident(report_text: str) -> dict[str, Any]:
    """Classify an incident report into type and severity based on content analysis.

    Args:
        report_text: The raw incident report text.

    Returns:
        Classification with incident_type, severity, confidence, and keywords_matched.
    """
    text_lower = report_text.lower()

    best_type = IncidentType.MEDICAL  # default fallback
    best_score = 0

    for itype, keywords in _TYPE_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in text_lower)
        if score > best_score:
            best_score = score
            best_type = itype

    # Severity heuristics
    severity = Severity.MODERATE
    critical_words = ["critical", "multiple", "spreading", "uncontrolled", "mass", "armed"]
    high_words = ["urgent", "serious", "large", "expanding", "trapped"]

    if any(w in text_lower for w in critical_words):
        severity = Severity.CRITICAL
    elif any(w in text_lower for w in high_words):
        severity = Severity.HIGH
    elif best_score <= 1:
        severity = Severity.LOW

    # Generate incident ID
    year = datetime.utcnow().year
    incident_id = f"{best_type.upper()}-{year}-{datetime.utcnow().strftime('%H%M%S')}"

    return {
        "incident_id": incident_id,
        "incident_type": best_type,
        "severity": severity,
        "confidence": min(best_score / 3.0, 1.0),
        "keywords_matched": best_score,
        "emergency_notice": "REMINDER: If this is a life-threatening emergency, call 911 immediately.",
    }


def extract_location(report_text: str) -> dict[str, str]:
    """Extract location details from an incident report.

    Args:
        report_text: The raw incident report text.

    Returns:
        Extracted location with building, floor, room, and zone fields.
    """
    # This is a simplified extraction — Gemini handles the real NLU
    text_lower = report_text.lower()
    location = {
        "building": "",
        "floor": "",
        "room": "",
        "zone": "",
        "raw_location": "",
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

    # Room extraction
    for marker in ["room ", "rm ", "lab ", "office "]:
        idx = text_lower.find(marker)
        if idx >= 0:
            rest = report_text[idx + len(marker):idx + len(marker) + 20].strip()
            room_id = rest.split()[0] if rest.split() else ""
            if room_id:
                location["room"] = room_id
                break

    # Near / by extraction for general location
    for marker in ["near ", "by ", "at ", "in "]:
        idx = text_lower.find(marker)
        if idx >= 0:
            rest = report_text[idx:idx + 50].strip()
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

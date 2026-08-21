"""Shared playbook content for CrisisMesh agents and transports."""

from __future__ import annotations


PLAYBOOKS: dict[str, dict] = {
    "earthquake": {
        "title": "Earthquake Response Playbook",
        "immediate_actions": [
            "Drop, Cover, Hold On — do not evacuate during shaking",
            "Once shaking stops: check for injuries, assess structural damage",
            "Evacuate if building damage is visible — use stairs, not elevators",
            "Move to designated assembly point",
            "Account for all personnel — check in with your team lead",
        ],
        "roles": [
            {"role": "Incident Commander", "resp": "Overall coordination, communication with emergency services"},
            {"role": "Safety Officer", "resp": "Building damage assessment, evacuation decisions"},
            {"role": "Communications Lead", "resp": "Internal updates, external notifications, family contact"},
            {"role": "Medical Lead", "resp": "First aid triage, coordinate with EMS"},
        ],
        "resources": ["First aid kits", "Emergency radios", "Flashlights and batteries", "Water and emergency supplies", "Building evacuation maps"],
    },
    "fire": {
        "title": "Fire Response Playbook",
        "immediate_actions": [
            "Activate fire alarm if not already triggered",
            "Call 911 / local fire department immediately",
            "Evacuate via nearest safe exit — do NOT use elevators",
            "Close doors behind you to slow fire spread",
            "Assemble at designated rally point for headcount",
        ],
        "roles": [
            {"role": "Incident Commander", "resp": "Coordinate evacuation, liaise with fire department"},
            {"role": "Floor Wardens", "resp": "Sweep assigned areas, confirm all clear"},
            {"role": "Communications Lead", "resp": "Notify all staff, update stakeholders"},
            {"role": "Assembly Point Lead", "resp": "Conduct headcount, report missing persons"},
        ],
        "resources": ["Fire extinguishers (know locations)", "Evacuation route maps", "Emergency contact list", "First aid supplies", "Megaphone or communication device"],
    },
    "flood": {
        "title": "Flood Response Playbook",
        "immediate_actions": [
            "Monitor weather alerts and water levels",
            "Move to higher ground if water is rising",
            "Disconnect electrical equipment in flood-risk areas",
            "Secure important documents and equipment",
            "Do NOT walk or drive through flood waters",
        ],
        "roles": [
            {"role": "Incident Commander", "resp": "Monitor conditions, evacuation decisions"},
            {"role": "Facilities Lead", "resp": "Sandbags, equipment protection, utility shutoff"},
            {"role": "Communications Lead", "resp": "Weather monitoring, staff notifications"},
            {"role": "Logistics Lead", "resp": "Transportation, temporary relocation coordination"},
        ],
        "resources": ["Sandbags and barriers", "Water pumps", "Waterproof containers for documents", "Emergency power supply", "Evacuation transportation"],
    },
    "active_threat": {
        "title": "Active Threat Response Playbook",
        "immediate_actions": [
            "RUN: Evacuate if safe path exists — leave belongings behind",
            "HIDE: If evacuation impossible, find secure room, lock/barricade door",
            "FIGHT: Last resort only — act with aggression, improvise weapons",
            "Call 911 when safe to do so — provide location and description",
            "Do NOT pull fire alarm — it causes people to gather in open areas",
        ],
        "roles": [
            {"role": "Incident Commander", "resp": "Coordinate with law enforcement, account for personnel"},
            {"role": "Communications Lead", "resp": "Send lockdown alerts, maintain communication with police"},
            {"role": "Medical Lead", "resp": "Triage injuries once scene is secured"},
            {"role": "Recovery Lead", "resp": "Post-incident support, counseling resources"},
        ],
        "resources": ["Lockdown notification system", "Room barricade capability", "First aid / trauma kits", "Law enforcement direct contact numbers", "Crisis counseling resources"],
    },
    "cyberattack": {
        "title": "Cyber Attack Response Playbook",
        "immediate_actions": [
            "Identify affected systems and scope of compromise",
            "Isolate compromised systems from the network immediately",
            "Preserve forensic evidence — do NOT reboot affected machines",
            "Activate incident response team communication channel",
            "Notify legal, compliance, and executive leadership",
        ],
        "roles": [
            {"role": "Incident Commander", "resp": "Overall response coordination, stakeholder communication"},
            {"role": "Technical Lead", "resp": "Containment, forensic analysis, system recovery"},
            {"role": "Communications Lead", "resp": "Internal/external notifications, regulatory reporting"},
            {"role": "Legal/Compliance Lead", "resp": "Regulatory obligations, evidence preservation, breach notification"},
        ],
        "resources": ["Incident response toolkit (forensic tools)", "Network diagrams and asset inventory", "Backup systems and recovery procedures", "Legal counsel contact", "Regulatory notification templates"],
    },
    "data_breach": {
        "title": "Data Breach Response Playbook",
        "immediate_actions": [
            "Confirm the breach — identify what data was exposed",
            "Contain the breach — revoke access, patch vulnerability",
            "Document everything — timestamps, affected records, actions taken",
            "Notify legal counsel and compliance team",
            "Begin regulatory breach notification timeline (72h GDPR, varies by jurisdiction)",
        ],
        "roles": [
            {"role": "Incident Commander", "resp": "Response coordination, executive briefings"},
            {"role": "Security Lead", "resp": "Containment, investigation, remediation"},
            {"role": "Legal/Privacy Lead", "resp": "Breach notification, regulatory compliance"},
            {"role": "Communications Lead", "resp": "Customer notification, media response"},
        ],
        "resources": ["Data classification inventory", "Breach notification templates", "Forensic investigation tools", "External legal counsel", "Customer communication channels"],
    },
    "outage": {
        "title": "Service Outage Response Playbook",
        "immediate_actions": [
            "Confirm outage scope — which services, which users affected",
            "Check monitoring dashboards for root cause indicators",
            "Engage on-call engineers for affected systems",
            "Post status page update within 15 minutes",
            "Establish communication cadence (every 30 min until resolved)",
        ],
        "roles": [
            {"role": "Incident Commander", "resp": "Coordination, stakeholder updates, escalation decisions"},
            {"role": "Technical Lead", "resp": "Root cause analysis, remediation, failover"},
            {"role": "Communications Lead", "resp": "Status page updates, customer notifications"},
            {"role": "Support Lead", "resp": "Customer impact assessment, support queue management"},
        ],
        "resources": ["Monitoring and alerting dashboards", "Runbooks for common failure modes", "Escalation contact list", "Status page access", "Post-incident review template"],
    },
    "weather": {
        "title": "Severe Weather Response Playbook",
        "immediate_actions": [
            "Monitor official weather alerts (NWS, local emergency management)",
            "Activate early dismissal or shelter-in-place protocol as warranted",
            "Secure outdoor equipment and close windows",
            "Identify interior safe rooms away from windows",
            "Account for all personnel in building",
        ],
        "roles": [
            {"role": "Incident Commander", "resp": "Weather monitoring, shelter/evacuation decisions"},
            {"role": "Facilities Lead", "resp": "Building preparation, utility management"},
            {"role": "Communications Lead", "resp": "Staff alerts, closure decisions"},
            {"role": "Transportation Lead", "resp": "Safe commute assessment, remote work activation"},
        ],
        "resources": ["NOAA weather radio", "Emergency supplies (water, food, flashlights)", "Interior safe room designations", "Remote work capability", "Emergency contact tree"],
    },
    "medical": {
        "title": "Medical Emergency Response Playbook",
        "immediate_actions": [
            "Call 911 immediately — provide exact location and nature of emergency",
            "Administer first aid if trained — CPR, AED, bleeding control",
            "Clear the area around the patient",
            "Send someone to meet and guide EMS to the patient",
            "Do NOT move the patient unless in immediate danger",
        ],
        "roles": [
            {"role": "Incident Commander", "resp": "Coordinate response, communicate with EMS"},
            {"role": "First Responder", "resp": "Administer first aid, stabilize patient"},
            {"role": "Guide", "resp": "Meet EMS at entrance, direct to patient location"},
            {"role": "Communications Lead", "resp": "Notify relevant parties, manage information flow"},
        ],
        "resources": ["First aid kit (with AED location known)", "Emergency medical information", "AED (Automated External Defibrillator)", "Emergency contact information", "Incident documentation forms"],
    },
    "generic": {
        "title": "General Incident Response Playbook",
        "immediate_actions": [
            "Assess the situation — determine scope and severity",
            "Ensure immediate safety of all personnel",
            "Notify relevant leadership and stakeholders",
            "Document the incident — what happened, when, who is affected",
            "Establish communication cadence for updates",
        ],
        "roles": [
            {"role": "Incident Commander", "resp": "Overall coordination, decision authority"},
            {"role": "Operations Lead", "resp": "Execute response actions, manage resources"},
            {"role": "Communications Lead", "resp": "Internal/external updates, stakeholder management"},
            {"role": "Documentation Lead", "resp": "Record timeline, actions, decisions for after-action review"},
        ],
        "resources": ["Incident documentation template", "Emergency contact list", "Communication tools (backup channels)", "Relevant SOPs and procedures", "Post-incident review template"],
    },
}

INCIDENT_TYPE_TO_PLAYBOOK_KEY: dict[str, str] = {
    "fire": "fire",
    "active_threat": "active_threat",
    "severe_weather": "weather",
    "medical": "medical",
    "flood": "flood",
    "cyber_ransomware": "cyberattack",
    "data_breach": "data_breach",
    "utility_outage": "outage",
    "hazmat": "generic",
    "bomb_threat": "active_threat",
    "earthquake": "earthquake",
}


def get_playbook_content(incident_type: str) -> dict | None:
    key = INCIDENT_TYPE_TO_PLAYBOOK_KEY.get(incident_type, "generic")
    return PLAYBOOKS.get(key)

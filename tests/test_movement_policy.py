"""The contract the critic enforces, written before the policy exists.

Two renderers once disagreed about the same incident: the Slack card printed
"Assembly: Athletic Field" while the WhatsApp alert deliberately omitted it and
said barricade in place. Shelter-vs-evacuate is the axis that gets people
killed, and it was decided independently in each renderer.

These tests define what a renderer is allowed to do, so the policy function is
shaped by what must be enforceable rather than retrofitted to it.

The fail-closed rule is the important one. A critic is itself a named action
that can silently not-act — the same defect as an `escalate` that notifies
nobody. So: when the policy cannot be determined, the answer is the restrictive
one, never the permissive one. An unknown incident type does not get an
assembly point.
"""

import pytest

from src.core import movement_policy


class TestDirective:
    """One question, one answer, for every surface."""

    @pytest.mark.parametrize("incident_type", ["active_threat", "bomb_threat"])
    def test_lockdown_incidents_shelter(self, incident_type):
        d = movement_policy.for_incident(incident_type)
        assert d.movement == movement_policy.SHELTER
        assert d.may_publish_assembly_point is False

    @pytest.mark.parametrize("incident_type", ["fire", "hazmat", "flood", "severe_weather"])
    def test_hazard_incidents_evacuate(self, incident_type):
        d = movement_policy.for_incident(incident_type)
        assert d.movement == movement_policy.EVACUATE
        assert d.may_publish_assembly_point is True

    def test_medical_does_not_move_anyone(self):
        d = movement_policy.for_incident("medical")
        assert d.movement == movement_policy.STAY
        assert d.may_publish_assembly_point is True


class TestFailsClosed:
    """When the policy cannot be determined, restrict. Never permit."""

    @pytest.mark.parametrize("incident_type", ["", None, "other", "unknown", "garbage"])
    def test_unknown_type_withholds_the_assembly_point(self, incident_type):
        d = movement_policy.for_incident(incident_type)
        assert d.may_publish_assembly_point is False, (
            f"{incident_type!r} permitted an assembly point — fails open"
        )

    def test_unknown_type_does_not_order_movement(self):
        """Not knowing the hazard is not a reason to move people through it."""
        assert movement_policy.for_incident("unknown").movement != movement_policy.EVACUATE

    def test_a_raising_lookup_still_restricts(self, monkeypatch):
        """If the policy itself errors, the caller must not get a permissive
        default. This is the critic-fails-open case, in miniature."""
        def _boom():
            raise RuntimeError("classification source unavailable")

        monkeypatch.setattr(movement_policy, "_lockdown_types", _boom)
        d = movement_policy.for_incident("active_threat")
        assert d.may_publish_assembly_point is False
        assert d.movement != movement_policy.EVACUATE

    def test_a_raising_lookup_restricts_an_evacuation_type_too(self, monkeypatch):
        """Even fire must not publish a rally point when the policy is broken —
        a broken policy cannot distinguish fire from an active threat."""
        def _boom():
            raise RuntimeError("classification source unavailable")

        monkeypatch.setattr(movement_policy, "_lockdown_types", _boom)
        assert movement_policy.for_incident("fire").may_publish_assembly_point is False


class TestRenderContract:
    """What a surface is allowed to emit, checkable from outside."""

    def test_assembly_line_is_withheld_not_blank(self):
        """A blank field reads as missing data, which invites someone to go
        look the rally point up."""
        line = movement_policy.assembly_line("active_threat", "Athletic Field")
        assert "Athletic Field" not in line
        assert line.strip()
        assert "withheld" in line.lower()

    def test_assembly_line_shows_the_point_when_permitted(self):
        assert "Athletic Field" in movement_policy.assembly_line("fire", "Athletic Field")

    def test_violation_detected_when_a_surface_leaks_the_point(self):
        """This is what the critic runs. It must catch the exact defect that
        shipped: a lockdown rendering that names the rally point."""
        violation = movement_policy.check_rendering(
            "active_threat",
            "INCIDENT DECLARED. Assembly: Athletic Field (Primary). React to check in.",
            assembly_name="Athletic Field",
            surface="slack_block_kit",
        )
        assert violation is not None
        assert "Athletic Field" in violation.detail
        assert violation.surface == "slack_block_kit"

    def test_no_violation_for_a_compliant_lockdown_rendering(self):
        assert movement_policy.check_rendering(
            "active_threat",
            "LOCKDOWN. Assembly: withheld during lockdown. Lock and barricade.",
            assembly_name="Athletic Field",
            surface="notify_sms",
        ) is None

    def test_evacuation_rendering_may_name_the_point(self):
        assert movement_policy.check_rendering(
            "fire",
            "FIRE. Assembly: Athletic Field. Evacuate via stairwell B.",
            assembly_name="Athletic Field",
            surface="notify_sms",
        ) is None

    def test_contradictory_movement_language_is_caught(self):
        """Telling people to evacuate during a lockdown is the same defect in
        words rather than in a field."""
        violation = movement_policy.check_rendering(
            "active_threat",
            "Proceed to the assembly point and evacuate the building immediately.",
            assembly_name="Athletic Field",
            surface="gemini_sitrep",
        )
        assert violation is not None


class TestCriticActs:
    """A verdict nobody acts on is the defect this exists to fix."""

    def test_enforce_strips_the_violation_rather_than_logging_it(self):
        cleaned, violation = movement_policy.enforce(
            "active_threat",
            "INCIDENT DECLARED. Assembly: Athletic Field (Primary).",
            assembly_name="Athletic Field",
            surface="slack_block_kit",
        )
        assert violation is not None
        assert "Athletic Field" not in cleaned
        assert "withheld" in cleaned.lower()

    def test_enforce_leaves_compliant_text_untouched(self):
        text = "FIRE. Assembly: Athletic Field. Evacuate via stairwell B."
        cleaned, violation = movement_policy.enforce(
            "fire", text, assembly_name="Athletic Field", surface="notify_sms")
        assert cleaned == text
        assert violation is None

    def test_enforce_on_an_unknown_type_still_strips(self):
        """Fail closed: an unclassified incident does not get to publish a
        rally point just because nothing matched."""
        cleaned, _ = movement_policy.enforce(
            "other", "Assembly: Athletic Field.",
            assembly_name="Athletic Field", surface="notify_sms")
        assert "Athletic Field" not in cleaned


class TestAllSurfacesAgree:
    """Six surfaces render the assembly point. Only two consulted the incident
    type, which is how a Slack card came to print "Assembly: Athletic Field"
    for the same incident whose WhatsApp alert said barricade in place."""

    LOCKDOWN = {
        "incident_id": "T-1",
        "report": "Armed intruder in the west wing",
        "classification": {"incident_type": "active_threat", "severity": "critical"},
        "location": {"zone_name": "West Wing Floor 1"},
        "assembly": {"name": "Athletic Field"},
    }

    def test_fan_out_message(self):
        from src.core import notify
        assert "Athletic Field" not in notify.compose_alert(self.LOCKDOWN)

    def test_slack_block_kit_card(self):
        from src.services.slack_transport import _assembly_line
        assert "Athletic Field" not in _assembly_line("active_threat", "Athletic Field")

    def test_generate_sitrep(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CRISISMESH_CONSENT_LOG", str(tmp_path / "c.jsonl"))
        from src.core.knowledge_base import KnowledgeBase, init_knowledge_base
        import os
        KnowledgeBase.reset()
        init_knowledge_base(os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "seed"))
        from src.agents.sitrep.tools import generate_sitrep
        result = generate_sitrep("T-1", "active_threat", "critical", "West Wing",
                                 {"total_tracked": 34, "accounted": 1, "unaccounted": 33})
        assert "Athletic Field" not in str(result.get("assembly_point", ""))
        KnowledgeBase.reset()

    def test_generate_arrival_brief(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CRISISMESH_CONSENT_LOG", str(tmp_path / "c.jsonl"))
        from src.core.knowledge_base import KnowledgeBase, init_knowledge_base
        import os
        KnowledgeBase.reset()
        init_knowledge_base(os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "seed"))
        from src.agents.sitrep.tools import generate_arrival_brief
        brief = generate_arrival_brief("T-1", "active_threat", "critical", "West Wing",
                                       "2026-08-25T10:00:00Z",
                                       {"total_tracked": 34, "accounted": 1, "unaccounted": 33})
        assert "Athletic Field" not in str(brief.get("assembly_point", ""))
        KnowledgeBase.reset()

    def test_console_spa_suppresses_it(self):
        html = open("static/index.html").read()
        assert "sheltering" in html
        assert "LOCKDOWN_TYPES" in html

    def test_fire_still_publishes_everywhere(self):
        from src.core import notify
        from src.services.slack_transport import _assembly_line
        fire = {**self.LOCKDOWN,
                "classification": {"incident_type": "fire", "severity": "high"}}
        assert "Athletic Field" in notify.compose_alert(fire)
        assert "Athletic Field" in _assembly_line("fire", "Athletic Field")


class TestEnforcementIsWiredNotJustAvailable:
    """A critic that only files verdicts is the defect it exists to fix."""

    @pytest.fixture(autouse=True)
    def lockdown(self, tmp_path, monkeypatch):
        import os
        monkeypatch.setenv("CRISISMESH_CONSENT_LOG", str(tmp_path / "c.jsonl"))
        monkeypatch.setenv("CRISISMESH_SMS_MODE", "twilio")
        monkeypatch.setenv("TWILIO_ACCOUNT_SID", "AC_t")
        monkeypatch.setenv("TWILIO_AUTH_TOKEN", "tok")
        monkeypatch.setenv("TWILIO_PHONE_NUMBER", "+15550000000")
        from src.core import incident_state
        from src.core.knowledge_base import KnowledgeBase, init_knowledge_base
        KnowledgeBase.reset()
        init_knowledge_base(os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "seed"))
        incident_state.declare(
            "T-1",
            {"incident_id": "T-1",
             "classification": {"incident_type": "active_threat", "severity": "critical"},
             "assembly": {"name": "Athletic Field"}},
            source="slack")
        yield
        incident_state.reset()
        KnowledgeBase.reset()

    def test_fanout_strips_a_contradiction_before_it_sends(self, monkeypatch):
        """Even if a composer upstream introduces one, it does not leave."""
        from src.core import notify
        sent = []
        monkeypatch.setattr("src.services.sms_transport.send_sms",
                            lambda to, body: (sent.append(body), {"delivered": True})[1])
        reach = notify.Reach("p001", "Principal Johnson", notify.CHANNEL_SMS, "+15551110000")
        notify._send(reach, "Proceed to Athletic Field and evacuate the building.")
        assert sent, "nothing was sent"
        assert "Athletic Field" not in sent[0]
        assert "withheld" in sent[0].lower()

    def test_slack_bot_message_is_enforced(self):
        from src.services.slack_transport import _enforced
        cleaned = _enforced("SITREP — Assembly: Athletic Field (Primary).",
                            surface="slack_bot_message")
        assert "Athletic Field" not in cleaned

    def test_compliant_text_passes_through_untouched(self):
        from src.services.slack_transport import _enforced
        text = "LOCKDOWN in effect. Lock and barricade where you are."
        assert _enforced(text, surface="slack_bot_message") == text

    def test_no_active_incident_is_a_no_op(self):
        from src.core import incident_state
        from src.services.slack_transport import _enforced
        incident_state.reset()
        text = "Assembly: Athletic Field."
        assert _enforced(text, surface="slack_bot_message") == text


class TestAgainstModelParaphrase:
    """Measured against phrasings a model actually emits, not hand-written ones.

    A matcher tuned to "evacuate" alone caught 4 of these 12 and missed "exit
    the building via the north stairwell" and "leave the building immediately"
    — the two most dangerous during a lockdown, because a reader acts on them
    without a rally point ever being named.
    """

    PARAPHRASES = [
        "head to the field out back",
        "Move all students to the athletic field immediately",
        "Exit the building via the north stairwell",
        "Everyone should make their way outside to the playing fields",
        "Gather outside near the flagpole and await further instruction",
        "Leave the building immediately and do not stop for belongings",
        "Assemble on the playing fields behind the gym",
        "Proceed calmly out of the west doors",
        "All classrooms should relocate to the exterior grounds",
        "Take the nearest exit and regroup at the track",
        "Evacuate via stairwell B",
        "Proceed to the assembly point",
    ]

    @pytest.mark.parametrize("text", PARAPHRASES)
    def test_caught_during_a_shelter_directive(self, text):
        assert movement_policy.check_rendering(
            "active_threat", text, assembly_name="Athletic Field", surface="gemini",
        ) is not None, f"paraphrase slipped: {text!r}"

    @pytest.mark.parametrize("text", PARAPHRASES)
    def test_permitted_during_an_evacuation(self, text):
        """The same sentence is correct guidance for a fire."""
        assert movement_policy.check_rendering(
            "fire", text, assembly_name="Athletic Field", surface="gemini") is None


class TestNegationIsNotViolation:
    """The system's own safety backstop says "Do NOT direct a general
    evacuation". Flagging that at error level is the noise that teaches an
    operator to ignore the alarm."""

    @pytest.mark.parametrize("text", [
        "Do NOT direct a general evacuation: move only along a route confirmed away from the threat",
        "Do not evacuate the building",
        "Never proceed to the assembly point during a lockdown",
        "Shelter in place rather than evacuating",
        "Avoid moving to the exterior grounds",
        "Occupants must not leave the classroom",
    ])
    def test_forbidding_movement_is_not_ordering_it(self, text):
        assert movement_policy.check_rendering(
            "active_threat", text, assembly_name="Athletic Field", surface="backstop",
        ) is None, f"false positive on negated guidance: {text!r}"

    def test_the_real_backstop_lines_are_clean(self):
        from src.core.tactical_reasoning import LOCKDOWN_BACKSTOP_LINES
        for line in LOCKDOWN_BACKSTOP_LINES:
            assert movement_policy.check_rendering(
                "active_threat", line, assembly_name="Athletic Field", surface="backstop",
            ) is None, f"backstop line flagged itself: {line!r}"

    def test_the_shipped_lockdown_alert_is_clean(self):
        from src.core import notify
        alert = notify.compose_alert({
            "incident_id": "T-1",
            "classification": {"incident_type": "active_threat", "severity": "critical"},
        })
        assert movement_policy.check_rendering(
            "active_threat", alert, assembly_name="Athletic Field", surface="notify") is None

    def test_a_negation_far_away_does_not_excuse_a_real_order(self):
        """The window is narrow on purpose — a "do not" earlier in a long
        message must not license an evacuation order later in it."""
        text = ("Do not pull the fire alarm. " + "Status is being tracked. " * 4
                + "Proceed to the assembly point now.")
        assert movement_policy.check_rendering(
            "active_threat", text, assembly_name="Athletic Field", surface="gemini") is not None

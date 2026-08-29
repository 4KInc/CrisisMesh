"""A route is not safe because a floor plan says so.

The arrival brief reported the threat's last known position as the gym and then
published, four lines below it, "Safe Routes: East Wing F1 Alternate -> Door 7
(Gym Exit)". Both facts were correct. Printing them together, with one of them
labelled *safe*, points responders and anyone reading over their shoulder at the
place the threat was last seen.

The route data is static building layout. It has no idea where the threat is.
Nothing was cross-checking the two.
"""

import pytest

from src.core import movement_policy


class TestRoutesAreCheckedAgainstReportedSightings:
    def test_a_route_through_a_reported_position_is_flagged(self):
        flagged = movement_policy.flag_routes_against_threat(
            ["East Wing F1 Alternate -> Door 7 (Gym Exit)"], ["gym"])
        assert flagged[0]["conflicts"] is True
        assert "gym" in flagged[0]["reason"].lower()

    def test_a_clear_route_is_not_flagged(self):
        flagged = movement_policy.flag_routes_against_threat(
            ["West Wing F1 Primary -> Door 2 (North Lot)"], ["gym"])
        assert flagged[0]["conflicts"] is False

    def test_every_reported_position_is_checked_not_just_the_latest(self):
        """A threat reported in the east wing and then the gym has been in both.
        A route through the east wing is not clear because it has moved on."""
        flagged = movement_policy.flag_routes_against_threat(
            ["East Wing F1 Alternate -> Door 7"], ["east wing", "gym"])
        assert flagged[0]["conflicts"] is True

    def test_no_sightings_flags_nothing(self):
        flagged = movement_policy.flag_routes_against_threat(
            ["East Wing F1 Alternate -> Door 7 (Gym Exit)"], [])
        assert flagged[0]["conflicts"] is False

    def test_it_matches_on_words_not_substrings(self):
        """"gym" must not fire on "gymnasium annex" being absent — and must not
        be found inside an unrelated word."""
        flagged = movement_policy.flag_routes_against_threat(
            ["Door 3 (Effigy Mound Exit)"], ["gym"])
        assert flagged[0]["conflicts"] is False


class TestTheBriefDoesNotCallThemSafeDuringALockdown:
    def _brief(self):
        from src.core import incident_state, observations
        from src.core.knowledge_base import KnowledgeBase, init_knowledge_base
        from src.services import slack_transport
        from unittest.mock import patch
        import os

        KnowledgeBase.reset()
        init_knowledge_base(os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "seed"))
        incident_state.reset()
        observations.reset()
        incident_state.declare("T-1", {
            "incident_id": "T-1",
            "report": "active shooter reported in the east wing, gunshots heard",
            "classification": {"incident_type": "active_threat", "severity": "critical"},
            "location": {"zone_id": "east-wing-f1", "zone_name": "East Wing Floor 1"},
        }, source="whatsapp")
        observations.record("T-1", "shooter last seen heading toward the gym",
                            source="whatsapp", person_name="Mrs. Rodriguez")
        posted = []
        with patch.object(slack_transport, "_post_bot_message",
                          lambda ch, t, **kw: posted.append(t)):
            slack_transport._handle_arrival_brief("C1", "")
        return "\n".join(posted)

    def test_nothing_is_labelled_safe_while_the_threat_is_loose(self):
        text = self._brief()
        assert "Safe Routes" not in text

    def test_the_conflict_is_called_out(self):
        """The threat is at the gym and the egress is the gym exit. A reader
        skimming must not have to notice that themselves."""
        text = self._brief()
        gym_line = [ln for ln in text.split("\n") if "Door 7" in ln]
        assert gym_line, text
        assert "sighting:" in gym_line[0].lower(), gym_line

    def test_a_fire_still_calls_them_safe_routes(self):
        from src.core import incident_state, observations
        from src.services import slack_transport
        from unittest.mock import patch

        incident_state.reset()
        observations.reset()
        incident_state.declare("F-1", {
            "incident_id": "F-1", "report": "smoke in the science lab",
            "classification": {"incident_type": "fire", "severity": "high"},
            "location": {"zone_id": "west-wing-f2", "zone_name": "West Wing Floor 2"},
        }, source="slack")
        posted = []
        with patch.object(slack_transport, "_post_bot_message",
                          lambda ch, t, **kw: posted.append(t)):
            slack_transport._handle_arrival_brief("C1", "")
        assert "Safe Routes" in "\n".join(posted)

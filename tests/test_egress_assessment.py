"""Work out which way is clear, do not just condemn the bad one.

The system holds the floor plan and every reported sighting. Flagging "Door 7
passes the gym" and stopping there leaves the actual question unanswered by the
one party that can answer it: thirteen routes exist, the threat has been
reported in two places, and which of the thirteen avoids both is a join nobody
should be doing in their head during a shooting.

Clear here means one thing and is never allowed to drift: no reported sighting
lies on this path. It is not a clearance, it does not mean law enforcement has
swept it, and it cannot see a threat nobody has reported.
"""

import os

import pytest

from src.core import movement_policy
from src.core.knowledge_base import KnowledgeBase

SEED = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "seed")


@pytest.fixture
def routes():
    kb = KnowledgeBase()
    kb.load_from_directory(SEED)
    return kb.get_all_routes_for_facility("jefferson")


class TestItPicksTheClearRoutes:
    def test_it_finds_a_way_out_that_avoids_every_sighting(self, routes):
        result = movement_policy.assess_egress(routes, ["east wing", "gym"])
        exits = {r["to_exit"] for r in result["clear"]}
        assert "Door 1 (West Exit)" in exits
        assert "Door 5 (Cafeteria Exit)" in exits

    def test_it_excludes_every_path_touching_a_sighting(self, routes):
        result = movement_policy.assess_egress(routes, ["east wing", "gym"])
        for route in result["clear"]:
            blob = f"{route['from_zone']} {route['to_exit']} {route['route_description']}".lower()
            assert "gym" not in blob
            assert "east" not in blob

    def test_the_condemned_routes_say_which_sighting_condemned_them(self, routes):
        result = movement_policy.assess_egress(routes, ["gym"])
        gym_routes = [r for r in result["conflicting"] if "Door 7" in r["to_exit"]]
        assert gym_routes
        assert "gym" in gym_routes[0]["conflict"].lower()

    def test_every_route_lands_in_exactly_one_bucket(self, routes):
        result = movement_policy.assess_egress(routes, ["east wing", "gym"])
        assert len(result["clear"]) + len(result["conflicting"]) == len(routes)

    def test_a_step_free_option_is_identifiable(self, routes):
        """The brief names people who cannot use stairs. A clear route they
        cannot physically take is not an answer for them."""
        result = movement_policy.assess_egress(routes, ["east wing", "gym"])
        assert any(r["step_free"] for r in result["clear"])

    def test_no_sightings_means_nothing_is_condemned(self, routes):
        result = movement_policy.assess_egress(routes, [])
        assert result["conflicting"] == []
        assert len(result["clear"]) == len(routes)

    def test_a_threat_everywhere_leaves_nothing_clear(self, routes):
        """When every path touches a sighting it says so, rather than
        promoting the least-bad one to safe."""
        result = movement_policy.assess_egress(
            routes, ["east wing", "west wing", "gym", "library", "cafeteria"])
        assert result["clear"] == []


class TestClearNeverMeansSafe:
    def test_the_word_safe_is_not_used(self, routes):
        result = movement_policy.assess_egress(routes, ["gym"])
        assert "safe" not in result["caveat"].lower()

    def test_the_caveat_says_what_clear_actually_means(self, routes):
        result = movement_policy.assess_egress(routes, ["gym"])
        c = result["caveat"].lower()
        assert "reported" in c
        assert "not" in c

    def test_it_reports_what_it_was_checked_against(self, routes):
        """A reader has to be able to see the sightings the answer is based on,
        because a stale sighting makes a clear route wrong."""
        result = movement_policy.assess_egress(routes, ["gym"])
        assert result["checked_against"] == ["gym"]


class TestTheBriefAnswersTheQuestion:
    def _brief(self):
        from unittest.mock import patch
        from src.core import incident_state, observations
        from src.core.knowledge_base import init_knowledge_base
        from src.services import slack_transport

        KnowledgeBase.reset()
        init_knowledge_base(SEED)
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

    def test_it_names_a_route_clear_of_both_sightings(self):
        text = self._brief()
        assert "Door 1 (West Exit)" in text or "Door 5 (Cafeteria Exit)" in text

    def test_it_still_condemns_the_gym_exit(self):
        text = self._brief()
        door7 = [ln for ln in text.split("\n") if "Door 7" in ln]
        assert door7
        # It sits under the warning heading with its condemning sighting named.
        assert all("sighting:" in ln for ln in door7), door7
        assert ":warning:" in text

    def test_it_does_not_call_anything_safe(self):
        assert "Safe Routes" not in self._brief()


class TestTheFloorPlansOwnExclusionsCount:
    """The brief carried a "Blocked Routes" line from the floor plan and an
    egress assessment from the sighting trail, and they could disagree about the
    same route: with only the gym reported, East Wing F1 Primary is blocked for
    this incident zone and carries no sighting, so it appeared in both lists —
    condemned above, clear below.

    Two reasons not to use a route, one list."""

    def test_a_statically_blocked_route_is_not_called_clear(self, routes):
        result = movement_policy.assess_egress(
            routes, ["gym"], blocked_names=["East Wing F1 Primary"])
        assert not any(r.get("name") == "East Wing F1 Primary" for r in result["clear"])

    def test_it_says_the_floor_plan_is_why(self, routes):
        result = movement_policy.assess_egress(
            routes, [], blocked_names=["East Wing F1 Primary"])
        hit = [r for r in result["conflicting"] if r["name"] == "East Wing F1 Primary"]
        assert hit
        assert "floor plan" in hit[0]["conflict"].lower()

    def test_a_sighting_outranks_the_floor_plan(self, routes):
        """Both true, one gets said. A person is more urgent than a layout."""
        result = movement_policy.assess_egress(
            routes, ["east wing"], blocked_names=["East Wing F1 Primary"])
        hit = [r for r in result["conflicting"] if r["name"] == "East Wing F1 Primary"]
        assert "sighting" in hit[0]["conflict"].lower()

    def test_routes_carry_their_name(self, routes):
        """Two east-wing-f2 routes reach Door 2 — the stairwell and the elevator.
        Rendered without names they read as the same line printed twice."""
        result = movement_policy.assess_egress(routes, ["east wing"])
        names = [r["name"] for r in result["conflicting"] if "Door 2" in r["to_exit"]]
        assert len(set(names)) == len(names)
        assert "East Wing F2 Elevator" in names


class TestTheBriefFitsOneMessage:
    def test_the_egress_section_groups_by_exit(self):
        """Six clear routes rendered one per line with descriptions pushed the
        brief past Slack's limit and split it mid-section. Three exits, three
        lines."""
        from unittest.mock import patch
        from src.core import incident_state, observations
        from src.core.knowledge_base import init_knowledge_base
        from src.services import slack_transport

        KnowledgeBase.reset()
        init_knowledge_base(SEED)
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
        text = "\n".join(posted)

        section = text[text.index("EGRESS ASSESSMENT"):text.index("On-Site Resources")]
        assert section.count("Door 1 (West Exit)") == 1, "the same exit listed repeatedly"
        assert "step-free" in section
        # _post_bot_message is mocked here and that is where splitting happens,
        # so counting calls to the mock would prove nothing. Run the real
        # splitter: the brief is allowed to need two messages, but the egress
        # assessment must not be cut in half by the break.
        parts = slack_transport._split_for_slack(text)
        holding = [p for p in parts if "EGRESS ASSESSMENT" in p]
        assert len(holding) == 1
        # Heading, both lists and the caveat all land in the same message.
        assert "Do not use" in holding[0], "the break fell inside the assessment"
        assert "No reported sighting on these paths" in holding[0]
        assert "not a clearance" in holding[0], "the caveat was split away"

    def test_the_blocked_routes_line_is_gone(self):
        from unittest.mock import patch
        from src.core import incident_state, observations
        from src.core.knowledge_base import init_knowledge_base
        from src.services import slack_transport

        KnowledgeBase.reset()
        init_knowledge_base(SEED)
        incident_state.reset()
        observations.reset()
        incident_state.declare("T-1", {
            "incident_id": "T-1", "report": "active shooter in the east wing",
            "classification": {"incident_type": "active_threat", "severity": "critical"},
            "location": {"zone_id": "east-wing-f1", "zone_name": "East Wing Floor 1"},
        }, source="whatsapp")
        posted = []
        with patch.object(slack_transport, "_post_bot_message",
                          lambda ch, t, **kw: posted.append(t)):
            slack_transport._handle_arrival_brief("C1", "")
        assert "Blocked Routes" not in "\n".join(posted)

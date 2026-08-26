"""The delivery seam, pinned before it is wired.

Everything built so far has the property that a bug's worst consequence stays
inside the system: a wrong transition, a lost check-in, a hung tick, a re-alarm.
The intent-recording boundary is what guaranteed that. Delivery removes it — the
first time the loop can send is the first time a bug pages a real person during
what they will read as a real emergency.

These are world-claims, in the form the last four bugs proved is the only form
that catches a correct-producer wired wrongly to a correct-sender. They are
skipped until delivery exists; whoever wires it removes the skip and makes them
pass, and the shape of the fix is dictated by what they demand rather than
retrofitted to it.

Three outcomes, not two. `send_sms` currently catches its own exception and
returns `delivered: False, "transport error, nothing was sent"` — so at the
boundary the loop decides on, a carrier refusal and a call that never completed
are indistinguishable, and the detail asserts nothing was sent when the honest
claim is that we do not know. A timeout after the request left the process may
have delivered. The transport has to surface accepted / rejected / unknown
before the loop can honour the distinction.
"""

import pytest

pytestmark = pytest.mark.skip(
    reason="delivery not wired — this is the contract for the next step"
)


class TestOnlyTheLoopsIntentsReachTheWire:
    def test_an_intent_the_loop_did_not_produce_never_sends(self):
        """The boundary that holds today. Keep it holding."""

    def test_each_produced_intent_reaches_the_wire_exactly_once(self):
        """Not zero (silent drop), not twice per tick (double page)."""


class TestOutcomeDecidesWhetherWeChaseAgain:
    """`last_acted_tick` advances on accepted alone. That is what turns the
    ordering delivery forces on us — send-then-commit, because no ordering of
    two network calls loses neither — from a chosen risk into a safe one: a
    lost commit means the person is chased again, a duplicate rather than a
    silent drop."""

    def test_an_accepted_send_advances_attempts_and_is_not_rechased(self):
        """Provider took it. This is the only outcome that counts as a ping."""

    def test_a_rejected_send_does_not_advance_attempts_and_is_rechased(self):
        """Carrier refused, or a 2xx carrying a terminal status. We know it did
        not arrive, so the person is still silent and still owed a ping."""

    def test_an_unknown_send_is_rechased_and_recorded_as_unknown_not_failed(self):
        """The throw. Silence wearing delivery's clothes — the Firestore hang
        at a different layer.

        Re-chasing is right (missed is worse than duplicate, already decided),
        but the *record* must say unknown. A system that writes `failed` for a
        send that actually arrived will contradict a delivery receipt later,
        and 'the carrier refused' is a different instruction to an incident
        commander than 'we never heard back'."""

    def test_a_suppressed_send_is_not_an_outcome_to_retry(self):
        """Opted out is a decision, not a failure. Re-chasing forever would be
        the system arguing with someone who said STOP."""


class TestTheCriticIsOnTheDeliveryPath:
    def test_a_contradicting_intent_is_stripped_before_the_wire(self):
        """`notify._send` already runs `movement_policy.enforce()` immediately
        before transmission, and a test already proves that. This is a claim
        about the *wiring*: it only holds if the loop actually sends through
        `_send`. Push a lockdown-contradicting intent through the loop's
        delivery path — not through `_send` directly, which already passes."""

    def test_the_loop_has_no_second_door_to_the_wire(self):
        """One funnel, `_send`, so the critic cannot be bypassed and the
        outcome cannot be recorded in two places that disagree. The check-in
        mirror needed this discipline after the fact; delivery gets it first."""


class TestTheTransportTellsTheTruthAboutWhatItKnows:
    def test_a_thrown_send_is_not_reported_as_nothing_was_sent(self):
        """`send_sms` catches the exception and returns "transport error,
        nothing was sent". That asserts more than it knows: a request that left
        the process and then timed out may have been delivered. The honest
        wording is that the outcome is unknown."""

    def test_rejected_and_unknown_are_distinguishable_at_the_boundary(self):
        """Both are `delivered: False` today, so the loop cannot honour the
        three-state contract above until the transport surfaces which one."""

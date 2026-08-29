"""Twilio gets its 200 before we do any work.

A witness typed "shooter last seen heading toward the gym" and nothing came
back. Twilio's own log has the message with error 11200 — HTTP retrieval
failure — and Cloud Run has no request at all for it: the POST never completed.
The handler ran the whole pipeline (classification, an LLM call, Firestore)
inside the webhook, and Twilio gives a webhook 15 seconds. Under any load that
is a coin flip, and losing it drops a sighting during a live incident.

The reply was never dependent on the webhook response: every other message the
system sends goes out through the REST API. Only the inbound acknowledgement
was riding on the work finishing.
"""

import threading
import time
from unittest.mock import patch

import pytest

from src.services import whatsapp_transport


class TestTheAcknowledgementDoesNotWaitForTheWork:
    def test_a_slow_pipeline_still_acks_fast(self):
        """The property, stated in seconds: work that would blow Twilio's
        15-second budget must not delay the 200."""
        started = threading.Event()

        def slow(from_number, body):
            started.set()
            time.sleep(2.0)
            return {"reply": "done", "action": "observation"}

        with patch.object(whatsapp_transport, "handle_inbound_message", slow), \
             patch.object(whatsapp_transport, "send_reply_async", lambda *a: None):
            t0 = time.monotonic()
            whatsapp_transport.process_inbound_async("+16692167706", "a sighting")
            elapsed = time.monotonic() - t0

        assert elapsed < 0.5, f"the ack waited {elapsed:.2f}s on the pipeline"
        assert started.wait(timeout=2), "the work never started"

    def test_the_reply_goes_out_over_rest(self):
        sent = []
        with patch.object(whatsapp_transport, "handle_inbound_message",
                          lambda f, b: {"reply": "Noted.", "action": "observation"}), \
             patch.object(whatsapp_transport, "send_reply_async",
                          lambda to, text: sent.append((to, text))):
            whatsapp_transport.process_inbound_async("+16692167706", "a sighting")
            for _ in range(50):
                if sent:
                    break
                time.sleep(0.02)
        assert sent == [("+16692167706", "Noted.")]

    def test_a_pipeline_that_raises_does_not_kill_the_thread_silently(self):
        """A crash must still tell the sender something, or a person who
        reported a shooter's position is left believing nobody heard."""
        sent = []

        def boom(from_number, body):
            raise RuntimeError("classifier exploded")

        with patch.object(whatsapp_transport, "handle_inbound_message", boom), \
             patch.object(whatsapp_transport, "send_reply_async",
                          lambda to, text: sent.append((to, text))):
            whatsapp_transport.process_inbound_async("+16692167706", "a sighting")
            for _ in range(50):
                if sent:
                    break
                time.sleep(0.02)
        assert sent, "the sender was left with silence"
        assert "911" in sent[0][1]

    def test_an_empty_reply_sends_nothing(self):
        sent = []
        with patch.object(whatsapp_transport, "handle_inbound_message",
                          lambda f, b: {"reply": "", "action": "ignored"}), \
             patch.object(whatsapp_transport, "send_reply_async",
                          lambda to, text: sent.append((to, text))):
            whatsapp_transport.process_inbound_async("+16692167706", "x")
            time.sleep(0.2)
        assert sent == []


class TestTheWebhookRouteUsesIt:
    def test_the_twilio_route_does_not_run_the_pipeline_inline(self):
        """The seam. The unit above is useless if the route still blocks."""
        import inspect
        from src.core import server

        src = inspect.getsource(server)
        route = src[src.index('elif path == "/whatsapp/twilio":'):]
        route = route[:route.index('elif path == "/whatsapp":')]
        assert "process_inbound_async" in route
        assert "result = handle_inbound_message" not in route


class TestSlackVerificationIsBounded:
    def test_a_lookup_cannot_stall_a_tick(self):
        """Thirty unverified ids at the client's 30-second default is fifteen
        minutes of tick, and a tick that never finishes chases nobody."""
        from src.core import notify

        seen = {}

        class _Recording:
            def __init__(self, token, **kwargs):
                seen.update(kwargs)

            def users_info(self, user):
                return {"ok": True}

        with patch("src.services.slack_transport.WebClient", _Recording), \
             patch.dict("os.environ", {"SLACK_BOT_TOKEN": "xoxb-test"}):
            notify.reset_slack_id_cache()
            notify.slack_id_resolves("U0123REAL")
        assert seen.get("timeout") == notify.SLACK_LOOKUP_TIMEOUT_SECONDS
        assert 0 < notify.SLACK_LOOKUP_TIMEOUT_SECONDS <= 10

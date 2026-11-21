import json
import unittest

from aural import AuralClient, AuralError, DEFAULT_BASE_URL


def _envelope(data=None, cursor=None):
    payload = {}
    if data is not None:
        payload["data"] = data
    if cursor is not None:
        payload["cursor"] = cursor
    return payload


class FakeTransport:
    """Callable standing in for HTTP. responses is a list of (status, envelope)."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": headers,
                "body": json.loads(body.decode("utf-8")) if body is not None else None,
            }
        )
        if not self.responses:
            raise AssertionError("unexpected extra request to {}".format(url))
        status, payload = self.responses.pop(0)
        return status, json.dumps(payload)


def make_client(responses, **kwargs):
    transport = FakeTransport(responses)
    client = AuralClient(
        api_key="dlv_test_key",
        transport=transport,
        backoff_seconds=kwargs.pop("backoff_seconds", 0.0),
        max_retries=kwargs.pop("max_retries", 3),
    )
    return client, transport


class ClientBasicsTest(unittest.TestCase):
    def test_requires_api_key(self):
        import os

        saved = os.environ.pop("AURAL_API_KEY", None)
        os.environ.pop("AURAL_API_KEY", None)
        try:
            with self.assertRaises(ValueError):
                AuralClient()
        finally:
            if saved is not None:
                os.environ["AURAL_API_KEY"] = saved

    def test_create_interview_maps_fields(self):
        client, transport = make_client(
            [(200, {"data": {"id": "int_1", "title": "Screen"}})]
        )
        result = client.create_interview(
            "Screen",
            follow_up_depth="DEEP",
            anti_cheating_enabled=True,
            time_limit_minutes=45,
        )
        self.assertEqual(result["id"], "int_1")
        call = transport.calls[0]
        self.assertEqual(call["method"], "POST")
        self.assertEqual(call["url"], DEFAULT_BASE_URL + "/interviews")
        self.assertEqual(call["headers"]["Authorization"], "Bearer dlv_test_key")
        self.assertEqual(call["headers"]["Content-Type"], "application/json")
        self.assertEqual(
            call["body"],
            {"title": "Screen", "followUpDepth": "DEEP", "antiCheatingEnabled": True, "timeLimitMinutes": 45},
        )

    def test_create_interview_rejects_unknown_field(self):
        client, _ = make_client([])
        with self.assertRaises(ValueError):
            client.create_interview("Screen", bogusField=1)

    def test_publish_posts_empty_object(self):
        client, transport = make_client(
            [(200, {"data": {"id": "int_1", "publicSlug": "abc", "url": "https://aural-ai.com/i/abc"}})]
        )
        result = client.publish_interview("int_1")
        self.assertEqual(result["url"], "https://aural-ai.com/i/abc")
        self.assertEqual(transport.calls[0]["url"], DEFAULT_BASE_URL + "/interviews/int_1/publish")
        self.assertEqual(transport.calls[0]["body"], {})


class ErrorHandlingTest(unittest.TestCase):
    def test_error_envelope_raises_aural_error(self):
        client, _ = make_client(
            [(401, {"error": {"code": "UNAUTHORIZED", "message": "bad key"}})]
        )
        with self.assertRaises(AuralError) as ctx:
            client.list_interviews()
        self.assertEqual(ctx.exception.status_code, 401)
        self.assertEqual(ctx.exception.code, "UNAUTHORIZED")
        self.assertEqual(ctx.exception.message, "bad key")

    def test_plan_limit_error_keeps_extra_fields(self):
        client, _ = make_client(
            [
                (
                    403,
                    {
                        "error": {
                            "code": "PLAN_LIMIT_EXCEEDED",
                            "message": "Template limit reached.",
                            "limit": "templates",
                            "current": 50,
                            "max": 50,
                            "upgrade_url": "https://aural-ai.com/billing",
                        }
                    },
                )
            ]
        )
        with self.assertRaises(AuralError) as ctx:
            client.create_interview("Screen")
        exc = ctx.exception
        self.assertTrue(exc.is_plan_limit_exceeded)
        self.assertEqual(exc.payload["limit"], "templates")
        self.assertEqual(exc.payload["upgrade_url"], "https://aural-ai.com/billing")

    def test_missing_key_rejected_by_value_error_not_request(self):
        client, transport = make_client([])
        with self.assertRaises(ValueError):
            client.create_interview("")
        self.assertEqual(len(transport.calls), 0)


class RetryTest(unittest.TestCase):
    def test_429_retries_then_succeeds(self):
        client, transport = make_client(
            [
                (429, {"error": {"code": "RATE_LIMITED", "message": "slow down"}}),
                (200, {"data": []}),
            ],
            backoff_seconds=1.0,
        )
        sleeps = []
        import time as time_module

        original_sleep = time_module.sleep
        time_module.sleep = lambda seconds: sleeps.append(seconds)
        try:
            items, cursor = client.list_interviews(limit=10)
        finally:
            time_module.sleep = original_sleep
        self.assertEqual(items, [])
        self.assertIsNone(cursor)
        self.assertEqual(len(transport.calls), 2)
        self.assertEqual(sleeps, [1.0])

    def test_exhausted_attempts_raises(self):
        client, transport = make_client(
            [(429, {"error": {"code": "RATE_LIMITED", "message": "slow down"}})] * 4,
            max_retries=3,
        )
        import time as time_module

        original_sleep = time_module.sleep
        time_module.sleep = lambda seconds: None
        try:
            with self.assertRaises(AuralError) as ctx:
                client.list_interviews()
        finally:
            time_module.sleep = original_sleep
        self.assertEqual(ctx.exception.code, "RATE_LIMITED")
        self.assertEqual(len(transport.calls), 4)

    def test_non_retryable_error_fails_fast(self):
        client, transport = make_client(
            [(404, {"error": {"code": "NOT_FOUND", "message": "nope"}})] * 2
        )
        with self.assertRaises(AuralError):
            client.get_interview("int_missing")
        self.assertEqual(len(transport.calls), 1)


class PaginationTest(unittest.TestCase):
    def test_list_sessions_follows_cursor(self):
        client, transport = make_client(
            [
                (200, {"data": [{"id": "s1"}], "cursor": "cursor-1"}),
                (200, {"data": [{"id": "s2"}], "cursor": None}),
            ]
        )
        items, cursor = client.list_sessions("int_1")
        self.assertEqual([item["id"] for item in items], ["s1"])
        self.assertEqual(cursor, "cursor-1")

        self.assertEqual(transport.calls[0]["url"], DEFAULT_BASE_URL + "/interviews/int_1/sessions?limit=100")

        iter_client, transport2 = make_client(
            [
                (200, {"data": [{"id": "s1"}], "cursor": "cursor-1"}),
                (200, {"data": [{"id": "s2"}], "cursor": None}),
            ]
        )
        collected = [session["id"] for session in iter_client.iter_sessions("int_1")]
        self.assertEqual(collected, ["s1", "s2"])
        self.assertEqual(transport2.calls[1]["url"], DEFAULT_BASE_URL + "/interviews/int_1/sessions?limit=100&cursor=cursor-1")

    def test_limit_is_clamped(self):
        client, transport = make_client(
            [
                (200, {"data": [], "cursor": None}),
                (200, {"data": [], "cursor": None}),
            ]
        )
        client.list_interviews(limit=500)
        client.list_interviews(limit=0)
        self.assertIn("limit=100", transport.calls[0]["url"])
        self.assertIn("limit=1", transport.calls[1]["url"])


class AiFieldWaitTest(unittest.TestCase):
    def test_wait_for_ai_fields_polls_until_populated(self):
        client, _ = make_client(
            [
                (200, {"data": {"id": "s1", "summary": "", "themes": []}}),
                (200, {"data": {"id": "s1", "summary": "Good answers", "themes": ["system design"]}}),
            ]
        )
        sleeps = []
        import time as time_module

        original_sleep = time_module.sleep
        original_monotonic = time_module.monotonic

        time_module.sleep = lambda seconds: sleeps.append(seconds)
        time_module.monotonic = lambda: 0 if len(sleeps) < 1 else float("inf")

        try:
            session = client.wait_for_ai_fields("s1", fields=("summary",), timeout_seconds=10, poll_seconds=2)
        finally:
            time_module.sleep = original_sleep
            time_module.monotonic = original_monotonic

        self.assertEqual(session["summary"], "Good answers")
        self.assertEqual(sleeps, [2])


if __name__ == "__main__":
    unittest.main()

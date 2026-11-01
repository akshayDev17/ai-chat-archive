"""Tests for how the Worker learns who is asking, and what it logs when it can't.

A live test saw about one signed-in ``/api/desk/*`` call in six answer 401.
These pin the two things that answer it:

* ``ctx.access.getIdentity()`` gets one retry when it throws or has no email;
* every 401 logs one line naming the branch that fired, and no line ever
  carries the email, a cookie or a header value.

Run:  python3 backend/test/test_identity.py
"""

from __future__ import annotations

import asyncio
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from chat_archive.auth import CloudflareAccessIdentity  # noqa: E402

EMAIL = "reader@example.com"


class Headers:
    """Case-insensitive, like both the JS ``Headers`` and the SDK's request headers."""

    def __init__(self, **values: str):
        self._values = {k.lower().replace("_", "-"): v for k, v in values.items()}

    def get(self, name: str):
        return self._values.get(name.lower())


def request(path: str = "/api/desk/whoami", **headers: str):
    return SimpleNamespace(url=f"https://akshayprabhakant.com{path}?next=%2Fdesk", headers=Headers(**headers))


class FakeAccess:
    """``ctx.access`` whose ``getIdentity()`` plays back a script of outcomes."""

    def __init__(self, *outcomes):
        self._outcomes = list(outcomes)
        self.calls = 0

    async def getIdentity(self):
        self.calls += 1
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def run(coro):
    return asyncio.run(coro)


def found(email: str = EMAIL):
    return SimpleNamespace(email=email)


class IdentityCase(unittest.TestCase):
    def setUp(self):
        self.lines: list[str] = []

    def provider(self, access=None) -> CloudflareAccessIdentity:
        ctx = SimpleNamespace(access=access) if access is not None else SimpleNamespace()
        return CloudflareAccessIdentity(ctx, log=self.lines.append)

    def only_record(self) -> dict:
        self.assertEqual(len(self.lines), 1, self.lines)
        return json.loads(self.lines[0])

    def assert_logs_no_secrets(self, *secrets: str):
        for line in self.lines:
            for secret in (EMAIL, *secrets):
                self.assertNotIn(secret, line)


class TestAccessIdentity(IdentityCase):
    def test_a_first_time_answer_is_used_and_logs_nothing(self):
        access = FakeAccess(found("Reader@Example.com"))
        identity = run(self.provider(access).identify(request()))
        self.assertEqual(identity.email, EMAIL)
        self.assertEqual(access.calls, 1)
        self.assertEqual(self.lines, [])

    def test_absent_ctx_access_is_a_logged_401(self):
        self.assertIsNone(run(self.provider().identify(request())))
        record = self.only_record()
        self.assertEqual(record["event"], "desk_identity_missing")
        self.assertEqual(record["branch"], "access_absent")
        self.assertEqual(record["path"], "/api/desk/whoami")

    def test_resolving_without_an_email_is_retried_once_then_logged(self):
        access = FakeAccess(None, SimpleNamespace(email=""))
        self.assertIsNone(run(self.provider(access).identify(request("/api/desk/media"))))
        self.assertEqual(access.calls, 2)
        record = self.only_record()
        self.assertEqual(record["branch"], "resolved_without_email")
        self.assertEqual([a["branch"] for a in record["attempts"]], ["resolved_without_email"] * 2)
        self.assertEqual(record["path"], "/api/desk/media")

    def test_a_retry_that_finds_the_email_rescues_the_request_and_says_so(self):
        access = FakeAccess(None, found())
        identity = run(self.provider(access).identify(request()))
        self.assertEqual(identity.email, EMAIL)
        record = self.only_record()
        self.assertEqual(record["event"], "desk_identity_recovered")
        self.assertEqual(record["branch"], "resolved_without_email")
        self.assert_logs_no_secrets()

    def test_a_throwing_lookup_fails_closed_with_its_class_and_message(self):
        access = FakeAccess(RuntimeError("identity service busy"), TimeoutError("slow"))
        self.assertIsNone(run(self.provider(access).identify(request())))
        record = self.only_record()
        self.assertEqual(record["branch"], "get_identity_threw")
        self.assertEqual(
            record["attempts"],
            [
                {"branch": "get_identity_threw", "error_class": "RuntimeError", "error": "identity service busy"},
                {"branch": "get_identity_threw", "error_class": "TimeoutError", "error": "slow"},
            ],
        )

    def test_a_throw_then_an_answer_is_a_rescue(self):
        identity = run(self.provider(FakeAccess(RuntimeError("blip"), found())).identify(request()))
        self.assertEqual(identity.email, EMAIL)
        self.assertEqual(self.only_record()["event"], "desk_identity_recovered")

    def test_a_raw_email_header_is_never_an_identity(self):
        req = request(cf_access_authenticated_user_email=EMAIL, cookie="CF_Authorization=abc")
        self.assertIsNone(run(self.provider(FakeAccess(None, None)).identify(req)))
        self.assertEqual(self.only_record()["event"], "desk_identity_missing")
        self.assert_logs_no_secrets("CF_Authorization", "abc")


if __name__ == "__main__":
    unittest.main()

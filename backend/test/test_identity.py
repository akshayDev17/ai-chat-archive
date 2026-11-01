"""Tests for how the Worker learns who is asking, and what it logs when it can't.

A live test saw about one signed-in ``/api/desk/*`` call in six answer 401.
These pin the three things that answer it:

* ``ctx.access.getIdentity()`` gets one retry when it throws or has no email;
* after that, Access's signed ``Cf-Access-Jwt-Assertion`` token is accepted
  only once its signature, audience, issuer and lifetime verify — a forged,
  re-signed, foreign or expired token is not an identity;
* every 401 logs one line naming the branch that fired, and no line ever
  carries the email, the token or a header value.

Runs offline: the RSA keys below are test-only, generated for this file.

Run:  python3 backend/test/test_identity.py
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from chat_archive import access_jwt  # noqa: E402
from chat_archive.access_jwt import (  # noqa: E402
    AccessJwtFallback,
    InvalidAccessJwt,
    email_from_verified_token,
    rsa_sha256_signature_valid,
)
from chat_archive.auth import CloudflareAccessIdentity  # noqa: E402

EMAIL = "reader@example.com"
TEAM = "team.cloudflareaccess.com"
ISSUER = f"https://{TEAM}"
AUD = "desk-aud-tag"
NOW = 1_800_000_000
KID = "access-key-1"

# fmt: off
ACCESS_N = int(
    "95b4d014d03dd502d5e458efe2ea6732e34141573b8ab6a367f7108c47f0dfc89efaca9c"
    "f02fa24b01127f6059839bb40f7d48d66608c312c9587e27b88a6b92fefd4bc29c311f8c"
    "4c4fbb925d8388c3e524b6e7fbf77c2cf0e126c17f696fedffb55a880e80bb19b7582817"
    "20a1cd7bb7f63b25bca33c81daff0f4e32f1a422516f89058eed6ad1c4fd39a8300fcd44"
    "e2b20641a30c34e8f8ac5b52836a14dee53627b367a99ec6a0241f4dca66fe71b0ff5b09"
    "c9a453477093b0bd67544d0fa41fbf21fdb4aecdc98e77edf03b3596c9e18d21d81df05b"
    "8cd0429bcbed2e6acadf88270172785930ace945f1825b77b25c55e997e962cef8b21176"
    "6369e31b",
    16,
)
ACCESS_D = int(
    "62d8a82b95f3bd99c9b0a428802355507c4cb1dfef287fbf14fe44a6db396809072f8068"
    "6f0e8492117b2d2f68eb44fb76deba2115ede31f9c6219aa106714f051fcdcf0fa40551c"
    "45a6bfaabca404c193a2d2420a543661560ad3941f94769b26a38fcb16085981795573e3"
    "1160c45453faf8c2c9f2de18bf88798aa0887c5e49e1bdfeb5427d2a0c4896c46382117e"
    "d2c8d89a73883f9d5ba37640529aa8a60f1d7ac60ca31b5e098f64fb833ac20255eeea72"
    "59b499b007cbb25dd78e1242910815d4eb94ab65d508e941e1e122da723dc3c6170e49eb"
    "32e9891d3985a338e59ea97944d9a6e94d7ed1fe204e0b7207a309a35f247ae601eb3c4a"
    "1bdb519",
    16,
)
ATTACKER_N = int(
    "be9981b2bc9e3e6d9bf7990e5fd74e243ca5d0ca843cfd475278543f1b03eae565afb139"
    "b817f2848f5d5d6c4c3e6f63d6fd4db04b716311020de451d8b7eeb237a1599155d7d73f"
    "1643af8d8acd5b49a4668c3ab1e7181fee1e5137f5c49399ad39e204e6b3e9d8da88c391"
    "840810223755cdd737ef37ba2a0db8bc597f41df",
    16,
)
ATTACKER_D = int(
    "b1e4b4990e52e41189b0deb9fbda08a4e9b619d9eaf2c1b2ffd62f4b76fb8111fa86b0f3"
    "1e4fbb9addf4566dedfb981ba75d64555714cd51e288f4c12f74fce2dc9a9897d271a833"
    "e5733cbf10a1cb32a4afcded6a56478ffa800bafd86ccec90dbbe3b0f5f0aa6efa706d07"
    "bb09b4fa89cda20075436393f6a569a3178c6b9",
    16,
)
# fmt: on
E = 65537
_DIGEST_INFO = bytes.fromhex("3031300d060960864801650304020105000420")


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64_int(n: int) -> str:
    return _b64(n.to_bytes((n.bit_length() + 7) // 8, "big"))


def sign(message: bytes, n: int, d: int) -> bytes:
    k = (n.bit_length() + 7) // 8
    digest = _DIGEST_INFO + hashlib.sha256(message).digest()
    encoded = b"\x00\x01" + b"\xff" * (k - len(digest) - 3) + b"\x00" + digest
    return pow(int.from_bytes(encoded, "big"), d, n).to_bytes(k, "big")


def make_token(header: dict | None = None, n: int = ACCESS_N, d: int = ACCESS_D, **claims) -> str:
    header = {"alg": "RS256", "kid": KID, **(header or {})}
    body = {"aud": [AUD], "iss": ISSUER, "exp": NOW + 600, "iat": NOW - 60, "email": EMAIL, **claims}
    body = {k: v for k, v in body.items() if v is not None}
    signing_input = f"{_b64(json.dumps(header).encode())}.{_b64(json.dumps(body).encode())}"
    return f"{signing_input}.{_b64(sign(signing_input.encode(), n, d))}"


def certs(*keys: tuple[str, int]) -> dict:
    return {"keys": [{"kid": kid, "kty": "RSA", "alg": "RS256", "e": "AQAB", "n": _b64_int(n)} for kid, n in keys]}


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


class FakeCerts:
    def __init__(self, *responses):
        self._responses = list(responses)
        self.urls: list[str] = []

    async def __call__(self, url: str) -> dict:
        self.urls.append(url)
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def run(coro):
    return asyncio.run(coro)


def found(email: str = EMAIL):
    return SimpleNamespace(email=email)


class IdentityCase(unittest.TestCase):
    def setUp(self):
        access_jwt._KEYS.clear()
        self.lines: list[str] = []

    def provider(self, access=None, fallback=None) -> CloudflareAccessIdentity:
        ctx = SimpleNamespace(access=access) if access is not None else SimpleNamespace()
        return CloudflareAccessIdentity(ctx, fallback, log=self.lines.append)

    def fallback(self, fetch=None) -> AccessJwtFallback:
        return AccessJwtFallback(TEAM, AUD, fetch or FakeCerts(certs((KID, ACCESS_N))), lambda: NOW)

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
        self.assertEqual(record["jwt_fallback"], "not_configured")

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
        self.assertNotIn("jwt_fallback", record)
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
        self.assertIsNone(run(self.provider(FakeAccess(None, None), self.fallback()).identify(req)))
        record = self.only_record()
        self.assertEqual(record["jwt_fallback"], "no_assertion")
        self.assert_logs_no_secrets("CF_Authorization", "abc")


class TestJwtFallback(IdentityCase):
    def test_a_verified_token_rescues_an_empty_lookup_without_logging_it(self):
        token = make_token()
        req = request(cf_access_jwt_assertion=token)
        identity = run(self.provider(FakeAccess(None, None), self.fallback()).identify(req))
        self.assertEqual(identity.email, EMAIL)
        record = self.only_record()
        self.assertEqual(record["event"], "desk_identity_recovered")
        self.assertEqual(record["jwt_fallback"], "verified")
        self.assert_logs_no_secrets(token, token.split(".")[2])

    def test_it_also_covers_an_absent_ctx_access(self):
        req = request(cf_access_jwt_assertion=make_token(aud=AUD))
        identity = run(self.provider(None, self.fallback()).identify(req))
        self.assertEqual(identity.email, EMAIL)
        self.assertEqual(self.only_record()["branch"], "access_absent")

    def test_untrustworthy_tokens_are_401s_that_name_the_reason(self):
        genuine = make_token()
        head, body, sig = genuine.split(".")
        other_body = _b64(json.dumps({"aud": [AUD], "iss": ISSUER, "exp": NOW + 600, "email": "boss@example.com"}).encode())
        cases = {
            "re-addressed": (f"{head}.{other_body}.{sig}", "bad_signature"),
            "self-signed": (make_token(n=ATTACKER_N, d=ATTACKER_D), "bad_signature"),
            "unsigned": (f"{_b64(json.dumps({'alg': 'none', 'kid': KID}).encode())}.{body}.", "unsupported_alg"),
            "HS256": (make_token(header={"alg": "HS256"}), "unsupported_alg"),
            "other app": (make_token(aud=["quiz-aud-tag"]), "wrong_audience"),
            "other team": (make_token(iss="https://evil.cloudflareaccess.com"), "wrong_issuer"),
            "expired": (make_token(exp=NOW - 1), "expired"),
            "no expiry": (make_token(exp=None), "expired"),
            "early": (make_token(nbf=NOW + 60), "not_yet_valid"),
            "service token": (make_token(email=None, common_name="ci.access"), "no_email"),
            "garbage": ("not-a-jwt", "malformed"),
            "list kid": (make_token(header={"kid": [KID]}), "unknown_kid"),
        }
        for name, (token, reason) in cases.items():
            with self.subTest(name):
                self.lines.clear()
                fetch = FakeCerts(certs((KID, ACCESS_N)), certs((KID, ACCESS_N)))
                req = request(cf_access_jwt_assertion=token)
                self.assertIsNone(run(self.provider(FakeAccess(None, None), self.fallback(fetch)).identify(req)))
                record = self.only_record()
                self.assertEqual(record["event"], "desk_identity_missing")
                self.assertEqual(record["jwt_fallback"], reason)
                self.assert_logs_no_secrets(token)

    def test_keys_are_fetched_once_and_refetched_for_a_rotated_kid(self):
        fetch = FakeCerts(certs((KID, ACCESS_N)), certs((KID, ACCESS_N), ("access-key-2", ACCESS_N)))
        fallback = self.fallback(fetch)
        self.assertEqual(run(fallback.email_from(make_token())), EMAIL)
        self.assertEqual(run(fallback.email_from(make_token())), EMAIL)
        self.assertEqual(fetch.urls, [f"https://{TEAM}/cdn-cgi/access/certs"])
        self.assertEqual(run(fallback.email_from(make_token(header={"kid": "access-key-2"}))), EMAIL)
        self.assertEqual(len(fetch.urls), 2)

    def test_an_unknown_kid_after_a_refetch_is_rejected(self):
        fetch = FakeCerts(certs((KID, ACCESS_N)), certs((KID, ACCESS_N)))
        with self.assertRaises(InvalidAccessJwt) as caught:
            run(self.fallback(fetch).email_from(make_token(header={"kid": "nobody"})))
        self.assertEqual(caught.exception.reason, "unknown_kid")

    def test_unreachable_certs_fail_closed(self):
        fetch = FakeCerts(OSError("dns"))
        req = request(cf_access_jwt_assertion=make_token())
        self.assertIsNone(run(self.provider(FakeAccess(None, None), self.fallback(fetch)).identify(req)))
        self.assertEqual(self.only_record()["jwt_fallback"], "certs_unavailable:OSError")


class TestRs256(unittest.TestCase):
    def test_signature_check(self):
        message = b"header.payload"
        signature = sign(message, ACCESS_N, ACCESS_D)
        self.assertTrue(rsa_sha256_signature_valid(ACCESS_N, E, message, signature))
        self.assertFalse(rsa_sha256_signature_valid(ACCESS_N, E, b"header.payloaD", signature))
        self.assertFalse(rsa_sha256_signature_valid(ACCESS_N, E, message, signature[:-1]))
        self.assertFalse(rsa_sha256_signature_valid(ACCESS_N, E, message, b"\xff" * len(signature)))

    def test_claims_are_checked_against_the_configured_app(self):
        keys = {KID: (ACCESS_N, E)}
        token = make_token()
        self.assertEqual(email_from_verified_token(token, keys, issuer=ISSUER, audience=AUD, now=NOW), EMAIL)
        with self.assertRaises(InvalidAccessJwt):
            email_from_verified_token(token, keys, issuer=ISSUER, audience="another-app", now=NOW)


if __name__ == "__main__":
    unittest.main()

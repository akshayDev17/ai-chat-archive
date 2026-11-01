"""The Access application token, verified — the identity fallback.

``ctx.access.getIdentity()`` is the documented way for a Worker to learn who
Access let in, but on the deployed Worker it intermittently comes back empty
for a reader whose session is valid: sibling ``/api/desk/*`` calls carrying the
same cookies split 200/401. When it does, Access has still attached its signed
application token to the request:

  "When Cloudflare sends a request to your origin, the request will include an
  application token as a ``Cf-Access-Jwt-Assertion`` request header."
  https://developers.cloudflare.com/cloudflare-one/identity/authorization-cookie/validating-json/

That page also says what makes the token trustworthy, and it is **not** the
header: anyone can send a header. "Cloudflare signs the token with a key pair
unique to your account", and "you should validate the token with your public
key to ensure that the request came from Access", checking that ``aud`` names
this application and that ``iss`` is the team domain. So the email is read only
from a token whose RS256 signature verifies against the team's published keys
(``https://<team>.cloudflareaccess.com/cdn-cgi/access/certs``) and whose
audience, issuer and lifetime all check out. Anything else is rejected, and the
rejection names its reason so the 401 log says why the fallback did not help.

The verification is plain Python (``pow`` and ``hashlib``), so it runs, and is
tested, without the Workers runtime.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from typing import Awaitable, Callable

#: Fetch a URL and return its JSON body. The Worker passes one backed by
#: ``js.fetch``; tests pass a fake.
FetchJson = Callable[[str], Awaitable[dict]]

#: The DER prefix of a SHA-256 DigestInfo (RFC 8017 §9.2, note 1).
_SHA256_DIGEST_INFO = bytes.fromhex("3031300d060960864801650304020105000420")

#: Signing keys by certs URL, kept for the isolate's lifetime. Access publishes
#: the next key before rotating to it, so a cached set only goes stale when a
#: token names a ``kid`` it lacks — which triggers one refetch.
_KEYS: dict[str, dict[str, tuple[int, int]]] = {}


class InvalidAccessJwt(Exception):
    """The token cannot be trusted. ``reason`` is safe to log; the token is not in it."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _b64url(segment: str) -> bytes:
    return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))


def _int_of(segment: str) -> int:
    return int.from_bytes(_b64url(segment), "big")


def rsa_sha256_signature_valid(n: int, e: int, message: bytes, signature: bytes) -> bool:
    """RSASSA-PKCS1-v1_5 with SHA-256 (RFC 8017 §8.2.2): the JWS ``RS256`` check."""
    k = (n.bit_length() + 7) // 8
    if len(signature) != k or k < len(_SHA256_DIGEST_INFO) + 32 + 11:
        return False
    s = int.from_bytes(signature, "big")
    if s >= n:
        return False
    encoded = pow(s, e, n).to_bytes(k, "big")
    digest = _SHA256_DIGEST_INFO + hashlib.sha256(message).digest()
    expected = b"\x00\x01" + b"\xff" * (k - len(digest) - 3) + b"\x00" + digest
    return hmac.compare_digest(encoded, expected)


def keys_from_certs(certs: dict) -> dict[str, tuple[int, int]]:
    """The RS256 public keys in a certs response, as ``{kid: (n, e)}``."""
    return {
        key["kid"]: (_int_of(key["n"]), _int_of(key["e"]))
        for key in certs.get("keys", [])
        if key.get("kty") == "RSA" and key.get("kid") and key.get("n") and key.get("e")
    }


def email_from_verified_token(
    token: str,
    keys: dict[str, tuple[int, int]],
    *,
    issuer: str,
    audience: str,
    now: float,
) -> str:
    """The token's email, or :class:`InvalidAccessJwt` if any check fails.

    ``keys`` must hold the token's ``kid``; :meth:`AccessJwtFallback.email_from`
    deals with fetching them.
    """
    try:
        header_b64, payload_b64, signature_b64 = token.split(".")
        header = json.loads(_b64url(header_b64))
        claims = json.loads(_b64url(payload_b64))
        signature = _b64url(signature_b64)
    except (ValueError, TypeError):
        raise InvalidAccessJwt("malformed") from None
    if not isinstance(header, dict) or not isinstance(claims, dict):
        raise InvalidAccessJwt("malformed")

    if header.get("alg") != "RS256":
        raise InvalidAccessJwt("unsupported_alg")
    kid = header.get("kid")
    key = keys.get(kid) if isinstance(kid, str) else None
    if key is None:
        raise InvalidAccessJwt("unknown_kid")
    if not rsa_sha256_signature_valid(*key, f"{header_b64}.{payload_b64}".encode(), signature):
        raise InvalidAccessJwt("bad_signature")

    aud = claims.get("aud")
    if audience not in (aud if isinstance(aud, list) else [aud]):
        raise InvalidAccessJwt("wrong_audience")
    if claims.get("iss") != issuer:
        raise InvalidAccessJwt("wrong_issuer")
    exp = claims.get("exp")
    if not isinstance(exp, (int, float)) or exp <= now:
        raise InvalidAccessJwt("expired")
    nbf = claims.get("nbf")
    if isinstance(nbf, (int, float)) and nbf > now:
        raise InvalidAccessJwt("not_yet_valid")

    email = claims.get("email")
    if not isinstance(email, str) or not email:
        # A service token authenticates a machine, not a reader.
        raise InvalidAccessJwt("no_email")
    return email


class AccessJwtFallback:
    """Verify a request's ``Cf-Access-Jwt-Assertion`` against one Access app."""

    def __init__(self, team_domain: str, audience: str, fetch_json: FetchJson, clock: Callable[[], float]):
        self._issuer = f"https://{team_domain}"
        self._certs_url = f"https://{team_domain}/cdn-cgi/access/certs"
        self._audience = audience
        self._fetch_json = fetch_json
        self._clock = clock

    async def _keys(self, refresh: bool) -> dict[str, tuple[int, int]]:
        if refresh or self._certs_url not in _KEYS:
            try:
                _KEYS[self._certs_url] = keys_from_certs(await self._fetch_json(self._certs_url))
            except Exception as exc:
                raise InvalidAccessJwt(f"certs_unavailable:{type(exc).__name__}") from None
        return _KEYS[self._certs_url]

    async def email_from(self, token: str) -> str:
        """The verified email, or :class:`InvalidAccessJwt` naming why not."""
        check = dict(issuer=self._issuer, audience=self._audience, now=self._clock())
        try:
            return email_from_verified_token(token, await self._keys(refresh=False), **check)
        except InvalidAccessJwt as exc:
            if exc.reason != "unknown_kid":
                raise
        return email_from_verified_token(token, await self._keys(refresh=True), **check)

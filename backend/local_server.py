"""Local development server for the backend.

The Cloudflare Worker runs inside a runtime that provides ``js.fetch`` and the
D1 binding. Plain Python has neither, so this file adapts those seams:

  * a local fetch built on urllib (so the share page can still be fetched),
  * the SQLite repository, which mirrors the D1 schema exactly, and
  * a **real sign-in**, so the signed-out and signed-in states are both
    reachable locally instead of one being hardcoded.

It reuses the exact same ports / service / parser / decoder / router semantics
as the Worker, so what runs here is the real backend logic, just wired to local
stand-ins. The public-read / private-filing split is enforced identically:
``GET /api/sessions`` and ``GET /api/sessions/<id>`` are open to anyone, while
``POST /api/desk/ingest`` and ``GET /api/desk/filings`` are 401 without an identity.

Identity, in precedence order:

  1. ``X-Archive-Email`` header — for scripted tests; can force anonymous with
     the empty-header form ``curl -H 'X-Archive-Email;'``.
  2. the ``archive_dev_email`` cookie, set by ``POST /api/dev/session``.
  3. ``DEV_EMAIL``, if set — an auto-identity for when you do not want to sign
     in at all.

**The default is anonymous.** It used to default to a fixed address, which
meant ``/api/desk/whoami`` always answered, the masthead always rendered its
signed-in state, and the sign-in screen could never be reached — the exact flow
the site is built around was unverifiable on the only machine you can run it on.
Use ``DEV_EMAIL=you@example.com`` if you want the old always-signed-in
behaviour back.

Run:  python3 backend/local_server.py     (then hit http://127.0.0.1:8787)

Environment:
  PORT             listen port (default 8787)
  DB_PATH          SQLite file (default backend/archive.db)
  DEV_EMAIL        auto-identity; EMPTY BY DEFAULT (anonymous), so the sign-in
                   flow is walkable. Set it to skip signing in locally.
  ARCHIVE_LEGACY_OWNER  owner assigned to pre-ownership rows by the migration.
"""

from __future__ import annotations

import asyncio
import json
import os
import urllib.request
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from chat_archive.chatgpt.decoder import ChatGptFlightDecoder
from chat_archive.chatgpt.fetcher import HttpShareFetcher
from chat_archive.chatgpt.parser import ChatGptParser
from chat_archive.models import normalize_email
from chat_archive.serializers import conversation_detail
from chat_archive.service import ShareService
from chat_archive.sqlite_repository import SqliteConversationRepository
from chat_archive.urls import first_param, query_of, safe_next

DEV_EMAIL = normalize_email(os.environ.get("DEV_EMAIL", ""))

#: Cookie holding the locally signed-in address. Host-only, and only ever set by
#: this file — the Worker has no equivalent endpoint, because there a real
#: identity provider is the whole point.
DEV_COOKIE = "archive_dev_email"

ALLOWED_HEADERS = "content-type, x-archive-email"

#: Every method this server implements, for the CORS preflight to advertise.
#
# The browser sends ``OPTIONS`` before any request whose method is not *simple*
# (GET, HEAD, POST) and refuses the real request if the response does not list
# it. ``DELETE`` was missing here, so sign-out failed in the browser with an
# opaque "Failed to fetch" while ``curl`` — which never preflights — kept
# passing. Same trap as the ``Access-Control-Allow-Origin: *`` bug: an HTTP
# client that does not run the browser's CORS rules cannot prove a browser path
# works. When a method is added to the Handler, add it here too.
ALLOWED_METHODS = "GET,POST,DELETE,OPTIONS"


class LocalResponse:
    """Minimal stand-in for the JS Response object HttpShareFetcher expects."""

    def __init__(self, status: int, body: str):
        self.status = status
        self._body = body

    async def text(self) -> str:
        return self._body


def make_local_fetch():
    async def local_fetch(url: str, headers=None, redirect: str = "follow"):
        def _get() -> LocalResponse:
            req = urllib.request.Request(url, headers=headers or {})
            with urllib.request.urlopen(req, timeout=30) as resp:
                return LocalResponse(resp.status, resp.read().decode("utf-8", "replace"))

        return await asyncio.to_thread(_get)

    return local_fetch


_DB_PATH = os.environ.get("DB_PATH", str(Path(__file__).resolve().parent / "archive.db"))
repo = SqliteConversationRepository(_DB_PATH)
service = ShareService(
    fetcher=HttpShareFetcher(make_local_fetch()),
    decoder=ChatGptFlightDecoder(),
    parser=ChatGptParser(),
)


class Handler(BaseHTTPRequestHandler):
    # ------------------------------------------------------------------ helpers

    def _cookie(self, name: str) -> str | None:
        raw = self.headers.get("Cookie")
        if not raw:
            return None
        try:
            jar = SimpleCookie(raw)
        except Exception:
            return None
        morsel = jar.get(name)
        return morsel.value if morsel else None

    def _identity(self) -> str:
        """The caller's email, or '' when anonymous.

        Precedence: the ``X-Archive-Email`` header (scripted tests, and the
        empty form to force anonymous), then the sign-in cookie, then
        ``DEV_EMAIL`` if one was configured. Anonymous otherwise — which is the
        point: the default must be the state a real visitor arrives in, or the
        sign-in flow cannot be exercised.

        Both of the first two exist **only** in this local server. The deployed
        Worker derives identity from its ``IdentityProvider`` and never trusts a
        client-supplied header or cookie, because a client can lie — and a local
        dev endpoint that minted identities would be exactly the thing that must
        never ship.
        """
        header = self.headers.get("X-Archive-Email")
        if header is not None:
            return normalize_email(header)
        cookie = self._cookie(DEV_COOKIE)
        if cookie:
            return normalize_email(cookie)
        return DEV_EMAIL

    def _cors_headers(self):
        """CORS headers that survive ``credentials: 'include'``.

        The frontend sends ``credentials: 'include'`` (so the real auth cookie
        would be carried in production). A wildcard ``Access-Control-Allow-Origin:
        *`` is **illegal together with credentials** — the browser rejects the
        response outright, and every API call fails with an opaque "Failed to
        fetch" that ``curl`` never reproduces, because curl does not enforce
        CORS. So the request's own ``Origin`` is echoed back, which is exactly
        what an allowlist-echoing server does, plus ``Vary: Origin`` so caches
        do not mix the two.
        """
        origin = self.headers.get("Origin")
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Credentials", "true")
            self.send_header("Vary", "Origin")

    def _json(self, obj, status=200, cookies: list[str] | None = None):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self._cors_headers()
        for cookie in cookies or []:
            self.send_header("Set-Cookie", cookie)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, location: str, status: int = 302):
        self.send_response(status)
        self.send_header("Location", location)
        self._cors_headers()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _cors_preflight(self):
        self.send_response(204)
        self._cors_headers()
        self.send_header("Access-Control-Allow-Methods", ALLOWED_METHODS)
        self.send_header("Access-Control-Allow-Headers", ALLOWED_HEADERS)
        self.end_headers()

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        try:
            return json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return None

    def _dev_sign_in(self):
        """Sign in locally by setting a cookie — no OTP, no email provider.

        This is the *only* way to reach the signed-in state locally, and it
        exists so the real flow can be walked end to end: public edition →
        Sign in → login screen → desk. It deliberately does **not** validate the
        address or send a code; the point is to stand in for whatever the
        production identity provider will do, not to imitate it.

        It cannot ship: this file is the local runner, and the Worker
        (`entry.py`) has no equivalent route — there, an identity provider is
        the whole point.
        """
        body = self._read_body()
        if body is None:
            return self._json({"error": "invalid JSON"}, 400)

        email = normalize_email((body or {}).get("email"))
        if not email or "@" not in email:
            return self._json({"error": "a valid email is required"}, 400)

        return self._json(
            {"email": email},
            cookies=[f"{DEV_COOKIE}={email}; Path=/; SameSite=Lax"],
        )

    # ------------------------------------------------------------------- verbs

    def do_OPTIONS(self):
        self._cors_preflight()

    def do_DELETE(self):
        """Sign out locally."""
        if self.path.split("?", 1)[0].rstrip("/") == "/api/dev/session":
            return self._json(
                {"email": None},
                cookies=[f"{DEV_COOKIE}=; Path=/; Max-Age=0; SameSite=Lax"],
            )
        self._json({"error": "not found"}, 404)

    def do_GET(self):
        path = self.path.split("?", 1)[0].rstrip("/")

        if path == "/api/health":
            return self._json(
                {
                    "ok": True,
                    "runtime": "local-server",
                    # A boolean, not the address. This endpoint is unauthenticated
                    # even locally, and an endpoint that echoes an email is an
                    # endpoint that leaks one the moment it is exposed by a
                    # tunnel or a mis-set PORT. Whether a dev identity exists is
                    # the whole debugging value; the value itself adds nothing.
                    "dev_identity_configured": bool(DEV_EMAIL),
                    "signed_in": bool(self._identity()),
                }
            )

        if path == "/api/desk/whoami":
            # Mirrors the Worker: the identity question lives under /api/desk/ so
            # it sits inside the path Cloudflare Access covers in production.
            email = self._identity()
            if not email:
                return self._json({"error": "sign-in required"}, 401)
            return self._json({"email": email})

        if path == "/api/desk/enter":
            # The sign-in handoff. In production this path sits behind Access,
            # so reaching it *means* Cloudflare already authenticated the
            # visitor and we only have to send them onward. Locally there is no
            # Access to do that, so this mirrors the post-authentication half
            # (redirect to `next`) and 401s when there is no identity.
            email = self._identity()
            if not email:
                return self._json({"error": "sign-in required"}, 401)
            query = query_of(self.path)
            return self._redirect(safe_next(first_param(query, "next")))

        if path == "/api/sessions":
            # Public: the edition is a newspaper, so the front page needs no
            # identity. Owned by nobody in particular — every filer's copy.
            return self._json({"sessions": asyncio.run(repo.list_all())})

        if path == "/api/desk/filings":
            email = self._identity()
            if not email:
                return self._json({"error": "sign-in required"}, 401)
            return self._json(
                {"owner": email, "sessions": asyncio.run(repo.list_recent(email))}
            )

        if path.startswith("/api/sessions/"):
            # Public: a story is readable by permalink with no identity.
            share_id = path.rsplit("/", 1)[-1]
            conversation = asyncio.run(repo.get(share_id))
            if conversation is None:
                return self._json({"error": "not found"}, 404)
            return self._json(conversation_detail(conversation))

        self._json({"error": "not found"}, 404)

    def do_POST(self):
        path = self.path.split("?", 1)[0].rstrip("/")

        if path == "/api/dev/session":
            return self._dev_sign_in()

        if path != "/api/desk/ingest":
            return self._json({"error": "not found"}, 404)

        email = self._identity()
        if not email:
            return self._json({"error": "sign-in required"}, 401)

        body = self._read_body()
        if body is None:
            return self._json({"error": "invalid JSON"}, 400)

        share_url = (body or {}).get("share_url", "")
        try:
            conversation = asyncio.run(service.ingest(share_url))
            asyncio.run(repo.upsert(conversation.owned_by(email)))
            self._json(
                {
                    "share_id": conversation.share_id,
                    "title": conversation.title,
                    "messages": len(conversation.messages),
                    "report_length": len(conversation.report or ""),
                    "citations": len(conversation.citations),
                    "owner": email,
                }
            )
        except Exception as exc:  # noqa: BLE001 - surface any ingest failure
            self._json({"error": str(exc)}, 400)

    def log_message(self, *_args):
        pass


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8787"))
    print(f"local backend listening on http://127.0.0.1:{port}")
    print(f"  db       : {_DB_PATH}")
    if DEV_EMAIL:
        print(f"  identity : auto-signed-in as {DEV_EMAIL} (DEV_EMAIL is set)")
    else:
        print("  identity : anonymous — sign in at /chat-archives/login to file")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()

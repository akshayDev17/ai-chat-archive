"""Local development server for the backend.

The Cloudflare Worker runs inside a runtime that provides ``js.fetch`` and the
D1 binding. Plain Python has neither, so this file adapts those seams:

  * a local fetch built on urllib (so the share page can still be fetched),
  * the SQLite repository, which mirrors the D1 schema exactly, and
  * :class:`DevIdentity`, a fixed local reader — the stand-in for Cloudflare
    Access.

It reuses the exact same ports / service / parser / decoder / router semantics
as the Worker, so what runs here is the real backend logic, just wired to local
stand-ins. The public-read / private-archive split is enforced identically:
``GET /api/sessions`` is 401 without an identity, ``GET /api/sessions/<id>`` is
open to anyone.

Run:  python3 backend/local_server.py     (then hit http://127.0.0.1:8787)

Environment:
  PORT             listen port (default 8787)
  DB_PATH          SQLite file (default backend/archive.db)
  DEV_EMAIL        the local reader (default akshay@akshayprabhakant.com).
                   Set DEV_EMAIL= to simulate a signed-out visitor.
  ARCHIVE_LEGACY_OWNER  owner assigned to pre-ownership rows by the migration.
"""

from __future__ import annotations

import asyncio
import json
import os
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from chat_archive.chatgpt.decoder import ChatGptFlightDecoder
from chat_archive.chatgpt.fetcher import HttpShareFetcher
from chat_archive.chatgpt.parser import ChatGptParser
from chat_archive.models import normalize_email
from chat_archive.serializers import conversation_detail
from chat_archive.service import ShareService
from chat_archive.sqlite_repository import SqliteConversationRepository

DEV_EMAIL = normalize_email(
    os.environ.get("DEV_EMAIL", "akshay@akshayprabhakant.com")
)

ALLOWED_HEADERS = "content-type, x-archive-email"


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

    def _identity(self) -> str:
        """The caller's email, or '' when anonymous.

        ``X-Archive-Email`` lets a test drive the signed-out and
        second-reader paths; otherwise the process-wide ``DEV_EMAIL`` applies.
        This header exists **only** in this local server — the deployed Worker
        derives identity from Cloudflare Access, never from a client-supplied
        header, because a client can lie.
        """
        header = self.headers.get("X-Archive-Email")
        if header is not None:
            return normalize_email(header)
        return DEV_EMAIL

    def _json(self, obj, status=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _cors_preflight(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", ALLOWED_HEADERS)
        self.end_headers()

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        try:
            return json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return None

    # ------------------------------------------------------------------- verbs

    def do_OPTIONS(self):
        self._cors_preflight()

    def do_GET(self):
        path = self.path.split("?", 1)[0].rstrip("/")

        if path == "/api/health":
            return self._json(
                {
                    "ok": True,
                    "runtime": "local-server",
                    "identity_provider": "DevIdentity",
                    "dev_email": DEV_EMAIL or None,
                }
            )

        if path == "/api/whoami":
            email = self._identity()
            if not email:
                return self._json({"error": "sign-in required"}, 401)
            return self._json({"email": email})

        if path == "/api/sessions":
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
        if not self.path.startswith("/api/ingest"):
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
    print(f"  dev email: {DEV_EMAIL or '(anonymous — every protected route will 401)'}")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()

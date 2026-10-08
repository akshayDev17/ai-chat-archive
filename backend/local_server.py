"""Local development server for the backend.

The Cloudflare Worker runs inside a runtime that provides `js.fetch` and the D1
binding. Plain Python has neither, so this file adapts those two seams:

  * a local fetch built on urllib (so the share page can still be fetched), and
  * the in-memory MemoryConversationRepository.

It reuses the exact same ports / service / parser / decoder as the Worker, so
what runs here is the real backend logic, just wired to local stand-ins.

Run:  python backend/local_server.py   (then hit http://127.0.0.1:8787)
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
from chat_archive.serializers import conversation_detail
from chat_archive.service import ShareService
from chat_archive.sqlite_repository import SqliteConversationRepository


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
    def _json(self, obj, status=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "content-type")
        self.end_headers()

    def do_GET(self):
        path = self.path.split("?", 1)[0].rstrip("/")

        if path == "/api/sessions":
            sessions = asyncio.run(repo.list_recent())
            return self._json({"sessions": sessions})

        if path.startswith("/api/sessions/"):
            share_id = path.rsplit("/", 1)[-1]
            conversation = asyncio.run(repo.get(share_id))
            if conversation is None:
                return self._json({"error": "not found"}, 404)
            return self._json(conversation_detail(conversation))

        self._json({"error": "not found"}, 404)

    def do_POST(self):
        if self.path.startswith("/api/ingest"):
            length = int(self.headers.get("Content-Length", 0))
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                return self._json({"error": "invalid JSON"}, 400)
            share_url = (body or {}).get("share_url", "")
            try:
                conversation = asyncio.run(service.ingest(share_url))
                asyncio.run(repo.upsert(conversation))
                self._json(
                    {
                        "share_id": conversation.share_id,
                        "title": conversation.title,
                        "messages": len(conversation.messages),
                        "report_length": len(conversation.report or ""),
                        "citations": len(conversation.citations),
                    }
                )
            except Exception as exc:  # noqa: BLE001 - surface any ingest failure
                self._json({"error": str(exc)}, 400)
        else:
            self._json({"error": "not found"}, 404)

    def log_message(self, *_args):
        pass


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8787"))
    print(f"local backend listening on http://127.0.0.1:{port}")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()

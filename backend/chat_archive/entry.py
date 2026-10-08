"""Cloudflare Workers (Python) entrypoint.

Composition root: wires the ports to the ChatGPT implementations and the D1
repository, then routes HTTP requests. Auth is enforced OUTSIDE this Worker by
Cloudflare Access (One-time PIN + email allowlist).
"""

from __future__ import annotations

import json

from js import Response, fetch  # type: ignore[import-not-found]

from .chatgpt.decoder import ChatGptFlightDecoder
from .chatgpt.fetcher import HttpShareFetcher
from .chatgpt.parser import ChatGptParser
from .repository import D1ConversationRepository
from .serializers import conversation_detail
from .service import InvalidShareUrl, ShareService


class Container:
    """Holds the wired dependencies (one per request for safety)."""

    def __init__(self, env):
        self.service = ShareService(
            fetcher=HttpShareFetcher(fetch),
            decoder=ChatGptFlightDecoder(),
            parser=ChatGptParser(),
        )
        self.repository = D1ConversationRepository(env.DB)


def _json(body: object, status: int = 200) -> Response:
    return Response.new(
        json.dumps(body),
        headers={"content-type": "application/json"},
        status=status,
    )


async def on_fetch(request, env):  # noqa: D401 - Workers Python entrypoint
    container = Container(env)
    path = request.url.split("?", 1)[0].rstrip("/") or "/"

    if path.endswith("/api/sessions") and request.method == "GET":
        sessions = await container.repository.list_recent()
        return _json({"sessions": sessions})

    if "/api/sessions/" in path and request.method == "GET":
        share_id = path.rsplit("/", 1)[-1]
        conversation = await container.repository.get(share_id)
        if conversation is None:
            return _json({"error": "Not found"}, status=404)
        return _json(conversation_detail(conversation))

    if path.endswith("/api/ingest") and request.method == "POST":
        try:
            body = await request.json()
        except Exception:
            return _json({"error": "Invalid JSON body"}, status=400)
        share_url = (body or {}).get("share_url")
        if not share_url:
            return _json({"error": "Missing share_url"}, status=400)
        try:
            conversation = await container.service.ingest(share_url)
            await container.repository.upsert(conversation)
        except InvalidShareUrl as exc:
            return _json({"error": str(exc)}, status=400)
        except Exception as exc:  # fetch/parse failures -> 4xx for the caller
            return _json({"error": str(exc)}, status=400)

        return _json(
            {
                "share_id": conversation.share_id,
                "title": conversation.title,
                "messages": len(conversation.messages),
                "report_length": len(conversation.report or ""),
                "citations": len(conversation.citations),
            }
        )

    return Response.new("Not found", status=404)

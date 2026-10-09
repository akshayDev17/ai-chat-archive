"""Cloudflare Workers (Python) entrypoint.

Composition root: wires the ports to the ChatGPT implementations, the D1
repository, and the identity provider, then routes HTTP requests.

Two things this file has to get right:

1. **The current Python Workers entrypoint API.** Cloudflare specifies a
   ``Default`` class extending ``WorkerEntrypoint`` with an ``async def
   fetch(self, request)`` method, and ``self.env`` for bindings.
   https://developers.cloudflare.com/workers/languages/python/

2. **Public reading, private archiving.** Stories are public — anyone holding a
   share id can read one — while *listing* a shelf and *adding* to it require
   an authenticated email. That split is enforced here, in the Worker, because
   Cloudflare Access cannot express it: an Access policy has no HTTP-method
   selector at all (the documented selector list is emails, IPs, countries,
   device posture, identity-provider groups, service tokens — no verbs), and
   Access application paths cannot contain query strings. So Access may sit in
   front as an extra lock, but the authority for "may this caller see the front
   page?" is this file, and the answer comes from an :class:`IdentityProvider`.
   See ``docs/access-limits.md`` for the citations.
"""

from __future__ import annotations

import json

from js import fetch  # FFI: JS fetch, used for outbound share-page requests
from workers import Response, WorkerEntrypoint

from .auth import CloudflareAccessIdentity
from .chatgpt.decoder import ChatGptFlightDecoder
from .chatgpt.fetcher import HttpShareFetcher
from .chatgpt.parser import ChatGptParser
from .repository import D1ConversationRepository
from .serializers import conversation_detail
from .service import InvalidShareUrl, ShareService
from .urls import first_param, path_of, query_of, safe_next


def _json(body: object, status: int = 200) -> Response:
    return Response(
        json.dumps(body),
        headers={"content-type": "application/json"},
        status=status,
    )


def _path_of(request) -> str:
    """The request path. ``request.url`` is a JS string over the FFI boundary."""
    return path_of(str(request.url))


class Default(WorkerEntrypoint):
    """HTTP entrypoint. One method per concern, so the router stays readable."""

    def _service(self) -> ShareService:
        return ShareService(
            fetcher=HttpShareFetcher(fetch),
            decoder=ChatGptFlightDecoder(),
            parser=ChatGptParser(),
        )

    def _repository(self) -> D1ConversationRepository:
        return D1ConversationRepository(self.env.DB)

    def _identity_provider(self) -> CloudflareAccessIdentity:
        return CloudflareAccessIdentity(getattr(self, "ctx", None))

    # ------------------------------------------------------------------ routes

    async def fetch(self, request):
        path = _path_of(request)
        method = str(request.method).upper()

        if path.endswith("/api/health"):
            return await self._health()

        # -- public -----------------------------------------------------------
        # A single story is reachable by its permalink with no identity at all.
        if "/api/sessions/" in path and method == "GET":
            return await self._read_story(path.rsplit("/", 1)[-1])

        # -- protected --------------------------------------------------------
        # Everything below needs an email, because it either reveals a shelf or
        # writes to one.
        identity = await self._identity_provider().identify(request)
        if identity is None:
            return _json({"error": "sign-in required"}, status=401)

        if path.endswith("/api/sessions") and method == "GET":
            return await self._list_shelf(identity)

        if path.endswith("/api/ingest") and method == "POST":
            return await self._ingest(request, identity)

        if path.endswith("/api/session/start"):
            return self._session_start(request)

        return Response("Not found", status=404)

    def _session_start(self, request) -> Response:
        """Where our own sign-in screen hands a browser over to Cloudflare Access.

        This route exists to solve one specific problem. Access authenticates by
        intercepting a **top-level navigation** and serving its one-time-PIN
        screen from ``<team>.cloudflareaccess.com`` — a different domain, so no
        path on ours can ever reach it. That means our Rail sign-in screen
        cannot *call* Access; it can only send the browser somewhere Access will
        catch it.

        So: the browser visits this path, Access intercepts, the visitor enters
        the code on Cloudflare's screen, and Access redirects back here with the
        ``CF_Authorization`` cookie now attached. Only then does our Worker run —
        by which point there is an identity, so we simply forward to ``next``.

        Reaching this method therefore *means* the visitor authenticated. It is
        unreachable anonymously when Access fronts the API, and 401s
        anonymously when it does not.

        Note: this must be a real navigation, not ``fetch()``. A protected
        ``fetch`` gets a 302 that the browser follows as a GET, dropping any
        request body — the silent failure described in ``UploadInline.tsx``.
        """
        url = str(request.url)
        return Response(
            "",
            status=302,
            headers={"location": safe_next(first_param(query_of(url), "next"))},
        )

    async def _health(self) -> Response:
        """Report whether Python Workers actually expose ``self.ctx``.

        The Access identity API is documented for JavaScript only, so the
        deployed Worker has to be able to answer "did Access reach me?" rather
        than have us assume it. This route is deliberately public and reveals
        nothing but that one boolean.
        """
        ctx = getattr(self, "ctx", None)
        return _json(
            {
                "ok": True,
                "runtime": "python-workers",
                "context_available": ctx is not None,
                "access_available": getattr(ctx, "access", None) is not None
                if ctx is not None
                else False,
            }
        )

    async def _list_shelf(self, identity) -> Response:
        sessions = await self._repository().list_recent(identity.email)
        return _json({"owner": identity.email, "sessions": sessions})

    async def _read_story(self, share_id: str) -> Response:
        conversation = await self._repository().get(share_id)
        if conversation is None:
            return _json({"error": "Not found"}, status=404)
        return _json(conversation_detail(conversation))

    async def _ingest(self, request, identity) -> Response:
        try:
            body = await request.json()
        except Exception:
            return _json({"error": "Invalid JSON body"}, status=400)

        share_url = (body or {}).get("share_url")
        if not share_url:
            return _json({"error": "Missing share_url"}, status=400)

        try:
            conversation = await self._service().ingest(share_url)
            # Stamp ownership here — the parser cannot know it, and the
            # repository refuses to store an unowned row.
            await self._repository().upsert(conversation.owned_by(identity.email))
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
                "owner": identity.email,
            }
        )

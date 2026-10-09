"""Cloudflare Workers (Python) entrypoint.

Composition root: wires the ports to the ChatGPT implementations, the D1
repository, and the identity provider, then routes HTTP requests.

Two things this file has to get right:

1. **The current Python Workers entrypoint API.** Cloudflare specifies a
   ``Default`` class extending ``WorkerEntrypoint`` with an ``async def
   fetch(self, request)`` method, and ``self.env`` for bindings.
   https://developers.cloudflare.com/workers/languages/python/

2. **Public reading, private filing.** The archive is a newspaper: the front
   page and every story are readable by anyone, with no identity at all. The
   only gated actions are filing copy (``POST /api/ingest``) and seeing your own
   filings. So the split is "public reads, private writes" — one rule, easy to
   check, and expressed as two contiguous blocks in :meth:`Default.fetch`.

   That split is enforced here, in the Worker, rather than in Cloudflare Access,
   for two reasons. Access application paths cannot contain query strings, and
   an Access policy has no HTTP-method selector at all (the documented selectors
   are emails, IPs, countries, device posture, identity-provider groups and
   service tokens — no verbs). See ``docs/access-limits.md`` for the citations.

   Ownership survives as *provenance*: every row records who filed it, which
   drives the `source`/byline on the front page and the copy desk's "your
   filings" list. It is no longer an access boundary, because there is no longer
   a boundary to draw — everyone reads the same edition.
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
        # Reading is entirely public: the edition is a newspaper, so every story
        # and the front page itself are readable without an identity.
        if path.endswith("/api/whoami"):
            # "Who am I?" answered with "nobody" is a 200, not a 401. Being
            # anonymous is the expected state for most visitors to a public
            # archive, and a 401 here makes every browser log a console error on
            # every page view for the entire public audience. 401 is reserved
            # for the routes that actually withhold something (`/api/filings`,
            # `/api/ingest`).
            identity = await self._identity_provider().identify(request)
            return _json({"email": identity.email if identity else None})

        if path.endswith("/api/sessions") and method == "GET":
            return await self._edition()

        if "/api/sessions/" in path and method == "GET":
            return await self._read_story(path.rsplit("/", 1)[-1])

        # -- protected --------------------------------------------------------
        # Everything below files copy (or asks who you are as a filer), so it
        # needs an email. None of it is reading, which is why the split is
        # legible: public reads, private writes.
        identity = await self._identity_provider().identify(request)
        if identity is None:
            return _json({"error": "sign-in required"}, status=401)

        if path.endswith("/api/filings") and method == "GET":
            return await self._filings(identity)

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

    async def _edition(self) -> Response:
        """The front page: every filed story, from every filer."""
        return _json({"sessions": await self._repository().list_all()})

    async def _filings(self, identity) -> Response:
        """What *you* filed — the copy desk showing your own recent work.

        Barely different from :meth:`_edition` today, and that is the point: the
        difference is the whole reason the owner column exists, and it is the
        read that will matter once the desk lists only your drafts.
        """
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

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
   only gated actions are filing copy (``POST /api/desk/ingest``), retracting it
   (``DELETE /api/desk/sessions/<id>``) and seeing your own filings. So the
   split is "public reads, private writes" — one rule, easy to
   check, and expressed as two contiguous blocks in :meth:`Default.fetch`.

   That split is enforced here, in the Worker, rather than in Cloudflare Access,
   for two reasons. Access application paths cannot contain query strings, and
   an Access policy has no HTTP-method selector at all (the documented selectors
   are emails, IPs, countries, device posture, identity-provider groups and
   service tokens — no verbs). See ``docs/access-limits.md`` for the citations.

   Ownership survives as *provenance*: every row records who filed it, which
   drives the `source`/byline on the front page and the copy desk's "your
   filings" list. It is not a boundary on *reading* — everyone reads the same
   edition — but it is the boundary on retracting: you can only take back what
   you filed.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from workers import Response, WorkerEntrypoint

from .auth import CloudflareAccessIdentity
from .chatgpt.decoder import ChatGptFlightDecoder
from .chatgpt.parser import ChatGptParser
from .chatgpt.proxy_fetcher import ProxyImageFetcher, ProxyShareFetcher, make_worker_fetch
from .media_copier import copy_pending, retract_story
from .r2_media_store import R2MediaStore
from .repository import D1ConversationRepository
from .serializers import conversation_detail, media_counts
from .service import InvalidShareUrl, ShareService
from .urls import desk_media_target, first_param, media_key_of, path_of, query_of, safe_next


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
        env = getattr(self, "env", None)
        proxy_base = getattr(env, "PROXY_BASE_URL", "") if env is not None else ""
        token = getattr(env, "FETCH_TOKEN", "") if env is not None else ""
        return ShareService(
            fetcher=ProxyShareFetcher(make_worker_fetch(), proxy_base or "", token or ""),
            decoder=ChatGptFlightDecoder(),
            parser=ChatGptParser(),
        )

    def _repository(self) -> D1ConversationRepository:
        return D1ConversationRepository(self.env.DB)

    def _media_store(self) -> R2MediaStore:
        return R2MediaStore(self.env.MEDIA)

    def _image_fetcher(self) -> ProxyImageFetcher:
        env = getattr(self, "env", None)
        return ProxyImageFetcher(
            make_worker_fetch(),
            getattr(env, "PROXY_BASE_URL", "") or "",
            getattr(env, "FETCH_TOKEN", "") or "",
        )

    async def _copy_media(self, only: tuple[str, str] | None = None) -> dict:
        return await copy_pending(
            self._repository(),
            self._media_store(),
            self._image_fetcher(),
            datetime.now(timezone.utc),
            only=only,
        )

    # --------------------------------------------------------------- schedule

    async def scheduled(self, controller, env, ctx):
        """The once-a-minute Cron Trigger: copy one batch of pending images.

        Filing only records images as pending, because the Free plan's 50
        subrequests per invocation cannot fit a 28-image story. This is where
        the bytes actually move. See ``media_copier.py``.
        """
        await self._copy_media()

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
        if path.endswith("/api/sessions") and method == "GET":
            return await self._edition()

        if "/api/sessions/" in path and method == "GET":
            return await self._read_story(path.rsplit("/", 1)[-1])

        if "/api/media/" in path and "/api/desk/" not in path and method == "GET":
            # A story's images are as public as the story.
            return await self._serve_media(media_key_of(path))

        # -- protected --------------------------------------------------------
        # Everything below files copy (or asks who you are as a filer), so it
        # needs an email. None of it is reading, which is why the split is
        # legible: public reads, private writes.
        identity = await self._identity_provider().identify(request)
        if identity is None:
            return _json({"error": "sign-in required"}, status=401)

        if path.endswith("/api/desk/whoami") and method == "GET":
            # "Who am I?" lives under /api/desk/ — not on a public path — because
            # ``ctx.access`` is populated only where Cloudflare Access actually
            # runs, and the Access app covers /api/desk/*. A public /api/whoami
            # answered ``{"email": null}`` even for a signed-in reader, which
            # bounced the copy desk straight back to the sign-in screen. Anonymous
            # now means an Access redirect (or a 401 when Access is absent), which
            # the frontend already treats as "signed out".
            return _json({"email": identity.email})

        if path.endswith("/api/desk/filings") and method == "GET":
            return await self._filings(identity)

        if path.endswith("/api/desk/ingest") and method == "POST":
            return await self._ingest(request, identity)

        if "/api/desk/sessions/" in path and method == "GET":
            return await self._filed_by(path.rsplit("/", 1)[-1], identity)

        if "/api/desk/sessions/" in path and method == "DELETE":
            return await self._retract(path.rsplit("/", 1)[-1], identity)

        media_target = desk_media_target(path)
        if media_target is not None:
            return await self._desk_media(media_target, method, identity)

        if path.endswith("/api/desk/enter"):
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

    async def _filed_by(self, share_id: str, identity) -> Response:
        """Whether *you* filed this story, and how its image copies stand.

        The reader uses it to offer the filer a retraction and a link to the
        story's images. A yes/no rather than the owner's address: the story
        itself is public, and the answer is only ever about the person asking.
        """
        story = await self._repository().media_for(share_id, identity.email)
        return _json(
            {
                "id": share_id,
                "yours": story is not None,
                "images": media_counts(story["images"]) if story else None,
            }
        )

    async def _retract(self, share_id: str, identity) -> Response:
        """Take one of your own stories out of the edition.

        Scoped to the caller's copy: someone else's copy of the same share link
        is untouched, and a story you never filed is a 404 rather than a 403 —
        from where you stand, there is nothing of yours there to retract. Once
        no copy of the story remains, its R2 folder goes with it.
        """
        if not await retract_story(self._repository(), self._media_store(), share_id, identity.email):
            return _json({"error": "You have no filed story with that id"}, status=404)
        return _json({"id": share_id, "retracted": True})

    async def _serve_media(self, key: str | None) -> Response:
        if key is None:
            return _json({"error": "Not found"}, status=404)
        found = await self._media_store().get(key)
        if found is None:
            return _json({"error": "Not found"}, status=404)
        data, content_type = found
        # Not `immutable`: a key is a place in a story, and re-filing can put a
        # different picture there.
        return Response(
            data,
            headers={"content-type": content_type, "cache-control": "public, max-age=86400"},
        )

    async def _desk_media(self, target, method: str, identity) -> Response:
        """The media page's endpoints, all scoped to the caller's own stories."""
        share_id, action = target
        repo = self._repository()

        if share_id is None and method == "GET":
            return _json({"filings": await repo.media_summary(identity.email)})

        if share_id is not None and action is None and method == "GET":
            story = await repo.media_for(share_id, identity.email)
            if story is None:
                return _json({"error": "You have no filed story with that id"}, status=404)
            return _json(story)

        if share_id is not None and action == "retry" and method == "POST":
            reset = await repo.retry_failed_media(share_id, identity.email)
            if reset is None:
                return _json({"error": "You have no filed story with that id"}, status=404)
            return _json({"reset": reset})

        if share_id is not None and action == "copy" and method == "POST":
            if await repo.media_for(share_id, identity.email) is None:
                return _json({"error": "You have no filed story with that id"}, status=404)
            return _json(await self._copy_media(only=(identity.email, share_id)))

        return Response("Not found", status=404)

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
                "images": sum(len(m.media) for m in conversation.messages),
                "owner": identity.email,
            }
        )

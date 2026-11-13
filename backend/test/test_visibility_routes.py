"""Public or private, as the HTTP routes answer it — offline.

`test_ownership.py` pins what the repositories return. This file pins what a
*caller* gets: a private story must be invisible on every public route, to a
signed-out visitor and to another signed-in reader alike, while its filer still
reads it through `/api/desk/*` and is the only one who can change it.

There are two routers — the Worker (`chat_archive/entry.py`) and the local
server (`local_server.py`) — and they are written out separately, so the same
assertions run against both. A rule enforced in one and forgotten in the other
is exactly what this is here to catch.

The Worker is run off-platform: its `workers` import is replaced by a stand-in
`Response`, and its D1 and R2 bindings by the SQLite-backed fake D1 and the
in-memory media store. The local server is run for real, on a loopback port.

Run:  python3 backend/test/test_visibility_routes.py
"""

from __future__ import annotations

import asyncio
import http.client
import json
import os
import sys
import tempfile
import threading
import unittest
from dataclasses import replace
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "test"))

from chat_archive.auth import Identity  # noqa: E402
from chat_archive.local_media_store import MemoryMediaStore  # noqa: E402
from chat_archive.models import Conversation, Media, Message  # noqa: E402
from chat_archive.repository import D1ConversationRepository  # noqa: E402
from chat_archive.sqlite_repository import SqliteConversationRepository  # noqa: E402
from test_ownership import FakeD1  # noqa: E402

OWNER = "owner@example.com"
OTHER = "other@example.com"
ANONYMOUS = None

SHARE = "6ac877f9-c690-83ec-8394-61b0727ba5eb"
SHARE_URL = f"https://chatgpt.com/share/{SHARE}"
KEY = f"chat-archives/{SHARE}/msg-1/01.jpg"
JPEG = b"\xff\xd8\xff fake jpeg"
NOT_YOURS = "You have no filed story with that id"


def story(title: str = "A story") -> Conversation:
    """A story with one image, as the parser hands it over: ownerless."""
    return Conversation(
        share_id=SHARE,
        title=title,
        messages=[
            Message(role="user", content="q"),
            Message(
                role="assistant",
                content="a",
                media=[Media(tag_start=0, ordinal=0, content_url="https://images.openai.com/1")],
                external_id="msg-1",
            ),
        ],
        report=f"# {title}\n\nA report.",
    )


class FakeShareService:
    """Stands in for fetching and parsing a share link."""

    async def ingest(self, share_url: str) -> Conversation:
        return story("Filed over HTTP")


# ------------------------------------------------------------------ the Worker


class _WorkerResponse:
    """The slice of `workers.Response` the router uses."""

    def __init__(self, body="", status: int = 200, headers: dict | None = None):
        self.body = body
        self.status = status
        self.headers = headers or {}


with mock.patch.dict(
    sys.modules,
    {"workers": SimpleNamespace(Response=_WorkerResponse, WorkerEntrypoint=object)},
):
    from chat_archive import entry  # noqa: E402


class _TestIdentity:
    """Reads the caller from the request, as Access would have resolved it."""

    async def identify(self, request):
        return Identity(request.email) if request.email else None


class _Worker(entry.Default):
    def __init__(self, repo, store):
        self._repo = repo
        self._store = store

    def _repository(self):
        return self._repo

    def _media_store(self):
        return self._store

    def _identity_provider(self):
        return _TestIdentity()

    def _service(self):
        return FakeShareService()


# ------------------------------------------------------------ the local server


def _load_local_server():
    """Import `local_server` with its database and media directory in a temp dir."""
    tmp = tempfile.TemporaryDirectory()
    with mock.patch.dict(
        os.environ,
        {"DB_PATH": str(Path(tmp.name) / "import.db"), "MEDIA_DIR": str(Path(tmp.name) / "media")},
    ):
        import local_server
    return local_server, tmp


local_server, _LOCAL_TMP = _load_local_server()


# ---------------------------------------------------------------- the contract


class VisibilityRoutes:
    """What every router must answer. Subclasses supply `repo`, `store`, `call`.

    ``call(method, path, email, body)`` → ``(status, payload, headers)``, where
    ``payload`` is parsed JSON, or bytes for an image.
    """

    # -- fixtures ------------------------------------------------------------

    def file(self, owner: str = OWNER, visibility: str | None = None, title: str = "A story"):
        """File the story for ``owner`` and copy its image into the store."""
        asyncio.run(self.repo.upsert(replace(story(title).owned_by(owner), visibility=visibility)))
        self.copy_image()

    def copy_image(self):
        for row in asyncio.run(
            self.repo.claim_media(10, "2026-11-13T00:00:00+00:00", "2026-11-12T00:00:00+00:00")
        ):
            asyncio.run(self.repo.finish_media(row["id"], KEY, "image/jpeg"))
        asyncio.run(self.store.put(KEY, JPEG, "image/jpeg"))

    def get(self, path: str, email: str | None = ANONYMOUS):
        return self.call("GET", path, email, None)

    def edition(self, email: str | None = ANONYMOUS) -> list[str]:
        status, payload, _ = self.get("/api/sessions", email)
        self.assertEqual(status, 200)
        return [s["id"] for s in payload["sessions"]]

    # -- a public story is unchanged -----------------------------------------

    def test_a_public_story_is_read_by_anyone(self):
        self.file()
        for who in (ANONYMOUS, OTHER, OWNER):
            with self.subTest(who=who):
                self.assertEqual(self.edition(who), [SHARE])
                status, payload, _ = self.get(f"/api/sessions/{SHARE}", who)
                self.assertEqual((status, payload["title"]), (200, "A story"))
                self.assertEqual(payload["messages"][1]["media"][0]["src"], f"/api/media/{KEY}")
                self.assertNotIn("visibility", payload)
                status, image, headers = self.get(f"/api/media/{KEY}", who)
                self.assertEqual((status, image), (200, JPEG))
                self.assertEqual(headers["cache-control"], "public, max-age=86400")

    # -- a private story is absent from every public route -------------------

    def test_a_private_story_is_absent_from_the_edition(self):
        self.file(visibility="private")
        for who in (ANONYMOUS, OTHER, OWNER):
            with self.subTest(who=who):
                self.assertEqual(self.edition(who), [])

    def test_a_private_story_is_404_at_its_public_permalink(self):
        self.file(visibility="private")
        for who in (ANONYMOUS, OTHER):
            with self.subTest(who=who):
                status, payload, _ = self.get(f"/api/sessions/{SHARE}", who)
                self.assertEqual(status, 404)
                self.assertNotIn("title", payload)

    def test_a_private_storys_image_is_404_on_the_public_media_route(self):
        self.file(visibility="private")
        for who in (ANONYMOUS, OTHER, OWNER):
            with self.subTest(who=who):
                status, payload, _ = self.get(f"/api/media/{KEY}", who)
                self.assertEqual(status, 404)
                self.assertNotEqual(payload, JPEG)

    def test_the_bucket_is_not_read_for_an_image_the_caller_may_not_see(self):
        self.file(visibility="private")
        with mock.patch.object(self.store, "get", side_effect=AssertionError("store was read")):
            self.assertEqual(self.get(f"/api/media/{KEY}")[0], 404)
            self.assertEqual(self.get(f"/api/desk/image/{KEY}", OTHER)[0], 404)

    # -- the desk routes need an identity, and it has to be the owner's -------

    def test_the_desk_routes_are_401_without_an_identity(self):
        self.file(visibility="private")
        for method, path, body in (
            ("GET", f"/api/desk/stories/{SHARE}", None),
            ("GET", f"/api/desk/image/{KEY}", None),
            ("GET", f"/api/desk/sessions/{SHARE}", None),
            ("PATCH", f"/api/desk/sessions/{SHARE}", {"visibility": "public"}),
            ("DELETE", f"/api/desk/sessions/{SHARE}", None),
            ("GET", "/api/desk/filings", None),
        ):
            with self.subTest(route=f"{method} {path}"):
                status, payload, _ = self.call(method, path, ANONYMOUS, body)
                self.assertEqual((status, payload), (401, {"error": "sign-in required"}))
        self.assertEqual(self.edition(), [], "the anonymous PATCH must not have published it")

    def test_another_signed_in_reader_gets_404_not_the_story(self):
        self.file(visibility="private")

        status, payload, _ = self.get(f"/api/desk/stories/{SHARE}", OTHER)
        self.assertEqual((status, payload), (404, {"error": NOT_YOURS}))

        status, payload, _ = self.get(f"/api/desk/image/{KEY}", OTHER)
        self.assertEqual(status, 404)
        self.assertNotEqual(payload, JPEG)

        status, payload, _ = self.get("/api/desk/filings", OTHER)
        self.assertEqual((status, payload["sessions"]), (200, []))

        status, payload, _ = self.get("/api/desk/media", OTHER)
        self.assertEqual((status, payload["filings"]), (200, []))

        status, payload, _ = self.get(f"/api/desk/sessions/{SHARE}", OTHER)
        self.assertEqual(
            (status, payload),
            (200, {"id": SHARE, "yours": False, "visibility": None, "images": None}),
        )

    def test_the_desk_image_route_is_only_for_your_own_stories(self):
        """Another reader's public image is on the public route, not on your desk."""
        self.file()
        self.assertEqual(self.get(f"/api/desk/image/{KEY}", OTHER)[0], 404)

    # -- the owner still has it ----------------------------------------------

    def test_the_owner_reads_their_private_story_through_the_desk(self):
        self.file(visibility="private", title="Mine alone")

        status, payload, _ = self.get(f"/api/desk/stories/{SHARE}", OWNER)
        self.assertEqual((status, payload["title"]), (200, "Mine alone"))
        self.assertEqual(payload["report"], "# Mine alone\n\nA report.")
        src = payload["messages"][1]["media"][0]["src"]
        self.assertEqual(src, f"/api/desk/image/{KEY}")

        status, image, headers = self.get(src, OWNER)
        self.assertEqual((status, image), (200, JPEG))
        self.assertEqual(headers["cache-control"], "private, max-age=3600")

    def test_the_owners_listings_show_the_private_story_and_say_so(self):
        self.file(visibility="private")

        status, payload, _ = self.get("/api/desk/filings", OWNER)
        self.assertEqual(status, 200)
        self.assertEqual([(s["id"], s["visibility"]) for s in payload["sessions"]], [(SHARE, "private")])

        status, payload, _ = self.get(f"/api/desk/sessions/{SHARE}", OWNER)
        self.assertEqual((status, payload["yours"], payload["visibility"]), (200, True, "private"))

        status, payload, _ = self.get("/api/desk/media", OWNER)
        self.assertEqual([f["visibility"] for f in payload["filings"]], ["private"])

    # -- changing it ---------------------------------------------------------

    def test_the_owner_makes_a_story_private_and_public_again(self):
        self.file()

        status, payload, _ = self.call(
            "PATCH", f"/api/desk/sessions/{SHARE}", OWNER, {"visibility": "private"}
        )
        self.assertEqual((status, payload), (200, {"id": SHARE, "visibility": "private"}))
        self.assertEqual(self.edition(), [])
        self.assertEqual(self.get(f"/api/sessions/{SHARE}")[0], 404)
        self.assertEqual(self.get(f"/api/media/{KEY}")[0], 404)

        status, payload, _ = self.call(
            "PATCH", f"/api/desk/sessions/{SHARE}", OWNER, {"visibility": "public"}
        )
        self.assertEqual((status, payload), (200, {"id": SHARE, "visibility": "public"}))
        self.assertEqual(self.edition(), [SHARE])
        self.assertEqual(self.get(f"/api/sessions/{SHARE}")[0], 200)
        self.assertEqual(self.get(f"/api/media/{KEY}")[0], 200)

    def test_only_the_owner_can_change_it(self):
        self.file(visibility="private")
        status, payload, _ = self.call(
            "PATCH", f"/api/desk/sessions/{SHARE}", OTHER, {"visibility": "public"}
        )
        self.assertEqual((status, payload), (404, {"error": NOT_YOURS}))
        self.assertEqual(self.edition(), [], "the story must still be private")
        self.assertEqual(self.get(f"/api/sessions/{SHARE}")[0], 404)

    def test_another_reader_cannot_hide_a_public_story(self):
        self.file()
        status, _, _ = self.call(
            "PATCH", f"/api/desk/sessions/{SHARE}", OTHER, {"visibility": "private"}
        )
        self.assertEqual(status, 404)
        self.assertEqual(self.edition(), [SHARE])

    def test_another_reader_cannot_delete_a_private_story(self):
        self.file(visibility="private")
        status, payload, _ = self.call("DELETE", f"/api/desk/sessions/{SHARE}", OTHER, None)
        self.assertEqual((status, payload), (404, {"error": NOT_YOURS}))
        self.assertEqual(self.get(f"/api/desk/stories/{SHARE}", OWNER)[0], 200)
        self.assertEqual(self.get(f"/api/desk/image/{KEY}", OWNER)[0], 200)

    def test_a_visibility_that_is_neither_is_refused(self):
        self.file(visibility="private")
        for body in ({"visibility": "secret"}, {"visibility": None}, {"visibility": True}, {}, []):
            with self.subTest(body=body):
                status, _, _ = self.call("PATCH", f"/api/desk/sessions/{SHARE}", OWNER, body)
                self.assertEqual(status, 400)
        self.assertEqual(self.edition(), [], "a refused change must not publish the story")

    # -- two filers of one link ----------------------------------------------

    def test_a_private_copy_does_not_hide_another_filers_public_copy(self):
        self.file(OTHER, title="The public copy")
        self.file(OWNER, visibility="private", title="The private copy")

        status, payload, _ = self.get(f"/api/sessions/{SHARE}")
        self.assertEqual((status, payload["title"]), (200, "The public copy"))
        self.assertEqual(self.get(f"/api/media/{KEY}")[0], 200)
        status, payload, _ = self.get(f"/api/desk/stories/{SHARE}", OWNER)
        self.assertEqual(payload["title"], "The private copy")

    # -- filing --------------------------------------------------------------

    def ingest(self, body: dict, email: str | None = OWNER):
        return self.call("POST", "/api/desk/ingest", email, {"share_url": SHARE_URL, **body})

    def test_a_filing_is_public_unless_it_says_otherwise(self):
        status, payload, _ = self.ingest({})
        self.assertEqual((status, payload["visibility"]), (200, "public"))
        self.assertEqual(self.edition(), [SHARE])

    def test_a_filing_can_be_private_from_the_start(self):
        status, payload, _ = self.ingest({"visibility": "private"})
        self.assertEqual((status, payload["visibility"]), (200, "private"))
        self.assertEqual(self.edition(), [])
        self.assertEqual(self.get(f"/api/sessions/{SHARE}")[0], 404)
        self.assertEqual(self.get(f"/api/desk/stories/{SHARE}", OWNER)[0], 200)

    def test_filing_again_without_saying_keeps_it_private(self):
        self.ingest({"visibility": "private"})
        self.copy_image()
        status, payload, _ = self.ingest({})
        self.assertEqual((status, payload["visibility"]), (200, "private"))
        for who in (ANONYMOUS, OTHER, OWNER):
            with self.subTest(who=who):
                self.assertEqual(self.edition(who), [])
                self.assertEqual(self.get(f"/api/sessions/{SHARE}", who)[0], 404)
                self.assertEqual(self.get(f"/api/media/{KEY}", who)[0], 404)
        status, payload, _ = self.get(f"/api/desk/sessions/{SHARE}", OWNER)
        self.assertEqual((status, payload["yours"], payload["visibility"]), (200, True, "private"))
        self.assertEqual(self.get(f"/api/desk/image/{KEY}", OWNER)[:2], (200, JPEG))

    def test_filing_again_saying_public_publishes_it(self):
        self.ingest({"visibility": "private"})
        status, payload, _ = self.ingest({"visibility": "public"})
        self.assertEqual((status, payload["visibility"]), (200, "public"))
        self.assertEqual(self.edition(), [SHARE])

    def test_a_filing_with_an_unknown_visibility_is_refused_and_files_nothing(self):
        status, _, _ = self.ingest({"visibility": "secret"})
        self.assertEqual(status, 400)
        self.assertEqual(self.edition(), [])
        self.assertEqual(self.get("/api/desk/filings", OWNER)[1]["sessions"], [])


class TestWorkerRoutes(VisibilityRoutes, unittest.TestCase):
    """`chat_archive/entry.py` — the production router."""

    def setUp(self):
        self.repo = D1ConversationRepository(FakeD1(BACKEND / "migrations"))
        self.store = MemoryMediaStore()
        self.worker = _Worker(self.repo, self.store)

    def call(self, method, path, email, body):
        async def read_json():
            return body

        request = SimpleNamespace(
            url=f"https://archive.test{path}", method=method, email=email, json=read_json
        )
        response = asyncio.run(self.worker.fetch(request))
        headers = {name.lower(): value for name, value in response.headers.items()}
        payload = response.body
        if headers.get("content-type") == "application/json":
            payload = json.loads(payload)
        return response.status, payload, headers


class TestLocalServerRoutes(VisibilityRoutes, unittest.TestCase):
    """`local_server.py` — the router local development runs."""

    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), local_server.Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = SqliteConversationRepository(str(Path(tmp.name) / "archive.db"))
        self.store = MemoryMediaStore()
        for name, value in (
            ("repo", self.repo), ("media_store", self.store), ("service", FakeShareService()),
        ):
            patcher = mock.patch.object(local_server, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def call(self, method, path, email, body):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=10)
        self.addCleanup(connection.close)
        # The empty header is the local server's "force anonymous".
        headers = {"X-Archive-Email": email or ""}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        connection.request(method, path, body=data, headers=headers)
        response = connection.getresponse()
        payload = response.read()
        headers = {name.lower(): value for name, value in response.getheaders()}
        if headers.get("content-type") == "application/json":
            payload = json.loads(payload)
        return response.status, payload, headers


if __name__ == "__main__":
    unittest.main(verbosity=2)

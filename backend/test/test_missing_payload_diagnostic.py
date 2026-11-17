"""The 400 for "No flight payload found in page" says *why* — offline.

A share page fetched with 200 but holding no ``streamController.enqueue(...)``
chunk is either a bot/challenge page, an unavailable-share shell, or a sign
that ChatGPT changed its page format. The 400 keeps its ``error`` string
exactly, and gains a ``diagnostic`` that tells those apart for a later RCA.

This pins three things:

  * the classifier sorts each kind of page into its reason code,
  * both routers (the Worker and the local server) return that diagnostic
    beside the unchanged ``error``, and log it with a bounded page sample, and
  * the response never carries page HTML or anything from the request headers.

The fixtures are small and synthetic — hand-written pages that carry just the
markers each class is recognised by.

Run:  python3 backend/test/test_missing_payload_diagnostic.py
"""

from __future__ import annotations

import asyncio
import contextlib
import http.client
import io
import json
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "test"))

from chat_archive.chatgpt.decoder import ChatGptFlightDecoder, MissingFlightPayload  # noqa: E402
from chat_archive.chatgpt.page_diagnostic import (  # noqa: E402
    LOG_SAMPLE_MAX_CHARS,
    TITLE_MAX_CHARS,
    diagnose_missing_payload,
)
from chat_archive.chatgpt.parser import ChatGptParser  # noqa: E402
from chat_archive.ingest_failure import ingest_failure_body  # noqa: E402
from chat_archive.local_media_store import MemoryMediaStore  # noqa: E402
from chat_archive.repository import D1ConversationRepository  # noqa: E402
from chat_archive.service import ShareService  # noqa: E402
from chat_archive.sqlite_repository import SqliteConversationRepository  # noqa: E402
from test_ownership import FakeD1  # noqa: E402
from test_visibility_routes import _Worker, local_server  # noqa: E402

ERROR = "No flight payload found in page"
SHARE_URL = "https://chatgpt.com/share/6ac877f9-c690-83ec-8394-61b0727ba5eb"
OWNER = "owner@example.com"
# Planted in every fixture's body; must never come back in a response.
PAGE_SECRET = "PAGE-BODY-SENTINEL-7f3a"

CHALLENGE = (
    "<!DOCTYPE html><html><head><title>Just a moment...</title></head><body>"
    f"<p>{PAGE_SECRET}</p>"
    '<script>window._cf_chl_opt={cvId:"3"};</script>'
    '<script src="/cdn-cgi/challenge-platform/h/b/orchestrate/chl_page/v1"></script>'
    "</body></html>"
)
UNAVAILABLE = (
    "<html><head><title>ChatGPT - This shared link has been deleted</title>"
    '<link href="https://cdn.oaistatic.com/assets/root.css">'
    f"</head><body><main><h1>404 Not Found</h1><p>{PAGE_SECRET}</p></main>"
    "<script>window.__reactRouterContext = {};</script></body></html>"
)
# The call site survives but is backtick-quoted, so the decoder's regex misses it.
FORMAT_CHANGE = (
    '<html><head><title>ChatGPT - Ocean tides</title><script src="https://cdn.oaistatic.com/'
    f'assets/entry.js"></script></head><body><p>{PAGE_SECRET}</p><script>'
    'window.__reactRouterContext = {"v":2};'
    'window.__reactRouterContext.streamController.enqueue(`[{"_1":2},"x","y"]`);'
    "</script></body></html>"
)
OTHER = f"<html><head><title>Welcome to nginx!</title></head><body><p>{PAGE_SECRET}</p></body></html>"
# Cloudflare bot management injects this into ordinary pages it serves.
CF_JSD_SCRIPT = (
    "<script>window.__CF$cv$params={r:'9a1b',t:'MTcz'};var a=document.createElement('script');"
    "a.src='/cdn-cgi/challenge-platform/scripts/jsd/main.js';"
    "document.getElementsByTagName('head')[0].appendChild(a);</script>"
)
# A ChatGPT share whose conversation prose and inlined JS bundle hold the very
# phrases challenge and unavailable pages show, behind Cloudflare's jsd script.
CHATGPT_PROSE_PAGE = (
    '<html><head><title>ChatGPT - Debugging a proxy</title><script src="https://cdn.oaistatic.com/'
    f'assets/entry.js"></script>{CF_JSD_SCRIPT}</head><body><article><p>{PAGE_SECRET}</p>'
    "<p>The proxy shows &quot;Just a moment...&quot;, then 404 Not Found, then"
    " Attention Required! | Cloudflare.</p></article>"
    '<script>const t={notFound:"Conversation not found",deleted:"This shared link has been deleted",'
    'chl:"cf-chl-bypass"};window.__reactRouterContext = {"v":3};</script></body></html>'
)

FIXTURES = {
    "challenge_page": CHALLENGE,
    "share_unavailable": UNAVAILABLE,
    "format_change": FORMAT_CHANGE,
    "unknown": OTHER,
}


def missing_payload(html: str) -> MissingFlightPayload:
    try:
        ChatGptFlightDecoder().decode(html)
    except MissingFlightPayload as exc:
        return exc
    raise AssertionError("the page decoded")


class TestClassifier(unittest.TestCase):
    def test_each_fixture_gets_its_reason(self):
        for reason, html in FIXTURES.items():
            with self.subTest(reason=reason):
                self.assertEqual(diagnose_missing_payload(html)["reason"], reason)

    def test_a_chatgpt_page_is_a_format_change_whatever_its_prose_or_scripts_say(self):
        with_enqueue = CHATGPT_PROSE_PAGE.replace(
            "</body>", "<script>window.__reactRouterContext.streamController.enqueue(`[]`);</script></body>"
        )
        for name, html in {"shell": CHATGPT_PROSE_PAGE, "enqueue call site": with_enqueue}.items():
            with self.subTest(page=name):
                self.assertEqual(diagnose_missing_payload(html)["reason"], "format_change")

    def test_the_injected_jsd_script_alone_is_not_a_challenge(self):
        html = OTHER.replace("</head>", CF_JSD_SCRIPT + "</head>")
        self.assertEqual(diagnose_missing_payload(html)["reason"], "unknown")

    def test_the_decoder_error_is_unchanged_and_carries_the_diagnostic(self):
        exc = missing_payload(CHALLENGE)
        self.assertIsInstance(exc, ValueError)
        self.assertEqual(str(exc), ERROR)
        self.assertEqual(exc.diagnostic, diagnose_missing_payload(CHALLENGE))

    def test_safe_facts(self):
        diagnostic = diagnose_missing_payload(FORMAT_CHANGE)
        self.assertEqual(diagnostic["page_bytes"], len(FORMAT_CHANGE.encode()))
        self.assertEqual(diagnostic["title"], "ChatGPT - Ocean tides")
        self.assertIn("stream_controller_enqueue", diagnostic["markers_present"])
        self.assertIn("cf_just_a_moment", diagnostic["markers_absent"])
        self.assertFalse(set(diagnostic["markers_present"]) & set(diagnostic["markers_absent"]))

    def test_the_title_is_bounded_and_tag_free(self):
        html = "<title>\n  <b>bold</b>  " + "x" * 500 + "</title>"
        title = diagnose_missing_payload(html)["title"]
        self.assertLessEqual(len(title), TITLE_MAX_CHARS)
        self.assertTrue(title.startswith("bbold/b x"))
        self.assertNotIn("<", title)
        self.assertIsNone(diagnose_missing_payload("no title here")["title"])

    def test_the_log_sample_is_bounded(self):
        html = OTHER + "z" * 10_000
        self.assertEqual(missing_payload(html).log_sample, html[:LOG_SAMPLE_MAX_CHARS])

    def test_other_failures_keep_the_plain_body_and_log_nothing(self):
        logged: list[str] = []
        body = ingest_failure_body(ValueError("Invalid share URL"), log=logged.append)
        self.assertEqual(body, {"error": "Invalid share URL"})
        self.assertEqual(logged, [])


class _PageFetcher:
    def __init__(self, html: str):
        self.html = html

    async def fetch(self, share_id: str) -> str:
        return self.html


def page_service(html: str) -> ShareService:
    """The real ingest path — decoder and parser — over a canned page."""
    return ShareService(_PageFetcher(html), ChatGptFlightDecoder(), ChatGptParser())


class MissingPayloadRoutes:
    """What every router answers. Subclasses supply ``ingest(html) -> (status, raw_body)``."""

    HEADER_SECRETS = ("HEADER-COOKIE-SENTINEL-91c2", "HEADER-CUSTOM-SENTINEL-44be")

    def ingest_logged(self, html: str):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            status, raw = self.ingest(html)
        return status, raw, out.getvalue()

    def test_every_class_is_a_400_with_the_same_error_and_its_diagnostic(self):
        for reason, html in FIXTURES.items():
            with self.subTest(reason=reason):
                status, raw, _ = self.ingest_logged(html)
                payload = json.loads(raw)
                self.assertEqual(status, 400)
                self.assertEqual(payload["error"], ERROR)
                self.assertEqual(payload["diagnostic"], diagnose_missing_payload(html))

    def test_the_response_has_no_page_html_and_no_header_values(self):
        for reason, html in FIXTURES.items():
            with self.subTest(reason=reason):
                _, raw, _ = self.ingest_logged(html)
                self.assertNotIn("<", raw)
                self.assertNotIn(PAGE_SECRET, raw)
                for secret in self.HEADER_SECRETS + (OWNER,):
                    self.assertNotIn(secret, raw)

    def test_the_diagnostic_is_logged_with_a_bounded_sample(self):
        html = CHALLENGE + "z" * 10_000
        _, _, logs = self.ingest_logged(html)
        line = next(line for line in logs.splitlines() if line.startswith("[ingest] "))
        record = json.loads(line.removeprefix("[ingest] "))
        self.assertEqual(record["error"], ERROR)
        self.assertEqual(record["diagnostic"]["reason"], "challenge_page")
        self.assertEqual(record["sample"], html[:LOG_SAMPLE_MAX_CHARS])


class TestWorkerRoutes(MissingPayloadRoutes, unittest.TestCase):
    """`chat_archive/entry.py` — the production router."""

    def ingest(self, html: str):
        worker = _Worker(D1ConversationRepository(FakeD1(BACKEND / "migrations")), MemoryMediaStore())
        worker._service = lambda: page_service(html)

        async def read_json():
            return {"share_url": SHARE_URL}

        request = SimpleNamespace(
            url="https://archive.test/api/desk/ingest",
            method="POST",
            email=OWNER,
            json=read_json,
            headers={"cookie": f"s={self.HEADER_SECRETS[0]}", "x-custom": self.HEADER_SECRETS[1]},
        )
        response = asyncio.run(worker.fetch(request))
        return response.status, response.body


class TestLocalServerRoutes(MissingPayloadRoutes, unittest.TestCase):
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
        repo = SqliteConversationRepository(str(Path(tmp.name) / "archive.db"))
        patcher = mock.patch.object(local_server, "repo", repo)
        patcher.start()
        self.addCleanup(patcher.stop)

    def ingest(self, html: str):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=10)
        self.addCleanup(connection.close)
        with mock.patch.object(local_server, "service", page_service(html)):
            connection.request(
                "POST",
                "/api/desk/ingest",
                body=json.dumps({"share_url": SHARE_URL}).encode(),
                headers={
                    "Content-Type": "application/json",
                    "X-Archive-Email": OWNER,
                    "Cookie": f"s={self.HEADER_SECRETS[0]}",
                    "X-Custom": self.HEADER_SECRETS[1],
                },
            )
            response = connection.getresponse()
            return response.status, response.read().decode()


if __name__ == "__main__":
    unittest.main(verbosity=2)

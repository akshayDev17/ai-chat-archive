"""The 400 for "Could not locate conversation data in payload" says *why* — offline.

A share page can carry a payload and still no conversation where the decoder
looks for one. On 2026-11-17 that is exactly what chatgpt.com serves for a
deleted or unknown share: a ~550 KB React Router page whose flight payload's
share route answers ``serverResponse: {"type": "error", "error":
"Conversation has been deleted. …"}``. It is also what a reshaped payload
would look like. The 400 keeps its ``error`` string exactly and, like "No
flight payload found in page", gains a ``diagnostic`` that tells them apart:

  * ``share_unavailable`` — the share route answered with an error,
  * ``format_change``     — the payload is there, the conversation is not,

with the payload kind, page and payload sizes, the bounded title, and which
keys on the way to the conversation were and were not present. Nothing from
the payload's text is in the response; a bounded JSON sample of the deepest
key found goes to the server log only.

Run:  python3 backend/test/test_missing_conversation_diagnostic.py
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "test"))

from chat_archive.chatgpt.decoder import ChatGptShareDecoder, MissingConversationData  # noqa: E402
from chat_archive.chatgpt.page_diagnostic import LOG_SAMPLE_MAX_CHARS  # noqa: E402
from share_pages import flight_page, octane_page  # noqa: E402
from test_missing_payload_diagnostic import (  # noqa: E402
    MissingPayloadRoutes,
    TestLocalServerRoutes,
    TestWorkerRoutes,
)

ERROR = "Could not locate conversation data in payload"
ROUTE = "routes/share.$shareId.($action)"
# Planted in each payload's free text; must never come back in a response.
PAYLOAD_SECRET = "PAYLOAD-TEXT-SENTINEL-5d0e"

# The deleted-share page, as chatgpt.com served the made-up share id
# 00000000-0000-4000-8000-000000000000 on 2026-11-17 (keys and values kept,
# the bulk of the payload trimmed).
def deleted_share(response_type: str = "error") -> str:
    return flight_page(
        {
            "loaderData": {
                "root": {"disableSSR": False, "promoteCss": True},
                ROUTE: {
                    "sharedConversationId": "00000000-0000-4000-8000-000000000000",
                    "serverResponse": {
                        "type": response_type,
                        "error": f"Conversation has been deleted. Start a new chat. {PAYLOAD_SECRET}",
                        "showInaccessibleToast": True,
                        "toastMessage": "Conversation has been deleted. Start a new chat.",
                    },
                    "moderationMode": False,
                    "meta": None,
                },
            },
            "actionData": None,
            "errors": None,
        }
    )


DELETED_SHARE = deleted_share()
# The share route answers, but not with a conversation under `data`.
RESHAPED_FLIGHT = flight_page(
    {"loaderData": {ROUTE: {"serverResponse": {"conversation": {"title": PAYLOAD_SECRET}}}}},
    title="ChatGPT - Ocean tides",
)
# No share route at all.
NO_ROUTE_FLIGHT = flight_page({"loaderData": {"root": {"note": PAYLOAD_SECRET}}})
# An octane page whose data has no conversation in it.
OCTANE_WITHOUT_CONVERSATION = octane_page(
    {"kind": "conversation", "locale": "en-US", "note": PAYLOAD_SECRET}, title="Ocean tides"
)

FIXTURES = {
    "deleted share": DELETED_SHARE,
    "reshaped flight payload": RESHAPED_FLIGHT,
    "flight payload with no share route": NO_ROUTE_FLIGHT,
    "octane page with no conversation": OCTANE_WITHOUT_CONVERSATION,
}


def missing_conversation(html: str) -> MissingConversationData:
    try:
        ChatGptShareDecoder().decode(html)
    except MissingConversationData as exc:
        return exc
    raise AssertionError("the page decoded")


def payload_bytes(html: str, marker: str, end: str) -> int:
    start = html.index(marker) + len(marker)
    return len(html[start : html.index(end, start)].encode("utf-8"))


class TestClassifier(unittest.TestCase):
    def test_the_error_is_unchanged_and_carries_a_diagnostic(self):
        for name, html in FIXTURES.items():
            with self.subTest(page=name):
                exc = missing_conversation(html)
                self.assertIsInstance(exc, ValueError)
                self.assertEqual(str(exc), ERROR)
                self.assertIsInstance(exc.diagnostic, dict)

    def test_a_deleted_share_is_share_unavailable(self):
        diagnostic = missing_conversation(DELETED_SHARE).diagnostic
        flight = json.loads(json.loads(DELETED_SHARE.split("enqueue(", 1)[1].split(");</script>", 1)[0]))
        self.assertEqual(
            diagnostic,
            {
                "reason": "share_unavailable",
                "payload_kind": "flight",
                "page_bytes": len(DELETED_SHARE.encode("utf-8")),
                "payload_bytes": len(json.dumps(flight, separators=(",", ":")).encode("utf-8")),
                "title": "ChatGPT",
                "keys_present": ["loaderData", ROUTE, "serverResponse"],
                "keys_absent": ["data"],
                "server_response_type": "error",
            },
        )

    def test_a_payload_without_the_conversation_is_a_format_change(self):
        reshaped = missing_conversation(RESHAPED_FLIGHT).diagnostic
        self.assertEqual(reshaped["reason"], "format_change")
        self.assertEqual(reshaped["keys_present"], ["loaderData", ROUTE, "serverResponse"])
        self.assertEqual(reshaped["keys_absent"], ["data"])
        self.assertIsNone(reshaped["server_response_type"])
        self.assertEqual(reshaped["title"], "ChatGPT - Ocean tides")

        no_route = missing_conversation(NO_ROUTE_FLIGHT).diagnostic
        self.assertEqual(no_route["reason"], "format_change")
        self.assertEqual(no_route["keys_present"], ["loaderData"])
        self.assertEqual(no_route["keys_absent"], [ROUTE, "serverResponse", "data"])

    def test_an_octane_page_without_a_conversation_is_a_format_change(self):
        diagnostic = missing_conversation(OCTANE_WITHOUT_CONVERSATION).diagnostic
        self.assertEqual(
            diagnostic,
            {
                "reason": "format_change",
                "payload_kind": "web_mobile_data",
                "page_bytes": len(OCTANE_WITHOUT_CONVERSATION.encode("utf-8")),
                "payload_bytes": payload_bytes(
                    OCTANE_WITHOUT_CONVERSATION, 'type="application/json">', "</script>"
                ),
                "title": "Ocean tides",
                "keys_present": [],
                "keys_absent": ["conversation", "messages"],
                "page_kind": "conversation",
            },
        )

    def test_an_unparseable_octane_payload_is_still_diagnosed(self):
        html = OCTANE_WITHOUT_CONVERSATION.replace('{"kind"', '{"kind" oops', 1)
        diagnostic = missing_conversation(html).diagnostic
        self.assertEqual(diagnostic["reason"], "format_change")
        self.assertEqual(diagnostic["keys_absent"], ["conversation", "messages"])
        self.assertIsNone(diagnostic["page_kind"])

    def test_payload_values_are_reported_only_as_identifiers(self):
        diagnostic = missing_conversation(deleted_share(f"error: {PAYLOAD_SECRET} <b>")).diagnostic
        self.assertIsNone(diagnostic["server_response_type"])
        self.assertEqual(diagnostic["reason"], "format_change")
        self.assertNotIn(PAYLOAD_SECRET, json.dumps(diagnostic))

    def test_no_payload_text_is_in_the_diagnostic(self):
        for name, html in FIXTURES.items():
            with self.subTest(page=name):
                text = json.dumps(missing_conversation(html).diagnostic)
                self.assertNotIn(PAYLOAD_SECRET, text)
                self.assertNotIn("Conversation has been deleted", text)
                self.assertNotIn("<", text)

    def test_the_log_sample_is_the_deepest_key_found_and_bounded(self):
        sample = missing_conversation(DELETED_SHARE).log_sample
        self.assertEqual(json.loads(sample)["type"], "error")
        self.assertIn(PAYLOAD_SECRET, sample)

        long_page = flight_page({"loaderData": {"root": {"note": "z" * 10_000}}})
        self.assertEqual(len(missing_conversation(long_page).log_sample), LOG_SAMPLE_MAX_CHARS)


class MissingConversationRoutes(MissingPayloadRoutes):
    """The same router contract as "No flight payload found in page"."""

    def test_every_class_is_a_400_with_the_same_error_and_its_diagnostic(self):
        for name, html in FIXTURES.items():
            with self.subTest(page=name):
                status, raw, _ = self.ingest_logged(html)
                payload = json.loads(raw)
                self.assertEqual(status, 400)
                self.assertEqual(payload["error"], ERROR)
                self.assertEqual(payload["diagnostic"], missing_conversation(html).diagnostic)

    def test_the_response_has_no_page_html_and_no_header_values(self):
        for name, html in FIXTURES.items():
            with self.subTest(page=name):
                _, raw, _ = self.ingest_logged(html)
                self.assertNotIn("<", raw)
                self.assertNotIn(PAYLOAD_SECRET, raw)
                self.assertNotIn("Conversation has been deleted", raw)
                for secret in self.HEADER_SECRETS + ("owner@example.com",):
                    self.assertNotIn(secret, raw)

    def test_the_diagnostic_is_logged_with_a_bounded_sample(self):
        _, _, logs = self.ingest_logged(DELETED_SHARE)
        line = next(line for line in logs.splitlines() if line.startswith("[ingest] "))
        record = json.loads(line.removeprefix("[ingest] "))
        self.assertEqual(record["error"], ERROR)
        self.assertEqual(record["diagnostic"]["reason"], "share_unavailable")
        self.assertEqual(json.loads(record["sample"])["type"], "error")
        self.assertLessEqual(len(record["sample"]), LOG_SAMPLE_MAX_CHARS)


class TestWorkerMissingConversation(MissingConversationRoutes, unittest.TestCase):
    """`chat_archive/entry.py` — the production router."""

    ingest = TestWorkerRoutes.ingest


class TestLocalServerMissingConversation(MissingConversationRoutes, TestLocalServerRoutes):
    """`local_server.py` — the router local development runs."""


# Imported for their harness only; their own tests run in their own file.
del TestWorkerRoutes, TestLocalServerRoutes


if __name__ == "__main__":
    unittest.main(verbosity=2)

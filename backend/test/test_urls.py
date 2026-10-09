"""Tests for the shared URL helpers, and the open-redirect guard in particular.

``safe_next`` decides the ``Location`` header of the sign-in handoff, so a bug
here is a phishing vector rather than a cosmetic one: if it ever accepted
``https://evil.example``, our own trusted sign-in URL would forward visitors to
an attacker. It is defined once and shared by both entrypoints precisely so the
guard cannot be hardened in one and forgotten in the other — and this file is
what pins the behaviour down.

Runs offline.

Run:  python3 backend/test/test_urls.py
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from chat_archive.urls import (  # noqa: E402
    DEFAULT_AFTER_SIGN_IN,
    first_param,
    path_of,
    query_of,
    safe_next,
)


class TestSafeNext(unittest.TestCase):
    def test_allows_a_same_site_path(self):
        self.assertEqual(safe_next("/chat-archives"), "/chat-archives")
        self.assertEqual(safe_next("/chat-archives/abc"), "/chat-archives/abc")

    def test_rejects_an_absolute_url(self):
        for hostile in (
            "https://evil.example",
            "http://evil.example/steal",
            "javascript:alert(1)",
            "data:text/html,<script>alert(1)</script>",
        ):
            with self.subTest(hostile=hostile):
                self.assertEqual(safe_next(hostile), DEFAULT_AFTER_SIGN_IN)

    def test_rejects_a_protocol_relative_url(self):
        # `//evil.example` is the classic bypass: it starts with "/" but the
        # browser treats it as a full URL with an inherited scheme.
        self.assertEqual(safe_next("//evil.example"), DEFAULT_AFTER_SIGN_IN)

    def test_falls_back_instead_of_raising(self):
        # A bad `next` must not cost the visitor their sign-in.
        for value in (None, "", "   "):
            with self.subTest(value=value):
                self.assertEqual(safe_next(value), DEFAULT_AFTER_SIGN_IN)


class TestPathAndQuery(unittest.TestCase):
    def test_path_strips_query_and_trailing_slash(self):
        self.assertEqual(path_of("https://a.com/x/y/?q=1"), "/x/y")
        self.assertEqual(path_of("https://a.com/"), "/")

    def test_query_is_parsed_and_percent_decoded(self):
        query = query_of("https://a.com/x?next=%2Fchat-archives%2Fabc&other=1")
        self.assertEqual(first_param(query, "next"), "/chat-archives/abc")

    def test_missing_query_is_empty(self):
        self.assertEqual(query_of("https://a.com/x"), {})
        self.assertIsNone(first_param(query_of("https://a.com/x"), "next"))

    def test_a_hostile_next_survives_decoding_then_gets_rejected(self):
        # The realistic attack path end to end: percent-encoded in the query
        # string, decoded by the parser, then refused by the guard.
        query = query_of("https://a.com/s?next=https%3A%2F%2Fevil.example")
        self.assertEqual(safe_next(first_param(query, "next")), DEFAULT_AFTER_SIGN_IN)


if __name__ == "__main__":
    unittest.main(verbosity=2)

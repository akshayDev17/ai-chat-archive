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
    desk_media_target,
    first_param,
    media_key_of,
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


KEY = "img/" + "a" * 64 + ".jpg"


class TestMediaPaths(unittest.TestCase):
    """The media routes: one public read, and the owner's desk endpoints."""

    def test_a_well_formed_key_is_accepted(self):
        self.assertEqual(media_key_of(f"/api/media/{KEY}"), KEY)

    def test_a_per_story_key_is_accepted(self):
        key = "chat-archives/6adf3276-41cc-83e8-8bb7-669b6f0a4748/47780937-aaaa-bbbb-cccc-0123456789ab/01.jpg"
        self.assertEqual(media_key_of(f"/api/media/{key}"), key)

    def test_a_per_story_key_cannot_climb_or_nest(self):
        for bad in (
            "/api/media/chat-archives/../secret/01.jpg",
            "/api/media/chat-archives/s1/../../x/01.jpg",
            "/api/media/chat-archives/s1/m/01.exe",
            "/api/media/chat-archives/s1/m/extra/01.jpg",
            "/api/media/chat-archives/s1/m/ab.jpg",
        ):
            with self.subTest(path=bad):
                self.assertIsNone(media_key_of(bad))

    def test_a_key_that_escapes_the_img_prefix_is_rejected(self):
        # The key becomes a store path locally; it must never climb out.
        for bad in ("/api/media/../secret", "/api/media/img/../../x.jpg",
                    "/api/media/img/abc.jpg", "/api/media/img/" + "a" * 64 + ".exe",
                    "/api/sessions/x"):
            with self.subTest(path=bad):
                self.assertIsNone(media_key_of(bad))

    def test_desk_media_targets(self):
        self.assertEqual(desk_media_target("/api/desk/media"), (None, None))
        self.assertEqual(desk_media_target("/api/desk/media/abc-123"), ("abc-123", None))
        self.assertEqual(desk_media_target("/api/desk/media/abc-123/retry"), ("abc-123", "retry"))
        self.assertEqual(desk_media_target("/api/desk/media/abc-123/copy"), ("abc-123", "copy"))

    def test_other_paths_are_not_desk_media(self):
        for other in ("/api/desk/filings", "/api/desk/media/a/b/c", "/api/desk/media/a/delete"):
            with self.subTest(path=other):
                self.assertIsNone(desk_media_target(other))


if __name__ == "__main__":
    unittest.main(verbosity=2)

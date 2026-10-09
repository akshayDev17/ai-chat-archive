"""Tests for the raw-socket share fetcher's parsing, offline.

The Worker cannot use `fetch()` to reach chatgpt.com — Cloudflare stamps every
subrequest with a `Cf-Worker` header and ChatGPT's edge refuses it — so the
Worker speaks HTTP over `cloudflare:sockets` instead. That means parsing a
response by hand, and the parsing is where the danger is.

The share page arrives `Transfer-Encoding: chunked`. A reader that ignores that
returns the *chunk framing* as the body; a reader that mishandles it returns a
truncated page. Either way the transcript still parses and still renders — it is
just wrong, or short, or full of hexadecimal size lines. Nothing raises. So the
parsing is pure functions over bytes, and this file is why they can be trusted.

Run:  python3 backend/test/test_socket_fetcher.py
"""

from __future__ import annotations

import gzip
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from chat_archive.chatgpt.socket_fetcher import (  # noqa: E402
    FetchError,
    IncompleteResponse,
    build_request,
    decode_chunked,
    parse_headers,
    parse_response,
    parse_status,
    resolve_location,
)

PAGE = b"<html><body>a share page</body></html>"


def chunked(*pieces: bytes) -> bytes:
    """Encode pieces as an HTTP chunked body, with a proper terminator."""
    out = b""
    for piece in pieces:
        out += b"%x\r\n%s\r\n" % (len(piece), piece)
    return out + b"0\r\n\r\n"


def response(status=200, headers=(), body=b"") -> bytes:
    head = b"HTTP/1.1 %d OK\r\n" % status
    for name, value in headers:
        head += b"%s: %s\r\n" % (name.encode(), value.encode())
    return head + b"\r\n" + body


class TestStatusAndHeaders(unittest.TestCase):
    def test_reads_the_status_code(self):
        self.assertEqual(parse_status(b"HTTP/1.1 403 Forbidden"), 403)
        self.assertEqual(parse_status(b"HTTP/2 200"), 200)

    def test_rejects_a_non_status_line(self):
        with self.assertRaises(FetchError):
            parse_status(b"<html>not http</html>")

    def test_lowercases_header_names(self):
        # Case varies between servers, and looking up "Location" when the server
        # sent "location" is a silent redirect failure.
        headers = parse_headers(b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nLocation: /x")
        self.assertEqual(headers["content-type"], "text/html")
        self.assertEqual(headers["location"], "/x")

    def test_ignores_continuation_and_blank_lines(self):
        headers = parse_headers(b"HTTP/1.1 200 OK\r\n\r\nX-A: 1")
        self.assertEqual(headers, {"x-a": "1"})


class TestChunkedDecoding(unittest.TestCase):
    def test_single_chunk(self):
        self.assertEqual(decode_chunked(chunked(b"hello")), b"hello")

    def test_multiple_chunks_concatenate(self):
        self.assertEqual(decode_chunked(chunked(b"one", b"two", b"three")), b"onetwothree")

    def test_chunk_extensions_are_ignored(self):
        body = b"5;ext=value\r\nhello\r\n0\r\n\r\n"
        self.assertEqual(decode_chunked(body), b"hello")

    def test_an_empty_body_decodes_to_empty(self):
        self.assertEqual(decode_chunked(b"0\r\n\r\n"), b"")

    def test_a_truncated_chunk_raises_rather_than_returning_short(self):
        """The whole point.

        A chunk that promises more than arrived must not yield what it got: a
        short transcript parses cleanly and is missing its last turns, which is
        a wrong answer with no symptom.
        """
        body = b"ff\r\nonly a few bytes\r\n0\r\n\r\n"
        with self.assertRaises(IncompleteResponse):
            decode_chunked(body)

    def test_a_missing_size_line_raises(self):
        with self.assertRaises(IncompleteResponse):
            decode_chunked(b"5\r\nhello")

    def test_an_unreadable_size_raises(self):
        with self.assertRaises(IncompleteResponse):
            decode_chunked(b"zz\r\nhello\r\n0\r\n\r\n")


class TestParseResponse(unittest.TestCase):
    def test_chunked_response_is_decoded(self):
        raw = response(200, [("Transfer-Encoding", "chunked")], chunked(PAGE[:10], PAGE[10:]))
        got = parse_response(raw)
        self.assertEqual(got.status, 200)
        self.assertEqual(got.body, PAGE)

    def test_content_length_response_is_used_as_is(self):
        raw = response(200, [("Content-Length", str(len(PAGE)))], PAGE)
        self.assertEqual(parse_response(raw).body, PAGE)

    def test_content_length_shorter_than_the_body_truncates_deliberately(self):
        # A keep-alive style response where more arrived than declared.
        raw = response(200, [("Content-Length", "4")], b"abcdEFGH")
        self.assertEqual(parse_response(raw).body, b"abcd")

    def test_content_length_longer_than_the_body_raises(self):
        raw = response(200, [("Content-Length", "500")], b"short")
        with self.assertRaises(IncompleteResponse):
            parse_response(raw)

    def test_a_gzipped_body_is_decompressed(self):
        raw = response(200, [("Content-Encoding", "gzip")], gzip.compress(PAGE))
        self.assertEqual(parse_response(raw).body, PAGE)

    def test_an_unsupported_encoding_raises_rather_than_returning_garbage(self):
        raw = response(200, [("Content-Encoding", "br")], PAGE)
        with self.assertRaises(FetchError):
            parse_response(raw)

    def test_a_response_with_no_blank_line_raises(self):
        with self.assertRaises(IncompleteResponse):
            parse_response(b"HTTP/1.1 200 OK\r\nContent-Type: text/html")


class TestRedirects(unittest.TestCase):
    def test_absolute_location(self):
        self.assertEqual(
            resolve_location("https://chatgpt.com/share/x", "chatgpt.com"),
            ("chatgpt.com", "/share/x"),
        )

    def test_relative_location_keeps_the_host(self):
        self.assertEqual(resolve_location("/share/x", "chatgpt.com"), ("chatgpt.com", "/share/x"))

    def test_an_empty_location_does_not_lose_the_path(self):
        self.assertEqual(resolve_location("", "chatgpt.com"), ("chatgpt.com", "/"))


class TestRequestFraming(unittest.TestCase):
    def test_requests_identity_encoding(self):
        # Decompression is a step that can fail; not asking for it removes it.
        self.assertIn("Accept-Encoding: identity\r\n", build_request("chatgpt.com", "/x", "UA"))

    def test_terminates_headers_and_uses_the_given_path(self):
        req = build_request("chatgpt.com", "/share/abc", "UA")
        self.assertTrue(req.startswith("GET /share/abc HTTP/1.1\r\n"))
        self.assertTrue(req.endswith("\r\n\r\n"))

    def test_closes_the_connection_so_the_read_loop_can_end(self):
        self.assertIn("Connection: close\r\n", build_request("chatgpt.com", "/x", "UA"))

    def test_returns_text_not_bytes(self):
        """Handed to TextEncoder, which takes a string.

        Given bytes, TextEncoder does not raise — it coerces with str() and
        sends the repr, so the server sees `b'GET /share/… '` and answers 400.
        That is exactly what happened the first time this ran in a Worker.
        """
        self.assertIsInstance(build_request("chatgpt.com", "/x", "UA"), str)


if __name__ == "__main__":
    unittest.main(verbosity=2)

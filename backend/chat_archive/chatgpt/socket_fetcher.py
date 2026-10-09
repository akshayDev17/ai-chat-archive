"""Fetch a ChatGPT share page over a raw TCP socket.

Why not ``js.fetch``
--------------------
Cloudflare's Workers runtime stamps every outbound subrequest with a
``Cf-Worker`` header identifying the Worker that made it. ChatGPT sits behind
Cloudflare; its edge can tell a Worker subrequest apart and answers **403**. The
header cannot be suppressed — setting it to an empty string just gets it
rewritten by the runtime:

    fetch(page, {headers: {"Cf-Worker": ""}})   ->  403, and httpbin still
                                                     reports "Cf-Worker: …"

Verified from inside a Worker, same machine, same moment:

    Worker fetch()                  -> 403
    Worker fetch(), no headers      -> 403
    Worker fetch(), Cf-Worker: ""   -> 403
    urllib from the same machine    -> 200
    curl from the same machine      -> 200
    raw socket from the same Worker -> 200 OK, 200,003 bytes

So the difference is the transport, not the request. ``cloudflare:sockets``
opens a raw connection, which the runtime does not stamp.

The cost is speaking HTTP by hand. That is why the parsing lives in pure
functions — :func:`parse_response` and :func:`decode_chunked` take bytes and
return bytes, with no socket, no FFI and no runtime in sight. The share page
arrives ``Transfer-Encoding: chunked``, and a naive reader **silently
truncates** it: the transcript still parses, still renders, and is simply
missing its last turns. That failure has tests; the plumbing around it is thin
on purpose.

Full evidence: ``docs/worker-fetch-block.md``.
"""

from __future__ import annotations

from typing import NamedTuple
from urllib.parse import urlsplit

from ..ports import ShareFetcher

DEFAULT_HOST = "chatgpt.com"
DEFAULT_PORT = 443
DEFAULT_BASE_PATH = "/share"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

#: The share page is roughly 800 KB uncompressed. Refuse anything wildly larger
#: rather than buffering without limit inside a Worker.
DEFAULT_MAX_BYTES = 8 * 1024 * 1024

REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})


class FetchError(RuntimeError):
    """Raised when the share page cannot be retrieved."""


class IncompleteResponse(FetchError):
    """The connection ended before the response was complete.

    Raised rather than returning what arrived, because a truncated share page
    parses into a conversation that looks entirely valid and is missing its
    last turns. Better a loud failure than a quietly short transcript.
    """


class HttpResponse(NamedTuple):
    status: int
    headers: dict[str, str]
    body: bytes


# ── pure parsing ──────────────────────────────────────────────────────────


def split_head(buf: bytes) -> tuple[bytes, bytes]:
    """Split a raw response into ``(head, body)`` at the blank line."""
    at = buf.find(b"\r\n\r\n")
    if at == -1:
        raise IncompleteResponse("no header/body separator in the response")
    return buf[:at], buf[at + 4 :]


def parse_status(head: bytes) -> int:
    """The status code from a response head, or a FetchError if unreadable."""
    line = head.split(b"\r\n", 1)[0].decode("latin-1", "replace").strip()
    parts = line.split(None, 2)
    if len(parts) < 2 or not parts[0].upper().startswith("HTTP/"):
        raise FetchError(f"unexpected status line: {line!r}")
    try:
        return int(parts[1])
    except ValueError as exc:
        raise FetchError(f"unexpected status line: {line!r}") from exc


def parse_headers(head: bytes) -> dict[str, str]:
    """Header name (lowercased) → value, for the lines after the status line."""
    headers: dict[str, str] = {}
    for line in head.split(b"\r\n")[1:]:
        if not line or b":" not in line:
            continue
        name, _, value = line.partition(b":")
        headers[name.decode("latin-1").strip().lower()] = value.decode("latin-1").strip()
    return headers


def decode_chunked(body: bytes) -> bytes:
    """Decode ``Transfer-Encoding: chunked``.

    Chunk extensions (``1a;ext=value``) are permitted and ignored. A size line
    that cannot be read, or a chunk shorter than its declared size, raises
    rather than truncating: that is precisely the failure this function exists
    to prevent.
    """
    out = bytearray()
    at = 0
    while True:
        eol = body.find(b"\r\n", at)
        if eol == -1:
            raise IncompleteResponse("truncated chunk size line")
        size_field = body[at:eol].split(b";", 1)[0].strip()
        try:
            size = int(size_field, 16)
        except ValueError as exc:
            raise IncompleteResponse(f"unreadable chunk size {size_field!r}") from exc
        if size == 0:
            return bytes(out)  # terminal chunk; trailer headers end it
        start = eol + 2
        end = start + size
        if end > len(body):
            raise IncompleteResponse(
                f"chunk of {size} bytes promised, only {len(body) - start} arrived"
            )
        out += body[start:end]
        at = end + 2  # skip the chunk's trailing CRLF


def parse_response(raw: bytes) -> HttpResponse:
    """Bytes off the wire → status, headers and a fully decoded body."""
    head, body = split_head(raw)
    status = parse_status(head)
    headers = parse_headers(head)

    if "chunked" in headers.get("transfer-encoding", "").lower():
        body = decode_chunked(body)
    elif "content-length" in headers:
        declared = int(headers["content-length"])
        if len(body) < declared:
            raise IncompleteResponse(
                f"Content-Length promised {declared} bytes, {len(body)} arrived"
            )
        body = body[:declared]

    encoding = headers.get("content-encoding", "").lower()
    if encoding == "gzip":
        body = gunzip(body)
    elif encoding not in ("", "identity"):
        raise FetchError(f"unsupported Content-Encoding: {encoding}")

    return HttpResponse(status, headers, body)


def gunzip(body: bytes) -> bytes:
    """Decompress a gzip body, if this runtime has zlib."""
    try:
        import zlib

        return zlib.decompress(body, 16 + zlib.MAX_WBITS)
    except ImportError as exc:  # pragma: no cover - depends on the runtime
        raise FetchError("gzip response but no zlib in this runtime") from exc


def resolve_location(location: str, host: str) -> tuple[str, str]:
    """A ``Location`` header → ``(host, path)``. Handles relative redirects."""
    if location.startswith("http://") or location.startswith("https://"):
        parts = urlsplit(location)
        return parts.netloc, parts.path or "/"
    return host, location or "/"


def build_request(host: str, path: str, user_agent: str) -> str:
    """A minimal HTTP/1.1 GET, as text.

    Returns ``str``, not ``bytes``, because it is handed straight to
    ``TextEncoder`` — which takes a string. Passing it bytes does not raise: the
    encoder coerces them with ``str()`` and sends the Python *repr*, so the
    server sees ``b'GET /share/… HTTP/1.1\r\n'`` and answers 400. Encoding is
    the transport's job, so the text stops here.

    ``Accept-Encoding: identity`` on purpose: asking for the uncompressed body
    removes a decompression step from the path that has to work, and the page is
    small enough that it does not matter.

    ``Connection: close`` means the response ends when the server closes, which
    is what the read loop waits for.
    """
    return (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {host}\r\n"
        f"User-Agent: {user_agent}\r\n"
        "Accept: text/html,application/xhtml+xml\r\n"
        "Accept-Encoding: identity\r\n"
        "Connection: close\r\n"
        "\r\n"
    )


# ── the transport ─────────────────────────────────────────────────────────


class SocketShareFetcher(ShareFetcher):
    """The Worker's implementation of the ``ShareFetcher`` port.

    ``HttpShareFetcher`` remains the off-Cloudflare one — it is what
    ``local_server.py`` injects — and this is the pair the port was built for.
    Nothing else in the backend changes: the service, decoder, parser and
    repositories do not know or care which of the two they were handed.
    """

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        base_path: str = DEFAULT_BASE_PATH,
        user_agent: str = DEFAULT_USER_AGENT,
        max_bytes: int = DEFAULT_MAX_BYTES,
        max_redirects: int = 3,
    ):
        self._host = host
        self._port = port
        self._base_path = base_path
        self._user_agent = user_agent
        self._max_bytes = max_bytes
        self._max_redirects = max_redirects

    async def fetch(self, share_id: str) -> str:
        host, path = self._host, f"{self._base_path}/{share_id}"

        for _ in range(self._max_redirects + 1):
            response = await self._request_with_retry(host, path)

            if response.status in REDIRECT_STATUSES and "location" in response.headers:
                host, path = resolve_location(response.headers["location"], host)
                continue

            if response.status != 200:
                raise FetchError(f"Share page fetch failed: HTTP {response.status}")

            return response.body.decode("utf-8", "replace")

        raise FetchError(f"Share page redirects exceeded {self._max_redirects}")

    async def _request_with_retry(self, host: str, path: str) -> HttpResponse:
        """One request, retried once if the stream is cut short mid-body.

        ``_request`` tolerates the peer closing a ``Connection: close`` response
        as a stream cancellation instead of EOF, but that close can still arrive
        before the last bytes, leaving the body truncated. A truncated share
        page parses into a conversation that looks valid and is quietly missing
        its final turns, so a truncation is retried once rather than accepted.
        Two truncations in a row are still surfaced loudly.
        """
        last = None
        for _ in range(2):
            try:
                return await self._request(host, path)
            except IncompleteResponse as exc:
                last = exc
        raise last

    async def _request(self, host: str, path: str) -> HttpResponse:
        """One request/response over a raw socket.

        The only Worker-specific code in this file. The imports are inside the
        method so that this module can be imported — and its parsing tested — in
        plain Python, where ``js`` and ``pyodide`` do not exist.
        """
        from js import Object, TextEncoder
        from pyodide.ffi import to_js as _to_js
        from workers.utils import import_from_javascript

        def to_js(obj):
            return _to_js(obj, dict_converter=Object.fromEntries)

        sockets = import_from_javascript("cloudflare:sockets")
        connection = sockets.connect(
            to_js({"hostname": host, "port": self._port}),
            to_js({"secureTransport": "on"}),
        )

        writer = connection.writable.getWriter()
        await writer.write(TextEncoder.new().encode(build_request(host, path, self._user_agent)))

        reader = connection.readable.getReader()
        raw = bytearray()
        while True:
            try:
                chunk = await reader.read()
            except Exception:
                # The runtime cancels the socket's readable stream when the
                # peer closes a `Connection: close` response, rather than
                # signalling EOF. Treat that as end-of-input: parse_response()
                # below rejects a truncated body instead of returning it.
                break
            if chunk.done:
                break
            raw += bytes(chunk.value.to_py())
            if len(raw) > self._max_bytes:
                raise FetchError(f"share page exceeded {self._max_bytes} bytes")

        return parse_response(bytes(raw))

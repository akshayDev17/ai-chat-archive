"""HTTP fetcher for ChatGPT share pages, for anywhere but a Worker.

This is the *local* implementation of the ``ShareFetcher`` port —
``local_server.py`` injects it. The Worker cannot use it: Cloudflare stamps
every outbound subrequest with a ``Cf-Worker`` header, ChatGPT's edge refuses
those with a 403, and the header cannot be suppressed. ``SocketShareFetcher``
speaks HTTP over ``cloudflare:sockets`` instead; see
``docs/worker-fetch-block.md``.

Both implement the same port, so ``ShareService`` — and everything it depends
on — is identical on either side.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Awaitable, Callable
from urllib.parse import quote

from ..ports import ImageFetcher, ReportFileFetcher, ShareFetcher

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


class FetchError(RuntimeError):
    """Raised when the share page cannot be retrieved."""


# The fetch callable is injected so this class is runtime-agnostic:
#   * Cloudflare Workers Python  -> pass `js.fetch`
#   * local tests / other hosts  -> pass any fetch with .status / .text()
FetchCallable = Callable[..., Awaitable[Any]]


class HttpShareFetcher(ShareFetcher):
    """Fetch the (anonymously reachable) share *page*, not the 403'd JSON API."""

    def __init__(
        self,
        fetch_func: FetchCallable,
        base_url: str = "https://chatgpt.com/share",
        user_agent: str = DEFAULT_USER_AGENT,
    ):
        self._fetch = fetch_func
        self._base_url = base_url
        self._user_agent = user_agent

    async def fetch(self, share_id: str) -> str:
        url = f"{self._base_url}/{share_id}"
        response = await self._fetch(
            url,
            headers={
                "User-Agent": self._user_agent,
                "Accept": "text/html,application/xhtml+xml",
            },
            redirect="follow",
        )
        status = getattr(response, "status", None)
        if status != 200:
            raise FetchError(f"Share page fetch failed: HTTP {status}")
        return await response.text()


class HttpImageFetcher(ImageFetcher):
    """Fetch an image directly with urllib — the local dev server's fetcher.

    Locally there is no Cf-Worker header and no Cloudflare egress IP, so the
    proxy hop the Worker needs is unnecessary.
    """

    async def fetch(self, url: str) -> tuple[bytes, str]:
        import asyncio
        import urllib.request

        def _get() -> tuple[bytes, str]:
            request = urllib.request.Request(url, headers={"User-Agent": DEFAULT_USER_AGENT})
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    return response.read(), response.headers.get("Content-Type") or ""
            except Exception as exc:  # noqa: BLE001 - surfaced as one failed attempt
                raise FetchError(f"image fetch failed: {exc}") from exc

        return await asyncio.to_thread(_get)


# -- report files ---------------------------------------------------------
#
# A share's chat-to-markdown report is a file in the reply's sandbox, linked as
# ``sandbox:/mnt/data/<name>.md``. Its bytes are behind two signed-out GETs,
# the same ones the share page's "Download" button makes:
#
#   1. the resolver, ``/backend-anon/share/<share>/file_from_message/<message>
#      ?file_path=<path>``, answers ``{"status": "success", "download_url": …}``
#      (or ``"retry"`` while the file is being prepared);
#   2. ``download_url`` is a signed blob URL on ``sdmntpr*.oaiusercontent.com``
#      that expires five minutes after it is issued.
#
# So the file is resolved and downloaded in one go, and the signed URL is never
# kept. Every failure — no such file, a deleted share, an expired signature, a
# body that is not text — means "no report", never a failed import.

REPORT_RESOLVER_BASE_URL = "https://chatgpt.com/backend-anon/share"

#: Only a blob URL of this shape is followed. The proxy enforces the same.
_REPORT_DOWNLOAD_URL_RE = re.compile(
    r"^https://sdmntpr[a-z0-9]+\.oaiusercontent\.com/files/[A-Za-z0-9_-]+/raw\?[^#\s]*$"
)

#: The share page caps the resolver's answer at 64 KB too.
REPORT_RESOLVER_MAX_BYTES = 64 * 1024
REPORT_FILE_MAX_BYTES = 1024 * 1024

#: Waits between resolver tries while it answers ``"retry"``: three tries, as
#: the share page makes, well inside its 15 s.
REPORT_RETRY_DELAYS = (2.0, 4.0)

#: ``get(url, accept, max_bytes) -> (status, content_type, text)``. Raises on a
#: transport failure or a body over ``max_bytes``.
ReportGet = Callable[[str, str, int], Awaitable[tuple[int, str, str]]]
SleepCallable = Callable[[float], Awaitable[None]]


def report_resolver_url(share_id: str, message_id: str, path: str) -> str:
    """The resolver URL for one linked file. Stable: it holds no signature."""
    return (
        f"{REPORT_RESOLVER_BASE_URL}/{quote(share_id, safe='')}/file_from_message/"
        f"{quote(message_id, safe='')}?file_path={quote(path, safe='/')}"
    )


async def download_report_file(
    get: ReportGet, resolver_url: str, sleep: SleepCallable = asyncio.sleep
) -> str | None:
    """Resolve the file, then download it at once: its Markdown, or ``None``."""
    try:
        for delay in (*REPORT_RETRY_DELAYS, None):
            answer = await _resolve(get, resolver_url)
            status = answer.get("status") if answer else None
            if status == "retry" and delay is not None:
                await sleep(delay)
                continue
            if status != "success":
                return None
            return await _download(get, answer.get("download_url"))
    except Exception:  # noqa: BLE001 - any failure is "no report"
        return None


async def _resolve(get: ReportGet, resolver_url: str) -> dict | None:
    status, _, body = await get(resolver_url, "application/json", REPORT_RESOLVER_MAX_BYTES)
    if status != 200:
        return None
    answer = json.loads(body)
    return answer if isinstance(answer, dict) else None


async def _download(get: ReportGet, download_url: object) -> str | None:
    if not isinstance(download_url, str) or not _REPORT_DOWNLOAD_URL_RE.match(download_url):
        return None
    status, content_type, body = await get(
        download_url, "text/markdown,text/plain", REPORT_FILE_MAX_BYTES
    )
    media_type = content_type.split(";")[0].strip().lower()
    if status != 200 or not media_type.startswith("text/") or media_type == "text/html":
        return None  # an error page is not a report
    return body.lstrip("\ufeff").strip() or None


class HttpReportFetcher(ReportFileFetcher):
    """Download a linked report file directly with urllib — the local dev
    server's fetcher, beside :class:`HttpImageFetcher`.

    Redirects are refused on both hops, as the proxy refuses them.
    """

    def __init__(self, sleep: SleepCallable = asyncio.sleep, timeout: float = 20.0):
        self._sleep = sleep
        self._timeout = timeout

    async def fetch(self, share_id: str, message_id: str, path: str) -> str | None:
        return await download_report_file(
            self._get, report_resolver_url(share_id, message_id, path), self._sleep
        )

    async def _get(self, url: str, accept: str, max_bytes: int) -> tuple[int, str, str]:
        import urllib.error
        import urllib.request

        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None  # the 3xx surfaces as an HTTPError

        def _get() -> tuple[int, str, str]:
            request = urllib.request.Request(
                url, headers={"User-Agent": DEFAULT_USER_AGENT, "Accept": accept}
            )
            try:
                response = urllib.request.build_opener(NoRedirect).open(request, timeout=self._timeout)
            except urllib.error.HTTPError as exc:
                return exc.code, "", ""
            with response:
                body = response.read(max_bytes + 1)
                if len(body) > max_bytes:
                    raise FetchError(f"report fetch over {max_bytes} bytes")
                content_type = response.headers.get("Content-Type") or ""
                return response.status, content_type, body.decode("utf-8", "replace")

        return await asyncio.to_thread(_get)

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

from typing import Any, Awaitable, Callable

from ..ports import ImageFetcher, ShareFetcher

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

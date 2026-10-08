"""HTTP fetcher for ChatGPT share pages."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from ..ports import ShareFetcher

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

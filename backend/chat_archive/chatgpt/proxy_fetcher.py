"""Fetch ChatGPT share pages through an off-Cloudflare proxy.

A Cloudflare Worker cannot reach chatgpt.com directly: ``fetch()`` is 403'd by
the unstoppable ``Cf-Worker`` header, and raw sockets to Cloudflare's own IP
ranges are blocked. So the share page is fetched by a tiny proxy running on
non-Cloudflare infrastructure (``fetch-proxy/main.ts`` on Deno Deploy), and
this fetcher calls that proxy instead.

The proxy is token-gated; this fetcher carries the shared secret. Like
``HttpShareFetcher``, the fetch callable is injected so the same class runs on
the Worker (``js.fetch``) and in plain Python (urllib).
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable
from urllib.parse import quote

from ..ports import ShareFetcher
from .fetcher import DEFAULT_USER_AGENT, FetchError

FetchCallable = Callable[..., Awaitable[Any]]


class ProxyShareFetcher(ShareFetcher):
    """Fetch a share page by asking the Deno proxy, not chatgpt.com directly."""

    def __init__(
        self,
        fetch_func: FetchCallable,
        proxy_base_url: str,
        token: str,
        chatgpt_base_url: str = "https://chatgpt.com/share",
        user_agent: str = DEFAULT_USER_AGENT,
    ):
        self._fetch = fetch_func
        self._proxy_base_url = proxy_base_url.rstrip("/")
        self._token = token
        self._chatgpt_base_url = chatgpt_base_url
        self._user_agent = user_agent

    async def fetch(self, share_id: str) -> str:
        target = f"{self._chatgpt_base_url}/{share_id}"
        url = f"{self._proxy_base_url}/?u={quote(target, safe='')}"
        response = await self._fetch(
            url,
            headers={
                "x-fetch-token": self._token,
                "user-agent": self._user_agent,
            },
            redirect="follow",
        )
        status = getattr(response, "status", None)
        if status != 200:
            raise FetchError(f"proxy fetch failed: HTTP {status}")
        return await response.text()


def make_worker_fetch() -> FetchCallable:
    """A fetch callable backed by the Worker's ``js.fetch``.

    Wrapped here so this module still imports in plain Python: the ``js`` and
    ``pyodide`` imports live inside the function. The ``dict_converter`` is the
    Workers-Python FFI fix — a bare Python dict handed to ``js.fetch`` is
    rejected by the runtime, so headers must be converted with
    ``Object.fromEntries``.
    """
    from js import Object, fetch
    from pyodide.ffi import to_js as _to_js

    def to_js(obj):
        return _to_js(obj, dict_converter=Object.fromEntries)

    async def worker_fetch(url: str, headers=None, redirect: str = "follow"):
        response = await fetch(
            url, to_js({"headers": headers or {}, "redirect": redirect})
        )
        return response

    return worker_fetch

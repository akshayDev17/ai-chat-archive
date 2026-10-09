"""Small URL helpers shared by the Worker and the local server.

Both entrypoints parse the same two things — a request path, and the ``next``
target of the sign-in handoff — so both facts live here rather than being
reimplemented per entrypoint.

The ``next`` sanitizer in particular is a **security control**, not a
convenience: it becomes a ``Location`` header. Duplicating it would mean it
could be hardened in one entrypoint and forgotten in the other, so it is defined
exactly once and imported by both.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

#: Where a signed-in visitor lands when ``next`` is absent or rejected.
DEFAULT_AFTER_SIGN_IN = "/chat-archives"


def path_of(url: str) -> str:
    """The **path** of a URL, without scheme, host, query string or trailing slash.

    This really returns a path (``/api/sessions``), not the URL with its query
    stripped (``https://host/api/sessions``). The distinction is not pedantic:
    the router matches on this value, so a function that quietly returned the
    whole origin would make every route comparison depend on the hostname.
    """
    return urlsplit(url).path.rstrip("/") or "/"


def query_of(url: str) -> dict[str, list[str]]:
    """Parsed query parameters, empty when the URL has no query string."""
    return parse_qs(url.split("?", 1)[1]) if "?" in url else {}


def first_param(query: dict[str, list[str]], name: str) -> str | None:
    """The first value of ``name``, or ``None`` when it is absent."""
    values = query.get(name) or []
    return values[0] if values else None


def safe_next(raw: str | None) -> str:
    """Sanitize the ``next`` target of the sign-in handoff.

    Only a same-site absolute path is allowed. This value becomes a ``Location``
    header, so accepting ``https://evil.example`` or the protocol-relative
    ``//evil.example`` would turn our own sign-in endpoint into an open redirect
    — phishing people from a domain they have a reason to trust.

    Everything else (missing, absolute URL, protocol-relative, ``javascript:``)
    falls back to :data:`DEFAULT_AFTER_SIGN_IN` rather than erroring, because a
    bad ``next`` should not cost the visitor their sign-in.
    """
    if not raw or not raw.startswith("/") or raw.startswith("//"):
        return DEFAULT_AFTER_SIGN_IN
    return raw

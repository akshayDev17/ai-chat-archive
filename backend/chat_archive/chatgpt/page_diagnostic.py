"""Why a fetched ChatGPT share page had no flight payload.

The decoder fails with "No flight payload found in page" whenever an HTTP 200
page holds no ``streamController.enqueue(...)`` chunk. That single message
covers three different root causes, and only one of them means our decoder is
out of date:

  * ``challenge_page``   — a bot / challenge page served with 200 (Cloudflare's
                           "Just a moment", a cf challenge script, ...).
  * ``share_unavailable``— a deleted, private or mistyped share, served as a
                           page whose title says so.
  * ``format_change``    — a normal-looking ChatGPT page with no enqueue chunk
                           our pattern matches: ChatGPT probably changed how it
                           embeds the conversation.
  * ``unknown``          — none of the above.

:func:`diagnose_missing_payload` turns the page into a small, safe summary —
a reason code, the page size, a bounded and tag-free ``<title>``, and which
markers were and were not seen — so an RCA can tell these apart. It never
returns page HTML.
"""

from __future__ import annotations

import re

TITLE_MAX_CHARS = 120
# Bound on the raw-page sample that is logged (never returned to the caller).
LOG_SAMPLE_MAX_CHARS = 2048

_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_TAG_CHARS_RE = re.compile(r"[<>]")
_SPACE_RE = re.compile(r"\s+")

# Marker name -> lowercase substring. Names are the stable identifiers reported
# in the diagnostic; the substrings are not.
#
# Prose is only trusted in the page ``<title>``: anywhere else it can be
# conversation text or a UI string inside a JS bundle.
_TITLE_CHALLENGE_MARKERS = {
    "cf_just_a_moment": "just a moment",
    "cf_attention_required": "attention required! | cloudflare",
}
_TITLE_UNAVAILABLE_MARKERS = {
    "share_not_found": "conversation not found",
    "share_deleted": "shared link has been deleted",
    "share_disabled": "shared link has been disabled",
    "share_unavailable_text": "this link is no longer available",
    "share_404_text": "404 not found",
}
# Searched page-wide. Only tokens of an actual challenge page — not the
# ``/cdn-cgi/challenge-platform/scripts/jsd/`` script Cloudflare injects into
# ordinary pages.
_CHALLENGE_TOKEN_MARKERS = {
    "cf_challenge_platform": "/cdn-cgi/challenge-platform/h/",
    "cf_chl_opt": "_cf_chl_opt",
    "cf_chl": "cf-chl",
}
_CHATGPT_MARKERS = {
    "chatgpt_title": "<title>chatgpt",
    "oaistatic_assets": "cdn.oaistatic.com",
    "react_router_context": "__reactroutercontext",
}
# Present when the call site still exists but our regex no longer matches it
# (an escaping or quoting change) — the sharpest format-change signal.
_ENQUEUE_MARKER = {"stream_controller_enqueue": "streamcontroller.enqueue"}

_TITLE_MARKERS = {**_TITLE_CHALLENGE_MARKERS, **_TITLE_UNAVAILABLE_MARKERS}
_PAGE_MARKERS = {**_CHALLENGE_TOKEN_MARKERS, **_CHATGPT_MARKERS, **_ENQUEUE_MARKER}
_ALL_MARKERS = {**_TITLE_MARKERS, **_PAGE_MARKERS}


def page_title(html: str) -> str | None:
    """The page ``<title>``, whitespace-collapsed, angle brackets removed, bounded."""
    match = _TITLE_RE.search(html)
    if match is None:
        return None
    title = _SPACE_RE.sub(" ", _TAG_CHARS_RE.sub("", match.group(1))).strip()
    return title[:TITLE_MAX_CHARS]


def _reason(present: set[str]) -> str:
    if present & _ENQUEUE_MARKER.keys():
        return "format_change"
    if present & _TITLE_CHALLENGE_MARKERS.keys():
        return "challenge_page"
    if present & _TITLE_UNAVAILABLE_MARKERS.keys():
        return "share_unavailable"
    if present & _CHATGPT_MARKERS.keys():
        return "format_change"
    if present & _CHALLENGE_TOKEN_MARKERS.keys():
        return "challenge_page"
    return "unknown"


def diagnose_missing_payload(html: str) -> dict:
    """Classify a page that held no flight payload. Safe to return to a caller."""
    title = page_title(html)
    lowered_title = (title or "").lower()
    lowered = html.lower()
    present = {name for name, needle in _TITLE_MARKERS.items() if needle in lowered_title}
    present |= {name for name, needle in _PAGE_MARKERS.items() if needle in lowered}
    return {
        "reason": _reason(present),
        "page_bytes": len(html.encode("utf-8")),
        "title": title,
        "markers_present": sorted(present),
        "markers_absent": sorted(_ALL_MARKERS.keys() - present),
    }


def log_sample(html: str) -> str:
    """The bounded head of the page, for server-side logs only."""
    return html[:LOG_SAMPLE_MAX_CHARS]

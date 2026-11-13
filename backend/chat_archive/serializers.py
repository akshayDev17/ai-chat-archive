"""Wire serialization shared by the Worker and the local dev server."""

from __future__ import annotations

from .models import Conversation, Media, Message, Source
from .urls import DESK_IMAGE_ROUTE, PUBLIC_MEDIA_ROUTE


def _utf16_offsets(content: str, spans: tuple[tuple[int, int], ...]) -> list[list[int]]:
    """Convert code-point offsets into UTF-16 offsets.

    The offsets in the payload are **code points** — one index per emoji — and
    Python slices strings the same way, so they line up all the way through the
    parser and the database. JavaScript does not: it indexes strings in UTF-16
    code units, where an emoji outside the Basic Multilingual Plane counts as
    two. A frontend slicing at the raw offset therefore lands a little early for
    every astral character before it.

    That is not hypothetical. A reply containing twelve emoji (🇺🇸 🇷🇺 🇮🇳 🛢 💻 🏥
    📦 …) was off by twelve, which replaced the wrong characters and left the
    tail of the citation token behind as literal "turn0news10" in the prose.
    Sessions with no emoji were correct, which is what made it look random.

    The conversion happens here, at the wire, because the wire's consumer is
    JavaScript: the stored form stays code points (correct for Python) and the
    delivered form is UTF-16 (correct for `slice`). Doing it in the reader
    instead would mean every future consumer has to remember.
    """
    if not spans:
        return []
    if not any(ord(ch) > 0xFFFF for ch in content):
        return [list(span) for span in spans]

    # prefix[i] = UTF-16 length of content[:i]
    prefix = [0] * (len(content) + 1)
    for i, ch in enumerate(content):
        prefix[i + 1] = prefix[i] + (2 if ord(ch) > 0xFFFF else 1)
    return [[prefix[start], prefix[end]] for start, end in spans]


def _source(source: Source, content: str) -> dict:
    return {
        "index": source.index,
        "kind": source.kind,
        "title": source.title,
        "url": source.url,
        "attribution": source.attribution,
        "pub_date": source.pub_date,
        # [[start, end], ...] — offsets into this message's text.
        "spans": _utf16_offsets(content, source.spans),
    }


def _media(media: Media, content: str, media_route: str) -> dict:
    # The same code-point → UTF-16 conversion as source spans, for the same
    # reason: the reader matches each image to its tag by offset.
    ((tag_start, _),) = _utf16_offsets(content, ((media.tag_start, media.tag_start),))
    return {
        "tag_start": tag_start,
        "ordinal": media.ordinal,
        # Only an image already in R2 has somewhere to point. Until then the
        # reader shows nothing rather than hotlinking images.openai.com.
        "src": f"{media_route}{media.r2_key}" if media.status == "copied" and media.r2_key else None,
    }


def _message(message: Message, media_route: str) -> dict:
    return {
        "role": message.role,
        "content": message.content,
        "sources": [_source(s, message.content) for s in message.sources],
        "media": [_media(m, message.content, media_route) for m in message.media],
    }


def conversation_detail(conversation: Conversation, for_owner: bool = False) -> dict:
    """The full payload the reader view needs: report + transcript + citations.

    ``for_owner`` is the filer reading their own copy through the desk: its
    images then point at the filer's own image route — the only one that serves
    a private story's.
    """
    media_route = DESK_IMAGE_ROUTE if for_owner else PUBLIC_MEDIA_ROUTE
    return {
        "id": conversation.share_id,
        "title": conversation.title,
        "source": "chatgpt",
        "report": conversation.report,
        "citations": [
            {"n": c.index, "title": c.title, "url": c.url} for c in conversation.citations
        ],
        "messages": [_message(m, media_route) for m in conversation.messages],
    }


def media_counts(images: list[dict]) -> dict | None:
    """A story's images by status — ``None`` when it has none.

    ``pending`` counts images still waiting *or* mid-copy: from where the filer
    stands, both mean "not there yet".
    """
    if not images:
        return None
    statuses = [image["status"] for image in images]
    return {
        "total": len(statuses),
        "copied": statuses.count("copied"),
        "failed": statuses.count("failed"),
        "pending": statuses.count("pending") + statuses.count("copying"),
    }


def media_cache_control(owner_email: str | None) -> str:
    """The ``Cache-Control`` of a served image.

    Not `immutable` either way: a key is a place in a story, and re-filing can
    put a different picture there. An image served to its filer over the desk
    route may be a private story's, so only their own browser may keep it.
    """
    return "private, max-age=3600" if owner_email else "public, max-age=86400"

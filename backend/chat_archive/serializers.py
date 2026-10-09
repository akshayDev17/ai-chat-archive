"""Wire serialization shared by the Worker and the local dev server."""

from __future__ import annotations

from .models import Conversation, Message, Source


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


def _message(message: Message) -> dict:
    return {
        "role": message.role,
        "content": message.content,
        "sources": [_source(s, message.content) for s in message.sources],
    }


def conversation_detail(conversation: Conversation) -> dict:
    """The full payload the reader view needs: report + transcript + citations."""
    return {
        "id": conversation.share_id,
        "title": conversation.title,
        "source": "chatgpt",
        "report": conversation.report,
        "citations": [
            {"n": c.index, "title": c.title, "url": c.url} for c in conversation.citations
        ],
        "messages": [_message(m) for m in conversation.messages],
    }

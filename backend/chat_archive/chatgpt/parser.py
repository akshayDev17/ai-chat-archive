"""Parse a decoded ChatGPT conversation dict into domain models."""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from ..models import Citation, Conversation, Message, Source
from ..ports import ConversationParser

# Which `content_references` types are actual sources, and how they read.
# Anything absent from this map is skipped: `followup_a` (suggested questions)
# and `sources_footnote` (the zero-width marker placing the Sources panel) are
# both in the payload and neither is a citation.
_KIND_BY_REFERENCE_TYPE = {
    "grouped_webpages": "cite",
    "url": "link",
}

# A `url` reference spells its label inline, and sometimes its target too:
#   \ue200url\ue202<label>\ue201
#   \ue200url\ue202<label>\ue202https://…\ue201
_URL_LABEL_RE = re.compile(r"\ue200url\ue202(?P<label>[^\ue202\ue201]*)")
_URL_REFERENCE_RE = re.compile(
    r"\ue200url\ue202(?P<label>[^\ue202\ue201]*)\ue202(?P<url>https?://[^\ue201]+)"
)

# The chat-to-markdown-report skill asks the model to emit the summary report as
# a Python raw-string literal:
#
#   report = r"""# ..."""
#
# But the VARIABLE NAME IS NOT PART OF THE CONTRACT. Observed in the wild as
# both `report` and `content`, and a fourth name is entirely possible — the
# model is writing plausible Python, not filling in a template. Matching on the
# name dropped the report silently for `content = r"""..."""`: the conversation
# still imported, so nothing failed, and it simply arrived with no report and no
# citations. That is the worst kind of bug — a missing field that looks like an
# empty one.
#
# So the name is ignored, and the choice is made on the content instead.
_RAW_ASSIGN_RE = re.compile(r"[A-Za-z_]\w*\s*=\s*r\"\"\"([\s\S]*?)\"\"\"")

# Bibliography entries look like:  [1] Title. \n https://...
_BIB_RE = re.compile(r"\[(\d+)\]\s+([^\n]+?)\s*\n\s*(https?://\S+)")


class ChatGptParser(ConversationParser):
    """Extract transcript, report and citations from a ChatGPT conversation."""

    def parse(self, share_id: str, raw: dict) -> Conversation:
        title = raw.get("title") or "Untitled"
        messages = self._extract_messages(raw)
        report = self._extract_report(raw)
        citations = self._extract_citations(report)
        return Conversation(
            share_id=share_id,
            title=title,
            messages=messages,
            report=report,
            citations=citations,
        )

    # -- messages ---------------------------------------------------------
    def _extract_messages(self, raw: dict) -> list[Message]:
        mapping = raw.get("mapping") or {}
        chain: list = []
        node_id = raw.get("current_node")
        while node_id and node_id in mapping:
            node = mapping[node_id]
            chain.insert(0, node)
            node_id = node.get("parent") if isinstance(node, dict) else None

        messages: list[Message] = []
        for node in chain:
            message = node.get("message") if isinstance(node, dict) else None
            if not isinstance(message, dict):
                continue
            role = (message.get("author") or {}).get("role")
            if not role:
                continue
            content = self._render_parts(message.get("content"))
            if content.strip():
                # The stored text is trimmed for display, but the offsets in
                # `content_references` are relative to the text as ChatGPT wrote
                # it — including any leading whitespace. Trimming without
                # accounting for that shifts every citation by the trim amount,
                # which puts each marker a few characters early and leaves the
                # tail of the token behind as literal "turn0search7" text.
                lead = _leading_trim(content)
                trimmed = content[lead:]
                messages.append(
                    Message(
                        role=role,
                        content=trimmed,
                        sources=_extract_sources(message, trimmed, shift=lead),
                    )
                )
        return messages

    @staticmethod
    def _render_parts(content: dict | None) -> str:
        """Join a message's parts, *untouched*.

        Deliberately not stripped: the caller trims for display and needs to
        know by how much, because the citation offsets are measured against this
        exact string.
        """
        if not isinstance(content, dict):
            return ""
        parts = content.get("parts") or []
        chunks: list[str] = []
        for part in parts:
            if isinstance(part, str):
                chunks.append(part)
            elif isinstance(part, dict) and part.get("text"):
                chunks.append(part["text"])
        return "\n".join(chunks)

    # -- report -----------------------------------------------------------
    def _extract_report(self, raw: dict) -> str | None:
        """Find the markdown report among the payload's raw-string literals.

        Every ``NAME = r\"\"\"…\"\"\"`` block is a candidate, whatever it is
        called. Preference goes to candidates that *look* like the report — they
        start with a markdown heading — and the longest wins, because the whole
        point of the skill is to produce one long document and any incidental
        snippet in the same conversation is far shorter.

        The flight payload repeats strings across chunks, so the same report can
        appear several times; picking the longest makes the duplicates harmless.
        """
        candidates: list[str] = []
        for text in _walk_strings(raw):
            candidates.extend(_RAW_ASSIGN_RE.findall(text))

        if not candidates:
            return None

        headings = [c for c in candidates if c.lstrip().startswith("#")]
        return max(headings or candidates, key=len).strip()

    # -- citations --------------------------------------------------------
    @staticmethod
    def _extract_citations(report: str | None) -> list[Citation]:
        if not report:
            return []
        return [
            Citation(index=int(n), title=title.strip(), url=url)
            for n, title, url in _BIB_RE.findall(report)
        ]


def _leading_trim(text: str) -> int:
    """How many characters ``str.strip()`` would remove from the left.

    Only the left matters: trimming the right shortens the string without
    moving anything, but trimming the left moves every character.
    """
    return len(text) - len(text.lstrip())


def _extract_sources(message: dict, content: str, shift: int = 0) -> list[Source]:
    """Pull the message's web sources out of ``metadata.content_references``.

    Four reference types appear in real payloads, and only two are sources:

    ``grouped_webpages``
        The inline citation pill. ``items[0]`` carries the title and URL;
        ``safe_urls`` adds variants (including a ``?utm_source=chatgpt.com``
        duplicate of the same URL, which is why the canonical URL comes from
        ``items`` and not from ``safe_urls``).
    ``url``
        A link the model wrote inline. Its label lives in ``matched_text``; the
        target is either written out there too or has to come from ``safe_urls``.
    ``sources_footnote``
        A zero-width marker at the end of the message saying "the Sources panel
        belongs here". Not a source; it carries no items.
    ``followup_a``
        Suggested follow-up questions. Not sources either, and they would be
        actively misleading if rendered as citations.

    Numbering is per message, by order of first appearance, so a source cited
    three times gets one number and one entry in the list — but all three of its
    spans are kept, so all three inline markers can point at it.
    """
    references = (message.get("metadata") or {}).get("content_references") or []

    order: list[str] = []                       # canonical URLs, first seen first
    fields: dict[str, dict] = {}
    spans: dict[str, list[tuple[int, int]]] = {}

    for reference in references:
        if not isinstance(reference, dict):
            continue
        kind = _KIND_BY_REFERENCE_TYPE.get(reference.get("type"))
        if kind is None:
            continue  # followup_a, sources_footnote, or something new

        title, url, attribution, pub_date = _pick_source_fields(reference)
        if not url:
            continue

        if url not in fields:
            order.append(url)
            fields[url] = {
                "title": title,
                "attribution": attribution,
                "pub_date": pub_date,
                "kind": kind,
            }
            spans[url] = []
        else:
            # Same target, cited again. Fill in a title if the first pass only
            # had a bare domain, but keep the first-seen kind.
            if not fields[url]["title"] and title:
                fields[url]["title"] = title

        start, end = reference.get("start_idx"), reference.get("end_idx")
        if isinstance(start, int) and isinstance(end, int):
            start, end = start - shift, end - shift
        if _span_is_trustworthy(content, start, end, reference.get("matched_text")):
            if (start, end) not in spans[url]:
                spans[url].append((start, end))

    return [
        Source(
            index=i + 1,
            title=fields[url]["title"] or _domain_of(url),
            url=url,
            attribution=fields[url]["attribution"],
            pub_date=fields[url]["pub_date"],
            kind=fields[url]["kind"],
            spans=tuple(sorted(spans[url])),
        )
        for i, url in enumerate(order)
    ]


def _pick_source_fields(reference: dict) -> tuple[str, str, str, int | None]:
    """Title, canonical URL, attribution and date for one reference."""
    # `grouped_webpages` — the structured answer.
    for item in reference.get("items") or []:
        if isinstance(item, dict) and item.get("url"):
            return (
                (item.get("title") or "").strip(),
                _strip_tracking(str(item["url"])),
                (item.get("attribution") or "").strip(),
                item.get("pub_date") if isinstance(item.get("pub_date"), int) else None,
            )

    # `url` — the label is inline; the target may or may not be.
    matched = reference.get("matched_text") or ""
    label_match = _URL_LABEL_RE.match(matched)
    label = label_match.group("label").strip() if label_match else ""

    with_url = _URL_REFERENCE_RE.search(matched)
    if with_url:
        return label or with_url.group("label").strip(), _strip_tracking(with_url.group("url")), "", None

    for candidate in reference.get("safe_urls") or []:
        if isinstance(candidate, str) and _strip_tracking(candidate):
            return label, _strip_tracking(candidate), "", None
    return "", "", "", None


def _strip_tracking(url: str) -> str:
    """Drop ChatGPT's ``utm_source=chatgpt.com`` from a URL.

    It is applied inconsistently — sometimes the same link appears twice in
    ``safe_urls``, once with and once without — so two references to one page
    would otherwise look like two different sources and get two numbers.
    """
    parts = urlsplit(url.strip())
    if not parts.query:
        return url.strip()
    kept = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not (k == "utm_source" and v == "chatgpt.com")
    ]
    return urlunsplit(parts._replace(query=urlencode(kept)))


def _span_is_trustworthy(content: str, start, end, matched_text) -> bool:
    """Whether ``content[start:end]`` really is the citation token.

    Checked rather than assumed because a bad offset does not fail loudly — it
    silently rewrites the wrong characters. The payload is self-describing here:
    the span must be in range and must contain the private-use token the
    reference claims to describe.
    """
    if not isinstance(start, int) or not isinstance(end, int):
        return False
    if start < 0 or end > len(content) or start >= end:
        return False
    span = content[start:end]
    if not any("\ue200" <= ch <= "\ue20f" for ch in span):
        return False
    if isinstance(matched_text, str) and matched_text and matched_text != span:
        return False
    return True


def _domain_of(url: str) -> str:
    match = re.match(r"https?://([^/]+)", url)
    return match.group(1).removeprefix("www.") if match else url


def _walk_strings(value: object):
    """Yield every string in a nested structure."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from _walk_strings(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _walk_strings(item)

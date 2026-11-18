"""Read the conversation out of an "octane" share page.

Since 2026-11 chatgpt.com serves most shares from its octane web app
(``/unauth-mweb/`` assets). Those pages carry no React Router flight payload;
the conversation is plain JSON in one script tag::

    <script id="__WEB_MOBILE_DATA__" type="application/json">
      {"conversation": {"title": "…", "messages": [
         {"id": "…", "role": "user", "content": "…"},
         {"id": "…", "role": "assistant", "content": "…",
          "source": {"references": [...], "dil": {...} | null}}, ...]}, ...}
    </script>

The messages are a flat list of the visible turns, and each assistant reply's
``content`` is Markdown *as rendered for the reader*: every citation token has
already been replaced by a ``([Publisher](url))`` link. Each entry of
``source.references`` still carries the old payload's ``content_reference`` in
``data`` — with offsets into the original, tokenised text — plus where its
rendering sits in ``content`` (``startIndex``/``endIndex``).

:func:`conversation_from_web_mobile` rebuilds the old conversation shape from
that — a ``mapping``/``current_node`` chain whose messages hold the original
text and ``metadata.content_references`` — so the parser serves both page
formats unchanged. A GenUI reply (``source.dil``) is turned back into its
markup by :mod:`.dil`.

The page carries no report body. The chat-to-markdown report is only linked,
as a file in the reply's sandbox (``sandbox:/mnt/data/<name>.md``);
:func:`report_links` finds those links so the file can be downloaded.
"""

from __future__ import annotations

import re

from .dil import UnsupportedDil, render_dil

_WEB_MOBILE_DATA_RE = re.compile(
    r'<script\b[^>]*\bid="__WEB_MOBILE_DATA__"[^>]*>(.*?)</script\s*>',
    re.IGNORECASE | re.DOTALL,
)


# A report link, as the reply's Markdown spells it: ``[label](sandbox:/mnt/…)``.
_SANDBOX_LINK_RE = re.compile(r"\]\(\s*<?(sandbox:/mnt/data/[^)\s>]+)")
# A Markdown backslash escape.
_ESCAPE_RE = re.compile(r"\\([!-/:-@\[-`{-~])")
_REPORT_PATH_RE = re.compile(r"^/mnt/data/(?:[^/]+/)*[^/]+\.md$", re.IGNORECASE)


def web_mobile_payload(html: str) -> str | None:
    """The raw JSON text of the page's ``__WEB_MOBILE_DATA__``, if it has one."""
    match = _WEB_MOBILE_DATA_RE.search(html)
    return None if match is None else match.group(1)


def web_mobile_conversation(data: object) -> dict | None:
    """``data.conversation``, when it is a conversation with a message list."""
    conversation = data.get("conversation") if isinstance(data, dict) else None
    if not isinstance(conversation, dict) or not isinstance(conversation.get("messages"), list):
        return None
    return conversation


def conversation_from_web_mobile(conversation: dict) -> dict:
    """The old payload's conversation shape, rebuilt from the octane one."""
    mapping: dict[str, dict] = {}
    parent: str | None = None
    for n, message in enumerate(conversation["messages"]):
        if not isinstance(message, dict):
            continue
        node_id = f"{n}:{message.get('id') or ''}"
        mapping[node_id] = {
            "id": node_id,
            "parent": parent,
            "message": _message(message),
        }
        parent = node_id
    shared = conversation.get("sharedContent")
    title = conversation.get("title") or (shared.get("title") if isinstance(shared, dict) else None)
    return {
        "title": title,
        "mapping": mapping,
        "current_node": parent,
        "report_files": _octane_report_links(conversation["messages"]),
    }


def report_links(message_id: object, app_data: object, text: object) -> list[dict]:
    """The report files one reply links to: ``[{"message_id", "path"}]``.

    A GenUI reply resolves its ``<Link/>`` in ``appData.opGenui.componentResults``
    — ``{"componentName": "Link", "state": {"url": "sandbox:/mnt/data/….md"}}`` —
    and that is read first. The reply's Markdown spells the same link as
    ``[…](sandbox:/mnt/data/….md)``, read as the fallback once its escapes are
    undone: a copy ending ``….md\\`` was seen live, and the resolver answers
    404 for that path and 200 for the clean one.
    """
    if not isinstance(message_id, str) or not message_id:
        return []
    urls = _link_component_urls(app_data)
    if isinstance(text, str):
        urls += _SANDBOX_LINK_RE.findall(text)
    links: list[dict] = []
    for url in urls:
        path = _report_path(url)
        if path is not None and all(link["path"] != path for link in links):
            links.append({"message_id": message_id, "path": path})
    return links


def _octane_report_links(messages: list) -> list[dict]:
    links: list[dict] = []
    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        source = message.get("source") if isinstance(message.get("source"), dict) else {}
        dil = source.get("dil") if isinstance(source.get("dil"), dict) else {}
        links += report_links(message.get("id"), dil.get("appData"), message.get("content"))
    return links


def _link_component_urls(app_data: object) -> list[str]:
    genui = app_data.get("opGenui") if isinstance(app_data, dict) else None
    results = genui.get("componentResults") if isinstance(genui, dict) else None
    if not isinstance(results, dict):
        return []
    urls: list[str] = []
    for result in results.values():
        if not isinstance(result, dict) or result.get("componentName") != "Link":
            continue
        state = result.get("state")
        url = state.get("url") if isinstance(state, dict) else None
        if isinstance(url, str):
            urls.append(url)
    return urls


def _report_path(url: str) -> str | None:
    """The sandbox path of a report link, or ``None`` when it is not one."""
    path = _ESCAPE_RE.sub(r"\1", url.strip()).rstrip("\\").removeprefix("sandbox:")
    if not _REPORT_PATH_RE.match(path) or "/../" in path or "/./" in path:
        return None
    return path


def _message(message: dict) -> dict:
    source = message.get("source") if isinstance(message.get("source"), dict) else {}
    references = [r for r in source.get("references") or [] if isinstance(r, dict)]
    content = message.get("content") if isinstance(message.get("content"), str) else ""
    metadata: dict = {
        "content_references": [r["data"] for r in references if isinstance(r.get("data"), dict)],
    }
    dil = source.get("dil")
    text = None
    if isinstance(dil, dict):
        try:
            text, components = render_dil(dil)
        except UnsupportedDil:
            text = None  # keep the page's own Markdown of the reply
        else:
            metadata["genui_components"] = components
            metadata["model_dil_v2"] = {"appData": dil.get("appData") or {}}
    if text is None:
        text = original_text(content, references)
    return {
        "id": message.get("id"),
        "author": {"role": message.get("role")},
        "content": {"parts": [text]},
        "metadata": metadata,
    }


def original_text(content: str, references: list[dict]) -> str:
    """``content`` with each rendered reference put back as its token.

    The rendering of a reference sits at ``content[startIndex:endIndex]``; the
    token it replaced is ``data.matched_text``. Swapping them back restores the
    text the reference's own ``start_idx``/``end_idx`` are measured against,
    which the parser then checks span by span. A reference that does not sit
    cleanly in ``content`` is left as rendered.

    ``startIndex``/``endIndex`` count UTF-16 code units (they come from
    JavaScript), so every emoji before a reference — a flag is two — moves it
    one unit per astral character; they are converted to string indices first.
    """
    index_of = _utf16_offsets(content)
    out: list[str] = []
    cursor = 0
    for reference in sorted(references, key=_rendered_start):
        start = index_of.get(reference.get("startIndex"))
        end = index_of.get(reference.get("endIndex"))
        token = (reference.get("data") or {}).get("matched_text")
        if start is None or end is None or not isinstance(token, str):
            continue  # missing, or not on a character boundary
        if start < cursor or end <= start:
            continue  # zero-width (a footnote marker) or overlapping
        out.append(content[cursor:start])
        out.append(token)
        cursor = end
    out.append(content[cursor:])
    return "".join(out)


def _utf16_offsets(text: str) -> dict[int, int]:
    """UTF-16 offset -> string index, for every character boundary of ``text``."""
    index_of: dict[int, int] = {}
    offset = 0
    for index, char in enumerate(text):
        index_of[offset] = index
        offset += 2 if ord(char) > 0xFFFF else 1
    index_of[offset] = len(text)
    return index_of


def _rendered_start(reference: dict) -> int:
    start = reference.get("startIndex")
    return start if isinstance(start, int) else -1

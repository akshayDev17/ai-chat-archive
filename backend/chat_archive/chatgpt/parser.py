"""Parse a decoded ChatGPT conversation dict into domain models."""

from __future__ import annotations

import re

from ..models import Citation, Conversation, Message
from ..ports import ConversationParser

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
            if content:
                messages.append(Message(role=role, content=content))
        return messages

    @staticmethod
    def _render_parts(content: dict | None) -> str:
        if not isinstance(content, dict):
            return ""
        parts = content.get("parts") or []
        chunks: list[str] = []
        for part in parts:
            if isinstance(part, str):
                chunks.append(part)
            elif isinstance(part, dict) and part.get("text"):
                chunks.append(part["text"])
        return "\n".join(chunks).strip()

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

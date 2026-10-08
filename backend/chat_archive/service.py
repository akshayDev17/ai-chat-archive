"""Application use case: ingest a share link into a Conversation.

This is the high-level orchestration. It depends only on the ports, so the
composition root (entry.py) can swap in any vendor or repository.
"""

from __future__ import annotations

import re

from .models import Conversation
from .ports import ConversationParser, PayloadDecoder, ShareFetcher

# SSRF guard: only a strict chatgpt.com/share/<uuid> URL is ever fetched.
_SHARE_URL_RE = re.compile(
    r"^https://chatgpt\.com/share/"
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$",
    re.IGNORECASE,
)


class InvalidShareUrl(ValueError):
    """Raised when a pasted URL does not match the expected share-link shape."""


class ShareService:
    def __init__(
        self,
        fetcher: ShareFetcher,
        decoder: PayloadDecoder,
        parser: ConversationParser,
    ):
        self._fetcher = fetcher
        self._decoder = decoder
        self._parser = parser

    async def ingest(self, share_url: str) -> Conversation:
        share_id = self._extract_share_id(share_url)
        raw = await self._fetcher.fetch(share_id)
        decoded = self._decoder.decode(raw)
        return self._parser.parse(share_id, decoded)

    @staticmethod
    def _extract_share_id(share_url: str) -> str:
        match = _SHARE_URL_RE.match((share_url or "").strip())
        if not match:
            raise InvalidShareUrl("Invalid share URL")
        return match.group(1)

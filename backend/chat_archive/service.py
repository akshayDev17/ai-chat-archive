"""Application use case: ingest a share link into a Conversation.

This is the high-level orchestration. It depends only on the ports, so the
composition root (entry.py) can swap in any vendor or repository.
"""

from __future__ import annotations

import asyncio
import re

from .models import Conversation, checked_visibility
from .ports import ConversationParser, PayloadDecoder, ReportFileFetcher, ShareFetcher

# SSRF guard: only a strict chatgpt.com/share/<uuid> URL is ever fetched.
_SHARE_URL_RE = re.compile(
    r"^https://chatgpt\.com/share/"
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$",
    re.IGNORECASE,
)

#: How many linked report files one filing tries, newest first. Each try is
#: two requests and up to two more on a retry.
MAX_REPORT_FILES_TRIED = 3

#: Seconds all the tries together may take before the share files with no report.
REPORT_DOWNLOAD_BUDGET = 30.0


class InvalidShareUrl(ValueError):
    """Raised when a pasted URL does not match the expected share-link shape."""


def requested_visibility(body: object, required: bool = False) -> str | None:
    """The ``visibility`` a request body asks for: ``public``, ``private``, or
    ``None`` when it says nothing.

    Raises ``ValueError`` for anything else, and for nothing when ``required``.
    """
    value = body.get("visibility") if isinstance(body, dict) else None
    if value is None and not required:
        return None
    if not isinstance(value, str):
        raise ValueError("visibility must be 'public' or 'private'")
    return checked_visibility(value)


class ShareService:
    def __init__(
        self,
        fetcher: ShareFetcher,
        decoder: PayloadDecoder,
        parser: ConversationParser,
        report_fetcher: ReportFileFetcher,
        report_budget: float = REPORT_DOWNLOAD_BUDGET,
    ):
        self._fetcher = fetcher
        self._decoder = decoder
        self._parser = parser
        self._report_fetcher = report_fetcher
        self._report_budget = report_budget

    async def ingest(self, share_url: str) -> Conversation:
        """File a share: its transcript, and its report wherever it is.

        A page that carries the report keeps it, and nothing more is fetched.
        Otherwise the report is the newest file a reply links to that can be
        downloaded — or none, which never fails the filing.
        """
        share_id = self._extract_share_id(share_url)
        raw = await self._fetcher.fetch(share_id)
        decoded = self._decoder.decode(raw)
        conversation = self._parser.parse(share_id, decoded)
        if conversation.report is not None:
            return conversation
        report_file = await self._download_report_file(share_id, decoded.get("report_files"))
        if report_file is None:
            return conversation
        return self._parser.parse(share_id, decoded, report_file=report_file)

    async def _download_report_file(self, share_id: str, links: object) -> str | None:
        if not isinstance(links, list):
            return None
        try:
            return await asyncio.wait_for(self._first_report_file(share_id, links), self._report_budget)
        except asyncio.TimeoutError:
            return None

    async def _first_report_file(self, share_id: str, links: list) -> str | None:
        for link in list(reversed(links))[:MAX_REPORT_FILES_TRIED]:
            try:
                report = await self._report_fetcher.fetch(share_id, link["message_id"], link["path"])
            except Exception:  # noqa: BLE001 - a report that cannot be had is no report
                report = None
            if report:
                return report
        return None

    @staticmethod
    def _extract_share_id(share_url: str) -> str:
        match = _SHARE_URL_RE.match((share_url or "").strip())
        if not match:
            raise InvalidShareUrl("Invalid share URL")
        return match.group(1)

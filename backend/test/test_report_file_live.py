"""Live test: a share whose report is only a linked file gets that report.

Share 6afc6e92 ("Meaning of Angen Gatram") was filed with an empty Report tab:
its page carries no report, only a "Download the Markdown report" link to
``sandbox:/mnt/data/angena-gatram-shloka-report.md``. This fetches the share
page, finds the link, downloads the file the way the share page's own button
does (resolver, then the signed blob), and asserts the filed story has the
report and its citations. Whichever page format chatgpt.com serves the share
in, the report comes from the file.

Run:  python backend/test/test_report_file_live.py

Like ``test_decode.py`` it touches the network — ChatGPT, three requests — so
CI sets ARCHIVE_LIVE_TESTS=0 and this skips itself with a reason. Default is
to run, because locally it is the useful thing.
"""

from __future__ import annotations

import asyncio
import os
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from chat_archive.chatgpt.decoder import ChatGptShareDecoder  # noqa: E402
from chat_archive.chatgpt.fetcher import DEFAULT_USER_AGENT, HttpReportFetcher  # noqa: E402
from chat_archive.chatgpt.parser import ChatGptParser  # noqa: E402
from chat_archive.service import ShareService  # noqa: E402

SHARE_ID = "6afc6e92-83c4-83e8-a528-542c00a41add"
SHARE_URL = f"https://chatgpt.com/share/{SHARE_ID}"
REPORT_PATH = "/mnt/data/angena-gatram-shloka-report.md"


class PageFetcher:
    async def fetch(self, share_id: str) -> str:
        request = urllib.request.Request(
            f"https://chatgpt.com/share/{share_id}",
            headers={"User-Agent": DEFAULT_USER_AGENT, "Accept": "text/html"},
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.read().decode("utf-8")


def main() -> int:
    if os.environ.get("ARCHIVE_LIVE_TESTS", "1") != "1":
        print("SKIPPED — ARCHIVE_LIVE_TESTS is not 1, so the live network test is off.")
        print("         (CI sets this. Remove it, or set it to 1, to run for real.)")
        return 0

    print(f"fetching {SHARE_URL} ...")
    html = asyncio.run(PageFetcher().fetch(SHARE_ID))
    decoded = ChatGptShareDecoder().decode(html)
    flight = "streamController.enqueue" in html
    print("format   :", "flight" if flight else "octane")
    print("links    :", decoded["report_files"])
    assert any(link["path"] == REPORT_PATH for link in decoded["report_files"]), "report link not found"

    service = ShareService(PageFetcher(), ChatGptShareDecoder(), ChatGptParser(), HttpReportFetcher())
    conversation = asyncio.run(service.ingest(SHARE_URL))
    print("title    :", conversation.title)
    print("messages :", len(conversation.messages))
    print("report   :", len(conversation.report or ""), "chars")
    print("citations:", len(conversation.citations))

    assert conversation.report and conversation.report.startswith("#"), "report missing"
    assert "Aṅgena Gātraṃ" in conversation.report, "not the linked report"
    assert len(conversation.citations) >= 2, "citations missing"
    assert all(c.url.startswith("https://") for c in conversation.citations), "citation without a URL"

    print("\nOK — the linked report file was downloaded, with its citations.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

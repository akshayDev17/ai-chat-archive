"""Local test for the pure backend logic (decoder + parser).

Downloads the share page with stdlib (so it runs anywhere) and asserts the
extraction. The Workers-specific pieces (js.fetch, D1) are exercised only at
deploy; this test proves the decoding/parsing core end to end.

Run:  python backend/test/test_decode.py
"""

from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from chat_archive.chatgpt.decoder import ChatGptFlightDecoder  # noqa: E402
from chat_archive.chatgpt.parser import ChatGptParser  # noqa: E402

SHARE_ID = "6ac7f3f2-fdec-83ec-938b-c27990599e68"
SHARE_URL = f"https://chatgpt.com/share/{SHARE_ID}"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"


def fetch_html(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8")


def main() -> int:
    print(f"fetching {SHARE_URL} ...")
    html = fetch_html(SHARE_URL)

    decoder = ChatGptFlightDecoder()
    parser = ChatGptParser()

    raw = decoder.decode(html)
    conversation = parser.parse(SHARE_ID, raw)

    print("title   :", conversation.title)
    print("messages:", len(conversation.messages))
    print("report  :", len(conversation.report or ""), "chars")
    print("citations:", len(conversation.citations))

    assert conversation.title, "title missing"
    assert len(conversation.messages) > 0, "no messages extracted"
    assert conversation.report and conversation.report.startswith("#"), "report missing"
    assert len(conversation.citations) > 0, "citations missing"

    print("\nOK — transcript, report and citations all extracted.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

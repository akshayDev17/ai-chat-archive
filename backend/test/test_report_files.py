"""A share's linked report file is downloaded when the page carries no report — offline.

On a new-format ("octane") share page the chat-to-markdown report is not in the
page at all: the reply only links it, as ``[Download the Markdown report]
(sandbox:/mnt/data/<name>.md)``, and such a story was filed with an empty Report
tab. The file downloads signed out in two requests, which the share page's own
button makes: a resolver on chatgpt.com answers a five-minute signed blob URL,
which is fetched at once. Older pages often carry no report either and link the
same file.

What this pins:

  * where the link is found — a GenUI reply's resolved ``Link`` component, else
    the reply's Markdown with its escapes undone — in both page formats;
  * resolve-then-download, with a bounded retry while the resolver answers
    ``retry``, and every other failure meaning "no report";
  * the ingest use case downloads only when the page has no report of its own,
    and never fails a filing over it;
  * the bibliography forms the downloaded files use all yield citations.

Run:  python3 backend/test/test_report_files.py
"""

from __future__ import annotations

import asyncio
import copy
import json
import sys
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "test"))

from chat_archive.chatgpt.decoder import ChatGptShareDecoder  # noqa: E402
from chat_archive.chatgpt.fetcher import (  # noqa: E402
    REPORT_FILE_MAX_BYTES,
    download_report_file,
    report_resolver_url,
)
from chat_archive.chatgpt.parser import ChatGptParser  # noqa: E402
from chat_archive.chatgpt.proxy_fetcher import ProxyReportFetcher  # noqa: E402
from chat_archive.service import ShareService  # noqa: E402
from share_pages import OCTANE_SHARE, flight_share, octane_page  # noqa: E402

SHARE_ID = "6afc6e92-83c4-83e8-a528-542c00a41add"
MESSAGE_ID = "98511ba3-be9f-4902-a9f0-840056cd2e6c"
REPORT_PATH = "/mnt/data/angena-gatram-shloka-report.md"
RESOLVER = report_resolver_url(SHARE_ID, MESSAGE_ID, REPORT_PATH)
BLOB = (
    "https://sdmntprnznorth.oaiusercontent.com/files/00000000-a4f4-820b-8b39-a3a54d01df67/raw"
    "?se=2026-11-18T08%3A32%3A13Z&sp=r&sv=2026-06-06&sr=b&sig=abc%2Bdef%3D"
)

REPORT = """# A Headline

The verse is listed as No. 331. [1]

## Bibliography

1. [Wisdomlib — "Sanskrit quote nr. 331"](https://www.wisdomlib.org/sanskrit/quote/mss/subhashita-331) — text.
2. [Abyasam](https://abyasam.com/en/subhashitam/sub-angena-gatram-336) — meaning.
"""


def run(coro):
    return asyncio.run(coro)


def octane_data() -> dict:
    start = OCTANE_SHARE.index('id="__WEB_MOBILE_DATA__"')
    body = OCTANE_SHARE[OCTANE_SHARE.index(">", start) + 1 : OCTANE_SHARE.index("</script>", start)]
    return json.loads(body)


def octane_with_report_link(
    *, component_url: str | None = None, content_link: str | None = None
) -> str:
    """The octane fixture with a report link in its GenUI reply (``msg-a2``)."""
    data = copy.deepcopy(octane_data())
    reply = next(m for m in data["conversation"]["messages"] if m["id"] == "msg-a2")
    if component_url is not None:
        results = reply["source"]["dil"]["appData"]["opGenui"]["componentResults"]
        results["res-report-1"] = {
            "componentName": "Link",
            "status": "resolved",
            "state": {"url": component_url, "title": "Download the Markdown report"},
        }
    if content_link is not None:
        reply["content"] += f"\n\n[Download the Markdown report]({content_link})"
    return octane_page(data)


def flight_with(assistant_text: str, message_id: str = MESSAGE_ID) -> str:
    return flight_share(
        {
            "title": "An older share",
            "current_node": "n2",
            "mapping": {
                "n1": {"id": "n1", "parent": None, "message": {
                    "id": "u1", "author": {"role": "user"}, "content": {"parts": ["Report?"]}}},
                "n2": {"id": "n2", "parent": "n1", "message": {
                    "id": message_id, "author": {"role": "assistant"},
                    "content": {"parts": [assistant_text]}}},
            },
        }
    )


def report_files(html: str) -> list[dict]:
    return ChatGptShareDecoder().decode(html)["report_files"]


# -- finding the link -------------------------------------------------------


class FindingTheReportLink(unittest.TestCase):
    def test_a_page_without_a_report_link_has_none(self):
        self.assertEqual(report_files(OCTANE_SHARE), [])

    def test_a_genui_reply_links_it_through_its_resolved_link_component(self):
        html = octane_with_report_link(component_url=f"sandbox:{REPORT_PATH}")
        self.assertEqual(report_files(html), [{"message_id": "msg-a2", "path": REPORT_PATH}])

    def test_the_component_and_the_markdown_copy_of_one_link_are_one_file(self):
        html = octane_with_report_link(
            component_url=f"sandbox:{REPORT_PATH}", content_link=f"sandbox:{REPORT_PATH}"
        )
        self.assertEqual(report_files(html), [{"message_id": "msg-a2", "path": REPORT_PATH}])

    def test_the_markdown_link_is_the_fallback_with_its_escapes_undone(self):
        """Seen live: the Markdown copy ended ``.md\\``, and the resolver 404s that path."""
        html = octane_with_report_link(content_link="sandbox:/mnt/data/arattai\\_report.md\\")
        self.assertEqual(
            report_files(html), [{"message_id": "msg-a2", "path": "/mnt/data/arattai_report.md"}]
        )

    def test_links_that_are_not_a_markdown_report_in_the_sandbox_are_ignored(self):
        for url in (
            "https://example.org/report.md",
            "sandbox:/mnt/data/chart.png",
            "sandbox:/mnt/data/report.pdf",
            "sandbox:/etc/report.md",
            "sandbox:/mnt/data/../secrets/report.md",
        ):
            with self.subTest(url=url):
                self.assertEqual(report_files(octane_with_report_link(component_url=url)), [])

    def test_an_older_flight_page_links_it_from_the_reply_text(self):
        html = flight_with(f"Done.\n\n[Download the Markdown report](sandbox:{REPORT_PATH})")
        self.assertEqual(report_files(html), [{"message_id": MESSAGE_ID, "path": REPORT_PATH}])


# -- resolving and downloading ------------------------------------------------


class FakeGet:
    """``get(url, accept, max_bytes)`` answering from a script of responses."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.urls: list[str] = []

    async def __call__(self, url: str, accept: str, max_bytes: int):
        self.urls.append(url)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def resolved(status: str = "success", download_url: str = BLOB):
    return 200, "application/json", json.dumps({"status": status, "download_url": download_url})


MARKDOWN = (200, "text/markdown", "﻿" + REPORT)


class Sleeps:
    def __init__(self):
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)


def download(get: FakeGet, sleep: Sleeps | None = None) -> str | None:
    return run(download_report_file(get, RESOLVER, sleep or Sleeps()))


class ResolvingAndDownloading(unittest.TestCase):
    def test_the_resolver_url_names_the_share_the_reply_and_the_path(self):
        parts = urlsplit(RESOLVER)
        self.assertEqual(
            f"{parts.scheme}://{parts.netloc}{parts.path}",
            f"https://chatgpt.com/backend-anon/share/{SHARE_ID}/file_from_message/{MESSAGE_ID}",
        )
        self.assertEqual(parse_qs(parts.query), {"file_path": [REPORT_PATH]})

    def test_it_resolves_then_downloads_the_signed_url_at_once(self):
        get = FakeGet(resolved(), MARKDOWN)
        self.assertEqual(download(get), REPORT.strip())
        self.assertEqual(get.urls, [RESOLVER, BLOB])

    def test_while_the_resolver_says_retry_it_waits_and_asks_again(self):
        get, sleeps = FakeGet(resolved("retry"), resolved(), MARKDOWN), Sleeps()
        self.assertEqual(download(get, sleeps), REPORT.strip())
        self.assertEqual(sleeps.delays, [2.0])

    def test_it_gives_up_after_three_tries_inside_the_pages_fifteen_seconds(self):
        get, sleeps = FakeGet(resolved("retry"), resolved("retry"), resolved("retry")), Sleeps()
        self.assertIsNone(download(get, sleeps))
        self.assertEqual(len(get.urls), 3)
        self.assertLessEqual(sum(sleeps.delays), 15)

    def test_every_other_failure_is_no_report(self):
        cases = {
            "file path not found": [(404, "application/json", '{"detail": "File path not found"}')],
            "deleted share": [(404, "application/json", '{"detail": "shared_conversation_deleted"}')],
            "not success": [resolved("error")],
            "resolver not json": [(200, "text/html", "<html>")],
            "expired signature": [resolved(), (403, "application/xml", "<Code>AuthenticationFailed</Code>")],
            "an error page": [resolved(), (200, "text/html; charset=utf-8", "<html>Sorry</html>")],
            "a binary blob": [resolved(), (200, "application/octet-stream", "PK")],
            "an empty file": [resolved(), (200, "text/markdown", "  \n")],
            "transport failure": [OSError("connection reset")],
            "too large": [resolved(), OSError(f"over {REPORT_FILE_MAX_BYTES} bytes")],
        }
        for name, answers in cases.items():
            with self.subTest(name):
                self.assertIsNone(download(FakeGet(*answers)))

    def test_a_download_url_of_any_other_shape_is_never_followed(self):
        for url in (
            "https://evil.example/files/x/raw?sig=1",
            "http://sdmntprnznorth.oaiusercontent.com/files/x/raw?sig=1",
            "https://sdmntprnznorth.oaiusercontent.com/files/x/other?sig=1",
            "https://sdmntprnznorth.oaiusercontent.com.evil.example/files/x/raw?sig=1",
            None,
        ):
            with self.subTest(url=url):
                get = FakeGet(resolved(download_url=url))
                self.assertIsNone(download(get))
                self.assertEqual(get.urls, [RESOLVER])


class ThroughTheProxy(unittest.TestCase):
    def test_both_hops_go_through_the_proxy_with_its_token(self):
        calls: list[tuple[str, dict]] = []
        bodies = [("application/json", json.dumps({"status": "success", "download_url": BLOB})),
                  ("text/markdown", REPORT)]

        class Response:
            status = 200

            def __init__(self, content_type, body):
                self.headers, self._body = {"content-type": content_type}, body

            async def text(self):
                return self._body

        async def fetch(url, headers=None, redirect="follow"):
            calls.append((url, headers))
            return Response(*bodies.pop(0))

        fetcher = ProxyReportFetcher(fetch, "https://proxy.example/", "tok", sleep=Sleeps())
        self.assertEqual(run(fetcher.fetch(SHARE_ID, MESSAGE_ID, REPORT_PATH)), REPORT.strip())
        targets = [parse_qs(urlsplit(url).query)["u"][0] for url, _ in calls]
        self.assertEqual(targets, [RESOLVER, BLOB])
        self.assertTrue(all(url.startswith("https://proxy.example/?u=") for url, _ in calls))
        self.assertTrue(all(h["x-fetch-token"] == "tok" for _, h in calls))

    def test_a_proxy_refusal_is_no_report(self):
        class Refused:
            status = 502

        async def fetch(url, headers=None, redirect="follow"):
            return Refused()

        fetcher = ProxyReportFetcher(fetch, "https://proxy.example", "tok", sleep=Sleeps())
        self.assertIsNone(run(fetcher.fetch(SHARE_ID, MESSAGE_ID, REPORT_PATH)))


# -- the ingest use case ----------------------------------------------------


class PageFetcher:
    def __init__(self, html: str):
        self.html = html

    async def fetch(self, share_id: str) -> str:
        return self.html


class ReportFiles:
    """A ``ReportFileFetcher`` answering from ``{path: report or exception}``."""

    def __init__(self, files: dict):
        self.files = files
        self.asked: list[tuple[str, str, str]] = []

    async def fetch(self, share_id: str, message_id: str, path: str) -> str | None:
        self.asked.append((share_id, message_id, path))
        found = self.files.get(path)
        if isinstance(found, Exception):
            raise found
        return found


class NeverAnswers:
    """A ``ReportFileFetcher`` whose fetch never returns."""

    async def fetch(self, share_id: str, message_id: str, path: str) -> str | None:
        await asyncio.Event().wait()


def ingest(html: str, files, report_budget: float = 30.0):
    service = ShareService(
        PageFetcher(html), ChatGptShareDecoder(), ChatGptParser(), files, report_budget=report_budget
    )
    return run(service.ingest(f"https://chatgpt.com/share/{SHARE_ID}"))


class IngestingALinkedReport(unittest.TestCase):
    def test_an_octane_share_gets_its_report_and_citations_from_the_file(self):
        files = ReportFiles({REPORT_PATH: REPORT})
        conversation = ingest(octane_with_report_link(component_url=f"sandbox:{REPORT_PATH}"), files)
        self.assertEqual(files.asked, [(SHARE_ID, "msg-a2", REPORT_PATH)])
        self.assertEqual(conversation.report, REPORT.strip())
        self.assertEqual([c.index for c in conversation.citations], [1, 2])
        self.assertEqual(len(conversation.messages), 4)

    def test_a_report_that_cannot_be_downloaded_still_files_the_share(self):
        for found in (None, OSError("proxy down")):
            with self.subTest(found=found):
                conversation = ingest(
                    octane_with_report_link(component_url=f"sandbox:{REPORT_PATH}"),
                    ReportFiles({REPORT_PATH: found}),
                )
                self.assertIsNone(conversation.report)
                self.assertEqual(conversation.citations, [])
                self.assertEqual(len(conversation.messages), 4)

    def test_a_report_fetch_that_never_returns_still_files_the_share_in_its_budget(self):
        conversation = ingest(
            octane_with_report_link(component_url=f"sandbox:{REPORT_PATH}"), NeverAnswers(), report_budget=0.05
        )
        self.assertIsNone(conversation.report)
        self.assertEqual(conversation.citations, [])
        self.assertEqual(len(conversation.messages), 4)

    def test_a_page_with_no_link_downloads_nothing(self):
        files = ReportFiles({})
        self.assertIsNone(ingest(OCTANE_SHARE, files).report)
        self.assertEqual(files.asked, [])

    def test_a_page_that_carries_its_report_keeps_it_and_downloads_nothing(self):
        literal = "# The page's own report\n\n## Bibliography\n\n[1] A story.\nhttps://example.com/a\n"
        html = flight_with(
            f'report = r"""{literal}"""\n\n[Download the Markdown report](sandbox:{REPORT_PATH})'
        )
        files = ReportFiles({REPORT_PATH: REPORT})
        conversation = ingest(html, files)
        self.assertEqual(files.asked, [])
        self.assertEqual(conversation.report, literal.strip())

    def test_an_older_page_without_its_report_downloads_the_linked_file(self):
        html = flight_with(f"Done.\n\n[Download the Markdown report](sandbox:{REPORT_PATH})")
        conversation = ingest(html, ReportFiles({REPORT_PATH: REPORT}))
        self.assertEqual(conversation.report, REPORT.strip())

    def test_the_newest_linked_file_is_tried_first_and_an_older_one_after_it(self):
        data = copy.deepcopy(octane_data())
        for message in data["conversation"]["messages"]:
            if message["role"] == "assistant":
                message["content"] += f"\n\n[report](sandbox:/mnt/data/{message['id']}.md)"
        files = ReportFiles({"/mnt/data/msg-a1.md": REPORT})
        conversation = ingest(octane_page(data), files)
        self.assertEqual(
            [path for _, _, path in files.asked], ["/mnt/data/msg-a2.md", "/mnt/data/msg-a1.md"]
        )
        self.assertEqual(conversation.report, REPORT.strip())


# -- citations in a downloaded file -----------------------------------------


def citations(report: str) -> list[tuple[int, str, str]]:
    return [(c.index, c.title, c.url) for c in ChatGptParser().parse("x", {}, report_file=report).citations]


class CitationsInADownloadedReport(unittest.TestCase):
    def test_the_report_literal_form_still_reads(self):
        report = "# R\n\n## Bibliography\n\n[1] Reuters. A story. 1 October 2026.\nhttps://example.com/story\n"
        self.assertEqual(citations(report), [(1, "Reuters. A story. 1 October 2026.", "https://example.com/story")])

    def test_pandoc_style_entries_with_escaped_brackets_and_wrapped_titles(self):
        report = (
            "# R\n\nSaid so.\\[1\\]\n\n## Bibliography\n\n"
            '\\[1\\] Reuters. "US-India trade deal not imminent, Greer says." 1 October\n'
            "2026.\\\n"
            "https://www.reuters.com/world/a/\n\n"
            "\\[2\\] The Economic Times. \\*Talks\\* stall. October\n"
            "2026.\\\n"
            "<https://example.com/b>\n"
        )
        self.assertEqual(
            citations(report),
            [
                (1, 'Reuters. "US-India trade deal not imminent, Greer says." 1 October 2026.',
                 "https://www.reuters.com/world/a/"),
                (2, "The Economic Times. *Talks* stall. October 2026.", "https://example.com/b"),
            ],
        )

    def test_numbered_markdown_links_under_the_bibliography(self):
        self.assertEqual(
            citations(REPORT),
            [
                (1, 'Wisdomlib — "Sanskrit quote nr. 331"',
                 "https://www.wisdomlib.org/sanskrit/quote/mss/subhashita-331"),
                (2, "Abyasam", "https://abyasam.com/en/subhashitam/sub-angena-gatram-336"),
            ],
        )

    def test_pandoc_numbered_plain_entries_under_the_bibliography(self):
        # Share 6ac877f9's downloaded file: no brackets and no links, just a
        # numbered list whose wrapped title ends in a hard break before the URL.
        report = (
            "# R\n\n## Bibliography\n\n"
            '1.  **The White House.** "United States-India Joint Statement." February\n'
            "    6, 2026, as identified in the conversation.\\\n"
            "    https://www.whitehouse.gov/briefings-statements/2026/02/united-states-india-joint-statement/\n\n"
            "2.  **A book.** No link.\n"
            "3.  **Press Information Bureau, Government of India.** Joint Statement\n"
            "    page referenced in the conversation.\\\n"
            "    https://www.pib.gov.in/PressReleaseIframePage.aspx?PRID=2224783&lang=2&reg=48\n"
        )
        self.assertEqual(
            citations(report),
            [
                (1, '**The White House.** "United States-India Joint Statement." February 6, 2026, '
                    "as identified in the conversation.",
                 "https://www.whitehouse.gov/briefings-statements/2026/02/united-states-india-joint-statement/"),
                (3, "**Press Information Bureau, Government of India.** Joint Statement page referenced "
                    "in the conversation.",
                 "https://www.pib.gov.in/PressReleaseIframePage.aspx?PRID=2224783&lang=2&reg=48"),
            ],
        )

    def test_a_numbered_list_with_url_lines_outside_the_bibliography_is_not_its_sources(self):
        report = "# R\n\n## Steps\n\n1. Open the page.\n   https://example.com/a\n"
        self.assertEqual(citations(report), [])

    def test_a_link_with_parentheses_in_its_url_keeps_them(self):
        report = "# R\n\n## References\n\n1. [Tide](https://en.wikipedia.org/wiki/Tide_(sea))\n"
        self.assertEqual(citations(report), [(1, "Tide", "https://en.wikipedia.org/wiki/Tide_(sea)")])

    def test_a_numbered_list_of_links_outside_the_bibliography_is_not_its_sources(self):
        report = "# R\n\n## Reading list\n\n1. [A](https://example.com/a)\n2. [B](https://example.com/b)\n"
        self.assertEqual(citations(report), [])

    def test_an_entry_without_a_url_does_not_swallow_the_next_one(self):
        for escape in ("", "\\"):
            with self.subTest(escape=escape):
                o, c = f"{escape}[", f"{escape}]"
                report = (
                    f"{o}1{c} A book, no link.\n{o}2{c} AP. Another.\nhttps://ap.example/x\n"
                    f"- {o}3{c} Reuters.\nhttps://r.example/y\n- {o}4{c} No link either.\n{o}5{c} BBC.\nhttps://b.example/z\n"
                )
                self.assertEqual(
                    citations(report),
                    [
                        (2, "AP. Another.", "https://ap.example/x"),
                        (3, "Reuters.", "https://r.example/y"),
                        (5, "BBC.", "https://b.example/z"),
                    ],
                )

    def test_an_inline_marker_in_the_body_is_not_an_entry(self):
        report = "# R\n\nA claim. [1]\nhttps://example.com/not-an-entry\n"
        self.assertEqual(citations(report), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)

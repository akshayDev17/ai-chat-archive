"""Parser tests, offline.

These pin the one thing the live share pages proved fragile: the report is
emitted by a *model*, so its shape varies. The regression that motivated this
file is real and was invisible — a session where ChatGPT wrote

    content = r\"\"\"# …\"\"\"

instead of

    report = r\"\"\"# …\"\"\"

imported perfectly happily and simply arrived with no report and no citations,
because the parser matched on the variable name. Nothing errored. The only
symptom was an absence, which is why it went unnoticed until someone looked at
a specific story and asked why it had no markdown.

Run:  python3 backend/test/test_parser.py
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from chat_archive.chatgpt.parser import ChatGptParser  # noqa: E402

REPORT_BODY = """# A Headline

## Executive summary

- Something happened.[1]

## Bibliography

[1] Reuters. A story. 1 October 2026.
https://example.com/story
"""


def payload_with(assistant_text: str, *, title: str = "A conversation") -> dict:
    """A minimal ChatGPT-shaped conversation whose last turn is `assistant_text`."""
    return payload_with_refs(assistant_text, [])


def payload_with_refs(
    assistant_text: str,
    references: list[dict],
    *,
    title: str = "A conversation",
) -> dict:
    """As above, with `metadata.content_references` attached to the last turn.

    The references are built from the text *as given*, so an offset is always
    exactly right for whatever string is passed in — which is precisely the
    property that broke: ChatGPT's offsets are relative to its own text, and the
    parser was measuring them against a trimmed copy.
    """
    return {
        "title": title,
        "current_node": "n2",
        "mapping": {
            "n1": {
                "id": "n1",
                "parent": None,
                "message": {
                    "author": {"role": "user"},
                    "content": {"parts": ["Write me a report."]},
                },
            },
            "n2": {
                "id": "n2",
                "parent": "n1",
                "message": {
                    "author": {"role": "assistant"},
                    "content": {"parts": [assistant_text]},
                    "metadata": {"content_references": references},
                },
            },
        },
    }


CITE = "\ue200cite\ue202turn0search0\ue201"


def cite_ref(text: str, token: str, *, title="A Source", url="https://example.com/a") -> dict:
    """A `grouped_webpages` reference positioned at `token` inside `text`."""
    start = text.index(token)
    return {
        "type": "grouped_webpages",
        "matched_text": token,
        "start_idx": start,
        "end_idx": start + len(token),
        "safe_urls": [url],
        "items": [{"title": title, "url": url, "attribution": "Example"}],
    }


class TestReportExtraction(unittest.TestCase):
    def setUp(self):
        self.parser = ChatGptParser()

    def test_finds_the_report_under_its_expected_name(self):
        raw = payload_with(f'report = r"""{REPORT_BODY}"""')
        conv = self.parser.parse("share-x", raw)
        self.assertIsNotNone(conv.report)
        self.assertTrue(conv.report.startswith("# A Headline"))
        self.assertEqual(len(conv.citations), 1)

    def test_finds_the_report_under_a_different_name(self):
        """The actual regression: `content`, not `report`."""
        raw = payload_with(f'content = r"""{REPORT_BODY}"""')
        conv = self.parser.parse("share-x", raw)
        self.assertIsNotNone(conv.report, "the variable name must not decide this")
        self.assertTrue(conv.report.startswith("# A Headline"))
        self.assertEqual(len(conv.citations), 1)

    def test_accepts_a_wrapped_skill_style_script(self):
        """The shape the payload actually had: imports and code around it."""
        script = (
            "import pypandoc, os, textwrap\n\n"
            f'content = r"""{REPORT_BODY}"""\n\n'
            'out = "report.pdf"\n'
            'doc.convert_text(content, "md", format="md", outputfile=out)\n'
        )
        conv = self.parser.parse("share-x", payload_with(script))
        self.assertIsNotNone(conv.report)
        self.assertTrue(conv.report.startswith("# A Headline"))

    def test_prefers_the_longest_markdown_candidate(self):
        raw = payload_with(
            'draft = r"""# Short draft\n\nA line."""\n\n'
            f'final = r"""{REPORT_BODY}"""'
        )
        conv = self.parser.parse("share-x", raw)
        self.assertIn("Executive summary", conv.report)

    def test_a_shorter_non_markdown_snippet_does_not_win(self):
        """A code snippet in the reply must not be mistaken for the report."""
        raw = payload_with(
            'SQL = r"""SELECT * FROM conversations WHERE id = ? ORDER BY seq"""\n\n'
            f'content = r"""{REPORT_BODY}"""'
        )
        conv = self.parser.parse("share-x", raw)
        self.assertTrue(conv.report.startswith("# A Headline"))

    def test_no_report_is_none_not_an_empty_string(self):
        """Absence must be distinguishable from an empty report.

        The ingest response reports `report_length`, and a caller that cannot
        tell "no report was found" from "the report was empty" cannot warn
        anyone — which is exactly how the original bug stayed hidden.
        """
        conv = self.parser.parse("share-x", payload_with("Sure, here is the answer."))
        self.assertIsNone(conv.report)
        self.assertEqual(conv.citations, [])

    def test_transcript_is_unaffected(self):
        conv = self.parser.parse("share-x", payload_with(f'report = r"""{REPORT_BODY}"""'))
        self.assertEqual([m.role for m in conv.messages], ["user", "assistant"])
        self.assertEqual(conv.title, "A conversation")


class TestSourceSpans(unittest.TestCase):
    """Citation offsets must survive the trim applied for display."""

    def setUp(self):
        self.parser = ChatGptParser()

    def _spans(self, text: str, *, pad: str = ""):
        body = f"{pad}{text}"
        raw = payload_with_refs(body, [cite_ref(body, CITE)])
        message = self.parser.parse("share-x", raw).messages[-1]
        return message, [span for s in message.sources for span in s.spans]

    def test_a_plain_message_gets_the_right_span(self):
        message, spans = self._spans(f"Body.{CITE}")
        self.assertEqual(message.sources[0].spans, ((5, 5 + len(CITE)),))
        self.assertEqual(message.content[spans[0][0]:spans[0][1]], CITE)

    def test_leading_whitespace_does_not_shift_the_span(self):
        """The bug: offsets are relative to ChatGPT's untrimmed text.

        `_render_parts` stripped the message for display, so with a leading
        newline every span landed a character early — replacing the wrong text
        and leaving the tail of the token behind as literal "turn0search0".
        """
        message, spans = self._spans(f"Body.{CITE}", pad="\n\n   ")
        self.assertEqual(message.content, f"Body.{CITE}", "display text is still trimmed")
        self.assertEqual(len(spans), 1, "the span must still be found after the shift")
        start, end = spans[0]
        self.assertEqual(message.content[start:end], CITE, "the span must cover the token")

    def test_a_source_cited_twice_records_both_spans(self):
        body = f"First{CITE} then{CITE}."
        raw = payload_with_refs(
            body,
            [
                cite_ref(body, CITE),
                {
                    "type": "grouped_webpages",
                    "matched_text": CITE,
                    "start_idx": body.rindex(CITE),
                    "end_idx": body.rindex(CITE) + len(CITE),
                    "safe_urls": ["https://example.com/a"],
                    "items": [{"title": "A Source", "url": "https://example.com/a"}],
                },
            ],
        )
        message = self.parser.parse("share-x", raw).messages[-1]
        self.assertEqual(len(message.sources), 1, "one url, one entry")
        self.assertEqual(len(message.sources[0].spans), 2, "but every marker is recorded")

    def test_non_source_reference_types_are_ignored(self):
        body = f"Body.{CITE}"
        raw = payload_with_refs(
            body,
            [
                cite_ref(body, CITE),
                {"type": "sources_footnote", "matched_text": " ", "start_idx": len(body), "end_idx": len(body)},
                {"type": "followup_a", "matched_text": "Summarize this", "start_idx": 0, "end_idx": 5},
            ],
        )
        message = self.parser.parse("share-x", raw).messages[-1]
        self.assertEqual([s.title for s in message.sources], ["A Source"])

    def test_an_untrusted_offset_yields_no_span_but_keeps_the_source(self):
        body = f"Body.{CITE}"
        ref = cite_ref(body, CITE)
        ref["start_idx"] = 0  # points at "Body", not the token
        ref["end_idx"] = 4
        raw = payload_with_refs(body, [ref])

        message = self.parser.parse("share-x", raw).messages[-1]
        self.assertEqual(len(message.sources), 1, "still cited, so still listed")
        self.assertEqual(message.sources[0].spans, (), "but with no span to splice at")

    def test_the_chatgpt_tracking_parameter_is_removed(self):
        body = f"Body.{CITE}"
        ref = cite_ref(body, CITE, url="https://example.com/a?utm_source=chatgpt.com")
        ref["safe_urls"] = [
            "https://example.com/a?utm_source=chatgpt.com",
            "https://example.com/a",
        ]
        message = self.parser.parse("share-x", payload_with_refs(body, [ref])).messages[-1]
        self.assertEqual(message.sources[0].url, "https://example.com/a")

    def test_a_url_reference_keeps_its_written_label(self):
        token = "\ue200url\ue202Read the statement\ue202turn0search0\ue201"
        body = f"See {token} here."
        ref = {
            "type": "url",
            "matched_text": token,
            "start_idx": body.index(token),
            "end_idx": body.index(token) + len(token),
            "safe_urls": ["https://example.com/a"],
            "items": [],
        }
        message = self.parser.parse("share-x", payload_with_refs(body, [ref])).messages[-1]
        self.assertEqual(message.sources[0].kind, "link")
        self.assertEqual(message.sources[0].title, "Read the statement")


if __name__ == "__main__":
    unittest.main(verbosity=2)

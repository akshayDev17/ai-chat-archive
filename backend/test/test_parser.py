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
    """A minimal ChatGPT-shaped conversation whose last turn is `assistant_text`.

    Shaped like the real decoded payload — a `mapping` of nodes and a
    `current_node` to walk back from — so the parser is exercised on its real
    input, not on a shortcut.
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
                },
            },
        },
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


if __name__ == "__main__":
    unittest.main(verbosity=2)

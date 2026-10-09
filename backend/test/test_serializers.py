"""Tests for the wire serializer, offline.

The one that matters is `_utf16_offsets`. The payload's citation offsets are
**code points** and JavaScript indexes strings in **UTF-16 code units**, so a
reply containing emoji needs a conversion at exactly this boundary. Getting it
wrong does not raise — it silently replaces the wrong characters and leaves the
tail of the citation token behind as literal prose ("turn0news10"), which is how
a reply with twelve emoji came out with one broken citation.

Run:  python3 backend/test/test_serializers.py
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from chat_archive.models import Conversation, Message, Source  # noqa: E402
from chat_archive.serializers import conversation_detail  # noqa: E402

FLAG = "\U0001f1fa\U0001f1f8"  # 🇺🇸 — one code point pair, four UTF-16 units
TOKEN = "\ue200cite\ue202turn0search0\ue201"


class TestUtf16Offsets(unittest.TestCase):
    def _spans(self, text: str, spans):
        message = Message(
            role="assistant",
            content=text,
            sources=[
                Source(index=1, title="S", url="https://e.com", spans=spans),
            ],
        )
        detail = conversation_detail(
            Conversation(share_id="x", title="t", messages=[message])
        )
        return detail["messages"][0]["sources"][0]["spans"]

    def test_no_astral_characters_leaves_offsets_alone(self):
        text = f"Body.{TOKEN}"
        self.assertEqual(self._spans(text, ((5, 5 + len(TOKEN)),)), [[5, 5 + len(TOKEN)]])

    def test_an_emoji_before_the_span_shifts_it_by_one_unit(self):
        # 🇺🇸 is two code units in UTF-16 but one code point, so everything after
        # it must move by one for a JavaScript `slice` to land correctly.
        text = f"{FLAG} Body.{TOKEN}"
        start = text.index(TOKEN)
        end = start + len(TOKEN)
        self.assertEqual(self._spans(text, ((start, end),)), [[start + 2, end + 2]])

    def test_several_emoji_accumulate(self):
        # Three flag pairs are six code points, each worth two UTF-16 units, so
        # anything after them moves by six.
        text = f"{FLAG}{FLAG}{FLAG} Body.{TOKEN}"
        start = text.index(TOKEN)
        self.assertEqual(
            self._spans(text, ((start, start + len(TOKEN)),)),
            [[start + 6, start + len(TOKEN) + 6]],
        )

    def test_the_converted_span_slices_the_token_in_javascript_terms(self):
        """The assertion that would have caught the bug, stated as JS sees it."""
        text = f"{FLAG} Body.{TOKEN}"
        start = text.index(TOKEN)
        [[js_start, js_end]] = self._spans(text, ((start, start + len(TOKEN)),))

        # Re-encode as UTF-16LE, which is exactly what a JS string indexes.
        units = text.encode("utf-16-le")
        sliced = units[js_start * 2 : js_end * 2].decode("utf-16-le")
        self.assertEqual(sliced, TOKEN)

    def test_a_span_after_the_emoji_only_counts_emoji_before_it(self):
        text = f"Body.{TOKEN} {FLAG} more{TOKEN}"
        first = text.index(TOKEN)
        second = text.rindex(TOKEN)
        spans = self._spans(text, ((first, first + len(TOKEN)), (second, second + len(TOKEN))))
        # The first is before any emoji, so unchanged; the second is after one.
        self.assertEqual(spans[0], [first, first + len(TOKEN)])
        self.assertEqual(spans[1], [second + 2, second + len(TOKEN) + 2])


class TestDetailShape(unittest.TestCase):
    def test_messages_carry_their_sources(self):
        detail = conversation_detail(
            Conversation(
                share_id="x",
                title="t",
                messages=[
                    Message(role="user", content="hi"),
                    Message(
                        role="assistant",
                        content="there",
                        sources=[Source(index=1, title="S", url="https://e.com/a")],
                    ),
                ],
            )
        )
        self.assertEqual(detail["messages"][0]["sources"], [])
        self.assertEqual(detail["messages"][1]["sources"][0]["url"], "https://e.com/a")
        self.assertEqual(detail["messages"][1]["sources"][0]["spans"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)

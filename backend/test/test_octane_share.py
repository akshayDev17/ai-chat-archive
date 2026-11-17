"""Share pages from chatgpt.com's octane app import again — offline.

On 2026-11-17 every real share filed through the archive failed with
"No flight payload found in page": chatgpt.com had moved most shares to its
octane web app, whose pages carry the conversation as JSON in
``<script id="__WEB_MOBILE_DATA__">`` and no ``streamController.enqueue`` call
at all. Deleted or unknown shares, and some real ones, still come from the
older React Router app, so both formats are decoded.

The octane fixture (``fixtures/octane_share.html``, see ``share_pages.py``)
keeps the real page's structure with synthetic content. What it pins:

  * a plain reply's citations: the page renders each token as a
    ``([Publisher](url))`` link and records where — in UTF-16 units — so the
    decoder swaps the tokens back and the parser's spans land on them, flag
    emoji and all;
  * a GenUI reply: shipped as a compiled DIL program, written back as the
    markup the reader renders, with its sources and images at their tags;
  * the older flight format still decodes, from the same decoder.

Run:  python3 backend/test/test_octane_share.py
"""

from __future__ import annotations

import asyncio
import json
import sys
import unittest
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "test"))

from chat_archive.chatgpt.decoder import ChatGptShareDecoder, MissingFlightPayload  # noqa: E402
from chat_archive.chatgpt.dil import UnsupportedDil, render_dil  # noqa: E402
from chat_archive.chatgpt.parser import ChatGptParser  # noqa: E402
from chat_archive.chatgpt.web_mobile import original_text  # noqa: E402
from chat_archive.service import ShareService  # noqa: E402
from share_pages import OCTANE_SHARE, flight_share, octane_page  # noqa: E402

SHARE_ID = "00000000-0000-4000-8000-0000000000f1"

# The plain reply as ChatGPT wrote it, with its citation tokens.
PLAIN_REPLY = (
    "Tides follow the Moon's pull. citeturn0search0\n\n"
    "The \U0001F30A bulge on the far side \U0001F1FA\U0001F1F8 matters too. "
    "citeturn0news1turn0search2\n\n"
    "Read more in urlthe tide primerhttps://example.org/tides.\n\n"
    "If you want, I can also draw the Earth–Moon geometry."
)

# The GenUI reply, written back from its DIL program.
GENUI_REPLY = """## How the tide moves

The **spring tide** comes after a full moon. <Cite refs={["turn1search0"]}/>

| Tide | Height |
|---|---:|
| Spring | 2.1 m |
| Neap | 0.9 m |

<AsyncImageGroup query={["harbour at high tide","harbour at low tide"]} aspectRatio="5:4" layout="bento" size="lg"/>

<box gap={3}>
  <row align="start" gap={3}>
    <AsyncImage query="harbour wall" aspectRatio="3:4" maxWidth="92px"/>
    <box flex="1" gap={1}>
      **Harbour**
      <text color="secondary" size="sm">Tide tables from <Link url="https://example.org/tables" title="the port office"/></text>
    </box>
  </row>
  <divider color="subtle"/>
</box>

- Gravity pulls water toward the <Entity value="Moon" category="celestial_body" disambig="Earth's natural satellite"/>.

Read the [harbour guide](https://example.org/harbour)

---

If you want, I can:

- Summarize this conversation"""


def web_mobile_data(html: str = OCTANE_SHARE) -> dict:
    start = html.index('id="__WEB_MOBILE_DATA__"')
    body = html[html.index(">", start) + 1 : html.index("</script>", start)]
    return json.loads(body)


def parse(html: str = OCTANE_SHARE):
    return ChatGptParser().parse(SHARE_ID, ChatGptShareDecoder().decode(html))


class OctaneShareImports(unittest.TestCase):
    def test_title_and_turns(self):
        conversation = parse()
        self.assertEqual(conversation.title, "Ocean tides explained")
        self.assertEqual(
            [(m.role, m.external_id) for m in conversation.messages],
            [("user", "msg-u1"), ("assistant", "msg-a1"), ("user", "msg-u2"), ("assistant", "msg-a2")],
        )
        self.assertEqual(conversation.messages[0].content, "Why are there two tides a day?")

    def test_the_decoder_reads_the_web_mobile_data_and_not_the_other_json_scripts(self):
        self.assertIn("data-octane-native-signals", OCTANE_SHARE)
        self.assertIn('id="octane-conversation-transcript"', OCTANE_SHARE)
        self.assertNotIn("streamController.enqueue", OCTANE_SHARE)
        raw = ChatGptShareDecoder().decode(OCTANE_SHARE)
        self.assertEqual(raw["title"], "Ocean tides explained")
        self.assertEqual(len(raw["mapping"]), 4)


class PlainReplyCitations(unittest.TestCase):
    def setUp(self):
        self.message = parse().messages[1]

    def test_the_rendered_links_are_swapped_back_for_their_tokens(self):
        self.assertEqual(self.message.content, PLAIN_REPLY)

    def test_each_citation_is_a_source_spanning_its_token(self):
        sources = self.message.sources
        self.assertEqual(
            [(s.index, s.kind, s.url, s.title, s.attribution) for s in sources],
            [
                (1, "cite", "https://example.com/moon", "Why the Moon makes tides", "Ocean Facts"),
                (2, "cite", "https://example.net/far-side", "The far-side bulge", "Sea Science"),
                (3, "link", "https://example.org/tides", "the tide primer", ""),
            ],
        )
        for source, token in zip(
            sources,
            [
                "citeturn0search0",
                "citeturn0news1turn0search2",
                "urlthe tide primerhttps://example.org/tides",
            ],
        ):
            (start, end), = source.spans
            self.assertEqual(self.message.content[start:end], token)

    def test_rendered_offsets_are_utf16_so_emoji_before_a_citation_do_not_shift_it(self):
        content = "Sea \U0001F30A\U0001F1FA\U0001F1F8 rises. ([A](https://a.example))"
        rendered = "([A](https://a.example))"
        start16 = len(content[: content.index(rendered)].encode("utf-16-le")) // 2
        reference = {
            "startIndex": start16,
            "endIndex": start16 + len(rendered),
            "data": {"matched_text": "citet0"},
        }
        self.assertEqual(original_text(content, [reference]), "Sea \U0001F30A\U0001F1FA\U0001F1F8 rises. citet0")

    def test_a_reference_off_a_character_boundary_or_out_of_range_is_left_as_rendered(self):
        content = "Sea \U0001F30A ([A](https://a.example))"
        for start, end in [(5, 9), (0, 999), (9, 9)]:
            with self.subTest(span=(start, end)):
                reference = {"startIndex": start, "endIndex": end, "data": {"matched_text": "T"}}
                self.assertEqual(original_text(content, [reference]), content)


class GenUIReply(unittest.TestCase):
    def setUp(self):
        self.message = parse().messages[3]

    def test_the_dil_program_is_written_back_as_markup(self):
        self.assertEqual(self.message.content, GENUI_REPLY)

    def test_a_cite_component_becomes_a_source_spanning_its_tag(self):
        (source,) = self.message.sources
        self.assertEqual(
            (source.kind, source.url, source.title, source.attribution, source.pub_date),
            ("cite", "https://example.com/spring", "Spring and neap tides", "Tide Weekly", 1791072000),
        )
        (start, end), = source.spans
        self.assertEqual(self.message.content[start:end], '<Cite refs={["turn1search0"]}/>')

    def test_image_components_become_media_at_their_tags(self):
        content = self.message.content
        group_at = content.index("<AsyncImageGroup")
        image_at = content.index("<AsyncImage query=")
        self.assertEqual(
            [(m.tag_start, m.ordinal, m.content_url) for m in self.message.media],
            [
                (group_at, 0, "https://images.openai.com/static-rsc-4/synthetic-1?purpose=fullsize"),
                (group_at, 1, "https://images.openai.com/static-rsc-4/synthetic-2?purpose=fullsize"),
                (image_at, 0, "https://images.openai.com/static-rsc-4/synthetic-3?purpose=fullsize"),
            ],
        )

    def test_an_unreadable_program_keeps_the_pages_own_markdown(self):
        data = web_mobile_data()
        reply = data["conversation"]["messages"][3]
        reply["source"]["dil"]["code"] = reply["source"]["dil"]["code"].replace(
            '__dil.jsx("title"', 'items.map((x)=>__dil.jsx("title"', 1
        )
        with self.assertRaises(UnsupportedDil):
            render_dil(reply["source"]["dil"])
        message = parse(octane_page(data)).messages[3]
        self.assertEqual(message.content, reply["content"])
        self.assertEqual((message.sources, message.media), ([], []))

    def test_function_props_are_dropped_and_never_run(self):
        self.assertNotIn("onClick", self.message.content)
        self.assertNotIn("issueNewTurn", self.message.content)


class BothFormats(unittest.TestCase):
    def test_a_flight_page_still_decodes(self):
        conversation = {
            "title": "An older share",
            "current_node": "n2",
            "mapping": {
                "n1": {"id": "n1", "parent": None, "message": {"author": {"role": "user"}, "content": {"parts": ["Hi?"]}}},
                "n2": {"id": "n2", "parent": "n1", "message": {"author": {"role": "assistant"}, "content": {"parts": ["Hello."]}}},
            },
        }
        decoded = parse(flight_share(conversation))
        self.assertEqual(decoded.title, "An older share")
        self.assertEqual([m.content for m in decoded.messages], ["Hi?", "Hello."])

    def test_a_page_with_neither_is_still_the_missing_payload_error(self):
        with self.assertRaises(MissingFlightPayload):
            ChatGptShareDecoder().decode("<html><head><title>ChatGPT</title></head><body></body></html>")

    def test_the_ingest_use_case_files_an_octane_share(self):
        class Fetcher:
            async def fetch(self, share_id: str) -> str:
                return OCTANE_SHARE

        service = ShareService(Fetcher(), ChatGptShareDecoder(), ChatGptParser())
        conversation = asyncio.run(service.ingest(f"https://chatgpt.com/share/{SHARE_ID}"))
        self.assertEqual(conversation.share_id, SHARE_ID)
        self.assertEqual(len(conversation.messages), 4)


if __name__ == "__main__":
    unittest.main(verbosity=2)

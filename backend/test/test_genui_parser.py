"""GenUI parser tests, offline.

ChatGPT now writes some replies as component markup — `<box>`, `<row>`,
`<Cite refs={…}/>`, `<AsyncImage …/>` — and resolves the data-bound components
in metadata the parser used to ignore:

  metadata.genui_components[]                      where each component sits
  metadata.model_dil_v2.appData.opGenui
          .componentResults[<resolution id>]       what it resolved to

Those replies were stored with zero sources, because their
`content_references` hold none of the types `_extract_sources` reads. These
tests pin the recovery: a `<Cite/>` becomes sources spanning the tag, and an
image component becomes pending media rows for the copier.

The payloads are synthetic but shaped exactly like the live share
`6adf3276-41cc-83e8-8bb7-669b6f0a4748` (decoded 2026-10-26).

Run:  python3 backend/test/test_genui_parser.py
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from chat_archive.chatgpt.parser import ChatGptParser  # noqa: E402
from chat_archive.models import Media  # noqa: E402

CITE_TAG = '<Cite refs={["t0s0"]}/>'
IMAGE_TAG = '<AsyncImage query="Starlink dish" aspectRatio="3:4" maxWidth="104px"/>'
GROUP_TAG = '<AsyncImageGroup query={["a","b","c"]} aspectRatio="5:4" layout="bento"/>'


def genui_payload(text: str, components: list[dict], results: dict) -> dict:
    """A conversation whose last turn is GenUI `text` with its resolutions attached."""
    return {
        "title": "A GenUI conversation",
        "current_node": "n2",
        "mapping": {
            "n1": {
                "id": "n1",
                "parent": None,
                "message": {"author": {"role": "user"}, "content": {"parts": ["Explain."]}},
            },
            "n2": {
                "id": "n2",
                "parent": "n1",
                "message": {
                    "author": {"role": "assistant"},
                    "content": {"parts": [text]},
                    "metadata": {
                        "content_references": [
                            {"type": "sources_footnote", "matched_text": " ", "start_idx": 0, "end_idx": 0}
                        ],
                        "genui_components": components,
                        "model_dil_v2": {"appData": {"opGenui": {"componentResults": results}}},
                    },
                },
            },
        },
    }


def item(url: str, title: str = "A story", label: str = "A News", ts: int | None = 1714003200) -> dict:
    return {"url": url, "title": title, "source_label": label, "pub_timestamp": ts, "ref_id": "t0s0"}


def cite_at(text: str, tag: str, rid: str, items: list[dict], *, occurrence: int = 0) -> tuple[dict, dict]:
    """A `cite` component placed on the `occurrence`-th `tag` in `text`, and its result."""
    start = -1
    for _ in range(occurrence + 1):
        start = text.index(tag, start + 1)
    component = {
        "type": "cite",
        "start_index": start,
        "end_index": start + len(tag),
        "component_resolution_id": rid,
    }
    return component, {rid: {"status": "resolved", "componentName": "Cite", "state": {"items": items}}}


def images_at(text: str, tag: str, rid: str, kind: str, images: list[dict]) -> tuple[dict, dict]:
    start = text.index(tag)
    component = {
        "type": kind,
        "start_index": start,
        "end_index": start + len(tag),
        "component_resolution_id": rid,
    }
    name = "AsyncImage" if kind == "async_image" else "AsyncImageGroup"
    return component, {rid: {"status": "resolved", "componentName": name, "state": {"images": images}}}


def image(n: int) -> dict:
    return {
        "url": f"https://example.com/page-{n}",
        "content_url": f"https://images.openai.com/static-rsc-4/img{n}?purpose=fullsize",
        "thumbnail_url": f"https://images.openai.com/static-rsc-4/thumb{n}",
    }


def last_message(payload: dict):
    return ChatGptParser().parse("share-id", payload).messages[-1]


class CiteBecomesSources(unittest.TestCase):
    def test_a_cite_tag_becomes_sources_spanning_the_tag(self):
        text = f"Fact. {CITE_TAG}"
        component, result = cite_at(
            text, CITE_TAG, "r1", [item("https://a.com/x?utm_source=chatgpt.com", "A", "A News")]
        )
        message = last_message(genui_payload(text, [component], result))

        self.assertEqual(len(message.sources), 1)
        source = message.sources[0]
        self.assertEqual(source.index, 1)
        self.assertEqual(source.kind, "cite")
        self.assertEqual(source.url, "https://a.com/x")
        self.assertEqual(source.title, "A")
        self.assertEqual(source.attribution, "A News")
        self.assertEqual(source.pub_date, 1714003200)
        self.assertEqual(source.spans, ((6, len(text)),))

    def test_a_multi_item_cite_gives_each_item_the_same_span(self):
        text = f"Fact. {CITE_TAG}"
        component, result = cite_at(
            text,
            CITE_TAG,
            "r1",
            [item("https://a.com/1"), item("https://b.com/2"), item("https://c.com/3")],
        )
        message = last_message(genui_payload(text, [component], result))

        self.assertEqual([s.index for s in message.sources], [1, 2, 3])
        self.assertEqual({s.spans for s in message.sources}, {((6, len(text)),)})

    def test_a_url_cited_twice_keeps_one_number_and_both_spans(self):
        text = f"One. {CITE_TAG} Two. {CITE_TAG}"
        first, r1 = cite_at(text, CITE_TAG, "r1", [item("https://a.com/x")])
        second, r2 = cite_at(text, CITE_TAG, "r2", [item("https://a.com/x")], occurrence=1)
        message = last_message(genui_payload(text, [first, second], {**r1, **r2}))

        self.assertEqual(len(message.sources), 1)
        self.assertEqual(len(message.sources[0].spans), 2)

    def test_a_cite_span_that_does_not_land_on_a_cite_tag_is_dropped(self):
        text = f"Fact. {CITE_TAG}"
        component, result = cite_at(text, CITE_TAG, "r1", [item("https://a.com/x")])
        component["start_index"], component["end_index"] = 0, 4  # "Fact"
        message = last_message(genui_payload(text, [component], result))

        self.assertEqual(len(message.sources), 1, "the source is still listed")
        self.assertEqual(message.sources[0].spans, (), "but its untrustworthy span is not")

    def test_leading_whitespace_shifts_genui_offsets(self):
        text = f"\n\nFact. {CITE_TAG}"
        component, result = cite_at(text, CITE_TAG, "r1", [item("https://a.com/x")])
        message = last_message(genui_payload(text, [component], result))

        stored = message.content
        (start, end), = message.sources[0].spans
        self.assertEqual(stored[start:end], CITE_TAG)


class ImagesBecomeMedia(unittest.TestCase):
    def test_async_image_becomes_one_pending_media_row(self):
        text = f"France\n\n{IMAGE_TAG}"
        component, result = images_at(text, IMAGE_TAG, "r1", "async_image", [image(1)])
        message = last_message(genui_payload(text, [component], result))

        self.assertEqual(
            message.media,
            [
                Media(
                    tag_start=text.index(IMAGE_TAG),
                    ordinal=0,
                    content_url=image(1)["content_url"],
                    origin_url=image(1)["url"],
                )
            ],
        )
        self.assertEqual(message.media[0].status, "pending")

    def test_media_offsets_follow_the_leading_trim(self):
        text = f"\n  {IMAGE_TAG}"
        component, result = images_at(text, IMAGE_TAG, "r1", "async_image", [image(1)])
        message = last_message(genui_payload(text, [component], result))

        start = message.media[0].tag_start
        self.assertTrue(message.content[start:].startswith("<AsyncImage"))

    def test_async_image_group_keeps_image_order(self):
        text = f"Look:\n\n{GROUP_TAG}"
        component, result = images_at(
            text, GROUP_TAG, "r1", "async_image_group", [image(1), image(2), image(3)]
        )
        message = last_message(genui_payload(text, [component], result))

        self.assertEqual([m.ordinal for m in message.media], [0, 1, 2])
        self.assertEqual({m.tag_start for m in message.media}, {text.index(GROUP_TAG)})
        self.assertEqual(
            [m.content_url for m in message.media], [image(n)["content_url"] for n in (1, 2, 3)]
        )

    def test_an_unresolved_image_component_yields_no_media(self):
        text = f"Look:\n\n{IMAGE_TAG}"
        component, _ = images_at(text, IMAGE_TAG, "r1", "async_image", [image(1)])
        message = last_message(genui_payload(text, [component], {}))

        self.assertEqual(message.media, [])


class PlainRepliesAreUnchanged(unittest.TestCase):
    def test_a_message_without_genui_has_no_media_and_unchanged_sources(self):
        token = "citeturn0search0"
        text = f"A claim. {token}"
        start = text.index(token)
        payload = genui_payload(text, [], {})
        metadata = payload["mapping"]["n2"]["message"]["metadata"]
        del metadata["genui_components"], metadata["model_dil_v2"]
        metadata["content_references"] = [
            {
                "type": "grouped_webpages",
                "matched_text": token,
                "start_idx": start,
                "end_idx": start + len(token),
                "items": [{"title": "A Source", "url": "https://example.com/a"}],
            }
        ]
        message = last_message(payload)

        self.assertEqual(message.media, [])
        self.assertEqual([s.url for s in message.sources], ["https://example.com/a"])
        self.assertEqual(message.sources[0].spans, ((start, start + len(token)),))


class MergedFragmentsAreNotRepeated(unittest.TestCase):
    """A streamed reply is stored twice: as fragments, and as the merged message.

    The fragment carries ``is_message_fragment`` and ``merged_message_id`` (and is
    ``is_visually_hidden_from_conversation`` — ChatGPT never shows it); the merged
    message carries the GenUI resolutions. Keeping both printed every GenUI
    reply twice, the first copy with no images and no citations.
    """

    def test_a_merged_fragment_is_skipped_in_favour_of_the_merged_message(self):
        text = f"Fact. {CITE_TAG}"
        component, result = cite_at(text, CITE_TAG, "r1", [item("https://a.com/x")])
        payload = genui_payload(text, [component], result)
        mapping = payload["mapping"]
        # Insert the fragment between the user turn and the merged reply.
        mapping["frag"] = {
            "id": "frag",
            "parent": "n1",
            "message": {
                "id": "frag",
                "author": {"role": "assistant"},
                "content": {"parts": [text]},
                "metadata": {
                    "is_message_fragment": True,
                    "merged_message_id": "n2",
                    "is_visually_hidden_from_conversation": True,
                },
            },
        }
        mapping["n2"]["parent"] = "frag"

        messages = ChatGptParser().parse("share-id", payload).messages

        self.assertEqual([m.role for m in messages], ["user", "assistant"])
        self.assertEqual(len(messages[1].sources), 1, "the kept copy is the resolved one")

    def test_an_unmerged_fragment_is_kept(self):
        payload = genui_payload("Only copy.", [], {})
        payload["mapping"]["n2"]["message"]["metadata"]["is_message_fragment"] = True
        messages = ChatGptParser().parse("share-id", payload).messages
        self.assertEqual([m.content for m in messages][-1], "Only copy.")


if __name__ == "__main__":
    unittest.main(verbosity=2)

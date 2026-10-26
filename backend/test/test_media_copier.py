"""Media copier tests, offline.

The copier is the body of the once-a-minute Cron Trigger: it claims a batch of
pending image rows, fetches each image, stores it under a content-addressed key
and marks the rows copied — or counts a failed attempt. These tests run it
against the in-memory repository and store, with a fake fetcher.

Run:  python3 backend/test/test_media_copier.py
"""

from __future__ import annotations

import asyncio
import hashlib
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from chat_archive.chatgpt.fetcher import FetchError  # noqa: E402
from chat_archive.chatgpt.proxy_fetcher import ProxyImageFetcher  # noqa: E402
from chat_archive.local_media_store import DirectoryMediaStore, MemoryMediaStore  # noqa: E402
from chat_archive.local_repository import MemoryConversationRepository  # noqa: E402
from chat_archive.media_copier import MAX_BYTES, copy_pending, media_key, retract_story  # noqa: E402
from chat_archive.models import Conversation, Media, Message  # noqa: E402
from chat_archive.r2_media_store import R2MediaStore  # noqa: E402

OWNER = "akshay@example.com"
NOW = datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc)
JPEG = b"\xff\xd8\xff fake jpeg"


def img(n: int) -> str:
    return f"https://images.openai.com/static-rsc-4/img{n}?purpose=fullsize"


def story(share_id: str, urls: list[str], owner: str = OWNER) -> Conversation:
    return Conversation(
        share_id=share_id,
        title=share_id,
        messages=[
            Message(role="user", content="q"),
            Message(
                role="assistant",
                content="a",
                media=[Media(tag_start=i, ordinal=0, content_url=u) for i, u in enumerate(urls)],
                external_id="msg-1",
            ),
        ],
    ).owned_by(owner)


class FakeFetcher:
    """Returns JPEG bytes, or raises for URLs listed in `failing`."""

    def __init__(self, failing=(), content_type="image/jpeg", body=JPEG):
        self.failing = set(failing)
        self.content_type = content_type
        self.body = body
        self.calls: list[str] = []

    async def fetch(self, url: str) -> tuple[bytes, str]:
        self.calls.append(url)
        if url in self.failing:
            raise FetchError("proxy fetch failed: HTTP 502")
        return self.body, self.content_type


class CopierTests(unittest.TestCase):
    def setUp(self):
        self.repo = MemoryConversationRepository()
        self.store = MemoryMediaStore()

    def run_copier(self, fetcher, only=None, now=NOW):
        return asyncio.run(copy_pending(self.repo, self.store, fetcher, now, only=only))

    def statuses(self, share_id: str) -> list[str]:
        return [i["status"] for i in asyncio.run(self.repo.media_for(share_id, OWNER))["images"]]

    def test_copies_pending_images_into_the_store(self):
        asyncio.run(self.repo.upsert(story("s1", [img(1), img(2)])))

        result = self.run_copier(FakeFetcher())

        self.assertEqual(result, {"copied": 2, "failed": 0})
        self.assertEqual(self.statuses("s1"), ["copied", "copied"])
        self.assertEqual(
            sorted(self.store.objects),
            ["chat-archives/s1/msg-1/01.jpg", "chat-archives/s1/msg-1/02.jpg"],
        )
        self.assertEqual(asyncio.run(self.store.get("chat-archives/s1/msg-1/01.jpg")), (JPEG, "image/jpeg"))

    def test_each_object_says_where_it_came_from(self):
        asyncio.run(self.repo.upsert(story("s1", [img(1)])))
        self.run_copier(FakeFetcher())
        self.assertEqual(
            self.store.metadata["chat-archives/s1/msg-1/01.jpg"],
            {"share_id": "s1", "message_id": "msg-1", "source_url": img(1), "origin_url": ""},
        )

    def test_the_same_url_in_one_batch_is_fetched_once(self):
        asyncio.run(self.repo.upsert(story("s1", [img(1), img(1)])))
        fetcher = FakeFetcher()

        self.run_copier(fetcher)

        self.assertEqual(fetcher.calls, [img(1)])
        self.assertEqual(self.statuses("s1"), ["copied", "copied"])
        self.assertEqual(len(self.store.objects), 2, "one fetch, but each image keeps its own place")

    def test_a_fetch_error_retries_then_fails_after_three_runs(self):
        asyncio.run(self.repo.upsert(story("s1", [img(1)])))
        fetcher = FakeFetcher(failing=[img(1)])

        for expected in ("pending", "pending", "failed"):
            self.assertEqual(self.run_copier(fetcher), {"copied": 0, "failed": 1})
            self.assertEqual(self.statuses("s1"), [expected])

        (image,) = asyncio.run(self.repo.media_for("s1", OWNER))["images"]
        self.assertEqual(image["last_error"], "proxy fetch failed: HTTP 502")
        self.assertEqual(self.run_copier(fetcher), {"copied": 0, "failed": 0}, "failed rows wait")

    def test_a_failure_fails_every_row_with_that_url_in_the_batch(self):
        asyncio.run(self.repo.upsert(story("s1", [img(1), img(1)])))

        self.run_copier(FakeFetcher(failing=[img(1)]))

        images = asyncio.run(self.repo.media_for("s1", OWNER))["images"]
        self.assertEqual([i["attempts"] for i in images], [1, 1])
        self.assertEqual([i["status"] for i in images], ["pending", "pending"])

    def test_an_unsupported_content_type_fails_the_row(self):
        asyncio.run(self.repo.upsert(story("s1", [img(1)])))

        self.run_copier(FakeFetcher(content_type="text/html; charset=utf-8"))

        (image,) = asyncio.run(self.repo.media_for("s1", OWNER))["images"]
        self.assertEqual(image["last_error"], "unsupported content type text/html")
        self.assertEqual(self.store.objects, {})

    def test_a_content_type_with_parameters_is_accepted(self):
        asyncio.run(self.repo.upsert(story("s1", [img(1)])))

        self.run_copier(FakeFetcher(content_type="image/png; charset=binary"))

        self.assertEqual(self.statuses("s1"), ["copied"])
        self.assertIn("chat-archives/s1/msg-1/01.png", self.store.objects)

    def test_an_oversized_image_fails_the_row(self):
        asyncio.run(self.repo.upsert(story("s1", [img(1)])))

        self.run_copier(FakeFetcher(body=b"x" * (MAX_BYTES + 1)))

        (image,) = asyncio.run(self.repo.media_for("s1", OWNER))["images"]
        self.assertEqual(image["last_error"], f"image larger than {MAX_BYTES} bytes")

    def test_only_copies_the_requested_story(self):
        asyncio.run(self.repo.upsert(story("s1", [img(1)])))
        asyncio.run(self.repo.upsert(story("s2", [img(2)])))

        self.run_copier(FakeFetcher(), only=(OWNER, "s2"))

        self.assertEqual(self.statuses("s1"), ["pending"])
        self.assertEqual(self.statuses("s2"), ["copied"])

    def test_a_batch_takes_at_most_ten(self):
        asyncio.run(self.repo.upsert(story("s1", [img(n) for n in range(12)])))

        self.assertEqual(self.run_copier(FakeFetcher()), {"copied": 10, "failed": 0})
        self.assertEqual(self.run_copier(FakeFetcher()), {"copied": 2, "failed": 0})

    def test_media_key_is_the_path_plus_the_type(self):
        self.assertEqual(media_key("chat-archives/s/m/01", "image/jpeg"), "chat-archives/s/m/01.jpg")
        self.assertEqual(media_key("chat-archives/s/m/01", "image/webp"), "chat-archives/s/m/01.webp")
        self.assertIsNone(media_key("chat-archives/s/m/01", "text/html"))

    def test_a_row_with_no_path_fails_with_the_fix(self):
        # A row filed before paths existed, put back in the queue by Retry.
        asyncio.run(self.repo.upsert(story("s1", [img(1)])))
        self.repo._media[0]["object_path"] = None

        self.assertEqual(self.run_copier(FakeFetcher()), {"copied": 0, "failed": 1})
        (image,) = asyncio.run(self.repo.media_for("s1", OWNER))["images"]
        self.assertIn("file this story again", image["last_error"])


class RetractTests(unittest.TestCase):
    """Retracting a story removes its R2 folder — unless someone else still files it."""

    def setUp(self):
        self.repo = MemoryConversationRepository()
        self.store = MemoryMediaStore()
        asyncio.run(self.store.put("chat-archives/s1/msg-1/01.jpg", JPEG, "image/jpeg"))
        asyncio.run(self.store.put("chat-archives/s10/msg-1/01.jpg", JPEG, "image/jpeg"))

    def test_retracting_the_only_copy_deletes_the_story_folder(self):
        asyncio.run(self.repo.upsert(story("s1", [img(1)])))

        self.assertTrue(asyncio.run(retract_story(self.repo, self.store, "s1", OWNER)))

        self.assertEqual(list(self.store.objects), ["chat-archives/s10/msg-1/01.jpg"])

    def test_the_folder_stays_while_another_filer_still_has_the_story(self):
        asyncio.run(self.repo.upsert(story("s1", [img(1)])))
        asyncio.run(self.repo.upsert(story("s1", [img(1)], owner="guest@example.com")))

        asyncio.run(retract_story(self.repo, self.store, "s1", OWNER))

        self.assertIn("chat-archives/s1/msg-1/01.jpg", self.store.objects)

    def test_retracting_also_frees_images_copied_before_per_story_folders(self):
        # A story copied into the old flat `img/` folder: its rows still name
        # those keys, and retracting the last copy deletes them too.
        asyncio.run(self.repo.upsert(story("s1", [img(1)])))
        asyncio.run(self.store.put("img/legacy.jpg", JPEG, "image/jpeg"))
        asyncio.run(self.store.put("img/someone-elses.jpg", JPEG, "image/jpeg"))
        row = self.repo._media[0]
        row.update(status="copied", r2_key="img/legacy.jpg", object_path=None)

        asyncio.run(retract_story(self.repo, self.store, "s1", OWNER))

        self.assertNotIn("img/legacy.jpg", self.store.objects)
        self.assertIn("img/someone-elses.jpg", self.store.objects)

    def test_an_old_image_another_story_still_shows_is_kept(self):
        # Old `img/` keys were shared between stories that showed the same picture.
        asyncio.run(self.repo.upsert(story("s1", [img(1)])))
        asyncio.run(self.repo.upsert(story("s2", [img(1)])))
        asyncio.run(self.store.put("img/shared.jpg", JPEG, "image/jpeg"))
        for row in self.repo._media:
            row.update(status="copied", r2_key="img/shared.jpg", object_path=None)

        asyncio.run(retract_story(self.repo, self.store, "s1", OWNER))

        self.assertIn("img/shared.jpg", self.store.objects)

    def test_retracting_what_you_never_filed_deletes_nothing(self):
        asyncio.run(self.repo.upsert(story("s1", [img(1)], owner="guest@example.com")))

        self.assertFalse(asyncio.run(retract_story(self.repo, self.store, "s1", OWNER)))
        self.assertIn("chat-archives/s1/msg-1/01.jpg", self.store.objects)


class ProxyImageFetcherTests(unittest.TestCase):
    def test_sends_the_token_and_reads_bytes(self):
        seen = {}

        class Buffer:
            def to_bytes(self):
                return JPEG

        async def fake_fetch(url, headers=None, redirect="follow"):
            seen["url"], seen["headers"] = url, headers

            async def array_buffer():
                return Buffer()

            return SimpleNamespace(
                status=200,
                headers=SimpleNamespace(get=lambda name: "image/jpeg" if name == "content-type" else None),
                arrayBuffer=array_buffer,
            )

        fetcher = ProxyImageFetcher(fake_fetch, "https://proxy.example/", "secret")
        data, content_type = asyncio.run(fetcher.fetch(img(1)))

        self.assertEqual((data, content_type), (JPEG, "image/jpeg"))
        self.assertTrue(seen["url"].startswith("https://proxy.example/?u=https%3A%2F%2Fimages.openai.com"))
        self.assertEqual(seen["headers"]["x-fetch-token"], "secret")

    def test_a_non_200_is_a_fetch_error(self):
        async def fake_fetch(url, headers=None, redirect="follow"):
            return SimpleNamespace(status=403)

        fetcher = ProxyImageFetcher(fake_fetch, "https://proxy.example", "secret")
        with self.assertRaises(FetchError):
            asyncio.run(fetcher.fetch(img(1)))


class DirectoryStoreTests(unittest.TestCase):
    def test_round_trips_bytes_and_type(self):
        with tempfile.TemporaryDirectory() as root:
            store = DirectoryMediaStore(root)
            asyncio.run(store.put("img/abc.png", b"png!", "image/png"))

            self.assertEqual(asyncio.run(store.get("img/abc.png")), (b"png!", "image/png"))
            self.assertIsNone(asyncio.run(store.get("img/missing.png")))

    def test_delete_prefix_removes_one_story_folder_only(self):
        with tempfile.TemporaryDirectory() as root:
            store = DirectoryMediaStore(root)
            for key in ("chat-archives/s1/m/01.jpg", "chat-archives/s1/n/01.jpg", "chat-archives/s10/m/01.jpg"):
                asyncio.run(store.put(key, b"x", "image/jpeg", {"share_id": "s"}))

            self.assertEqual(asyncio.run(store.delete_prefix("chat-archives/s1/")), 2)
            self.assertIsNone(asyncio.run(store.get("chat-archives/s1/m/01.jpg")))
            self.assertIsNotNone(asyncio.run(store.get("chat-archives/s10/m/01.jpg")))


class R2ReadTests(unittest.TestCase):
    """Reading an object back from R2, as the Workers Python SDK delivers it.

    The SDK wraps the R2 binding (`_BindingWrapper`) and converts every result:
    `arrayBuffer()` comes back as a Python memoryview, not a JS buffer, and
    `httpMetadata` as an attribute-dict. The first version called
    `.to_bytes()` on it and every image request died with error 1101.
    """

    def bucket(self, buffer, metadata):
        class Found:
            httpMetadata = metadata

            async def arrayBuffer(self):
                return buffer

        class Bucket:
            async def get(self, key):
                return Found() if key == "img/a.jpg" else None

        return Bucket()

    def test_the_sdk_wrapped_shape_memoryview(self):
        store = R2MediaStore(self.bucket(memoryview(JPEG), SimpleNamespace(contentType="image/jpeg")))
        self.assertEqual(asyncio.run(store.get("img/a.jpg")), (JPEG, "image/jpeg"))

    def test_a_raw_js_buffer_shape_still_works(self):
        class JsBuffer:
            def to_bytes(self):
                return JPEG

        store = R2MediaStore(self.bucket(JsBuffer(), SimpleNamespace(contentType="image/png")))
        self.assertEqual(asyncio.run(store.get("img/a.jpg")), (JPEG, "image/png"))

    def test_a_missing_object_is_none(self):
        store = R2MediaStore(self.bucket(memoryview(JPEG), None))
        self.assertIsNone(asyncio.run(store.get("img/missing.jpg")))

    def test_delete_prefix_pages_through_the_listing_and_deletes_each_page(self):
        pages = {
            None: SimpleNamespace(
                objects=[SimpleNamespace(key="chat-archives/s1/m/01.jpg")], truncated=True, cursor="c2"
            ),
            "c2": SimpleNamespace(
                objects=[SimpleNamespace(key="chat-archives/s1/n/01.jpg")], truncated=False, cursor=None
            ),
        }
        calls = {"list": [], "delete": []}

        class Bucket:
            async def list(self, options):
                calls["list"].append(options)
                return pages[options.get("cursor")]

            async def delete(self, keys):
                calls["delete"].append(keys)

        deleted = asyncio.run(R2MediaStore(Bucket()).delete_prefix("chat-archives/s1/"))

        self.assertEqual(deleted, 2)
        self.assertEqual([o["prefix"] for o in calls["list"]], ["chat-archives/s1/"] * 2)
        self.assertEqual(calls["delete"], [["chat-archives/s1/m/01.jpg"], ["chat-archives/s1/n/01.jpg"]])

    def test_delete_keys_deletes_exactly_those_keys(self):
        calls = []

        class Bucket:
            async def delete(self, keys):
                calls.append(keys)

        self.assertEqual(asyncio.run(R2MediaStore(Bucket()).delete_keys(["img/a.jpg", "img/b.jpg"])), 2)
        self.assertEqual(calls, [["img/a.jpg", "img/b.jpg"]])
        self.assertEqual(asyncio.run(R2MediaStore(Bucket()).delete_keys([])), 0)
        self.assertEqual(len(calls), 1, "nothing to delete is no call at all")

    def test_delete_prefix_refuses_anything_but_a_story_folder(self):
        for bad in ("", "chat-archives/", "chat-archives/s1", "img/"):
            with self.subTest(prefix=bad), self.assertRaises(ValueError):
                asyncio.run(R2MediaStore(None).delete_prefix(bad))


if __name__ == "__main__":
    unittest.main(verbosity=2)

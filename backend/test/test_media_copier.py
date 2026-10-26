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
from chat_archive.media_copier import MAX_BYTES, copy_pending, media_key  # noqa: E402
from chat_archive.models import Conversation, Media, Message  # noqa: E402

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
        key = media_key(img(1), "image/jpeg")
        self.assertEqual(asyncio.run(self.store.get(key)), (JPEG, "image/jpeg"))

    def test_the_same_url_in_one_batch_is_fetched_once(self):
        asyncio.run(self.repo.upsert(story("s1", [img(1), img(1)])))
        fetcher = FakeFetcher()

        self.run_copier(fetcher)

        self.assertEqual(fetcher.calls, [img(1)])
        self.assertEqual(self.statuses("s1"), ["copied", "copied"])

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
        self.assertIn(media_key(img(1), "image/png"), self.store.objects)

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

    def test_media_key_is_stable_and_typed(self):
        digest = hashlib.sha256(b"https://x/a").hexdigest()
        self.assertEqual(media_key("https://x/a", "image/jpeg"), f"img/{digest}.jpg")
        self.assertEqual(media_key("https://x/a", "image/webp"), f"img/{digest}.webp")
        self.assertIsNone(media_key("https://x/a", "text/html"))


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


if __name__ == "__main__":
    unittest.main(verbosity=2)

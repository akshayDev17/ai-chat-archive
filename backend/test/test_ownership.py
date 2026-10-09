"""Ownership contract test: one suite, every ConversationRepository.

The archive is per-email, and the whole point of having a port is that all three
implementations must behave identically. So this file is a *contract test*: the
same assertions run against

  * D1ConversationRepository   (production, against a fake D1 backed by SQLite)
  * SqliteConversationRepository (local dev, real file)
  * MemoryConversationRepository (test double)

If a future implementation of the port leaks another reader's shelf, this test
fails for that implementation specifically — which is exactly the guarantee a
port is supposed to buy.

Runs offline. No network, no Cloudflare account.

Run:  python3 backend/test/test_ownership.py
"""

from __future__ import annotations

import asyncio
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from chat_archive.local_repository import MemoryConversationRepository  # noqa: E402
from chat_archive.models import Conversation, Message  # noqa: E402
from chat_archive.repository import D1ConversationRepository  # noqa: E402
from chat_archive.sqlite_repository import SqliteConversationRepository  # noqa: E402

AKSHAY = "akshay@akshayprabhakant.com"
GUEST = "guest@example.com"


# --------------------------------------------------------------------- fake D1


class _FakeD1Statement:
    """The slice of the D1 prepared-statement API the repository actually uses."""

    def __init__(self, conn: sqlite3.Connection, sql: str, params: tuple = ()):
        self._conn = conn
        self._sql = sql
        self._params = params

    def bind(self, *params):
        return _FakeD1Statement(self._conn, self._sql, params)

    async def first(self):
        row = self._conn.execute(self._sql, self._params).fetchone()
        return dict(row) if row is not None else None

    async def all(self):
        rows = self._conn.execute(self._sql, self._params).fetchall()
        return SimpleNamespace(results=[dict(r) for r in rows])

    async def run(self):
        self._conn.execute(self._sql, self._params)
        self._conn.commit()
        return SimpleNamespace(success=True)


class FakeD1:
    """A D1 binding backed by SQLite, so the repository's real SQL is exercised.

    This is the piece that makes the D1 implementation testable off-platform:
    the repository's queries, joins and WHERE clauses all run for real.
    """

    def __init__(self, schema_path: Path):
        self._conn = sqlite3.connect(":memory:")
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(schema_path.read_text())

    def prepare(self, sql: str) -> _FakeD1Statement:
        return _FakeD1Statement(self._conn, sql)

    async def batch(self, statements):
        for statement in statements:
            await statement.run()
        return []


# ------------------------------------------------------------------- fixture


def conversation(share_id: str, title: str, owner: str = "") -> Conversation:
    conv = Conversation(
        share_id=share_id,
        title=title,
        messages=[Message(role="user", content="hi"), Message(role="assistant", content="hello")],
        report=f"# {title}\n\nA report.",
    )
    return conv.owned_by(owner) if owner else conv


class OwnershipContract:
    """The behaviour every ConversationRepository must exhibit.

    Subclasses supply ``make_repository()``; every test below is then run
    against each implementation.
    """

    def make_repository(self):
        raise NotImplementedError

    def setUp(self):
        self.repo = self.make_repository()

    # -- the public edition is unscoped --------------------------------------

    def test_the_edition_shows_every_filer(self):
        """The front page is a newspaper: everyone reads the same edition."""
        asyncio.run(self.repo.upsert(conversation("share-a", "Akshay's story", AKSHAY)))
        asyncio.run(self.repo.upsert(conversation("share-b", "Guest's story", GUEST)))

        edition = asyncio.run(self.repo.list_all())
        self.assertEqual({s["id"] for s in edition}, {"share-a", "share-b"})

    def test_the_edition_records_who_filed_each_story(self):
        """Provenance is stored, even though it is never published.

        The row shape must NOT carry the address: `/api/sessions` is served to
        anonymous visitors, so returning `owner_email` here publishes the
        filer's email to anyone who opens the JSON. Provenance stays in the
        database — it keys the upsert and scopes list_recent.
        """
        asyncio.run(self.repo.upsert(conversation("share-a", "A story", AKSHAY)))
        row = asyncio.run(self.repo.list_all())[0]
        self.assertNotIn("owner_email", row, "the public edition must not carry filer addresses")

    def test_no_listing_row_ever_carries_an_email(self):
        """Guard for both listings: no address on the wire, public or private.

        The scoped endpoint already returns `owner` at the top level to the one
        person it belongs to, so a per-row address is redundant as well as
        risky.
        """
        asyncio.run(self.repo.upsert(conversation("share-a", "A story", AKSHAY)))

        for label, rows in (
            ("list_all", asyncio.run(self.repo.list_all())),
            ("list_recent", asyncio.run(self.repo.list_recent(AKSHAY))),
        ):
            with self.subTest(listing=label):
                self.assertTrue(rows, "expected at least one row")
                for row in rows:
                    self.assertNotIn("owner_email", row)
                    for value in row.values():
                        self.assertNotIn(
                            AKSHAY,
                            str(value),
                            f"{label} leaked the filer's address in a field value",
                        )

    # -- your own filings stay scoped ----------------------------------------

    def test_list_is_scoped_to_the_owner(self):
        asyncio.run(self.repo.upsert(conversation("share-a", "Akshay's story", AKSHAY)))
        asyncio.run(self.repo.upsert(conversation("share-b", "Guest's story", GUEST)))

        mine = asyncio.run(self.repo.list_recent(AKSHAY))
        theirs = asyncio.run(self.repo.list_recent(GUEST))

        self.assertEqual([s["id"] for s in mine], ["share-a"])
        self.assertEqual([s["id"] for s in theirs], ["share-b"])

    def test_unknown_filer_sees_an_empty_list(self):
        asyncio.run(self.repo.upsert(conversation("share-a", "Akshay's story", AKSHAY)))
        self.assertEqual(asyncio.run(self.repo.list_recent("nobody@example.com")), [])
        # ...but still reads the public edition.
        self.assertEqual(len(asyncio.run(self.repo.list_all())), 1)

    def test_the_same_share_link_can_be_filed_by_two_people(self):
        """The reason the unique key is (owner_email, external_id), not external_id."""
        asyncio.run(self.repo.upsert(conversation("shared-id", "As Akshay saw it", AKSHAY)))
        asyncio.run(self.repo.upsert(conversation("shared-id", "As the guest saw it", GUEST)))

        self.assertEqual(len(asyncio.run(self.repo.list_recent(AKSHAY))), 1)
        self.assertEqual(len(asyncio.run(self.repo.list_recent(GUEST))), 1)
        # Both are in the edition; the public permalink resolves to one of them
        # rather than 404ing.
        self.assertEqual(len(asyncio.run(self.repo.list_all())), 2)
        self.assertIsNotNone(asyncio.run(self.repo.get("shared-id")))
        self.assertEqual(
            asyncio.run(self.repo.get("shared-id", AKSHAY)).title, "As Akshay saw it"
        )
        self.assertEqual(asyncio.run(self.repo.get("shared-id", GUEST)).title, "As the guest saw it")

    def test_reingesting_the_same_link_updates_rather_than_duplicates(self):
        asyncio.run(self.repo.upsert(conversation("share-a", "First title", AKSHAY)))
        asyncio.run(self.repo.upsert(conversation("share-a", "Second title", AKSHAY)))

        shelf = asyncio.run(self.repo.list_recent(AKSHAY))
        self.assertEqual(len(shelf), 1)
        self.assertEqual(shelf[0]["title"], "Second title")

    def test_email_normalization_does_not_split_a_shelf(self):
        asyncio.run(self.repo.upsert(conversation("share-a", "Story", "  Akshay@Example.COM ")))
        self.assertEqual(len(asyncio.run(self.repo.list_recent("akshay@example.com"))), 1)

    # -- reading is public ---------------------------------------------------

    def test_public_lookup_ignores_ownership(self):
        asyncio.run(self.repo.upsert(conversation("share-a", "Akshay's story", AKSHAY)))
        story = asyncio.run(self.repo.get("share-a"))
        self.assertIsNotNone(story, "a story must be readable by permalink with no identity")
        self.assertEqual(story.title, "Akshay's story")
        self.assertEqual(story.owner_email, AKSHAY)

    def test_owner_scoped_lookup_misses_for_a_non_owner(self):
        asyncio.run(self.repo.upsert(conversation("share-a", "Akshay's story", AKSHAY)))
        self.assertIsNone(asyncio.run(self.repo.get("share-a", GUEST)))

    def test_round_trips_the_report_and_transcript(self):
        asyncio.run(self.repo.upsert(conversation("share-a", "Story", AKSHAY)))
        story = asyncio.run(self.repo.get("share-a", AKSHAY))
        self.assertEqual(len(story.messages), 2)
        self.assertTrue(story.report.startswith("# Story"))

    # -- an unowned row is refused, not silently stored ----------------------

    def test_upsert_refuses_an_unowned_conversation(self):
        with self.assertRaises(ValueError):
            asyncio.run(self.repo.upsert(conversation("share-a", "Orphan")))

    def test_list_recent_refuses_an_empty_owner(self):
        # The *scoped* listing fails loudly on a lost identity. list_all is the
        # public edition and deliberately has no such guard -- it is unscoped by
        # design, so there is no identity for it to lose.
        with self.assertRaises(ValueError):
            asyncio.run(self.repo.list_recent(""))

    def test_an_empty_edition_is_an_empty_list_not_an_error(self):
        self.assertEqual(asyncio.run(self.repo.list_all()), [])


# ------------------------------------------------------------ implementations


class TestMemoryRepository(OwnershipContract, unittest.TestCase):
    def make_repository(self):
        return MemoryConversationRepository()


class TestSqliteRepository(OwnershipContract, unittest.TestCase):
    def make_repository(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        return SqliteConversationRepository(str(Path(self._tmp.name) / "archive.db"))


class TestD1Repository(OwnershipContract, unittest.TestCase):
    """Runs the D1 repository's real SQL against a SQLite-backed fake binding."""

    def make_repository(self):
        self._d1 = FakeD1(BACKEND / "schema.sql")
        return D1ConversationRepository(self._d1)


# ------------------------------------------------------------ schema migration


class TestSqliteMigration(unittest.TestCase):
    """A pre-ownership database must upgrade without losing stories.

    Before this change ``list_recent`` had no owner filter, so a row without an
    owner would simply disappear from the front page after the upgrade — silent
    data loss. The migration backfills instead.
    """

    def test_legacy_rows_are_backfilled_with_the_legacy_owner(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = str(Path(tmp.name) / "archive.db")

        # Build the OLD schema and insert one story, as the previous version did.
        conn = sqlite3.connect(path)
        conn.executescript(
            """
            CREATE TABLE conversations (
              id TEXT PRIMARY KEY,
              external_id TEXT UNIQUE NOT NULL,
              title TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              source TEXT DEFAULT 'chatgpt',
              report_id TEXT
            );
            CREATE TABLE messages (
              id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, seq INTEGER NOT NULL,
              role TEXT NOT NULL, content TEXT, created_at TEXT
            );
            CREATE TABLE reports (
              id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, title TEXT,
              markdown TEXT NOT NULL, citations TEXT, skill TEXT, generated_at TEXT
            );
            INSERT INTO conversations (id, external_id, title, created_at, updated_at)
            VALUES ('c1', 'legacy-share', 'An old story', '2024-01-01T00:00:00Z', '2024-01-01T00:00:00Z');
            """
        )
        conn.commit()
        conn.close()

        # Opening it with the new repository migrates in place.
        repo = SqliteConversationRepository(path)
        shelf = asyncio.run(repo.list_recent("akshay@akshayprabhakant.com"))
        self.assertEqual([s["id"] for s in shelf], ["legacy-share"])

        # And only the legacy owner sees it.
        self.assertEqual(asyncio.run(repo.list_recent("someone@else.com")), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)

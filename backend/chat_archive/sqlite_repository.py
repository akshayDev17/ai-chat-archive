"""SQLite-backed ConversationRepository for local development.

A third implementation of the ConversationRepository port — alongside the D1
repository (production) and the in-memory one (test double). It persists to a
local file and mirrors the D1 schema exactly, so local upsert / list behaviour
matches production.

Two things worth knowing:

1. **Threading.** The local server handles each request on its own thread, so
   the single shared connection is created with ``check_same_thread=False`` and
   guarded by a lock. Local dev is single-user, so serializing is free.

2. **Migration.** SQLite cannot drop a table constraint with ALTER, and the
   earlier schema had ``external_id TEXT UNIQUE`` — which would forbid the same
   share link from living in two readers' archives. The ``_migrate`` step
   therefore does a full table rebuild (SQLite's documented procedure) and
   backfills existing rows with ``LEGACY_OWNER_EMAIL`` so no story is orphaned
   by the upgrade. Because ``list_recent`` is owner-scoped, an un-migrated row
   would simply vanish from the front page — a silent data loss, not an error.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timezone

from .models import Citation, Conversation, Message, Source, normalize_email
from .ports import ConversationRepository
from .repository import (
    MEDIA_CLAIM_ONE_STORY_SQL,
    MEDIA_CLAIM_SQL,
    MEDIA_COUNT_FAILED_SQL,
    MEDIA_FAIL_SQL,
    MEDIA_FINISH_SQL,
    MEDIA_FOR_CONVERSATION_SQL,
    MEDIA_INSERT_SQL,
    MEDIA_RECLAIM_SQL,
    MEDIA_RETRY_SQL,
    RECLAIM_ERROR,
    MEDIA_SUMMARY_SQL,
    _sources_by_message,
    COPIED_MEDIA_SQL,
    media_by_message,
    media_image,
    media_rows_for,
)


def _dicts(cursor) -> list[dict]:
    """Rows as dicts keyed by column name, the shape the shared media helpers read."""
    names = [d[0] for d in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]

#: Stories imported before archives became per-email are attributed to this
#: address. Override with ARCHIVE_LEGACY_OWNER if the local DB was populated
#: under a different identity.
LEGACY_OWNER_EMAIL = os.environ.get("ARCHIVE_LEGACY_OWNER", "akshay@akshayprabhakant.com")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
  id          TEXT PRIMARY KEY,
  owner_email TEXT NOT NULL,
  external_id TEXT NOT NULL,
  title       TEXT,
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL,
  source      TEXT DEFAULT 'chatgpt',
  report_id   TEXT
);
CREATE TABLE IF NOT EXISTS messages (
  id              TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL REFERENCES conversations(id),
  seq             INTEGER NOT NULL,
  role            TEXT NOT NULL,
  content         TEXT,
  created_at      TEXT
);
CREATE TABLE IF NOT EXISTS reports (
  id              TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL REFERENCES conversations(id),
  title           TEXT,
  markdown        TEXT NOT NULL,
  citations       TEXT,
  skill           TEXT,
  generated_at    TEXT
);
CREATE TABLE IF NOT EXISTS sources (
  id              TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL REFERENCES conversations(id),
  message_seq     INTEGER NOT NULL,
  seq             INTEGER NOT NULL,
  kind            TEXT NOT NULL,
  title           TEXT,
  url             TEXT NOT NULL,
  attribution     TEXT,
  pub_date        INTEGER,
  spans           TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_conversations_owner_external
  ON conversations(owner_email, external_id);
CREATE INDEX IF NOT EXISTS idx_conversations_owner_recent
  ON conversations(owner_email, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_conversations_external ON conversations(external_id);
CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id, seq);
CREATE INDEX IF NOT EXISTS idx_reports_conv ON reports(conversation_id);
CREATE INDEX IF NOT EXISTS idx_sources_message ON sources(conversation_id, message_seq, seq);
CREATE TABLE IF NOT EXISTS media (
  id              TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL REFERENCES conversations(id),
  message_seq     INTEGER NOT NULL,
  tag_start       INTEGER NOT NULL,
  ordinal         INTEGER NOT NULL,
  content_url     TEXT NOT NULL,
  origin_url      TEXT,
  status          TEXT NOT NULL DEFAULT 'pending',
  attempts        INTEGER NOT NULL DEFAULT 0,
  last_error      TEXT,
  claimed_at      TEXT,
  r2_key          TEXT,
  content_type    TEXT
);
CREATE INDEX IF NOT EXISTS idx_media_message ON media(conversation_id, message_seq, tag_start, ordinal);
CREATE INDEX IF NOT EXISTS idx_media_status ON media(status, claimed_at);
CREATE INDEX IF NOT EXISTS idx_media_content_url ON media(content_url);
"""


class SqliteConversationRepository(ConversationRepository):
    def __init__(self, path: str):
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.Lock()
        self._migrate()
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # ------------------------------------------------------------------ schema

    def _columns(self) -> set[str]:
        rows = self._conn.execute("PRAGMA table_info(conversations)").fetchall()
        return {row[1] for row in rows}

    def _migrate(self) -> None:
        """Rebuild a pre-ownership ``conversations`` table in place, keeping rows."""
        existing = self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'conversations'"
        ).fetchone()
        if not existing or "owner_email" in self._columns():
            return

        self._conn.executescript(
            f"""
            ALTER TABLE conversations RENAME TO conversations_pre_owner;

            CREATE TABLE conversations (
              id          TEXT PRIMARY KEY,
              owner_email TEXT NOT NULL,
              external_id TEXT NOT NULL,
              title       TEXT,
              created_at  TEXT NOT NULL,
              updated_at  TEXT NOT NULL,
              source      TEXT DEFAULT 'chatgpt',
              report_id   TEXT
            );

            INSERT INTO conversations
              (id, owner_email, external_id, title, created_at, updated_at, source, report_id)
            SELECT id, '{LEGACY_OWNER_EMAIL}', external_id, title, created_at, updated_at,
                   source, report_id
              FROM conversations_pre_owner;

            DROP TABLE conversations_pre_owner;
            """
        )
        self._conn.commit()
        print(
            f"[migration] conversations table rebuilt with owner_email; "
            f"existing rows assigned to {LEGACY_OWNER_EMAIL!r}"
        )

    # ------------------------------------------------------------- repository

    async def upsert(self, conversation: Conversation) -> None:
        owner = normalize_email(conversation.owner_email)
        if not owner:
            raise ValueError("Refusing to store a conversation with no owner_email.")

        with self._lock:
            now = datetime.now(timezone.utc).isoformat()
            row = self._conn.execute(
                "SELECT id FROM conversations WHERE owner_email = ? AND external_id = ?",
                (owner, conversation.share_id),
            ).fetchone()

            # Read before the delete below, as in the D1 repository.
            urls = sorted({m.content_url for msg in conversation.messages for m in msg.media})
            copied = {}
            if urls:
                found = _dicts(self._conn.execute(COPIED_MEDIA_SQL, (json.dumps(urls),)))
                copied = {r["content_url"]: (r["r2_key"], r["content_type"]) for r in found}

            conv_id = row[0] if row else str(uuid.uuid4())
            if row:
                self._conn.execute(
                    "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?",
                    (conversation.title, now, conv_id),
                )
                self._conn.execute("DELETE FROM messages WHERE conversation_id = ?", (conv_id,))
                self._conn.execute("DELETE FROM reports WHERE conversation_id = ?", (conv_id,))
                self._conn.execute("DELETE FROM sources WHERE conversation_id = ?", (conv_id,))
                self._conn.execute("DELETE FROM media WHERE conversation_id = ?", (conv_id,))
            else:
                self._conn.execute(
                    "INSERT INTO conversations "
                    "(id, owner_email, external_id, title, created_at, updated_at, source) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        conv_id,
                        owner,
                        conversation.share_id,
                        conversation.title,
                        now,
                        now,
                        "chatgpt",
                    ),
                )

            self._conn.executemany(
                "INSERT INTO messages (id, conversation_id, seq, role, content, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (str(uuid.uuid4()), conv_id, seq, msg.role, msg.content, now)
                    for seq, msg in enumerate(conversation.messages)
                ],
            )

            self._conn.executemany(
                "INSERT INTO sources "
                "(id, conversation_id, message_seq, seq, kind, title, url, attribution, pub_date, spans) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        str(uuid.uuid4()),
                        conv_id,
                        message_seq,
                        source.index,
                        source.kind,
                        source.title,
                        source.url,
                        source.attribution,
                        source.pub_date,
                        json.dumps([list(span) for span in source.spans]),
                    )
                    for message_seq, message in enumerate(conversation.messages)
                    for source in message.sources
                ],
            )

            self._conn.executemany(
                MEDIA_INSERT_SQL, media_rows_for(conv_id, conversation.messages, copied)
            )

            if conversation.report:
                report_id = str(uuid.uuid4())
                self._conn.execute(
                    "INSERT INTO reports (id, conversation_id, title, markdown, citations, skill, generated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        report_id,
                        conv_id,
                        conversation.title,
                        conversation.report,
                        json.dumps(
                            [
                                {"n": c.index, "title": c.title, "url": c.url}
                                for c in conversation.citations
                            ]
                        ),
                        "chat-to-markdown-report",
                        now,
                    ),
                )
                self._conn.execute(
                    "UPDATE conversations SET report_id = ? WHERE id = ?", (report_id, conv_id)
                )

            self._conn.commit()

    async def delete(self, share_id: str, owner_email: str) -> bool:
        owner = normalize_email(owner_email)
        if not owner:
            raise ValueError("delete requires an owner_email.")

        with self._lock:
            row = self._conn.execute(
                "SELECT id FROM conversations WHERE owner_email = ? AND external_id = ?",
                (owner, share_id),
            ).fetchone()
            if not row:
                return False

            conv_id = row[0]
            self._conn.execute("DELETE FROM media WHERE conversation_id = ?", (conv_id,))
            self._conn.execute("DELETE FROM sources WHERE conversation_id = ?", (conv_id,))
            self._conn.execute("DELETE FROM reports WHERE conversation_id = ?", (conv_id,))
            self._conn.execute("DELETE FROM messages WHERE conversation_id = ?", (conv_id,))
            self._conn.execute("DELETE FROM conversations WHERE id = ?", (conv_id,))
            self._conn.commit()
        return True

    async def get(self, share_id: str, owner_email: str | None = None) -> Conversation | None:
        owner = normalize_email(owner_email)

        with self._lock:
            if owner:
                row = self._conn.execute(
                    "SELECT id, title, owner_email FROM conversations "
                    "WHERE external_id = ? AND owner_email = ?",
                    (share_id, owner),
                ).fetchone()
            else:
                row = self._conn.execute(
                    "SELECT id, title, owner_email FROM conversations "
                    "WHERE external_id = ? ORDER BY created_at DESC LIMIT 1",
                    (share_id,),
                ).fetchone()

            if not row:
                return None

            conv_id, title, stored_owner = row
            messages = self._conn.execute(
                "SELECT seq, role, content FROM messages WHERE conversation_id = ? ORDER BY seq",
                (conv_id,),
            ).fetchall()
            source_rows = self._conn.execute(
                "SELECT message_seq, seq, kind, title, url, attribution, pub_date, spans "
                "FROM sources WHERE conversation_id = ? ORDER BY message_seq, seq",
                (conv_id,),
            ).fetchall()
            report_row = self._conn.execute(
                "SELECT markdown, citations FROM reports WHERE conversation_id = ?",
                (conv_id,),
            ).fetchone()
            media_rows = _dicts(self._conn.execute(MEDIA_FOR_CONVERSATION_SQL, (conv_id,)))

        citations = []
        if report_row and report_row[1]:
            citations = [
                Citation(index=c["n"], title=c["title"], url=c["url"])
                for c in json.loads(report_row[1])
            ]

        source_dicts = [
            {
                "message_seq": r[0], "seq": r[1], "kind": r[2], "title": r[3],
                "url": r[4], "attribution": r[5], "pub_date": r[6], "spans": r[7],
            }
            for r in source_rows
        ]

        return Conversation(
            share_id=share_id,
            title=title,
            messages=[
                Message(
                    role=r[1],
                    content=r[2],
                    sources=_sources_by_message(source_dicts, r[0]),
                    media=media_by_message(media_rows, r[0]),
                )
                for r in messages
            ],
            report=report_row[0] if report_row else None,
            citations=citations,
            owner_email=stored_owner,
        )

    async def list_all(self, limit: int = 200) -> list[dict]:
        """The public edition: every filed story, newest first, no owner filter.

        ``owner_email`` is deliberately not selected — see the D1 implementation
        for why.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT c.external_id AS id, c.title, c.created_at, c.source, r.markdown "
                "FROM conversations c "
                "LEFT JOIN reports r ON r.conversation_id = c.id "
                "ORDER BY c.created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            {"id": r[0], "title": r[1], "created_at": r[2], "source": r[3], "markdown": r[4]}
            for r in rows
        ]

    async def list_recent(self, owner_email: str, limit: int = 200) -> list[dict]:
        owner = normalize_email(owner_email)
        if not owner:
            raise ValueError("list_recent requires an owner_email.")

        with self._lock:
            rows = self._conn.execute(
                "SELECT c.external_id AS id, c.title, c.created_at, c.source, r.markdown "
                "FROM conversations c "
                "LEFT JOIN reports r ON r.conversation_id = c.id "
                "WHERE c.owner_email = ? "
                "ORDER BY c.created_at DESC LIMIT ?",
                (owner, limit),
            ).fetchall()
        return [
            {"id": r[0], "title": r[1], "created_at": r[2], "source": r[3], "markdown": r[4]}
            for r in rows
        ]

    # -- media (same SQL as D1; see repository.py) --------------------------

    def _owned_conversation(self, share_id: str, owner_email: str):
        owner = normalize_email(owner_email)
        if not owner:
            raise ValueError("media operations require an owner_email.")
        return self._conn.execute(
            "SELECT id, title FROM conversations WHERE owner_email = ? AND external_id = ?",
            (owner, share_id),
        ).fetchone()

    async def media_summary(self, owner_email: str) -> list[dict]:
        owner = normalize_email(owner_email)
        if not owner:
            raise ValueError("media_summary requires an owner_email.")
        with self._lock:
            return _dicts(self._conn.execute(MEDIA_SUMMARY_SQL, (owner,)))

    async def media_for(self, share_id: str, owner_email: str) -> dict | None:
        with self._lock:
            conv = self._owned_conversation(share_id, owner_email)
            if not conv:
                return None
            rows = _dicts(self._conn.execute(MEDIA_FOR_CONVERSATION_SQL, (conv[0],)))
        return {"id": share_id, "title": conv[1], "images": [media_image(r) for r in rows]}

    async def retry_failed_media(self, share_id: str, owner_email: str) -> int | None:
        with self._lock:
            conv = self._owned_conversation(share_id, owner_email)
            if not conv:
                return None
            failed = self._conn.execute(MEDIA_COUNT_FAILED_SQL, (conv[0],)).fetchone()[0]
            self._conn.execute(MEDIA_RETRY_SQL, (conv[0],))
            self._conn.commit()
        return failed

    async def claim_media(
        self,
        limit: int,
        now: str,
        stale_before: str,
        only: tuple[str, str] | None = None,
        max_attempts: int = 3,
    ) -> list[dict]:
        with self._lock:
            self._conn.execute(MEDIA_RECLAIM_SQL, (RECLAIM_ERROR, max_attempts, stale_before))
            if only:
                cursor = self._conn.execute(
                    MEDIA_CLAIM_ONE_STORY_SQL, (now, normalize_email(only[0]), only[1], limit)
                )
            else:
                cursor = self._conn.execute(MEDIA_CLAIM_SQL, (now, limit))
            claimed = _dicts(cursor)
            self._conn.commit()
        return [{"id": r["id"], "content_url": r["content_url"]} for r in claimed]

    async def finish_media(self, content_url: str, r2_key: str, content_type: str) -> None:
        with self._lock:
            self._conn.execute(MEDIA_FINISH_SQL, (r2_key, content_type, content_url))
            self._conn.commit()

    async def fail_media(self, media_id: str, error: str, max_attempts: int = 3) -> None:
        with self._lock:
            self._conn.execute(MEDIA_FAIL_SQL, (error, max_attempts, media_id))
            self._conn.commit()

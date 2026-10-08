"""SQLite-backed ConversationRepository for local development.

A third implementation of the ConversationRepository port — alongside the D1
repository (production) and the in-memory one (test double). It persists to a
local file and mirrors the D1 schema exactly, so local upsert / list behaviour
matches production.

The local server handles each request on its own thread, so the single shared
SQLite connection is created with ``check_same_thread=False`` and guarded by a
lock. Local dev is single-user, so serializing writes is free.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone

from .models import Citation, Conversation, Message
from .ports import ConversationRepository

_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
  id          TEXT PRIMARY KEY,
  external_id TEXT UNIQUE NOT NULL,
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
CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id, seq);
CREATE INDEX IF NOT EXISTS idx_reports_conv ON reports(conversation_id);
"""


class SqliteConversationRepository(ConversationRepository):
    def __init__(self, path: str):
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.Lock()
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    async def upsert(self, conversation: Conversation) -> None:
        with self._lock:
            now = datetime.now(timezone.utc).isoformat()
            row = self._conn.execute(
                "SELECT id FROM conversations WHERE external_id = ?",
                (conversation.share_id,),
            ).fetchone()

            conv_id = row[0] if row else str(uuid.uuid4())
            if row:
                self._conn.execute(
                    "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?",
                    (conversation.title, now, conv_id),
                )
                self._conn.execute("DELETE FROM messages WHERE conversation_id = ?", (conv_id,))
                self._conn.execute("DELETE FROM reports WHERE conversation_id = ?", (conv_id,))
            else:
                self._conn.execute(
                    "INSERT INTO conversations (id, external_id, title, created_at, updated_at, source) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (conv_id, conversation.share_id, conversation.title, now, now, "chatgpt"),
                )

            self._conn.executemany(
                "INSERT INTO messages (id, conversation_id, seq, role, content, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (str(uuid.uuid4()), conv_id, seq, msg.role, msg.content, now)
                    for seq, msg in enumerate(conversation.messages)
                ],
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

    async def get(self, conversation_id: str) -> Conversation | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT id, title FROM conversations WHERE external_id = ?",
                (conversation_id,),
            ).fetchone()
            if not row:
                return None
            conv_id, title = row
            messages = self._conn.execute(
                "SELECT role, content FROM messages WHERE conversation_id = ? ORDER BY seq",
                (conv_id,),
            ).fetchall()
            report_row = self._conn.execute(
                "SELECT markdown, citations FROM reports WHERE conversation_id = ?",
                (conv_id,),
            ).fetchone()

        citations = []
        if report_row and report_row[1]:
            citations = [
                Citation(index=c["n"], title=c["title"], url=c["url"])
                for c in json.loads(report_row[1])
            ]

        return Conversation(
            share_id=conversation_id,
            title=title,
            messages=[Message(role=r, content=c) for r, c in messages],
            report=report_row[0] if report_row else None,
            citations=citations,
        )

    async def list_recent(self, limit: int = 200) -> list[dict]:
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

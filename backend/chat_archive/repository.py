"""D1-backed conversation persistence."""

from __future__ import annotations

import json
import uuid

from .models import Citation, Conversation, Message, normalize_email
from .ports import ConversationRepository


class D1ConversationRepository(ConversationRepository):
    """Store conversations in Cloudflare D1.

    ``db`` is the D1 binding (``env.DB`` in the Worker). It is injected so the
    repository can be unit-tested against a fake.

    Every statement that enumerates stories carries an ``owner_email`` filter.
    Making that filter mandatory in the SQL, rather than optional, is what keeps
    a second reader's archive from leaking onto the front page.
    """

    def __init__(self, db):
        self._db = db

    async def upsert(self, conversation: Conversation) -> None:
        owner = normalize_email(conversation.owner_email)
        if not owner:
            raise ValueError(
                "Refusing to store a conversation with no owner_email: "
                "an unowned row is invisible to list_recent and would be "
                "unreachable dead data."
            )

        now = self._now()
        existing = await self._db.prepare(
            "SELECT id FROM conversations WHERE owner_email = ? AND external_id = ?"
        ).bind(owner, conversation.share_id).first()

        conv_id = existing["id"] if existing else str(uuid.uuid4())
        if existing:
            await self._db.prepare(
                "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?"
            ).bind(conversation.title, now, conv_id).run()
            await self._db.prepare(
                "DELETE FROM messages WHERE conversation_id = ?"
            ).bind(conv_id).run()
            await self._db.prepare(
                "DELETE FROM reports WHERE conversation_id = ?"
            ).bind(conv_id).run()
        else:
            await self._db.prepare(
                "INSERT INTO conversations "
                "(id, owner_email, external_id, title, created_at, updated_at, source) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)"
            ).bind(
                conv_id,
                owner,
                conversation.share_id,
                conversation.title,
                now,
                now,
                "chatgpt",
            ).run()

        message_stmt = self._db.prepare(
            "INSERT INTO messages (id, conversation_id, seq, role, content, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)"
        )
        batch = [
            message_stmt.bind(str(uuid.uuid4()), conv_id, seq, msg.role, msg.content, now)
            for seq, msg in enumerate(conversation.messages)
        ]
        if batch:
            await self._db.batch(batch)

        if conversation.report:
            report_id = str(uuid.uuid4())
            await self._db.prepare(
                "INSERT INTO reports "
                "(id, conversation_id, title, markdown, citations, skill, generated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)"
            ).bind(
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
            ).run()
            await self._db.prepare(
                "UPDATE conversations SET report_id = ? WHERE id = ?"
            ).bind(report_id, conv_id).run()

    async def list_all(self, limit: int = 200) -> list[dict]:
        """The public edition: every filed story, newest first, no owner filter."""
        result = await self._db.prepare(
            "SELECT c.external_id AS id, c.title, c.created_at, c.source, c.owner_email, "
            "r.markdown "
            "FROM conversations c "
            "LEFT JOIN reports r ON r.conversation_id = c.id "
            "ORDER BY c.created_at DESC LIMIT ?"
        ).bind(limit).all()
        return list(result.results)

    async def list_recent(self, owner_email: str, limit: int = 200) -> list[dict]:
        owner = normalize_email(owner_email)
        if not owner:
            # Raised, not `return []`: an empty owner means the caller lost the
            # identity, and a silent empty shelf would look identical to "this
            # reader has filed nothing yet". Fail loudly instead. (The public
            # edition does not go through here — see list_all.)
            raise ValueError("list_recent requires an owner_email.")

        result = await self._db.prepare(
            "SELECT c.external_id AS id, c.title, c.created_at, c.source, c.owner_email, "
            "r.markdown "
            "FROM conversations c "
            "LEFT JOIN reports r ON r.conversation_id = c.id "
            "WHERE c.owner_email = ? "
            "ORDER BY c.created_at DESC LIMIT ?"
        ).bind(owner, limit).all()
        return list(result.results)

    async def get(self, share_id: str, owner_email: str | None = None) -> Conversation | None:
        owner = normalize_email(owner_email)

        if owner:
            row = await self._db.prepare(
                "SELECT id, title, owner_email FROM conversations "
                "WHERE external_id = ? AND owner_email = ?"
            ).bind(share_id, owner).first()
        else:
            # Public permalink: newest copy wins, so a story that two readers
            # archived still resolves to something stable rather than 404ing.
            row = await self._db.prepare(
                "SELECT id, title, owner_email FROM conversations "
                "WHERE external_id = ? ORDER BY created_at DESC LIMIT 1"
            ).bind(share_id).first()

        if not row:
            return None

        messages = await self._db.prepare(
            "SELECT role, content FROM messages WHERE conversation_id = ? ORDER BY seq"
        ).bind(row["id"]).all()
        report = await self._db.prepare(
            "SELECT markdown, citations FROM reports WHERE conversation_id = ?"
        ).bind(row["id"]).first()

        citations: list[Citation] = []
        if report and report.get("citations"):
            citations = [
                Citation(index=c["n"], title=c["title"], url=c["url"])
                for c in json.loads(report["citations"])
            ]

        return Conversation(
            share_id=share_id,
            title=row["title"],
            messages=[Message(role=m["role"], content=m["content"]) for m in messages.results],
            report=report["markdown"] if report else None,
            citations=citations,
            owner_email=row["owner_email"],
        )

    @staticmethod
    def _now() -> str:
        # Workers Python exposes the browser-ish Date via `js`; use stdlib as a
        # safe fallback that is good enough for ordering.
        from datetime import datetime, timezone

        return datetime.now(timezone.utc).isoformat()

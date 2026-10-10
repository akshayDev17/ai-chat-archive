"""D1-backed conversation persistence."""

from __future__ import annotations

import json
import uuid

from .models import Citation, Conversation, Message, Source, normalize_email
from .ports import ConversationRepository


def _sources_by_message(rows, message_seq: int) -> list[Source]:
    """The sources belonging to one message, in order.

    Shared by both SQL repositories so the wire shape of a source cannot differ
    between D1 and local development — which is exactly the kind of drift that
    shows up as a bug on deploy only.
    """
    return [
        Source(
            index=row["seq"],
            kind=row["kind"],
            title=row["title"] or "",
            url=row["url"],
            attribution=row["attribution"] or "",
            pub_date=row["pub_date"],
            spans=tuple(tuple(span) for span in json.loads(row["spans"] or "[]")),
        )
        for row in rows
        if row["message_seq"] == message_seq
    ]


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
            await self._db.prepare(
                "DELETE FROM sources WHERE conversation_id = ?"
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

        # Sources are keyed by the message's position, so they are written after
        # the messages and only for the ones that have any.
        source_stmt = self._db.prepare(
            "INSERT INTO sources "
            "(id, conversation_id, message_seq, seq, kind, title, url, attribution, pub_date, spans) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
        )
        source_batch = [
            source_stmt.bind(
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
        ]
        if source_batch:
            await self._db.batch(source_batch)

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
        """The public edition: every filed story, newest first, no owner filter.

        ``owner_email`` is deliberately **not selected**. This row shape is
        served to anonymous visitors, so including the filer's address would
        publish it to anyone who opened the JSON. Provenance stays in the
        database — it keys the upsert and scopes :meth:`list_recent` — and the
        only place it is ever returned to a client is `/api/desk/whoami`, to the
        person it belongs to.
        """
        result = await self._db.prepare(
            "SELECT c.external_id AS id, c.title, c.created_at, c.source, r.markdown "
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

        # Same row shape as list_all, and for the same reason: no owner_email on
        # the wire. The endpoint that calls this already knows who is asking —
        # it returns `owner` at the top level.
        result = await self._db.prepare(
            "SELECT c.external_id AS id, c.title, c.created_at, c.source, r.markdown "
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
            "SELECT seq, role, content FROM messages WHERE conversation_id = ? ORDER BY seq"
        ).bind(row["id"]).all()
        report = await self._db.prepare(
            "SELECT markdown, citations FROM reports WHERE conversation_id = ?"
        ).bind(row["id"]).first()
        source_rows = await self._db.prepare(
            "SELECT message_seq, seq, kind, title, url, attribution, pub_date, spans "
            "FROM sources WHERE conversation_id = ? ORDER BY message_seq, seq"
        ).bind(row["id"]).all()

        citations: list[Citation] = []
        if report and report.get("citations"):
            citations = [
                Citation(index=c["n"], title=c["title"], url=c["url"])
                for c in json.loads(report["citations"])
            ]

        return Conversation(
            share_id=share_id,
            title=row["title"],
            messages=[
                Message(
                    role=m["role"],
                    content=m["content"],
                    sources=_sources_by_message(source_rows.results, m["seq"]),
                )
                for m in messages.results
            ],
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

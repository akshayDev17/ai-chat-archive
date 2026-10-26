"""D1-backed conversation persistence."""

from __future__ import annotations

import json
import uuid

from .models import Citation, Conversation, Media, Message, Source, normalize_email
from .ports import ConversationRepository

# -- media SQL, shared with the SQLite repository -----------------------------
#
# Written once so local development cannot drift from D1, the same reason
# `_sources_by_message` is shared. Each is a single statement; parameters are
# positional and listed beside each one.

#: (conversation_id, message_seq, tag_start, ordinal, content_url, origin_url,
#:  status, r2_key, content_type, id, object_path)
MEDIA_INSERT_SQL = (
    "INSERT INTO media "
    "(conversation_id, message_seq, tag_start, ordinal, content_url, origin_url, "
    "status, r2_key, content_type, id, object_path) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)

#: Where a story's images live in R2: one folder per share link.
MEDIA_ROOT = "chat-archives"


def story_prefix(share_id: str) -> str:
    """The R2 folder holding every image of one story (trailing slash included)."""
    return f"{MEDIA_ROOT}/{share_id}/"

#: (conversation_id,)
MEDIA_FOR_CONVERSATION_SQL = (
    "SELECT id, message_seq, tag_start, ordinal, content_url, origin_url, status, "
    "attempts, last_error, r2_key, content_type, object_path FROM media "
    "WHERE conversation_id = ? ORDER BY message_seq, tag_start, ordinal"
)

#: (owner_email,)
MEDIA_SUMMARY_SQL = (
    "SELECT c.external_id AS id, c.title, COUNT(m.id) AS total, "
    "SUM(CASE WHEN m.status = 'copied' THEN 1 ELSE 0 END) AS copied, "
    "SUM(CASE WHEN m.status = 'failed' THEN 1 ELSE 0 END) AS failed, "
    "SUM(CASE WHEN m.status IN ('pending', 'copying') THEN 1 ELSE 0 END) AS pending "
    "FROM conversations c JOIN media m ON m.conversation_id = c.id "
    "WHERE c.owner_email = ? GROUP BY c.id ORDER BY c.created_at DESC"
)

#: (conversation_id,)
MEDIA_COUNT_FAILED_SQL = "SELECT COUNT(*) AS n FROM media WHERE conversation_id = ? AND status = 'failed'"

#: (conversation_id,)
MEDIA_RETRY_SQL = (
    "UPDATE media SET status = 'pending', attempts = 0, last_error = NULL, claimed_at = NULL "
    "WHERE conversation_id = ? AND status = 'failed'"
)

#: The error a reclaimed row carries: its run was killed mid-copy.
RECLAIM_ERROR = "copy run did not finish (timed out or was killed)"

#: (RECLAIM_ERROR, max_attempts, stale_before)
#: A reclaim counts as an attempt. Otherwise an image whose fetch always hangs
#: kills every run that takes it, is reclaimed as fresh, and loops forever —
#: never reaching `failed`, where the owner's Retry can see it.
MEDIA_RECLAIM_SQL = (
    "UPDATE media SET attempts = attempts + 1, last_error = ?, claimed_at = NULL, "
    "status = CASE WHEN attempts + 1 >= ? THEN 'failed' ELSE 'pending' END "
    "WHERE status = 'copying' AND claimed_at < ?"
)

# One statement, so a claim is atomic: two runs (the cron and a "Copy now")
# can never both take the same row. Oldest story first, then reading order.
_MEDIA_CLAIM_SQL = (
    "UPDATE media SET status = 'copying', claimed_at = ? "
    "WHERE status = 'pending' AND id IN ("
    "SELECT m.id FROM media m JOIN conversations c ON c.id = m.conversation_id "
    "WHERE m.status = 'pending'{scope} "
    "ORDER BY c.created_at, m.message_seq, m.tag_start, m.ordinal LIMIT ?"
    ") RETURNING id, content_url, origin_url, object_path"
)
#: (now, limit)
MEDIA_CLAIM_SQL = _MEDIA_CLAIM_SQL.format(scope="")
#: (now, owner_email, share_id, limit)
MEDIA_CLAIM_ONE_STORY_SQL = _MEDIA_CLAIM_SQL.format(
    scope=" AND c.owner_email = ? AND c.external_id = ?"
)

#: (json.dumps(r2_keys),) — which of these keys rows still point at.
REFERENCED_KEYS_SQL = (
    "SELECT DISTINCT r2_key FROM media WHERE r2_key IN (SELECT value FROM json_each(?))"
)

#: (r2_key, content_type, media_id)
MEDIA_FINISH_SQL = (
    "UPDATE media SET status = 'copied', r2_key = ?, content_type = ?, "
    "last_error = NULL, claimed_at = NULL WHERE id = ?"
)

#: (error, max_attempts, media_id)
MEDIA_FAIL_SQL = (
    "UPDATE media SET attempts = attempts + 1, last_error = ?, claimed_at = NULL, "
    "status = CASE WHEN attempts + 1 >= ? THEN 'failed' ELSE 'pending' END WHERE id = ?"
)


#: (json.dumps(object_paths),) — one JSON parameter, not one per path: D1
#: refuses more than 100 bound parameters, and a long GenUI story can show
#: more images than that.
COPIED_MEDIA_SQL = (
    "SELECT object_path, content_url, r2_key, content_type FROM media "
    "WHERE status = 'copied' AND object_path IN (SELECT value FROM json_each(?))"
)


def media_paths(share_id: str, messages: list[Message]) -> list[list[str]]:
    """Each image's R2 path (without extension), message by message.

    ``chat-archives/<share_id>/<message id>/<NN>``: the story's folder, the
    message's own folder (ChatGPT's id, stable across re-filings), and the
    image's order in that message. A message without an id falls back to its
    position.
    """
    paths = []
    for seq, message in enumerate(messages):
        folder = f"{story_prefix(share_id)}{message.external_id or f'm{seq:03d}'}"
        media = message.media
        ordered = sorted(range(len(media)), key=lambda i: (media[i].tag_start, media[i].ordinal))
        numbers = {i: n for n, i in enumerate(ordered, start=1)}
        paths.append([f"{folder}/{numbers[i]:02d}" for i in range(len(message.media))])
    return paths


def media_rows_for(
    conv_id: str, share_id: str, messages: list[Message], copied: dict
) -> list[tuple]:
    """Insert parameters for a conversation's media, carrying copies over.

    ``copied`` maps object_path → (content_url, r2_key, content_type) for
    images already in R2. An image is carried over only when the same picture
    sits at the same path — re-filing a story, or a second filer of the same
    link — so a copy is never made twice, and never shared between stories.
    """
    rows = []
    for seq, (message, paths) in enumerate(zip(messages, media_paths(share_id, messages))):
        for media, path in zip(message.media, paths):
            url, key, content_type = copied.get(path, (None, None, None))
            if url != media.content_url:
                key = content_type = None
            rows.append(
                (
                    conv_id,
                    seq,
                    media.tag_start,
                    media.ordinal,
                    media.content_url,
                    media.origin_url,
                    "copied" if key else "pending",
                    key,
                    content_type,
                    str(uuid.uuid4()),
                    path,
                )
            )
    return rows


def media_by_message(rows, message_seq: int) -> list[Media]:
    """The media rows belonging to one message, as models (rows are dict-like)."""
    return [
        Media(
            tag_start=row["tag_start"],
            ordinal=row["ordinal"],
            content_url=row["content_url"],
            origin_url=row["origin_url"] or "",
            status=row["status"],
            r2_key=row["r2_key"],
            content_type=row["content_type"],
            attempts=row["attempts"],
            last_error=row["last_error"],
        )
        for row in rows
        if row["message_seq"] == message_seq
    ]


def claimed_row(row) -> dict:
    """What the copier needs of a claimed row."""
    return {key: row[key] for key in ("id", "content_url", "origin_url", "object_path")}


def media_image(row) -> dict:
    """One image as the media page sees it."""
    return {
        key: row[key]
        for key in (
            "id", "message_seq", "tag_start", "ordinal", "content_url",
            "origin_url", "status", "attempts", "last_error", "r2_key", "object_path",
        )
    }


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

        # Read before the delete below: this story's own copied images are among
        # the ones a re-filing must not copy again.
        paths = [p for ps in media_paths(conversation.share_id, conversation.messages) for p in ps]
        copied = {}
        if paths:
            found = await self._db.prepare(COPIED_MEDIA_SQL).bind(json.dumps(paths)).all()
            copied = {
                r["object_path"]: (r["content_url"], r["r2_key"], r["content_type"])
                for r in found.results
            }

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
            await self._db.prepare(
                "DELETE FROM media WHERE conversation_id = ?"
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

        media_stmt = self._db.prepare(MEDIA_INSERT_SQL)
        media_batch = [
            media_stmt.bind(*row)
            for row in media_rows_for(conv_id, conversation.share_id, conversation.messages, copied)
        ]
        if media_batch:
            await self._db.batch(media_batch)

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

    async def delete(self, share_id: str, owner_email: str) -> bool:
        owner = normalize_email(owner_email)
        if not owner:
            raise ValueError("delete requires an owner_email.")

        row = await self._db.prepare(
            "SELECT id FROM conversations WHERE owner_email = ? AND external_id = ?"
        ).bind(owner, share_id).first()
        if not row:
            return False

        # One batch, because a D1 batch is a transaction: a retraction that
        # failed halfway would leave a story with no transcript in the edition.
        # Children first — nothing cascades (see migrations/0001_init.sql).
        conv_id = row["id"]
        await self._db.batch(
            [
                self._db.prepare(f"DELETE FROM {table} WHERE conversation_id = ?").bind(conv_id)
                for table in ("media", "sources", "reports", "messages")
            ]
            + [self._db.prepare("DELETE FROM conversations WHERE id = ?").bind(conv_id)]
        )
        return True

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
        media_rows = await self._db.prepare(MEDIA_FOR_CONVERSATION_SQL).bind(row["id"]).all()

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
                    media=media_by_message(media_rows.results, m["seq"]),
                )
                for m in messages.results
            ],
            report=report["markdown"] if report else None,
            citations=citations,
            owner_email=row["owner_email"],
        )

    # -- media ----------------------------------------------------------------

    async def _owned_conversation(self, share_id: str, owner_email: str):
        owner = normalize_email(owner_email)
        if not owner:
            raise ValueError("media operations require an owner_email.")
        return await self._db.prepare(
            "SELECT id, title FROM conversations WHERE owner_email = ? AND external_id = ?"
        ).bind(owner, share_id).first()

    async def media_summary(self, owner_email: str) -> list[dict]:
        owner = normalize_email(owner_email)
        if not owner:
            raise ValueError("media_summary requires an owner_email.")
        result = await self._db.prepare(MEDIA_SUMMARY_SQL).bind(owner).all()
        return [dict(r) for r in result.results]

    async def media_for(self, share_id: str, owner_email: str) -> dict | None:
        conv = await self._owned_conversation(share_id, owner_email)
        if not conv:
            return None
        rows = await self._db.prepare(MEDIA_FOR_CONVERSATION_SQL).bind(conv["id"]).all()
        return {
            "id": share_id,
            "title": conv["title"],
            "images": [media_image(r) for r in rows.results],
        }

    async def retry_failed_media(self, share_id: str, owner_email: str) -> int | None:
        conv = await self._owned_conversation(share_id, owner_email)
        if not conv:
            return None
        failed = await self._db.prepare(MEDIA_COUNT_FAILED_SQL).bind(conv["id"]).first()
        await self._db.prepare(MEDIA_RETRY_SQL).bind(conv["id"]).run()
        return failed["n"]

    async def claim_media(
        self,
        limit: int,
        now: str,
        stale_before: str,
        only: tuple[str, str] | None = None,
        max_attempts: int = 3,
    ) -> list[dict]:
        await self._db.prepare(MEDIA_RECLAIM_SQL).bind(RECLAIM_ERROR, max_attempts, stale_before).run()
        if only:
            owner, share_id = normalize_email(only[0]), only[1]
            stmt = self._db.prepare(MEDIA_CLAIM_ONE_STORY_SQL).bind(now, owner, share_id, limit)
        else:
            stmt = self._db.prepare(MEDIA_CLAIM_SQL).bind(now, limit)
        result = await stmt.all()
        return [claimed_row(r) for r in result.results]

    async def finish_media(self, media_id: str, r2_key: str, content_type: str) -> None:
        await self._db.prepare(MEDIA_FINISH_SQL).bind(r2_key, content_type, media_id).run()

    async def fail_media(self, media_id: str, error: str, max_attempts: int = 3) -> None:
        await self._db.prepare(MEDIA_FAIL_SQL).bind(error, max_attempts, media_id).run()

    async def referenced_keys(self, keys: list[str]) -> set[str]:
        if not keys:
            return set()
        result = await self._db.prepare(REFERENCED_KEYS_SQL).bind(json.dumps(keys)).all()
        return {r["r2_key"] for r in result.results}

    @staticmethod
    def _now() -> str:
        # Workers Python exposes the browser-ish Date via `js`; use stdlib as a
        # safe fallback that is good enough for ordering.
        from datetime import datetime, timezone

        return datetime.now(timezone.utc).isoformat()

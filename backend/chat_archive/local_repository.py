"""In-memory implementation of ConversationRepository.

A second implementation of the same port as D1ConversationRepository — the
Open/Closed seam made concrete: the Worker injects D1, tests inject this, and
the use case never knows the difference.

It doubles as the reference for the ownership contract: the in-memory key is
``(owner_email, share_id)`` and ``list_recent`` refuses to run unscoped, so a
test that forgets the owner fails loudly here instead of passing locally and
leaking in production.

Media rows live in a flat list of dicts shaped like the SQL rows, so the shared
helpers in ``repository.py`` read them unchanged.
"""

from __future__ import annotations

from dataclasses import replace

from .models import Conversation, normalize_email
from .ports import ConversationRepository
from .repository import RECLAIM_ERROR, claimed_row, media_by_message, media_image, media_paths, media_rows_for

#: Column order of `media_rows_for`'s tuples (the INSERT's parameter order).
_MEDIA_COLUMNS = (
    "key", "message_seq", "tag_start", "ordinal", "content_url", "origin_url",
    "status", "r2_key", "content_type", "id", "object_path",
)


class MemoryConversationRepository(ConversationRepository):
    def __init__(self):
        #: keyed by (owner_email, share_id) — the same pair the D1 unique index uses.
        self._by_key: dict[tuple[str, str], Conversation] = {}
        self._media: list[dict] = []

    async def upsert(self, conversation: Conversation) -> None:
        owner = normalize_email(conversation.owner_email)
        if not owner:
            raise ValueError("Refusing to store a conversation with no owner_email.")
        key = (owner, conversation.share_id)

        paths = {p for ps in media_paths(conversation.share_id, conversation.messages) for p in ps}
        copied = {
            row["object_path"]: (row["content_url"], row["r2_key"], row["content_type"])
            for row in self._media
            if row["status"] == "copied" and row["object_path"] in paths
        }
        self._media = [row for row in self._media if row["key"] != key]
        for values in media_rows_for(key, conversation.share_id, conversation.messages, copied):
            self._media.append(
                {**dict(zip(_MEDIA_COLUMNS, values)), "attempts": 0, "last_error": None, "claimed_at": None}
            )
        self._by_key[key] = conversation

    async def get(self, share_id: str, owner_email: str | None = None) -> Conversation | None:
        owner = normalize_email(owner_email)
        if owner:
            key = (owner, share_id) if (owner, share_id) in self._by_key else None
        else:
            # Public permalink: most recently inserted copy wins.
            key = next(
                (k for k in reversed(list(self._by_key)) if k[1] == share_id), None
            )
        if key is None:
            return None
        conversation = self._by_key[key]
        rows = self._rows_for(key)
        return replace(
            conversation,
            messages=[
                replace(message, media=media_by_message(rows, seq))
                for seq, message in enumerate(conversation.messages)
            ],
        )

    async def delete(self, share_id: str, owner_email: str) -> bool:
        owner = normalize_email(owner_email)
        if not owner:
            raise ValueError("delete requires an owner_email.")
        self._media = [row for row in self._media if row["key"] != (owner, share_id)]
        return self._by_key.pop((owner, share_id), None) is not None

    async def list_all(self, limit: int = 200) -> list[dict]:
        """The public edition: every filed story, newest first, no owner filter."""
        return self._rows(list(reversed(list(self._by_key.items()))), limit)

    async def list_recent(self, owner_email: str, limit: int = 200) -> list[dict]:
        owner = normalize_email(owner_email)
        if not owner:
            raise ValueError("list_recent requires an owner_email.")

        matching = [
            (key, conversation)
            for key, conversation in reversed(list(self._by_key.items()))
            if key[0] == owner
        ]
        return self._rows(matching, limit)

    # -- media ----------------------------------------------------------------

    def _rows_for(self, key: tuple[str, str]) -> list[dict]:
        return sorted(
            (row for row in self._media if row["key"] == key),
            key=lambda r: (r["message_seq"], r["tag_start"], r["ordinal"]),
        )

    def _owned_key(self, share_id: str, owner_email: str) -> tuple[str, str] | None:
        owner = normalize_email(owner_email)
        if not owner:
            raise ValueError("media operations require an owner_email.")
        return (owner, share_id) if (owner, share_id) in self._by_key else None

    async def media_summary(self, owner_email: str) -> list[dict]:
        owner = normalize_email(owner_email)
        if not owner:
            raise ValueError("media_summary requires an owner_email.")
        summary = []
        for key in reversed(list(self._by_key)):
            rows = self._rows_for(key)
            if key[0] != owner or not rows:
                continue
            statuses = [r["status"] for r in rows]
            summary.append(
                {
                    "id": key[1],
                    "title": self._by_key[key].title,
                    "total": len(rows),
                    "copied": statuses.count("copied"),
                    "failed": statuses.count("failed"),
                    "pending": statuses.count("pending") + statuses.count("copying"),
                }
            )
        return summary

    async def media_for(self, share_id: str, owner_email: str) -> dict | None:
        key = self._owned_key(share_id, owner_email)
        if key is None:
            return None
        return {
            "id": share_id,
            "title": self._by_key[key].title,
            "images": [media_image(r) for r in self._rows_for(key)],
        }

    async def retry_failed_media(self, share_id: str, owner_email: str) -> int | None:
        key = self._owned_key(share_id, owner_email)
        if key is None:
            return None
        failed = [r for r in self._media if r["key"] == key and r["status"] == "failed"]
        for row in failed:
            row.update(status="pending", attempts=0, last_error=None, claimed_at=None)
        return len(failed)

    async def claim_media(
        self,
        limit: int,
        now: str,
        stale_before: str,
        only: tuple[str, str] | None = None,
        max_attempts: int = 3,
    ) -> list[dict]:
        for row in self._media:
            if row["status"] == "copying" and (row["claimed_at"] or "") < stale_before:
                attempts = row["attempts"] + 1
                row.update(
                    attempts=attempts,
                    last_error=RECLAIM_ERROR,
                    claimed_at=None,
                    status="failed" if attempts >= max_attempts else "pending",
                )

        scope = (normalize_email(only[0]), only[1]) if only else None
        filing_order = {key: i for i, key in enumerate(self._by_key)}
        candidates = sorted(
            (
                row for row in self._media
                if row["status"] == "pending" and (scope is None or row["key"] == scope)
            ),
            key=lambda r: (filing_order[r["key"]], r["message_seq"], r["tag_start"], r["ordinal"]),
        )[:limit]
        for row in candidates:
            row.update(status="copying", claimed_at=now)
        return [claimed_row(r) for r in candidates]

    async def finish_media(self, media_id: str, r2_key: str, content_type: str) -> None:
        for row in self._media:
            if row["id"] == media_id:
                row.update(
                    status="copied", r2_key=r2_key, content_type=content_type,
                    last_error=None, claimed_at=None,
                )

    async def fail_media(self, media_id: str, error: str, max_attempts: int = 3) -> None:
        for row in self._media:
            if row["id"] == media_id:
                attempts = row["attempts"] + 1
                row.update(
                    attempts=attempts,
                    last_error=error,
                    claimed_at=None,
                    status="failed" if attempts >= max_attempts else "pending",
                )

    @staticmethod
    def _rows(items, limit: int) -> list[dict]:
        # No owner_email in the row shape — these rows are served to anonymous
        # visitors on the edition endpoint. See the D1 implementation.
        rows: list[dict] = []
        for (_, _unused), conversation in items:
            rows.append(
                {
                    "id": conversation.share_id,
                    "title": conversation.title,
                    "created_at": "",
                    "source": "chatgpt",
                    "markdown": conversation.report,
                }
            )
            if len(rows) >= limit:
                break
        return rows

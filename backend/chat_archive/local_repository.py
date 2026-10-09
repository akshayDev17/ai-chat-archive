"""In-memory implementation of ConversationRepository.

A second implementation of the same port as D1ConversationRepository — the
Open/Closed seam made concrete: the Worker injects D1, tests inject this, and
the use case never knows the difference.

It doubles as the reference for the ownership contract: the in-memory key is
``(owner_email, share_id)`` and ``list_recent`` refuses to run unscoped, so a
test that forgets the owner fails loudly here instead of passing locally and
leaking in production.
"""

from __future__ import annotations

from .models import Conversation, normalize_email
from .ports import ConversationRepository


class MemoryConversationRepository(ConversationRepository):
    def __init__(self):
        #: keyed by (owner_email, share_id) — the same pair the D1 unique index uses.
        self._by_key: dict[tuple[str, str], Conversation] = {}

    async def upsert(self, conversation: Conversation) -> None:
        owner = normalize_email(conversation.owner_email)
        if not owner:
            raise ValueError("Refusing to store a conversation with no owner_email.")
        self._by_key[(owner, conversation.share_id)] = conversation

    async def get(self, share_id: str, owner_email: str | None = None) -> Conversation | None:
        owner = normalize_email(owner_email)
        if owner:
            return self._by_key.get((owner, share_id))
        # Public permalink: most recently inserted copy wins.
        for (_, stored_share_id), conversation in reversed(list(self._by_key.items())):
            if stored_share_id == share_id:
                return conversation
        return None

    async def list_recent(self, owner_email: str, limit: int = 200) -> list[dict]:
        owner = normalize_email(owner_email)
        if not owner:
            raise ValueError("list_recent requires an owner_email.")

        rows: list[dict] = []
        for (stored_owner, _), conversation in reversed(list(self._by_key.items())):
            if stored_owner != owner:
                continue
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

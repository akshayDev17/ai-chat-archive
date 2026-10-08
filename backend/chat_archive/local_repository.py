"""In-memory implementation of ConversationRepository for local development.

This is a second implementation of the same port as D1ConversationRepository —
the Open/Closed seam made concrete: the Worker injects D1, the local runner
injects this, and the use case (ShareService) never knows the difference.
"""

from __future__ import annotations

from .models import Conversation
from .ports import ConversationRepository


class MemoryConversationRepository(ConversationRepository):
    def __init__(self):
        self._by_share_id: dict[str, Conversation] = {}

    async def upsert(self, conversation: Conversation) -> None:
        self._by_share_id[conversation.share_id] = conversation

    async def get(self, share_id: str) -> Conversation | None:
        return self._by_share_id.get(share_id)

    async def list_recent(self, limit: int = 200) -> list[dict]:
        rows: list[dict] = []
        for conversation in reversed(list(self._by_share_id.values())):
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

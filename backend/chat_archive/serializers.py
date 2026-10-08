"""Wire serialization shared by the Worker and the local dev server."""

from __future__ import annotations

from .models import Conversation


def conversation_detail(conversation: Conversation) -> dict:
    """The full payload the reader view needs: report + transcript + citations."""
    return {
        "id": conversation.share_id,
        "title": conversation.title,
        "source": "chatgpt",
        "report": conversation.report,
        "citations": [
            {"n": c.index, "title": c.title, "url": c.url} for c in conversation.citations
        ],
        "messages": [
            {"role": m.role, "content": m.content} for m in conversation.messages
        ],
    }

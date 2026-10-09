"""Ports (interfaces) for the backend.

Every high-level use case depends on these abstractions, never on a concrete
vendor implementation. That is the Dependency-Inversion + Open/Closed seam:
adding a new source (Gemini, Claude, Elicit) means adding a new implementation
of these three ports — no existing code changes.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from .models import Conversation


class ShareFetcher(ABC):
    """Fetch the raw share page for a given vendor share id."""

    @abstractmethod
    async def fetch(self, share_id: str) -> str:
        """Return the raw HTML (or wire format) of the shared conversation."""


class PayloadDecoder(ABC):
    """Decode a vendor's serialized share payload into a plain conversation dict."""

    @abstractmethod
    def decode(self, raw: str) -> dict:
        """Decode raw wire content -> a plain, vendor-agnostic dict."""


class ConversationParser(ABC):
    """Parse a decoded conversation dict into domain models."""

    @abstractmethod
    def parse(self, share_id: str, raw: dict) -> Conversation:
        """Build a :class:`Conversation` from the decoded dict."""


class ConversationRepository(ABC):
    """Persistence boundary for conversations.

    The port is *owner-aware*: every read that returns a shelf of stories is
    scoped to one reader's email, so a missing ``owner_email`` filter is a
    signature error rather than a silent data leak. Only :meth:`get` may run
    unscoped, because a single story is public once archived.
    """

    @abstractmethod
    async def upsert(self, conversation: Conversation) -> None:
        """Insert or replace a conversation, its messages and its report.

        The conversation's ``owner_email`` decides which shelf it lands on; an
        empty owner is rejected by implementations rather than stored.
        """

    @abstractmethod
    async def list_recent(self, owner_email: str, limit: int = 200) -> list[dict]:
        """Return ``owner_email``'s recent conversations, newest first.

        This is the only way to enumerate stories, so it is always scoped.
        """

    @abstractmethod
    async def get(self, share_id: str, owner_email: str | None = None) -> Conversation | None:
        """Return one conversation (messages + report), or None if absent.

        With ``owner_email`` set, only that reader's copy matches. With it left
        as ``None`` the lookup is the *public* one: a story reachable by its
        permalink regardless of who archived it.
        """

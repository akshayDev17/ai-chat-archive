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
    """Persistence boundary for conversations."""

    @abstractmethod
    async def upsert(self, conversation: Conversation) -> None:
        """Insert or replace a conversation and its messages/report."""

    @abstractmethod
    async def list_recent(self, limit: int = 200) -> list[dict]:
        """Return recent conversations (with their report markdown)."""

    @abstractmethod
    async def get(self, share_id: str) -> Conversation | None:
        """Return one conversation (messages + report), or None if absent."""

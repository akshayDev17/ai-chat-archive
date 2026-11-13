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

    Ownership is *provenance*, not access control. Every row records who filed
    it, and that is used to show a byline and to answer "what did I file?" — but
    the edition is public, so the primary read is deliberately unscoped.

    The port therefore offers two listings rather than one, and the names say
    which is which: :meth:`list_all` is the public edition,
    :meth:`list_recent` is one reader's own filings. There is no overload where
    a missing argument silently widens the query.

    Visibility *is* access control. A ``private`` conversation is returned only
    by the methods that take its owner's email; every unscoped read
    (:meth:`list_all`, :meth:`get` without an owner, :meth:`media_key_readable`
    without an owner) leaves it out.
    """

    @abstractmethod
    async def upsert(self, conversation: Conversation) -> None:
        """Insert or replace a conversation, its messages and its report.

        The conversation's ``owner_email`` records who filed it; an empty owner
        is rejected by implementations rather than stored.

        Its ``visibility`` is stored as given. ``None`` means the filer did not
        say: a new story is stored ``public``, and a story filed again keeps
        the visibility it had.
        """

    @abstractmethod
    async def list_all(self, limit: int = 200) -> list[dict]:
        """Every public conversation, newest first — the public edition.

        Deliberately not filtered by owner: the front page is the newspaper, and
        everyone reads the same edition. Private conversations are left out.
        """

    @abstractmethod
    async def list_recent(self, owner_email: str, limit: int = 200) -> list[dict]:
        """One reader's own filings, newest first — what the copy desk shows back.

        Public and private alike: each row carries its ``visibility``.
        """

    @abstractmethod
    async def set_visibility(self, share_id: str, owner_email: str, visibility: str) -> bool:
        """Make the caller's copy of a story ``public`` or ``private``.

        Owner-scoped like :meth:`delete`: returns False when this reader has no
        copy, and another filer's copy of the same link is untouched. Raises on
        an empty owner or a visibility that is neither.
        """

    @abstractmethod
    async def is_filed(self, share_id: str) -> bool:
        """Whether anyone still has a copy of this story, public or private.

        Not a read: it decides whether the story's shared image folder is still
        in use, so a private copy counts.
        """

    @abstractmethod
    async def delete(self, share_id: str, owner_email: str) -> bool:
        """Retract one reader's copy of a story, with its messages, report and sources.

        Ownership *is* an access boundary here: you can only take back what you
        filed. Another filer's copy of the same share link stays as it was.
        Returns False when this reader has no copy to retract, and
        raises on an empty owner rather than deleting unscoped.
        """

    # -- media: images from GenUI replies, and the job of copying them ------
    #
    # `upsert` writes a message's `media` as rows, each with its R2 path
    # `chat-archives/<share_id>/<message id>/<NN>` (an image already copied to
    # that path arrives `copied`, so re-filing never re-copies),
    # `get` returns them on each message, and `delete` removes them. The rows are
    # the copy job itself; the methods below are its transitions.

    @abstractmethod
    async def media_summary(self, owner_email: str) -> list[dict]:
        """Per filing with images, newest first:
        ``{"id", "title", "visibility", "total", "copied", "failed", "pending"}``.

        ``pending`` counts rows still ``pending`` or ``copying``.
        """

    @abstractmethod
    async def media_for(self, share_id: str, owner_email: str) -> dict | None:
        """``{"id", "title", "visibility", "images": [...]}`` for one of the caller's stories.

        ``None`` when the caller has not filed it. Each image:
        ``{"id", "message_seq", "tag_start", "ordinal", "content_url",
        "origin_url", "status", "attempts", "last_error", "r2_key", "object_path"}``.
        """

    @abstractmethod
    async def retry_failed_media(self, share_id: str, owner_email: str) -> int | None:
        """Put the caller's ``failed`` images back to ``pending`` with no attempts.

        Returns how many were reset, or ``None`` when the caller has not filed it.
        """

    @abstractmethod
    async def claim_media(
        self,
        limit: int,
        now: str,
        stale_before: str,
        only: tuple[str, str] | None = None,
        max_attempts: int = 3,
    ) -> list[dict]:
        """Take up to ``limit`` pending images for copying:
        ``[{"id", "content_url", "origin_url", "object_path"}]``.

        First reclaims rows left ``copying`` since before ``stale_before`` (their
        run was killed) — counting that as a failed attempt, so an image that
        always hangs ends ``failed`` after ``max_attempts`` instead of looping. Claimed rows become ``copying`` with ``claimed_at =
        now``. ``only=(owner_email, share_id)`` restricts it to one story — the
        media page's "Copy now". Timestamps are ISO-8601 UTC strings.
        """

    @abstractmethod
    async def finish_media(self, media_id: str, r2_key: str, content_type: str) -> None:
        """Mark one image ``copied``, stored at ``r2_key``."""

    @abstractmethod
    async def fail_media(self, media_id: str, error: str, max_attempts: int = 3) -> None:
        """Count a failed attempt: back to ``pending``, or ``failed`` at ``max_attempts``."""

    @abstractmethod
    async def referenced_keys(self, keys: list[str]) -> set[str]:
        """Which of these R2 keys any image row still points at."""

    @abstractmethod
    async def media_key_readable(self, key: str, owner_email: str | None = None) -> bool:
        """Whether the image stored at ``key`` may be served.

        With ``owner_email`` left as ``None`` the question is the *public* one:
        true only when a public story shows the image. With it set, true only
        when one of that reader's own stories shows it, public or private. A
        key no story points at is readable by nobody.
        """

    @abstractmethod
    async def get(self, share_id: str, owner_email: str | None = None) -> Conversation | None:
        """Return one conversation (messages + report), or None if absent.

        With ``owner_email`` set, only that reader's copy matches, public or
        private. With it left as ``None`` the lookup is the *public* one: the
        newest public copy of the story, regardless of who filed it — a story
        whose every copy is private is absent.
        """


class MediaStore(ABC):
    """Where copied images live: R2 in the Worker, a directory locally."""

    @abstractmethod
    async def put(
        self, key: str, data: bytes, content_type: str, metadata: dict[str, str] | None = None
    ) -> None:
        """Store ``data`` under ``key``, with optional string ``metadata`` (provenance)."""

    @abstractmethod
    async def delete_prefix(self, prefix: str) -> int:
        """Delete every object whose key starts with ``prefix``; returns how many."""

    @abstractmethod
    async def delete_keys(self, keys: list[str]) -> int:
        """Delete exactly these objects; returns how many were asked for."""

    @abstractmethod
    async def get(self, key: str) -> tuple[bytes, str] | None:
        """``(bytes, content_type)``, or ``None`` when nothing is stored there."""


class ImageFetcher(ABC):
    """Fetch one image's bytes from where ChatGPT hosts it."""

    @abstractmethod
    async def fetch(self, url: str) -> tuple[bytes, str]:
        """``(bytes, content_type)``; raises ``FetchError`` on any failure."""

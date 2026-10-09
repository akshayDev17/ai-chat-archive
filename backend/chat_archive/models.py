"""Domain models shared across the backend.

Ownership
---------
An archive belongs to exactly one reader, identified by their email address.
That fact lives on :class:`Conversation` as ``owner_email`` because a stored
conversation is meaningless without knowing whose shelf it sits on: two readers
may archive the very same share link and must never see each other's shelf.

A parser never sets it — a public ChatGPT payload has no idea who is importing
it. ``owner_email`` is stamped at ingest time by the composition root, the only
layer that knows the authenticated identity. See :meth:`Conversation.owned_by`.

Articles themselves are public once archived: anyone holding a share id can
read the story at ``/chat-archives/<share-id>``. Ownership governs the
*listing*, not the *reading*.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace


@dataclass(frozen=True)
class Message:
    role: str
    content: str


@dataclass(frozen=True)
class Citation:
    index: int
    title: str
    url: str


@dataclass(frozen=True)
class Conversation:
    share_id: str
    title: str
    messages: list[Message] = field(default_factory=list)
    report: str | None = None
    citations: list[Citation] = field(default_factory=list)
    #: The reader whose archive holds this conversation. Empty until an
    #: authenticated ingest stamps it — see :meth:`owned_by`.
    owner_email: str = ""

    def owned_by(self, email: str) -> "Conversation":
        """Return a copy of this conversation filed under ``email``.

        Parsers build ownerless conversations from a public share payload; the
        composition root calls this with the authenticated identity just before
        the repository write. Keeping it here — rather than in the parser — is
        what lets the parser stay a pure payload -> domain function.
        """
        return replace(self, owner_email=normalize_email(email))

    def is_owned_by(self, email: str) -> bool:
        """Whether ``email`` owns this conversation."""
        return bool(self.owner_email) and self.owner_email == normalize_email(email)


def normalize_email(email: str | None) -> str:
    """Canonical form of an email used as an archive key.

    Emails are compared as identity keys, so ``Akshay@Example.com`` and
    ``akshay@example.com`` must not create two shelves. Lower-casing and
    stripping is deliberately the *only* transformation: no plus-address or
    dot-folding, because those rules are provider-specific and silently merging
    two distinct mailboxes is worse than treating them as distinct.
    """
    return (email or "").strip().lower()

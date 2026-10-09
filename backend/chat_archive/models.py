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
class Source:
    """A web source cited *inside* one message.

    ChatGPT stores these in ``message.metadata.content_references``, not in the
    message text. The text only carries an opaque private-use token where the
    citation belongs:

        …the agreement was "not imminent."\\ue200cite\\ue202turn0search1\\ue201

    and the reference tells you what that token means — ``matched_text`` for the
    token itself, ``start_idx``/``end_idx`` for where it sits, and ``items`` for
    the title and URL. Without joining those two halves the token is noise, so
    the app used to delete it; with them it becomes a source link.

    One source can be cited repeatedly, so it owns a list of spans rather than a
    single offset. Collapsing repeats into one span would leave the second
    inline marker with nothing to point at, and it would silently disappear with
    the token stripper.

    ``url`` is the canonical URL: ChatGPT's own ``?utm_source=chatgpt.com``
    tracking parameter is removed, and the duplicate it creates in
    ``safe_urls`` collapses into the same source.
    """

    #: 1-based position within the message, by order of first appearance.
    index: int
    title: str
    url: str
    attribution: str = ""
    pub_date: int | None = None
    #: ``cite`` for an inline citation pill, ``link`` for a link the model wrote.
    kind: str = "cite"
    #: ``(start, end)`` offsets into the message text, in order, deduplicated.
    #: Empty when no span could be trusted — the source is still listed.
    spans: tuple[tuple[int, int], ...] = ()


@dataclass(frozen=True)
class Message:
    role: str
    content: str
    sources: list[Source] = field(default_factory=list)


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

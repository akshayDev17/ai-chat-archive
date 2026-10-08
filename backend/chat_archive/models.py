"""Domain models shared across the backend."""

from __future__ import annotations

from dataclasses import dataclass, field


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

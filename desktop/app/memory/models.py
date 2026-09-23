"""Plain data models shared by the memory subsystem."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


@dataclass
class Message:
    role: str  # "user" | "assistant" | "system"
    content: str
    session_id: str = ""
    created_at: str = field(default_factory=now_iso)
    id: int | None = None


@dataclass
class Memory:
    """A durable fact about the user, e.g. 'User works as a teacher'."""
    content: str
    category: str = "fact"  # fact | preference | goal | person | event
    importance: int = 3  # 1..5
    embedding: list[float] | None = None
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)
    id: int | None = None


@dataclass
class Goal:
    title: str
    status: str = "active"  # active | done | dropped
    notes: str = ""
    created_at: str = field(default_factory=now_iso)
    id: int | None = None


@dataclass
class CheckIn:
    mood: str
    notes: str = ""
    created_at: str = field(default_factory=now_iso)
    id: int | None = None

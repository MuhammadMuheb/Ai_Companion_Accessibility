"""SQLite persistence: conversation history, long-term memories, goals, check-ins, reminders."""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

from app.config import get_config
from app.memory.models import CheckIn, Goal, Memory, Message, now_iso

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, id);

CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'fact',
    importance INTEGER NOT NULL DEFAULT 3,
    embedding TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS goals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS checkins (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mood TEXT NOT NULL,
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS memory_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,              -- 'memory' | 'goal'
    item_id INTEGER NOT NULL,
    action TEXT NOT NULL,            -- 'updated' | 'forgotten' | 'removed' | 'done'
    old_content TEXT,
    new_content TEXT,
    changed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
    due_at TEXT NOT NULL,
    done INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
"""


class MemoryDB:
    def __init__(self, path: str | Path | None = None):
        cfg = get_config()
        self.path = Path(path) if path else cfg.path(cfg.storage.memory_db)
        self._lock = threading.Lock()  # scheduler jobs run on other threads
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def _exec(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self.conn.execute(sql, params)
            self.conn.commit()
            return cur

    def _query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self.conn.execute(sql, params).fetchall()

    # ---- messages -----------------------------------------------------
    def add_message(self, msg: Message) -> int:
        cur = self._exec(
            "INSERT INTO messages(session_id, role, content, created_at) VALUES (?,?,?,?)",
            (msg.session_id, msg.role, msg.content, msg.created_at),
        )
        return cur.lastrowid

    def recent_messages(self, session_id: str | None = None, limit: int = 20) -> list[Message]:
        if session_id:
            rows = self._query(
                "SELECT * FROM messages WHERE session_id=? ORDER BY id DESC LIMIT ?", (session_id, limit)
            )
        else:
            rows = self._query("SELECT * FROM messages ORDER BY id DESC LIMIT ?", (limit,))
        return [Message(r["role"], r["content"], r["session_id"], r["created_at"], r["id"]) for r in reversed(rows)]

    # ---- memories -----------------------------------------------------
    def add_memory(self, mem: Memory) -> int:
        cur = self._exec(
            "INSERT INTO memories(content, category, importance, embedding, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?)",
            (mem.content, mem.category, mem.importance, _dump(mem.embedding), mem.created_at, mem.updated_at),
        )
        return cur.lastrowid

    def update_memory(self, mem_id: int, content: str, embedding: list[float] | None, importance: int | None = None):
        if importance is None:
            self._exec(
                "UPDATE memories SET content=?, embedding=?, updated_at=? WHERE id=?",
                (content, _dump(embedding), now_iso(), mem_id),
            )
        else:
            self._exec(
                "UPDATE memories SET content=?, embedding=?, importance=?, updated_at=? WHERE id=?",
                (content, _dump(embedding), importance, now_iso(), mem_id),
            )

    def delete_memory(self, mem_id: int) -> bool:
        return self._exec("DELETE FROM memories WHERE id=?", (mem_id,)).rowcount > 0

    def all_memories(self) -> list[Memory]:
        rows = self._query("SELECT * FROM memories ORDER BY importance DESC, updated_at DESC")
        return [_row_to_memory(r) for r in rows]

    # ---- goals --------------------------------------------------------
    def add_goal(self, title: str, notes: str = "") -> int:
        return self._exec(
            "INSERT INTO goals(title, notes, created_at) VALUES (?,?,?)", (title, notes, now_iso())
        ).lastrowid

    def goals(self, status: str | None = "active") -> list[Goal]:
        if status:
            rows = self._query("SELECT * FROM goals WHERE status=? ORDER BY id", (status,))
        else:
            rows = self._query("SELECT * FROM goals ORDER BY id")
        return [Goal(r["title"], r["status"], r["notes"], r["created_at"], r["id"]) for r in rows]

    def set_goal_status(self, goal_id: int, status: str) -> bool:
        return self._exec("UPDATE goals SET status=? WHERE id=?", (status, goal_id)).rowcount > 0

    # ---- check-ins ----------------------------------------------------
    def add_checkin(self, mood: str, notes: str = "") -> int:
        return self._exec(
            "INSERT INTO checkins(mood, notes, created_at) VALUES (?,?,?)", (mood, notes, now_iso())
        ).lastrowid

    def recent_checkins(self, limit: int = 7) -> list[CheckIn]:
        rows = self._query("SELECT * FROM checkins ORDER BY id DESC LIMIT ?", (limit,))
        return [CheckIn(r["mood"], r["notes"], r["created_at"], r["id"]) for r in rows]

    # ---- reminders ----------------------------------------------------
    def add_reminder(self, text: str, due_at: str) -> int:
        return self._exec(
            "INSERT INTO reminders(text, due_at, created_at) VALUES (?,?,?)", (text, due_at, now_iso())
        ).lastrowid

    def due_reminders(self, now: str) -> list[sqlite3.Row]:
        return self._query("SELECT * FROM reminders WHERE done=0 AND due_at<=? ORDER BY due_at", (now,))

    def pending_reminders(self) -> list[sqlite3.Row]:
        return self._query("SELECT * FROM reminders WHERE done=0 ORDER BY due_at")

    def complete_reminder(self, reminder_id: int) -> None:
        self._exec("UPDATE reminders SET done=1 WHERE id=?", (reminder_id,))

    # ---- change history (so "what was my old goal?" can be answered) ----------------
    def add_history(self, kind: str, item_id: int, action: str, old: str | None, new: str | None) -> None:
        self._exec("INSERT INTO memory_history(kind, item_id, action, old_content, new_content, changed_at) "
                   "VALUES (?,?,?,?,?,?)", (kind, item_id, action, old, new, now_iso()))

    def history(self, kind: str | None = None, limit: int = 20) -> list[sqlite3.Row]:
        if kind:
            return self._query("SELECT * FROM memory_history WHERE kind=? ORDER BY id DESC LIMIT ?", (kind, limit))
        return self._query("SELECT * FROM memory_history ORDER BY id DESC LIMIT ?", (limit,))

    def update_goal(self, goal_id: int, title: str) -> bool:
        return self._exec("UPDATE goals SET title=? WHERE id=?", (title, goal_id)).rowcount > 0

    def get_memory(self, mem_id: int) -> Memory | None:
        rows = self._query("SELECT * FROM memories WHERE id=?", (mem_id,))
        return _row_to_memory(rows[0]) if rows else None

    def close(self) -> None:
        self.conn.close()


def _dump(vec: list[float] | None) -> str | None:
    return json.dumps(vec) if vec else None


def _row_to_memory(r: sqlite3.Row) -> Memory:
    emb = json.loads(r["embedding"]) if r["embedding"] else None
    return Memory(r["content"], r["category"], r["importance"], emb, r["created_at"], r["updated_at"], r["id"])


_db: MemoryDB | None = None


def get_db() -> MemoryDB:
    global _db
    if _db is None:
        _db = MemoryDB()
    return _db

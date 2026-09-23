"""Remembers what happened during the day (commands, results, active window) so the
user can ask 'what did I just do?' or 'what did you say?'."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from app.config import get_config
from app.mentor.focus import active_window_title


class ContextLogger:
    def __init__(self):
        cfg = get_config()
        self.enabled = cfg.accessibility.context_memory_enabled
        self.folder = cfg.dir("data/conversations")
        self.last_reply = ""

    def _file(self) -> Path:
        return self.folder / f"context-{datetime.now():%Y-%m-%d}.jsonl"

    def log(self, user_said: str, intent: str, result: str) -> None:
        self.last_reply = result
        if not self.enabled:
            return
        entry = {
            "time": datetime.now().isoformat(timespec="seconds"),
            "said": user_said,
            "intent": intent,
            "result": result[:1000],
            "window": active_window_title(),
        }
        with self._file().open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def recent(self, n: int = 5) -> list[dict]:
        path = self._file()
        if not path.exists():
            return []
        lines = path.read_text(encoding="utf-8").splitlines()[-n:]
        return [json.loads(line) for line in lines if line.strip()]

    def describe_recent(self, n: int = 5) -> str:
        items = [e for e in self.recent(n + 1) if e["intent"] != "recent_activity"][-n:]
        if not items:
            return "Nothing recorded yet today."
        parts = [f"At {e['time'][11:16]} you asked '{e['said']}'" for e in items]
        return ". ".join(parts) + "."

"""Morning wake-up message: greeting, today's prayer times and goals."""

from __future__ import annotations

from datetime import datetime

from app.config import get_config
from app.memory_db import MemoryDB, get_db
from app.mentor import prayer


def greeting(hour: int | None = None) -> str:
    hour = datetime.now().hour if hour is None else hour
    if 4 <= hour < 12:
        return "Good morning"
    if 12 <= hour < 17:
        return "Good afternoon"
    if 17 <= hour < 21:
        return "Good evening"
    return "Hello"


def morning_message(db: MemoryDB | None = None) -> str:
    db = db or get_db()
    cfg = get_config()
    parts = [f"{greeting()}, {cfg.user.name}! It's {datetime.now().strftime('%A, %I:%M %p').replace(' 0', ' ')}."]
    if prayer.is_configured():
        parts.append(prayer.describe_today())
    goals = db.goals()
    if goals:
        parts.append("Today's goals: " + "; ".join(g.title for g in goals[:3]) + ".")
    pending = db.pending_reminders()
    if pending:
        parts.append(f"You have {len(pending)} reminder(s) pending.")
    return " ".join(parts)

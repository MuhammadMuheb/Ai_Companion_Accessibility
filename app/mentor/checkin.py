"""Daily check-ins: ask how the user is doing, record mood, and reflect on trends."""

from __future__ import annotations

from app.memory_db import MemoryDB, get_db
from app.mentor.wakeup import greeting

GOOD = {"great", "good", "happy", "fine", "ok", "okay", "achha", "acha", "theek", "badhiya", "khush", "alhamdulillah"}
LOW = {"sad", "tired", "bad", "stressed", "anxious", "low", "thaka", "pareshan", "udaas", "bura"}


def prompt_message(db: MemoryDB | None = None) -> str:
    db = db or get_db()
    goals = db.goals()
    msg = f"{greeting()}! How are you feeling today?"
    if goals:
        msg += f" Your top goal is: {goals[0].title}. What is one step you'll take on it today?"
    msg += " (Reply with /checkin <mood> <notes>)"
    return msg


def record(mood: str, notes: str = "", db: MemoryDB | None = None) -> str:
    db = db or get_db()
    db.add_checkin(mood, notes)
    word = mood.lower().strip()
    if word in LOW:
        reply = "Thank you for sharing. Be gentle with yourself today — pick one small task and take breaks."
    elif word in GOOD:
        reply = "That's wonderful to hear! Let's make good use of the energy today."
    else:
        reply = "Noted, thank you for checking in."
    return f"{reply}\n{trend(db)}"


def trend(db: MemoryDB | None = None) -> str:
    db = db or get_db()
    recent = db.recent_checkins(7)
    if len(recent) < 3:
        return f"You've checked in {len(recent)} time(s) so far."
    low = sum(1 for c in recent if c.mood.lower() in LOW)
    if low >= len(recent) / 2:
        return f"You've felt low on {low} of the last {len(recent)} check-ins. Consider talking to someone you trust."
    return f"Last {len(recent)} check-ins: mostly steady. Keep it up!"

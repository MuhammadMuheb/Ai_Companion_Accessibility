"""Context-aware suggestions: looks at the time, prayers, goals, mood and reminders
and offers one practical next step."""

from __future__ import annotations

from datetime import datetime

from app.llm import LLMError, get_llm
from app.memory_db import MemoryDB, get_db
from app.mentor import prayer
from app.mentor.checkin import LOW
from app.mentor.focus import get_session

SYSTEM = (
    "You are a kind, practical life mentor. Given the user's current situation, suggest ONE concrete next step "
    "in 1-2 short sentences. Be encouraging, never preachy."
)


def situation(db: MemoryDB | None = None) -> list[str]:
    db = db or get_db()
    now = datetime.now()
    facts = [f"It is {now:%A %I:%M %p}."]
    if prayer.is_configured():
        name, when = prayer.next_prayer()
        mins = int((when - datetime.now(when.tzinfo)).total_seconds() // 60)
        facts.append(f"Next prayer {prayer.DISPLAY[name]} in {mins} minutes.")
    goals = db.goals()
    if goals:
        facts.append("Active goals: " + "; ".join(g.title for g in goals[:5]) + ".")
    checkins = db.recent_checkins(3)
    if checkins:
        facts.append("Recent moods: " + ", ".join(c.mood for c in checkins) + ".")
    pending = db.pending_reminders()
    if pending:
        facts.append(f"{len(pending)} pending reminders, next: {pending[0]['text']}.")
    session = get_session()
    if session.active:
        facts.append(session.status())
    return facts


def rule_based(db: MemoryDB | None = None) -> str:
    db = db or get_db()
    hour = datetime.now().hour
    if prayer.is_configured():
        name, when = prayer.next_prayer()
        mins = (when - datetime.now(when.tzinfo)).total_seconds() / 60
        if mins <= 15:
            return f"{prayer.DISPLAY[name]} is in {int(mins)} minutes — a good time to make wudu and wrap up."
    if hour >= 23 or hour < 4:
        return "It's late. A good night's sleep will help tomorrow's goals more than staying up."
    checkins = db.recent_checkins(1)
    if checkins and checkins[0].mood.lower() in LOW:
        return "You mentioned feeling low. Take a short walk or drink some water, then try one small task."
    goals = db.goals()
    if goals:
        return f"How about a 25 minute focus session on '{goals[0].title}'? Say 'start focus' to begin."
    return "Set one goal for today — say 'add goal' followed by what you want to achieve."


def get_advice(db: MemoryDB | None = None) -> str:
    facts = situation(db)
    try:
        return get_llm().ask("\n".join(facts), system=SYSTEM, temperature=0.6, max_tokens=100).strip()
    except LLMError:
        return rule_based(db)

"""Interactive text chat with slash commands for memory and goals."""

from __future__ import annotations

import sys

from app.companion import Companion
from app.config import get_config
from app.mentor import checkin, prayer, wakeup
from app.mentor.focus import get_session
from app.scheduler import CompanionScheduler

HELP = """
Commands:
  /remember <fact>   Save something about you        /memories      List what I remember
  /forget <id>       Delete a memory                 /goals         List active goals
  /goal <title>      Add a goal                      /done <id>     Mark a goal complete
  /remind <min> <text>  Remind me in N minutes      /prayer        Today's prayer times
  /checkin <mood> [notes]  Log how you feel          /morning       Morning summary
  /focus [min] [task]  Start a focus session         /focus stop    End it (/focus status)
  /clear             Start a fresh conversation      /help          Show this help
  /quit              Exit (or type: exit, bye, khuda hafiz)
Anything else is a normal chat message.
"""

EXIT_WORDS = {"/quit", "/exit", "exit", "quit", "bye", "khuda hafiz", "allah hafiz"}


def handle_command(companion: Companion, line: str, scheduler: CompanionScheduler | None = None) -> str | None:
    """Run a slash command. Returns text to print, or None if `line` isn't a command."""
    if not line.startswith("/"):
        return None
    cmd, _, arg = line.partition(" ")
    cmd, arg = cmd.lower(), arg.strip()
    db, memory = companion.db, companion.memory

    if cmd == "/help":
        return HELP
    if cmd == "/remember":
        if not arg:
            return "Usage: /remember <fact>"
        action, mem_id = memory.remember(arg, importance=4)
        return f"Got it — memory #{mem_id} {action}."
    if cmd == "/memories":
        mems = memory.all()
        if not mems:
            return "I don't remember anything about you yet."
        return "\n".join(f"  #{m.id} [{m.category}, {m.importance}★] {m.content}" for m in mems)
    if cmd == "/forget":
        if not arg.lstrip("#").isdigit():
            return "Usage: /forget <id>   (see /memories for ids)"
        return "Forgotten." if memory.forget(int(arg.lstrip("#"))) else "No memory with that id."
    if cmd == "/goals":
        goals = db.goals()
        return "\n".join(f"  #{g.id} {g.title}" for g in goals) if goals else "No active goals. Add one with /goal <title>."
    if cmd == "/goal":
        if not arg:
            return "Usage: /goal <title>"
        return f"Goal #{db.add_goal(arg)} added: {arg}"
    if cmd == "/done":
        if not arg.lstrip("#").isdigit():
            return "Usage: /done <id>"
        return "Well done! 🎉 Goal marked complete." if db.set_goal_status(int(arg.lstrip("#")), "done") else "No goal with that id."
    if cmd == "/prayer":
        return prayer.describe_today()
    if cmd == "/morning":
        return wakeup.morning_message(db)
    if cmd == "/checkin":
        if not arg:
            return checkin.prompt_message(db)
        mood, _, notes = arg.partition(" ")
        return checkin.record(mood, notes, db)
    if cmd == "/remind":
        minutes, _, text = arg.partition(" ")
        try:
            minutes = float(minutes)
        except ValueError:
            return "Usage: /remind <minutes> <text>   e.g. /remind 20 drink water"
        if not text:
            return "What should I remind you about?"
        if scheduler is None:
            return "Reminders need the scheduler, which isn't running."
        return scheduler.add_reminder(text, minutes)
    if cmd == "/focus":
        session = get_session()
        if arg in {"stop", "end"}:
            return session.stop()
        if arg == "status":
            return session.status()
        first, _, task = arg.partition(" ")
        if first.isdigit():
            return session.start(int(first), task)
        return session.start(None, arg)
    if cmd == "/clear":
        companion.reset()
        return "Started a fresh conversation (long-term memories are kept)."
    return f"Unknown command {cmd}. Type /help."


def run(companion: Companion | None = None, scheduler: CompanionScheduler | None = None) -> None:
    companion = companion or Companion()
    if scheduler is None:
        scheduler = CompanionScheduler(companion.db)
        scheduler.start()
    name = get_config().user.name
    print(f"\nAssalam-o-Alaikum, {name}! I'm your AI companion. Type /help for commands.\n")
    while True:
        try:
            line = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        if line.lower() in EXIT_WORDS:
            break
        output = handle_command(companion, line, scheduler)
        if output is not None:
            print(output + "\n")
            continue
        sys.stdout.write("AI: ")
        for piece in companion.reply_stream(line):
            sys.stdout.write(piece)
            sys.stdout.flush()
        print("\n")
    scheduler.shutdown()
    print("Allah Hafiz! 👋")

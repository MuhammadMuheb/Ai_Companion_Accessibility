"""Text CLI that understands natural-language commands ("open notepad", "remind me in
10 minutes to ...") as well as chat and the slash commands of the basic CLI."""

from __future__ import annotations

import sys

from app.cli import EXIT_WORDS, handle_command
from app.companion import Companion
from app.config import get_config
from app.interaction import console_ask
from app.scheduler import CompanionScheduler
from app.services import Services
from app.workflow_extended import ExtendedWorkflow


def run(speak: bool = False) -> None:
    companion = Companion()
    scheduler = CompanionScheduler(companion.db)
    scheduler.start()
    workflow = ExtendedWorkflow(companion, scheduler, ask=console_ask)
    services = Services(workflow, companion)
    services.start_monitor()
    if get_config().hotkeys.enabled:
        services.start_hotkeys()
    say = None
    if speak:
        from app.mentor import notifications
        from app.voice.tts import speak as say

        notifications.add_listener(lambda title, msg: say(f"{title}. {msg}", wait=False))

    print(f"\nAssalam-o-Alaikum, {get_config().user.name}! Chat with me or give commands like "
          "'open notepad' or 'remind me in 10 minutes to drink water'. Type /help or 'help'.\n")
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
        if output is None:
            _, output = workflow.run_text(line)
        if output is not None:
            print(f"AI: {output}\n")
            if say:
                say(output)
            continue
        sys.stdout.write("AI: ")
        reply = []
        for piece in companion.reply_stream(line):
            reply.append(piece)
            sys.stdout.write(piece)
            sys.stdout.flush()
        print("\n")
        workflow.context.log(line, "chat", "".join(reply))
        if say:
            say("".join(reply))
    services.stop()
    scheduler.shutdown()
    print("Allah Hafiz! 👋")

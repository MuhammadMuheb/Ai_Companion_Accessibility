"""Voice interface.

Modes:
- "wake" (default when the wake word is on): say the companion's name, then your command —
  no keys needed. Typed commands still work.
- "push": press Enter and speak — or type a message instead.
- "hands_free": always listening; every sentence you say is handled.

Everything the companion says is spoken aloud. Notifications are watched and read out,
and Ctrl+Alt+T translates selected / pointed-at English into Roman Urdu.
"""

from __future__ import annotations

import threading

from app.accessibility.voice_engine import VoiceEngine
from app.cli import EXIT_WORDS
from app.companion import Companion
from app.config import get_config
from app.scheduler import CompanionScheduler
from app.services import Services
from app.workflow_extended import ExtendedWorkflow

VOICE_EXIT = EXIT_WORDS | {"goodbye", "good bye", "stop listening", "exit program", "band ho jao", "shut down companion"}


def _is_exit(text: str) -> bool:
    t = text.lower().strip(" .!?")
    return t in VOICE_EXIT


def run(mode: str = "push", echo: bool = True, hands_free: bool | None = None) -> None:
    if hands_free is not None:  # older callers
        mode = "hands_free" if hands_free else "push"
    cfg = get_config()
    companion = Companion()
    scheduler = CompanionScheduler(companion.db)
    scheduler.start()
    voice = VoiceEngine(echo=echo)
    workflow = ExtendedWorkflow(companion, scheduler, ask=voice.ask)
    lock = threading.Lock()

    print("Loading speech recognition (the first run downloads the model)...", flush=True)
    voice.warm_up()
    services = Services(workflow, companion, voice, lock=lock)
    if mode == "wake":
        services.start()  # wake word + hotkeys + notifications
    else:
        services.start_monitor()
        if cfg.hotkeys.enabled:
            services.start_hotkeys()

    phrase = cfg.wake_phrases[0]
    how = {"wake": f"Say “{phrase}” whenever you need me.",
           "hands_free": "Just speak whenever you're ready.",
           "push": "Press Enter, then speak. You can also type."}[mode]
    voice.say(f"Assalam-o-Alaikum {cfg.user.name}. {how} Say help to hear what I can do.")
    misses = 0
    try:
        while True:
            if mode == "wake":
                # the wake listener runs in the background; the keyboard stays available for typing
                try:
                    typed = input(f"(say “{phrase}”, or type) > ").strip()
                except (EOFError, KeyboardInterrupt):
                    break
                if not typed:
                    services.talk_hotkey()  # Enter = talk now
                    continue
                if _is_exit(typed):
                    break
                services.run_command(typed)
                continue
            if mode == "hands_free":
                text = voice.listen(wait_seconds=30)
            else:
                try:
                    typed = input("⏎ Enter to speak (or type): ").strip()
                except (EOFError, KeyboardInterrupt):
                    break
                if not any(c.isalnum() for c in typed):
                    typed = ""  # a stray key like "\" counts as just pressing Enter
                text = typed or voice.listen(require_speech=False)
            if not text:
                misses += 1
                if mode == "push" or misses % 4 == 0:
                    voice.say("I didn't hear anything. Try speaking a little closer to the microphone."
                              if mode == "push" else "I'm still here when you need me.")
                    if mode == "push" and misses == 3:
                        print("Tip: run  python main_accessibility.py --mic-test  to check your microphone.")
                continue
            misses = 0
            if _is_exit(text):
                break
            with lock:
                intent, result = workflow.run_text(text)
                if result is not None:
                    voice.say(result or "Done.")
                else:
                    reply = voice.say_stream(companion.reply_stream(text))
                    workflow.context.log(text, "chat", reply)
    except KeyboardInterrupt:
        pass
    finally:
        services.stop()
        voice.say("Allah Hafiz. Take care.")
        voice.close()
        scheduler.shutdown()

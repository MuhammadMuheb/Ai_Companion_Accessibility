"""Background services shared by the voice CLI and the web interface:

- wake word listener  (say the companion's name to talk — no button)
- global hotkeys      (Ctrl+Alt+T translate what's selected / under the mouse, Ctrl+Alt+Space talk)
- notification monitor (Windows toasts + unread counts in browser tabs)
"""

from __future__ import annotations

import threading
from typing import Callable

from app.config import get_config
from app.logger import get_logger
from app.mentor.notifications import notify

log = get_logger(__name__)

GREETINGS = ("Ji?", "Haan ji, boliye.", "Yes?")
FAREWELLS = ("Theek hai, Allah Hafiz.", "Okay, bye! Bulana ho to mera naam lein.")
MAX_TURNS = 20       # a conversation ends by itself after this many sentences
MAX_GIBBERISH = 2    # noise in a row before Nova stops listening
SPEAK_LIMIT = 600  # characters; longer answers are summarised aloud and shown in full in the window


def speakable(text: str) -> tuple[str, bool]:
    """(what to say aloud, whether the full text needs showing). Code blocks and very long answers
    aren't read out character by character."""
    import re

    has_code = "```" in text
    spoken = re.sub(r"```.*?(```|$)", " ", text, flags=re.S)
    spoken = re.sub(r"\s+", " ", spoken).strip()
    long = len(spoken) > SPEAK_LIMIT
    if long:
        cut = spoken[:SPEAK_LIMIT]
        spoken = cut[: cut.rfind(".") + 1 or len(cut)]
    note = []
    if has_code:
        note.append("the code")
    if long:
        note.append("the full answer")
    if note:
        spoken += f" I've put {' and '.join(note)} in my window — say “open your window” to see it."
    return spoken or "Done.", bool(note)


class Services:
    def __init__(self, workflow, companion, voice=None, on_event: Callable[[str, str], None] | None = None,
                 lock: threading.Lock | None = None):
        """`voice` is a VoiceEngine (needed for the wake word and talk hotkey); `lock` serialises
        workflow use with other callers such as web requests."""
        self.workflow = workflow
        self.companion = companion
        self.voice = voice
        self.on_event = on_event
        self.lock = lock or threading.Lock()
        self.wake = None
        self.monitor = None
        self.hotkeys = None
        self.calls = None
        self.mouse = None
        self._watchdog = None
        self._greet = 0
        self.extra_hotkeys: dict[str, Callable[[], None]] = {}  # e.g. open_window -> show the chat window
        workflow.services = self

    # ---- lifecycle ---------------------------------------------------------------
    def start(self) -> "Services":
        cfg = get_config()
        if hasattr(self.companion, "warm_up"):
            threading.Thread(target=self.companion.warm_up, daemon=True, name="llm-warm-up").start()
        if cfg.notifications.enabled:
            self.start_monitor()
        if cfg.hotkeys.enabled:
            self.start_hotkeys()
        if self.voice is not None and cfg.voice.wake_enabled and cfg.feature_on("wake"):
            self.start_wake()
        if cfg.feature_on("translate") and cfg.llm.translate_model:
            from app.accessibility import translate

            threading.Thread(target=translate.preload, daemon=True).start()
        if cfg.calls.enabled and cfg.feature_on("calls"):
            self.start_calls()
        if self._watchdog is None:
            self._watchdog = threading.Thread(target=self._watch, daemon=True, name="services-watchdog")
            self._stopping = threading.Event()
            self._watchdog.start()
        return self

    def stop(self) -> None:
        if getattr(self, "_stopping", None):
            self._stopping.set()
        self._watchdog = None
        for svc in (self.wake, self.monitor, self.calls, self.mouse):
            if svc is not None:
                svc.stop()
        self.calls = self.mouse = None
        if self.hotkeys is not None:
            try:
                self.hotkeys.stop()
            except Exception:
                pass
        self.wake = self.monitor = self.hotkeys = None

    def restart(self) -> None:
        self.stop()
        self.start()

    def start_monitor(self) -> None:
        from app.mentor.notification_monitor import NotificationMonitor

        if self.monitor is None:
            self.monitor = NotificationMonitor()
            self.monitor.start()

    def start_wake(self) -> None:
        from app.voice.wake import WakeListener

        if self.wake is None:
            self.wake = WakeListener(self.voice.listener, self.handle_wake)
            self.wake.start()

    def start_hotkeys(self) -> None:
        try:
            from pynput import keyboard
        except ImportError:
            log.warning("pynput not installed; global hotkeys disabled")
            return
        cfg = get_config().hotkeys
        bindings = {}
        if cfg.translate:
            bindings[cfg.translate] = lambda: self._in_thread(self.translate_hotkey)
        if cfg.talk:
            bindings[cfg.talk] = lambda: self._in_thread(self.talk_hotkey)
        opener = self.extra_hotkeys.get("open_window")
        if cfg.open_window and opener:
            bindings[cfg.open_window] = lambda: self._in_thread(opener)
        try:
            self.hotkeys = keyboard.GlobalHotKeys(bindings)
            self.hotkeys.start()
            log.info("Hotkeys: %s", list(bindings))
        except (ValueError, Exception) as e:
            log.warning("Couldn't register hotkeys %s: %s", list(bindings), e)
            self.hotkeys = None

    @staticmethod
    def _in_thread(fn) -> None:
        threading.Thread(target=fn, daemon=True).start()

    # ---- handlers --------------------------------------------------------------------
    def _event(self, kind: str, text: str) -> None:
        if self.on_event:
            self.on_event(kind, text)

    # commands that take a while on this computer: say so straight away instead of going silent
    SLOW = {"expert", "research", "translate_screen", "translate_text", "describe_screen", "generate_code", "web_answer",
            "summarize_page", "send_whatsapp", "install_app", "diagnose", "create_project", "debug_file"}

    def _acknowledge(self, text: str) -> None:
        from app.command_parser import parse
        from app.expert import is_technical

        intent = parse(text, use_llm=False)
        if intent.name in self.SLOW or (intent.is_chat and is_technical(text)):
            self.voice.say("Ek minute, dekh rahi hoon…")

    def run_command(self, text: str) -> None:
        """Execute a spoken command, answering by voice (questions are asked by voice too)."""
        self._event("heard", text)
        with self.lock:
            previous_ask, previous_confirm = self.workflow.ask, self.workflow._confirm
            self.workflow.ask, self.workflow._confirm = self.voice.ask, None
            try:
                self._acknowledge(text)
                intent, result = self.workflow.run_text(text)
                if result is not None:
                    spoken, full = speakable(result or "Done.")
                    if full:
                        self._event("said", result)  # the whole answer (code etc.) goes to Nova's window
                    self.voice.say(spoken)
                else:
                    reply = self.voice.say_stream(self.companion.reply_stream(text))
                    self.workflow.context.log(text, "chat", reply)
            finally:
                self.workflow.ask, self.workflow._confirm = previous_ask, previous_confirm

    CALL_COMMANDS = {"record_call", "call_notes"}

    def _silent_call_command(self, rest: str, audio) -> None:
        """During a call: act on "record this call" / "stop recording" without speaking."""
        from app.command_parser import parse
        from app.overlay import get_overlay
        from app.voice.voiceprint import get_voiceprint

        intent = parse(rest, use_llm=False) if rest else None
        if intent is None or intent.name not in self.CALL_COMMANDS:
            return  # anything else waits until the call is over
        vp = get_voiceprint()
        if vp.active() and audio is not None and not vp.verify(audio)[0]:
            return
        with self.lock:
            result = self.workflow.execute(intent) or ""
        self._event("note", f"📞 {result}")
        get_overlay().prompt("Nova", result, ["OK"], lambda _c: None, timeout=4)

    def handle_wake(self, rest: str, audio=None) -> None:
        import numpy as np

        from app.runtime import in_call
        from app.voice.voiceprint import get_voiceprint
        from app.voice.wake import find_wake

        if in_call.is_set():
            self._silent_call_command(rest.strip(), audio)
            return
        vp = get_voiceprint()
        check = vp.active() and audio is not None  # the talk hotkey (audio None) is a deliberate key press
        command = rest.strip()
        if len(command.split()) < 1 or find_wake(command, get_config().wake_phrases) and len(command.split()) < 3:
            command = ""
        # "Hey Nova" alone is too short to recognise a voice; decide after hearing the command too
        if command and (not check or vp.speech_seconds(audio) >= 1.2):
            if check and not self._authorised(audio):
                return
            if self._turn(command):
                self.converse(check)
            return
        self.voice.say(GREETINGS[self._greet % len(GREETINGS)])
        self._greet += 1
        heard, more = self.voice.listen_with_audio(wait_seconds=7)
        if not heard:
            return
        if check and not self._authorised(np.concatenate([audio, more]) if more.size else audio):
            return
        if self._turn(f"{command} {heard}".strip()):
            self.converse(check)

    def _turn(self, text: str) -> bool:
        """Answer one sentence of a conversation. False = the conversation is over."""
        from app.voice.conversation import is_goodbye

        if is_goodbye(text):
            self.voice.say(FAREWELLS[self._greet % len(FAREWELLS)])
            self._event("note", "Conversation ended.")
            return False
        self.run_command(text)
        paused = self.wake is not None and getattr(self.wake, "asleep", False)
        return not paused  # "stop listening" / "go to sleep" also ends it

    def converse(self, check_voice: bool = False) -> None:
        """Keep the conversation going without the wake word: record a sentence, stop on a
        goodbye or silence, skip gibberish, otherwise answer and listen again."""
        from app.runtime import in_call
        from app.voice.conversation import is_gibberish

        cfg = get_config().voice
        if not cfg.conversation:
            return
        noise = 0
        for _ in range(MAX_TURNS):
            if in_call.is_set():
                return
            heard, audio = self.voice.listen_with_audio(wait_seconds=cfg.follow_up_seconds)
            if not heard:
                self._event("note", "Conversation ended — say my name when you need me.")
                return  # silence: back to waiting for the wake word
            if is_gibberish(heard):
                log.info("Skipped gibberish: %r", heard)
                noise += 1
                if noise >= MAX_GIBBERISH:
                    self.voice.say("Samajh nahi aaya. Zaroorat ho to mera naam lein.")
                    return
                continue
            if check_voice and audio is not None and audio.size and not self._authorised(audio):
                return
            noise = 0
            if not self._turn(heard):
                return

    def _authorised(self, audio) -> bool:
        from app.voice.voiceprint import get_voiceprint

        ok, who, score = get_voiceprint().verify(audio)
        if not ok:
            log.info("Ignored an unrecognised voice (score %.2f)", score)
            self._event("note", f"Ignored a voice I don't recognise (match {score:.2f}).")
            self.voice.say("Maaf kijiye, main sirf pehchaani hui awaaz par kaam karti hoon.")
        return ok

    def talk_hotkey(self) -> None:
        if self.wake is not None:
            self.wake.trigger()  # handled on the wake thread so the mic isn't used twice
        elif self.voice is not None:
            command = self.voice.listen(wait_seconds=7)
            if command:
                self.run_command(command)

    def translate_hotkey(self) -> None:
        from app.accessibility import translate

        if not get_config().feature_on("translate"):
            return
        english, result = translate.translate_on_screen()
        title = "Tarjuma"
        self._event("said", (f"English: {english}\n" if english else "") + result)
        # notify() prints, shows it in the web page and — through the voice engine — speaks it
        notify(title, result, toast=False)

    # ---- keeping things alive (sleep / resume, crashed threads) --------------------------
    def _watch(self) -> None:
        import time

        last = time.time()
        while not self._stopping.wait(10):
            now = time.time()
            resumed = now - last > 40  # the loop sleeps 10 s; a big gap means the laptop was asleep
            last = now
            if resumed:
                log.info("Resumed from sleep — restarting listening")
            if self.voice is not None and (resumed or (self.wake is not None and not self.wake.is_alive())):
                if self.wake is not None:
                    self.wake.stop()
                    self.wake = None
                if get_config().voice.wake_enabled and get_config().feature_on("wake"):
                    self.start_wake()
            if self.monitor is not None and not self.monitor.is_alive():
                self.monitor = None
                self.start_monitor()
            if self.calls is not None and not self.calls.is_alive():
                self.calls = None
                self.start_calls()

    # ---- calls ----------------------------------------------------------------------------
    def start_calls(self) -> None:
        from app.calls import CallWatcher

        if self.calls is None:
            self.calls = CallWatcher(self._call_started, self._call_ended, self._ask_record)
            self.calls.start()

    def _call_started(self, call) -> None:
        # the wake word keeps working, but only call commands are accepted and nothing is spoken
        self._event("note", f"📞 Call started on {call.app}. I'll stay quiet until it ends "
                            "(you can still say my name + “record this call”).")

    def _ask_record(self, call, decide) -> None:
        from app.overlay import get_overlay

        if not get_config().calls.offer_recording:
            decide(False)
            return
        self._pending_call_decision = decide
        self._event("call", f"Call on {call.app}: record it and take notes?")
        get_overlay().prompt(
            f"📞 Call on {call.app}", "Record this call and take notes for you?", ["Record", "No"],
            lambda choice: decide(choice == "Record"), timeout=30,
            note="Only record if everyone on the call agrees — recording people without consent is illegal in many places.")

    def decide_call(self, record: bool) -> None:
        """Answer from the web page instead of the pop-up."""
        from app.overlay import get_overlay

        if self.calls is not None:
            self.calls.decide(record)
        get_overlay().dismiss()

    def _call_ended(self, call) -> None:
        from app.overlay import get_overlay

        get_overlay().dismiss()
        try:
            if get_config().calls.debrief and self.voice is not None:
                with self.lock:
                    self._debrief(call)
        finally:
            if self.wake is not None:
                self.wake.resume()

    def _debrief(self, call) -> None:
        import datetime as dt

        from app import calls as calls_mod
        from app.llm import LLMError

        minutes = max(1, int((dt.datetime.now() - call.started).total_seconds() // 60))
        say, ask = self.voice.say, self.voice.ask
        items: list[str] = []
        if call.recorder is not None:
            folder = call.recorder.folder
            say(f"Call khatam ho gayi, {minutes} minute ki thi. Recording save ho gayi hai — main notes bana rahi hoon.")
            try:
                transcript = calls_mod.transcribe_call(folder, self.voice.listener)
                notes = calls_mod.summarize(transcript)
            except (LLMError, Exception) as e:
                log.warning("Call notes failed: %s", e)
                notes = f"Summary: I couldn't write notes ({e}). The recording is in {folder}."
            (folder / "notes.md").write_text(f"# Call on {call.app} — {call.started:%d %b %Y %I:%M %p}\n\n{notes}\n",
                                             encoding="utf-8")
            self._event("said", notes)
            items = calls_mod.action_items(notes)
            summary = notes.split("Action items")[0].replace("Summary:", "").strip()
            say(summary[:600])
            if items:
                say("Action items: " + "; ".join(items[:5]) + ".")
                if ask("Kya main inhein aapke goals mein add kar doon?", ["Yes", "No"]) == "Yes":
                    for item in items[:5]:
                        self.companion.db.add_goal(item)
                    say("Add kar diye.")
        else:
            say(f"Call khatam ho gayi, {minutes} minute ki thi.")
        feeling = ask("Kaisi rahi call?", None)
        reply = self._care_reply(feeling or "", minutes, bool(items))
        say(reply)

    def _care_reply(self, feeling: str, minutes: int, has_items: bool) -> str:
        from app.llm import LLMError, get_llm

        system = ("You are Nova, a caring companion. The user just finished a phone call. Reply in 1-2 short, warm "
                  "sentences in Roman Urdu. If they sound stressed or tired, suggest a glass of water, a few deep "
                  "breaths or a short break. If the call was long, suggest stretching. Never lecture.")
        try:
            return get_llm().ask(f"Call length: {minutes} minutes. The user says: {feeling or '(no answer)'}",
                                 system=system, temperature=0.6, max_tokens=80).strip()
        except LLMError:
            return "Achha. Thoda paani pee lein aur ek minute aaraam kar lein."

    # ---- voice print enrolment ---------------------------------------------------------------
    ENROL_SENTENCES = ["Nova, aaj ka mausam kaisa hai?", "Please open YouTube and play some nasheeds.",
                       "Mujhe kal subah nau baje yaad dilana."]

    def enroll_voice(self, ask) -> str:
        from app.voice.voiceprint import MAX_PROFILES, VoiceprintError, get_voiceprint

        vp = get_voiceprint()
        name = get_config().user.name if get_config().user.name != "User" else "Me"
        if name not in vp.profiles() and len(vp.profiles()) >= MAX_PROFILES:
            return f"{MAX_PROFILES} voices are already enrolled. Remove one in Settings first."
        clips = []
        self.voice.say("Main aapki awaaz pehchaanna seekhungi. Beep ke baad yeh teen jumle bolein.")
        for sentence in self.ENROL_SENTENCES:
            self.voice.say(f"Boliye: {sentence}")
            _, audio = self.voice.listen_with_audio(wait_seconds=8, max_seconds=8)
            clips.append(audio)
        try:
            saved = vp.enroll(name, clips)
        except VoiceprintError as e:
            return str(e)
        return f"Done — I'll now only respond to enrolled voices ({saved}). You can add one more person or remove voices in Settings."

    # ---- mouse reading ---------------------------------------------------------------------------
    def mouse_reading(self, on: bool) -> str:
        from app.accessibility.pointer import MouseReader

        if on:
            if self.mouse is None:
                say = self.voice.say if self.voice is not None else (lambda t: notify("Mouse", t, toast=False))
                self.mouse = MouseReader(say)
                self.mouse.start()
            return "Mouse reading is on — rest the pointer on something and I'll say what it is."
        if self.mouse is not None:
            self.mouse.stop()
            self.mouse = None
        return "Mouse reading is off."

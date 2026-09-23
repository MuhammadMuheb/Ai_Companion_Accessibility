"""Glue between the microphone, the speaker and the companion: listen for a
command, speak replies sentence-by-sentence as they stream, and ask spoken
questions (yes/no, "which account?", "what should the message say?")."""

from __future__ import annotations

import re
import threading
from typing import Callable, Iterable

from app.interaction import is_yes, match_option  # noqa: F401  (is_yes re-exported for callers)
from app.logger import get_logger
from app.mentor import notifications
from app.voice.stt import Listener
from app.voice.tts import Speaker, get_speaker

log = get_logger(__name__)

_SENTENCE_END = re.compile(r"(?<=[.!?۔])\s+|\n+")


class VoiceEngine:
    def __init__(self, listener: Listener | None = None, speaker: Speaker | None = None, echo: bool = True,
                 on_event: Callable[[str, str], None] | None = None):
        self.listener = listener or Listener()
        self.speaker = speaker or get_speaker()
        self.echo = echo  # also print what is heard and said
        self.on_event = on_event  # ("heard" | "said", text) — lets the web page show voice conversations
        self._busy = threading.Lock()
        notifications.add_listener(self._speak_notification)

    def _emit(self, kind: str, text: str) -> None:
        if self.on_event and text:
            try:
                self.on_event(kind, text)
            except Exception as e:
                log.debug("on_event failed: %s", e)

    def _speak_notification(self, title: str, message: str) -> None:
        # don't talk over the user mid-conversation; the console/toast still shows it
        if self._busy.acquire(blocking=False):
            try:
                self.speaker.say(f"{title}. {message}")
            finally:
                self._busy.release()

    def warm_up(self) -> None:
        """Load the speech model and measure room noise up front so the first command is fast."""
        _ = self.listener.model
        self.listener.calibrate()

    def say(self, text: str) -> None:
        if self.echo:
            print(f"AI: {text}\n")
        self._emit("said", text)
        with self._busy:
            self.speaker.say(text)

    def say_stream(self, pieces: Iterable[str]) -> str:
        """Speak a streamed reply one sentence at a time, starting before it's complete."""
        buffer, full = "", []
        if self.echo:
            print("AI: ", end="", flush=True)
        with self._busy:
            for piece in pieces:
                full.append(piece)
                if self.echo:
                    print(piece, end="", flush=True)
                buffer += piece
                *done, buffer = _SENTENCE_END.split(buffer)
                for sentence in done:
                    if sentence.strip():
                        self.speaker.say(sentence, wait=False)
            if buffer.strip():
                self.speaker.say(buffer, wait=False)
            self.speaker.wait_until_done()
        if self.echo:
            print("\n")
        self._emit("said", "".join(full))
        return "".join(full)

    @staticmethod
    def _meter(level: float, threshold: float, started: bool) -> None:
        # kept short (~30 columns) so it never wraps; "\r" can only redraw a single line
        bar = "#" * min(15, int(level / 0.002))
        state = "hearing" if started else ("loud" if level > threshold else "speak")
        print(f"\rMic [{bar:<15}] {state:<7}", end="", flush=True)

    def listen(self, **kwargs) -> str:
        return self.listen_with_audio(**kwargs)[0]

    def listen_with_audio(self, **kwargs):
        """(text, audio) — the audio is used for voice-print checks."""
        with self._busy:
            audio = self.listener.record(beeps=True, on_level=self._meter if self.echo else None, **kwargs)
            if self.echo:
                print("\r" + " " * 32 + "\r", end="", flush=True)
                if audio.size and self.listener.last_peak <= self.listener.threshold:
                    print(f"(mic level stayed very low: {self.listener.last_peak:.4f})")
            text = self.listener.transcribe(audio)
        if self.echo and text:
            print(f"You: {text}")
        self._emit("heard", text)
        return text, audio

    def ask(self, question: str, options: list[str] | None = None, attempts: int = 2) -> str | None:
        """Speak a question and return the spoken answer (matched to `options` when given)."""
        yes_no = options is not None and [o.lower() for o in options] == ["yes", "no"]
        for attempt in range(attempts):
            prompt = question if attempt == 0 else "Sorry, I didn't catch that. " + question
            if yes_no:
                prompt += " Say yes or no."
            self.say(prompt)
            heard = self.listen(wait_seconds=8, max_seconds=15 if not options else 5)
            if not heard:
                continue
            if options is None:
                return heard
            if yes_no:
                answer = is_yes(heard)
                if answer is not None:
                    return "Yes" if answer else "No"
                continue
            choice = match_option(heard, options)
            if choice:
                return choice
        self.say("I didn't catch that, so I'll leave it.")
        return None

    def confirm(self, question: str, attempts: int = 2) -> bool:
        return self.ask(question, ["Yes", "No"], attempts) == "Yes"

    def close(self) -> None:
        notifications.remove_listener(self._speak_notification)

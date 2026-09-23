"""Text-to-speech with pyttsx3 (offline, uses Windows SAPI voices).

pyttsx3 is not thread-safe and hangs if used from several threads on Windows, so a
single worker thread owns the engine and everything else just queues text.
"""

from __future__ import annotations

import queue
import re
import threading

from app.config import get_config
from app.logger import get_logger

log = get_logger(__name__)

_MARKDOWN = re.compile(r"[*_`#>|]+")
_URL = re.compile(r"https?://\S+")


def clean_for_speech(text: str) -> str:
    text = _URL.sub("link", text)
    text = _MARKDOWN.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


class Speaker:
    def __init__(self, rate: int = 175, volume: float = 1.0):
        self.rate, self.volume = rate, volume
        self._queue: queue.Queue[tuple[str, threading.Event] | None] = queue.Queue()
        self._ready = threading.Event()
        self.available = True
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()
        self._ready.wait(timeout=10)

    def _worker(self) -> None:
        try:
            import pyttsx3

            engine = pyttsx3.init()
            engine.setProperty("rate", self.rate)
            engine.setProperty("volume", self.volume)
        except Exception as e:
            log.warning("Text-to-speech unavailable: %s", e)
            self.available = False
            self._ready.set()
            return
        self._ready.set()
        while True:
            item = self._queue.get()
            if item is None:
                break
            text, done = item
            try:
                if text:  # empty text is a "wait until everything before me is spoken" marker
                    engine.say(text)
                    engine.runAndWait()
            except Exception as e:
                log.warning("Speech failed: %s", e)
            finally:
                done.set()

    def say(self, text: str, wait: bool = True) -> None:
        from app.runtime import in_call

        text = clean_for_speech(text)
        if not text or not self.available or in_call.is_set():
            return  # during a call MD stays silent so nothing is heard on the call
        done = threading.Event()
        self._queue.put((text, done))
        if wait:
            done.wait()

    def stop(self) -> None:
        """Drop everything still waiting to be spoken (the sentence being spoken finishes)."""
        while True:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                break
            if item is None:
                self._queue.put(None)
                break
            item[1].set()

    def wait_until_done(self) -> None:
        if not self.available:
            return
        done = threading.Event()
        self._queue.put(("", done))
        done.wait()

    def close(self) -> None:
        self._queue.put(None)


_speaker: Speaker | None = None


def get_speaker() -> Speaker:
    global _speaker
    if _speaker is None:
        _speaker = Speaker()
    return _speaker


def speak(text: str, wait: bool = True) -> None:
    if get_config().voice.enabled:
        get_speaker().say(text, wait=wait)

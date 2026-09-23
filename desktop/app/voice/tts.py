"""Text-to-speech: Lyra's speaking voice.

Two engines, chosen by the selected voice (see ``app.voice.voices``):

- neural — Piper (offline ONNX neural voices), played through ``sounddevice``. Can be cut off
  mid-sentence by ``stop()``.
- system — Windows' built-in SAPI voices through pyttsx3. Always available; the fallback when a
  neural voice isn't downloaded or fails to load.

Neither engine is thread-safe (pyttsx3 hangs on Windows if used from several threads), so a single
worker thread owns them and everything else just queues text. Voice, speed and volume can change
at any time; the next sentence uses the new settings.
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
BASE_WPM = 175  # SAPI words per minute at speed 1.0


def clean_for_speech(text: str) -> str:
    text = _URL.sub("link", text)
    text = _MARKDOWN.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


class _SystemEngine:
    """Windows SAPI voices via pyttsx3."""

    def __init__(self):
        import pyttsx3

        self.engine = pyttsx3.init()
        self._voice_key = None

    def speak(self, text: str, voice, rate: float, volume: float) -> None:
        key = voice.model if voice.engine == "system" else ""
        if key != self._voice_key:
            match = next((v for v in self.engine.getProperty("voices") if key and key.lower() in v.name.lower()), None)
            if match is not None:
                self.engine.setProperty("voice", match.id)
            self._voice_key = key
        self.engine.setProperty("rate", int(BASE_WPM * rate))
        self.engine.setProperty("volume", max(0.0, min(1.0, volume)))
        self.engine.say(text)
        self.engine.runAndWait()

    def stop(self) -> None:
        try:
            self.engine.stop()
        except Exception:
            pass


class _NeuralEngine:
    """Piper neural voices. Models are loaded lazily and cached (one at a time — they're ~60 MB)."""

    def __init__(self):
        from piper import PiperVoice  # noqa: F401  (fails fast when the engine isn't installed)

        self._loaded: tuple[str, object] | None = None
        self._interrupt = threading.Event()

    def _voice(self, voice):
        from piper import PiperVoice

        from app.voice.voices import model_files

        if self._loaded and self._loaded[0] == voice.id:
            return self._loaded[1]
        files = model_files(voice)
        if files is None:
            raise FileNotFoundError(f"voice {voice.id} is not downloaded")
        model = PiperVoice.load(str(files[0]), config_path=str(files[1]))
        self._loaded = (voice.id, model)
        return model

    def speak(self, text: str, voice, rate: float, volume: float) -> None:
        import numpy as np
        import sounddevice as sd
        from piper import SynthesisConfig
        from app.voice.audio_guard import require_shared_mode, release_audio

        model = self._voice(voice)
        config = SynthesisConfig(length_scale=1.0 / max(0.5, min(2.0, rate)), volume=max(0.0, min(1.0, volume)))
        self._interrupt.clear()
        rate_hz = model.config.sample_rate
        require_shared_mode("neural_engine", "speaker")
        try:
            out_stream = sd.OutputStream(samplerate=rate_hz, channels=1, dtype="float32", exclusive=False)
            log.debug("Opened speaker in shared mode (exclusive=False)")
        except TypeError:
            out_stream = sd.OutputStream(samplerate=rate_hz, channels=1, dtype="float32")
            log.debug("Opened speaker (exclusive parameter not supported)")
        try:
            with out_stream as out:
                for chunk in model.synthesize(text, syn_config=config):
                    audio = chunk.audio_float_array.astype(np.float32)
                    # write in small blocks so stop() takes effect within ~0.1 s
                    step = rate_hz // 10
                    for i in range(0, len(audio), step):
                        if self._interrupt.is_set():
                            return
                        out.write(audio[i:i + step])
        finally:
            release_audio("neural_engine", "speaker")

    def stop(self) -> None:
        self._interrupt.set()


class Speaker:
    def __init__(self, rate: float | None = None, volume: float | None = None):
        self.rate_override, self.volume_override = rate, volume
        self._queue: queue.Queue[tuple[str, threading.Event, object] | None] = queue.Queue()
        self._ready = threading.Event()
        self.available = True
        self.speaking = False
        self.last_error = ""
        self._engines: dict[str, object] = {}
        self._current = None
        self._thread = threading.Thread(target=self._worker, daemon=True, name="tts")
        self._thread.start()
        self._ready.wait(timeout=10)

    # ---- engines ------------------------------------------------------------------------------
    def _engine(self, kind: str):
        if kind not in self._engines:
            try:
                self._engines[kind] = _NeuralEngine() if kind == "neural" else _SystemEngine()
            except Exception as e:
                log.warning("%s text-to-speech unavailable: %s", kind.capitalize(), e)
                self._engines[kind] = None
        return self._engines[kind]

    def _settings(self, voice_override=None):
        from app.voice.voices import get_voice

        cfg = get_config().voice
        voice = voice_override or get_voice(cfg.tts_voice)
        rate = self.rate_override if self.rate_override is not None else cfg.tts_rate
        volume = self.volume_override if self.volume_override is not None else cfg.tts_volume
        return voice, float(rate or 1.0), float(volume if volume is not None else 1.0)

    def _speak_now(self, text: str, voice_override=None) -> None:
        from app.voice.voices import get_voice

        voice, rate, volume = self._settings(voice_override)
        if voice.engine == "neural":
            engine = self._engine("neural")
            if engine is not None:
                try:
                    self._current = engine
                    engine.speak(text, voice, rate, volume)
                    self.last_error = ""
                    return
                except Exception as e:
                    self.last_error = str(e)
                    log.info("Neural voice %s unavailable (%s); using the Windows voice", voice.id, e)
            voice = get_voice("david" if voice.gender == "male" else "zira")
        engine = self._engine("system")
        if engine is None:
            raise RuntimeError("no speech engine available")
        self._current = engine
        engine.speak(text, voice, rate, volume)

    def _worker(self) -> None:
        # the system engine must exist on this thread before anything else; it's also the proof that
        # speech works at all on this computer
        if self._engine("system") is None and self._engine("neural") is None:
            self.available = False
            self._ready.set()
            return
        self._ready.set()
        while True:
            item = self._queue.get()
            if item is None:
                break
            text, done, voice = item
            try:
                if text:  # empty text is a "wait until everything before me is spoken" marker
                    self.speaking = True
                    self._speak_now(text, voice)
            except Exception as e:
                log.warning("Speech failed: %s", e)
            finally:
                self.speaking = False
                self._current = None
                done.set()

    # ---- public API -------------------------------------------------------------------------
    def say(self, text: str, wait: bool = True, voice=None) -> None:
        from app.runtime import in_call

        text = clean_for_speech(text)
        if not text or not self.available or in_call.is_set():
            return  # during a call Lyra stays silent so nothing is heard on the call
        done = threading.Event()
        self._queue.put((text, done, voice))
        if wait:
            done.wait()

    def preview(self, voice) -> None:
        """Say a short sample in `voice` (used by the Voice page), interrupting anything queued."""
        from app.voice.voices import SAMPLE_TEXT

        self.stop(interrupt=True)
        self.say(SAMPLE_TEXT.format(name=voice.name), wait=False, voice=voice)

    def stop(self, interrupt: bool = False) -> None:
        """Drop everything still waiting to be spoken. With `interrupt`, also cut off the current
        sentence (neural voices stop at once; Windows voices at the end of the word)."""
        while True:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                break
            if item is None:
                self._queue.put(None)
                break
            item[1].set()
        if interrupt and self._current is not None:
            self._current.stop()

    def wait_until_done(self) -> None:
        if not self.available:
            return
        done = threading.Event()
        self._queue.put(("", done, None))
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

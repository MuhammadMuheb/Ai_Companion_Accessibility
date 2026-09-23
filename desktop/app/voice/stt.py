"""Speech-to-text: record from the microphone until the user stops talking, then
transcribe locally with faster-whisper."""

from __future__ import annotations

import os

import numpy as np

from app.config import get_config
from app.logger import get_logger

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

log = get_logger(__name__)

SAMPLE_RATE = 16_000
BLOCK_SECONDS = 0.1
# Speech-level bounds (RMS). Laptop mic arrays are quiet: room noise here is ~0.0007 and
# normal speech a metre away is often only 0.005-0.02.
MIN_THRESHOLD = 0.004
MAX_THRESHOLD = 0.012
WAKE_WINDOW = 3.0  # seconds at the start of a clip where the wake word must be


def whisper_source(size: str) -> str:
    """A Whisper model shipped with the installer (models/whisper-<size>), else the name to download."""
    from app.config import ROOT

    bundled = ROOT / "models" / f"whisper-{size}"
    return str(bundled) if (bundled / "model.bin").exists() else size


def resolve_device(setting) -> str | None:
    """config voice.input_device: None/""/"auto" = follow the Windows default, else part of a mic's name.
    (Older settings stored a PortAudio device number; those fall back to the default.)"""
    if setting in (None, "", "default", "auto") or isinstance(setting, int) or str(setting).isdigit():
        return None
    return str(setting)


def beep(kind: str) -> None:
    """Audible cue so a blind user knows when to start and stop talking."""
    try:
        import winsound

        if kind == "start":
            winsound.Beep(880, 120)
        else:
            winsound.Beep(520, 90)
    except Exception:
        pass


def rms(block: np.ndarray) -> float:
    return float(np.sqrt(np.mean(block ** 2))) if block.size else 0.0


# Whisper invents these on silence / noise
HALLUCINATIONS = {"", "you", "thank you", "thanks", "thank you for watching", "thanks for watching", "bye",
                  "subscribe", "please subscribe", "okay", "so", "uh", "um", "hmm", "music"}


def is_hallucination(text: str) -> bool:
    t = "".join(c for c in text.lower() if c.isalnum() or c == " ").strip()
    return t in HALLUCINATIONS or len(t) < 2


class UtteranceDetector:
    """Decides when an utterance starts and ends, fed one 0.1 s block at a time.

    - The first IGNORE_START seconds are ignored (the Enter-key click is picked up by the mic).
    - Speech starts only after START_BLOCKS consecutive loud blocks, so a single click or tap
      doesn't count.
    - Once speech has started, recording stops after `silence_seconds` of quiet or `max_seconds`.
    - If speech never starts within `wait_seconds`: with `require_speech` (hands-free) nothing is
      returned; otherwise (push-to-talk) everything recorded is returned and Whisper decides.
    """
    IGNORE_START = 0.3
    START_BLOCKS = 2
    PRE_ROLL = 5  # blocks of audio kept from just before speech was detected

    def __init__(self, threshold: float, max_seconds: float = 15, silence_seconds: float = 1.2,
                 wait_seconds: float = 8, require_speech: bool = True):
        self.threshold = threshold
        self.max_blocks = int(max_seconds / BLOCK_SECONDS)
        self.silence_blocks = int(silence_seconds / BLOCK_SECONDS)
        self.wait_blocks = int(wait_seconds / BLOCK_SECONDS)
        self.ignore_blocks = int(self.IGNORE_START / BLOCK_SECONDS)
        self.require_speech = require_speech
        self.chunks: list[np.ndarray] = []
        self.started = False
        self.done = False
        self.peak = 0.0
        self._seen = 0
        self._loud_run = 0
        self._quiet_run = 0
        self._speech_start = 0

    def feed(self, block: np.ndarray) -> None:
        if self.done:
            return
        self._seen += 1
        self.chunks.append(block)
        if self._seen <= self.ignore_blocks:
            return
        level = rms(block)
        self.peak = max(self.peak, level)
        loud = level > self.threshold
        if not self.started:
            self._loud_run = self._loud_run + 1 if loud else 0
            if self._loud_run >= self.START_BLOCKS:
                self.started = True
                self._speech_start = max(0, len(self.chunks) - self.START_BLOCKS - self.PRE_ROLL)
            elif self._seen >= self.wait_blocks:
                self.done = True
            return
        self._quiet_run = 0 if loud else self._quiet_run + 1
        if self._quiet_run >= self.silence_blocks or len(self.chunks) >= self.max_blocks:
            self.done = True

    def audio(self) -> np.ndarray:
        if not self.started:
            if self.require_speech or not self.chunks:
                return np.zeros(0, dtype="float32")
            return np.concatenate(self.chunks[self.ignore_blocks:] or self.chunks)
        return np.concatenate(self.chunks[self._speech_start:])


class Listener:
    def __init__(self, model_size: str | None = None, language: str | None = None):
        cfg = get_config().voice
        self.model_size = model_size or cfg.stt_model_size
        self.language = (language if language is not None else cfg.language) or None
        self.device = resolve_device(cfg.input_device)
        name = get_config().user.name
        wake = ", ".join(p.title() for p in get_config().wake_phrases) if cfg.wake_enabled else ""
        self.prompt = (cfg.hint + (f" My name is {name}." if name and name != "User" else "")
                       + (f" {wake}." if wake else ""))
        self.wake_model_size = cfg.wake_model_size or self.model_size
        self._model = None
        self._wake_model = None
        self.noise_floor = 0.002
        self.last_peak = 0.0
        self.mic_name = ""

    @property
    def model(self):
        if self._model is None:
            from faster_whisper import WhisperModel

            log.info("Loading Whisper model '%s' (first time downloads it)", self.model_size)
            self._model = WhisperModel(whisper_source(self.model_size), device="cpu", compute_type="int8")
        return self._model

    @property
    def wake_model(self):
        if self.wake_model_size == self.model_size:
            return self.model
        if self._wake_model is None:
            from faster_whisper import WhisperModel

            log.info("Loading Whisper model '%s' for the wake word", self.wake_model_size)
            self._wake_model = WhisperModel(whisper_source(self.wake_model_size), device="cpu", compute_type="int8")
        return self._wake_model

    @property
    def threshold(self) -> float:
        # capped so a noisy calibration second (fan, typing) can't make the mic "deaf"
        return min(MAX_THRESHOLD, max(MIN_THRESHOLD, self.noise_floor * 3))

    def calibrate(self, seconds: float = 1.0) -> None:
        """Measure background noise so speech detection adapts to the room. Uses the median
        block level so a single click or cough doesn't raise the threshold."""
        from app.voice.mic import MicStream

        with MicStream(self.device) as mic:
            audio = mic.record(seconds)
            self.mic_name = mic.name
        block = int(BLOCK_SECONDS * SAMPLE_RATE)
        levels = [rms(audio[i:i + block]) for i in range(0, len(audio) - block + 1, block)]
        self.noise_floor = max(0.0005, float(np.median(levels)) if levels else 0.002)
        log.info("Mic noise floor %.4f, speech threshold %.4f", self.noise_floor, self.threshold)

    def record(self, max_seconds: float = 15, silence_seconds: float = 1.2, wait_seconds: float = 8,
               require_speech: bool = True, on_level=None, beeps: bool = False,
               should_stop=None) -> np.ndarray:
        """Record one utterance from the microphone (see UtteranceDetector for the stopping rules).
        `on_level(level, threshold, speech_started)` is called for every 0.1 s block;
        `should_stop()` returning True ends the recording early (e.g. a call started)."""
        from app.voice.mic import MicStream

        detector = UtteranceDetector(self.threshold, max_seconds, silence_seconds, wait_seconds, require_speech)
        block = int(BLOCK_SECONDS * SAMPLE_RATE)
        if beeps:
            beep("start")  # played before the stream opens so the mic doesn't pick it up
        with MicStream(self.device) as stream:
            while not detector.done:
                data = stream.read(block)
                detector.feed(data)
                if on_level:
                    on_level(rms(data), self.threshold, detector.started)
                if should_stop is not None and should_stop():
                    break
            self.mic_name = stream.name
        if beeps:
            beep("stop")
        self.last_peak = detector.peak
        audio = detector.audio()
        log.info("Recorded %.1fs, loudest block %.4f (threshold %.4f), speech detected: %s",
                 len(audio) / SAMPLE_RATE, detector.peak, self.threshold, detector.started)
        self._save_debug(audio)
        return audio

    def _save_debug(self, audio: np.ndarray) -> None:
        """Keep the latest recording so microphone problems can be diagnosed by listening to it."""
        if not audio.size:
            return
        try:
            import soundfile as sf

            cfg = get_config()
            sf.write(cfg.dir(cfg.storage.logs_dir) / "last_utterance.wav", audio, SAMPLE_RATE)
        except Exception as e:
            log.debug("Couldn't save debug audio: %s", e)

    @staticmethod
    def _normalise(audio: np.ndarray) -> np.ndarray | None:
        if audio.size < SAMPLE_RATE * 0.3:
            return None
        # A key click can be far louder than distant speech, so scale by a high percentile, not the max
        level = float(np.percentile(np.abs(audio), 99.5))
        if level < 1e-4:
            return None  # digital silence — mic muted or blocked
        return np.clip(audio * min(40.0, 0.5 / level), -1.0, 1.0).astype("float32")

    def transcribe_wake(self, audio: np.ndarray, seconds: float = WAKE_WINDOW) -> str:
        """Quick look at the start of a clip with the light wake model — only to spot the wake word."""
        audio = self._normalise(audio[: int(seconds * SAMPLE_RATE)])
        if audio is None:
            return ""
        text, _ = self._whisper(audio, vad=True, model=self.wake_model)
        return "" if is_hallucination(text) else text

    def transcribe(self, audio: np.ndarray) -> str:
        audio = self._normalise(audio)
        if audio is None:
            return ""
        text, lang = self._whisper(audio, vad=True)
        if not text:
            # Whisper's voice-activity filter sometimes drops quiet speech entirely; try once without it
            text, lang = self._whisper(audio, vad=False)
            text = "" if is_hallucination(text) else text
        log.info("Heard (%s): %s", lang, text)
        return text

    def _whisper(self, audio: np.ndarray, vad: bool, model=None) -> tuple[str, str]:
        kwargs = {"vad_parameters": {"threshold": 0.3, "min_silence_duration_ms": 500}} if vad else {}
        segments, info = (model or self.model).transcribe(
            audio, language=self.language, beam_size=1, vad_filter=vad,
            condition_on_previous_text=False, no_speech_threshold=0.6, initial_prompt=self.prompt or None,
            **kwargs,
        )
        segments = [s for s in segments if s.no_speech_prob < 0.8]
        return " ".join(s.text.strip() for s in segments).strip(), info.language

    def listen(self, **kwargs) -> str:
        return self.transcribe(self.record(**kwargs))


def mic_test(seconds: float = 10, device=None) -> None:
    """Show a live level meter so the user can check the microphone works and is loud enough."""
    from app.voice.mic import MicStream, list_microphones

    listener = Listener()
    if device is not None:
        listener.device = resolve_device(device)
    print("Stay quiet for a second...")
    listener.calibrate()
    print(f"Microphone: {listener.mic_name}")
    print(f"Room noise {listener.noise_floor:.4f}; speech must go above {listener.threshold:.4f}.")
    print(f"Now speak normally for {seconds:.0f} seconds (connect/disconnect earbuds to test switching):\n")
    block = int(BLOCK_SECONDS * SAMPLE_RATE)
    loudest = 0.0
    with MicStream(listener.device) as stream:
        for _ in range(int(seconds / BLOCK_SECONDS)):
            level = rms(stream.read(block))
            loudest = max(loudest, level)
            bar = "█" * min(50, int(level / 0.002))
            mark = " ← speech" if level > listener.threshold else ""
            print(f"\r{level:.4f} {bar:<50}{mark:<10}", end="", flush=True)
    print(f"\n\nLoudest: {loudest:.4f}")
    if loudest < 1e-4:
        print("The microphone sends silence. Check Windows Settings > Privacy & security > Microphone "
              "('Let desktop apps access your microphone') and that the mic isn't muted.")
    elif loudest < listener.threshold:
        print("Your voice didn't reach the speech level. Speak closer, raise the mic level in Windows "
              "Sound settings, or pick another mic with voice.input_device in config.yaml.")
    else:
        print("The microphone works. 👍")
    print("\nAvailable microphones (set voice.input_device in config.yaml to part of a name, or leave it null):")
    for name in list_microphones():
        print(f"  - {name}")

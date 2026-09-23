"""Microphone input that survives hardware changes.

Uses Windows' own audio system (WASAPI, via `soundcard`) instead of PortAudio, because
PortAudio reads the device list once at start-up and never notices AirPods/earbuds being
connected or disconnected.

`MicStream` delivers 16 kHz mono blocks and, while reading:
- follows the Windows default microphone — connect AirPods and it moves to them, take
  them out and it moves back to the laptop mic, without dropping the recording;
- recovers when the device disappears mid-read (reopens the new default);
- in "auto" mode, if the current mic sends pure digital silence for a few seconds (e.g.
  earbuds connected but their mic not active), switches to another microphone that
  actually delivers sound.
"""

from __future__ import annotations

import threading
import time
import warnings

import numpy as np

# soundcard warns about a harmless gap at the start of every recording
warnings.filterwarnings("ignore", message="data discontinuity in recording")

from app.config import get_config
from app.logger import get_logger

log = get_logger(__name__)

SAMPLE_RATE = 16_000
DEAD_SILENCE = 1e-5          # a real mic always has some noise; exact zeros mean "not delivering"
DEAD_SECONDS = 3.0
DEFAULT_CHECK_SECONDS = 1.0

_com_lock = threading.Lock()


def _sc():
    import soundcard

    return soundcard


def list_microphones() -> list[str]:
    try:
        return [m.name for m in _sc().all_microphones()]
    except Exception as e:
        log.warning("Couldn't list microphones: %s", e)
        return []


def default_microphone_name() -> str:
    try:
        return _sc().default_microphone().name
    except Exception:
        return ""


def _find(name: str):
    name = name.lower()
    for m in _sc().all_microphones():
        if name in m.name.lower():
            return m
    return None


class MicStream:
    """Context manager: `with MicStream() as mic: block = mic.read(1600)`."""

    def __init__(self, device: str | None = None, follow_default: bool = True):
        setting = device if device is not None else get_config().voice.input_device
        self.fixed = str(setting).strip() if setting not in (None, "", "default", "auto") else None
        self.follow_default = follow_default and self.fixed is None
        self.name = ""
        self._mic = None
        self._rec = None
        self._silent_blocks = 0
        self._last_check = 0.0
        self._dead: dict[str, float] = {}  # mic id -> until when to avoid it
        self.switches = 0

    # ---- choosing a microphone ----------------------------------------------------
    def _choose(self):
        sc = _sc()
        if self.fixed:
            mic = _find(self.fixed)
            if mic is not None:
                return mic
            log.warning("Microphone %r not found; using the default", self.fixed)
        now = time.time()
        default = sc.default_microphone()
        if self._dead.get(default.id, 0) < now:
            return default
        for mic in sc.all_microphones():  # default is silent: try the others
            if self._dead.get(mic.id, 0) < now:
                return mic
        return default

    def _open(self) -> None:
        self._close()
        mic = self._choose()
        self._rec = mic.recorder(samplerate=SAMPLE_RATE, channels=1, blocksize=int(SAMPLE_RATE * 0.1))
        self._rec.__enter__()
        if self._mic is not None and mic.id != self._mic.id:
            self.switches += 1
            log.info("Microphone switched: %s -> %s", self.name, mic.name)
        self._mic, self.name = mic, mic.name
        self._silent_blocks = 0
        self._last_check = time.time()

    def _close(self) -> None:
        if self._rec is not None:
            try:
                self._rec.__exit__(None, None, None)
            except Exception:
                pass
            self._rec = None

    def __enter__(self) -> "MicStream":
        self._open()
        return self

    def __exit__(self, *exc) -> None:
        self._close()

    # ---- reading --------------------------------------------------------------------
    def _maybe_follow_default(self) -> None:
        if not self.follow_default or time.time() - self._last_check < DEFAULT_CHECK_SECONDS:
            return
        self._last_check = time.time()
        try:
            default = _sc().default_microphone()
        except Exception:
            return
        if default.id != self._mic.id and self._dead.get(default.id, 0) < time.time():
            log.info("Windows default microphone changed to %s", default.name)
            self._open()

    def read(self, frames: int) -> np.ndarray:
        for attempt in range(5):
            try:
                self._maybe_follow_default()
                data = self._rec.record(numframes=frames)
                block = data[:, 0] if data.ndim == 2 else data
                if block.size < frames:
                    block = np.pad(block, (0, frames - block.size))
                if np.abs(block).max() < DEAD_SILENCE:
                    self._silent_blocks += 1
                    if self.fixed is None and self._silent_blocks * frames / SAMPLE_RATE >= DEAD_SECONDS:
                        log.info("%s delivers no sound; trying another microphone", self.name)
                        self._dead[self._mic.id] = time.time() + 30
                        self._open()
                else:
                    self._silent_blocks = 0
                return block.astype(np.float32)
            except Exception as e:  # device unplugged / Bluetooth profile change
                log.warning("Microphone read failed (%s); reopening", e)
                time.sleep(0.3 * (attempt + 1))
                try:
                    self._open()
                except Exception as e2:
                    log.warning("Reopen failed: %s", e2)
        return np.zeros(frames, dtype=np.float32)

    def record(self, seconds: float) -> np.ndarray:
        frames = int(SAMPLE_RATE * 0.1)
        return np.concatenate([self.read(frames) for _ in range(max(1, int(seconds / 0.1)))])

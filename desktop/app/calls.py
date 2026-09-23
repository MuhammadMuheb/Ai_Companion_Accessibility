"""Calls: notice when a call starts or ends, record it and take notes only if the user says yes,
then talk the call through afterwards.

How a call is detected: Windows records which app is using the microphone right now
(Settings > Privacy > Microphone keeps this in the registry). When WhatsApp, Teams, Zoom,
Skype, Discord, Phone Link — or a browser showing a Meet/Zoom/Teams/WhatsApp page — starts using
the mic, a call has started; when it stops, the call has ended. Incoming-call notifications
("... is calling") are announced as they arrive.

During a call Lyra stays silent (no speech, no wake word) so nothing of it is heard in the call.
Recording is never automatic: a small pop-up asks every time, and reminds the user that the
other people on the call must agree to being recorded.
"""

from __future__ import annotations

import re
import sys
import threading
import time
import winreg
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

import numpy as np

from app.config import get_config
from app.logger import audit, get_logger
from app.runtime import in_call

log = get_logger(__name__)

MIC_KEY = r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone"
CALL_APPS = {
    "whatsapp": "WhatsApp", "teams": "Teams", "msteams": "Teams", "zoom": "Zoom", "skype": "Skype",
    "discord": "Discord", "slack": "Slack", "telegram": "Telegram", "yourphone": "Phone Link",
    "phonelink": "Phone Link", "signal": "Signal", "viber": "Viber", "webex": "Webex",
}
BROWSERS = {"chrome.exe", "msedge.exe", "brave.exe", "firefox.exe", "opera.exe"}
BROWSER_CALL_TITLES = re.compile(r"\bmeet\b|zoom|teams|whatsapp|discord|call|webex|skype", re.I)
IGNORE = {"python.exe", "pythonw.exe"}  # Lyra itself
INCOMING = re.compile(r"incoming (?:voice |video )?call|is calling|calling you|voice call|video call", re.I)
SAMPLE_RATE = 16_000


def _app_label(key_name: str) -> str | None:
    low = key_name.lower()
    for token, label in CALL_APPS.items():
        if token in low:
            return label
    return None


def mic_users() -> list[str]:
    """Registry key names of apps using the microphone right now (LastUsedTimeStop == 0)."""
    users = []
    for sub in ("", "\\NonPackaged"):
        try:
            root = winreg.OpenKey(winreg.HKEY_CURRENT_USER, MIC_KEY + sub)
        except OSError:
            continue
        i = 0
        while True:
            try:
                name = winreg.EnumKey(root, i)
            except OSError:
                break
            i += 1
            if name == "NonPackaged":
                continue
            try:
                with winreg.OpenKey(root, name) as k:
                    start = winreg.QueryValueEx(k, "LastUsedTimeStart")[0]
                    stop = winreg.QueryValueEx(k, "LastUsedTimeStop")[0]
            except OSError:
                continue
            if start and not stop:
                users.append(name)
        winreg.CloseKey(root)
    return users


def _browser_call_title() -> str | None:
    try:
        import pygetwindow

        for title in pygetwindow.getAllTitles():
            if title and BROWSER_CALL_TITLES.search(title):
                return title
    except Exception:
        pass
    return None


def active_call_app(users: list[str] | None = None) -> str | None:
    """Which calling app is using the mic, if any."""
    for key in users if users is not None else mic_users():
        exe = key.split("#")[-1].lower()
        if exe in IGNORE:
            continue
        label = _app_label(key)
        if label:
            return label
        if exe in BROWSERS and _browser_call_title():
            return "a browser call"
    return None


# ---- recording ------------------------------------------------------------------------------

class CallRecorder:
    """Records your microphone and the computer's sound output (the other people) to two WAV
    files, written as it goes so nothing is lost if the app closes."""

    def __init__(self, folder: Path):
        self.folder = folder
        self.folder.mkdir(parents=True, exist_ok=True)
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self.started_at = datetime.now()
        self.errors: list[str] = []

    def _loop(self, name: str, open_source) -> None:
        import soundfile as sf

        try:
            with sf.SoundFile(self.folder / f"{name}.wav", "w", SAMPLE_RATE, 1) as out, open_source() as src:
                while not self._stop.is_set():
                    out.write(src(1600))
        except Exception as e:
            log.warning("Call recording (%s) failed: %s", name, e)
            self.errors.append(f"{name}: {e}")

    def start(self) -> None:
        from contextlib import contextmanager

        import soundcard as sc

        from app.voice.mic import MicStream

        @contextmanager
        def mic():
            with MicStream() as m:
                yield m.read

        @contextmanager
        def loopback():
            speaker = sc.default_speaker()
            source = sc.get_microphone(speaker.name, include_loopback=True)
            with source.recorder(samplerate=SAMPLE_RATE, channels=1, blocksize=1600) as r:
                yield lambda n: r.record(numframes=n)[:, 0]

        for name, src in (("me", mic), ("them", loopback)):
            t = threading.Thread(target=self._loop, args=(name, src), daemon=True, name=f"call-rec-{name}")
            t.start()
            self._threads.append(t)
        audit("call_recording_started", folder=str(self.folder))

    def stop(self) -> Path:
        self._stop.set()
        for t in self._threads:
            t.join(timeout=5)
        audit("call_recording_stopped", folder=str(self.folder),
              seconds=int((datetime.now() - self.started_at).total_seconds()))
        return self.folder


# ---- notes -----------------------------------------------------------------------------------

NOTES_SYSTEM = """You write short notes about a phone call for the user ("Me").
From the transcript write, in simple English with Roman Urdu words kept as they are:
Summary: 2-3 sentences.
Action items: a bullet list of concrete things "Me" should do (or "none").
Mood: one line on how the call went.
Only use what is in the transcript."""


def transcribe_call(folder: Path, listener) -> str:
    """Interleave what I said and what they said, by time."""
    import soundfile as sf

    parts = []
    for who, label in (("me", "Me"), ("them", "Them")):
        path = folder / f"{who}.wav"
        if not path.exists() or path.stat().st_size < 10_000:
            continue
        audio, _ = sf.read(path, dtype="float32")
        if audio.ndim > 1:
            audio = audio[:, 0]
        peak = float(np.percentile(np.abs(audio), 99.5)) if audio.size else 0
        if peak < 1e-4:
            continue
        audio = np.clip(audio * min(20.0, 0.5 / peak), -1, 1).astype("float32")
        segments, _ = listener.model.transcribe(audio, language=listener.language, beam_size=1, vad_filter=True,
                                                condition_on_previous_text=False)
        parts += [(s.start, label, s.text.strip()) for s in segments if s.text.strip()]
    parts.sort()
    lines = [f"[{int(t // 60):02d}:{int(t % 60):02d}] {who}: {text}" for t, who, text in parts]
    (folder / "transcript.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return "\n".join(lines)


def summarize(transcript: str, llm=None) -> str:
    from app.expert import get_expert

    if not transcript.strip():
        return "Summary: I couldn't make out any speech in the recording.\nAction items: none"
    expert = get_expert()
    return expert.ask(f"Transcript:\n{transcript[-12000:]}", system=NOTES_SYSTEM, max_tokens=500, purpose="call notes")


def action_items(notes: str) -> list[str]:
    m = re.search(r"action items?:\s*(.+?)(?:\n\s*mood:|\Z)", notes, re.I | re.S)
    if not m:
        return []
    items = [re.sub(r"^[\s*\-•\d.)]+", "", ln).strip() for ln in m.group(1).splitlines()]
    return [i for i in items if i and i.lower() not in {"none", "none.", "n/a"}]


# ---- the watcher -------------------------------------------------------------------------------

@dataclass
class CallState:
    app: str
    started: datetime = field(default_factory=datetime.now)
    recorder: CallRecorder | None = None
    popup: object | None = None
    decided: bool = False


class CallWatcher(threading.Thread):
    """Polls the microphone registry; drives the pop-up, recording and the debrief."""

    def __init__(self, on_start: Callable[[CallState], None], on_end: Callable[[CallState], None],
                 ask_record: Callable[[CallState, Callable[[bool], None]], None]):
        super().__init__(daemon=True, name="call-watcher")
        self.on_start, self.on_end, self.ask_record = on_start, on_end, ask_record
        self._stop = threading.Event()
        self.call: CallState | None = None
        self._quiet_since: float | None = None

    def stop(self) -> None:
        self._stop.set()

    def _folder(self, app: str) -> Path:
        cfg = get_config()
        return cfg.dir("data/calls") / f"{datetime.now():%Y-%m-%d_%H%M%S}_{re.sub(r'[^A-Za-z]+', '', app)}"

    def decide(self, record: bool) -> None:
        call = self.call
        if call is None or call.decided:
            return
        call.decided = True
        if record:
            call.recorder = CallRecorder(self._folder(call.app))
            call.recorder.start()
            log.info("Recording call on %s", call.app)

    def run(self) -> None:
        while not self._stop.is_set():
            try:
                app = active_call_app() if get_config().feature_on("calls") else None
            except Exception as e:
                log.debug("Call check failed: %s", e)
                app = None
            if app and self.call is None:
                self.call = CallState(app)
                in_call.set()
                log.info("Call started on %s", app)
                self.on_start(self.call)
                self.ask_record(self.call, self.decide)
            elif self.call is not None and not app:
                # a short gap (mute/unmute, switching devices) is not the end of the call
                self._quiet_since = self._quiet_since or time.time()
                if time.time() - self._quiet_since >= 4:
                    call, self.call, self._quiet_since = self.call, None, None
                    if call.recorder:
                        call.recorder.stop()
                    in_call.clear()
                    log.info("Call on %s ended after %s", call.app, datetime.now() - call.started)
                    threading.Thread(target=self.on_end, args=(call,), daemon=True).start()
            elif app:
                self._quiet_since = None
            self._stop.wait(2)


def is_incoming_call(title: str, body: str) -> bool:
    return bool(INCOMING.search(f"{title} {body}"))

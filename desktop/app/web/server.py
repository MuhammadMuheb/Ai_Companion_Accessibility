"""Lyra's API — used in-process by the native window (see app.daemon.Bridge) and, optionally,
over HTTP by the legacy browser mode. The window is a voice-first mirror: it shows what Lyra hears
and says, lets you pick her voice, and holds the settings, routines, accounts and memory.

The wake word, global hotkeys and notification monitor run here in the background, using
this computer's microphone and speakers; what they hear and say also appears in the page.

Listens on 127.0.0.1 only. Every API call must carry a random per-launch token that is
embedded in the page, so other websites open in the same browser can't drive the companion.
"""

from __future__ import annotations

import json
import re
import secrets
import tempfile
import threading
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime
from itertools import count
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app.automation import accounts as accounts_mod
from app.command_parser import parse
from app.companion import Companion
from app import settings_service
from app.config import get_config, read_settings, save_settings
from app.interaction import match_option
from app.llm import get_llm
from app.logger import get_logger
from app.mentor import notifications, prayer
from app.scheduler import CompanionScheduler
from app.workflow import EXTRA_FEATURES, FEATURES
from app.workflow_extended import ExtendedWorkflow

log = get_logger(__name__)

STATIC = Path(__file__).parent / "static"
TOKEN = secrets.token_urlsafe(24)

EDITABLE = settings_service.EDITABLE


class State:
    def __init__(self):
        self.lock = threading.Lock()  # the workflow/companion are used by one caller at a time
        self.companion = Companion()
        self.scheduler = CompanionScheduler(self.companion.db)
        self.scheduler.start()
        self.workflow = ExtendedWorkflow(self.companion, self.scheduler)
        self.workflow.after_settings = self._after_settings
        self._listener = None
        self._listener_lock = threading.Lock()
        self.voice = None
        self.services = None
        self.events: deque[dict] = deque(maxlen=100)
        self.enroll_clips: list = []
        self.extra_hotkeys: dict = {}  # the native app adds "open_window"
        self._ids = count(1)
        notifications.add_listener(self.push_notification)

    # ---- events shown in the page ---------------------------------------------------
    def push(self, kind: str, text: str, title: str = "", speak: bool = False) -> None:
        self.events.append({"id": next(self._ids), "kind": kind, "title": title, "message": text, "speak": speak,
                            "time": datetime.now().strftime("%I:%M %p").lstrip("0")})

    def push_notification(self, title: str, message: str) -> None:
        # the voice engine reads notifications aloud while it runs; until it has started, the
        # speaker reads them directly (the window itself never produces sound)
        self.push("notification", message, title)
        if self.voice is None and get_config().voice.enabled:
            from app.voice.tts import get_speaker

            threading.Thread(target=lambda: get_speaker().say(f"{title}. {message}"), daemon=True).start()

    def push_voice(self, kind: str, text: str) -> None:
        self.push(kind, text)

    # ---- speech ------------------------------------------------------------------------
    @property
    def listener(self):
        with self._listener_lock:
            if self._listener is None:
                from app.voice.stt import Listener

                self._listener = Listener()
                _ = self._listener.model  # load now so the first transcription isn't slow
            return self._listener

    def start_background(self) -> None:
        """Voice engine + wake word + hotkeys + notification monitor (after the speech model loads)."""
        from app.services import Services

        voice = None
        cfg = get_config()
        if cfg.voice.enabled:
            try:
                from app.accessibility.voice_engine import VoiceEngine

                voice = VoiceEngine(listener=self.listener, echo=False, on_event=self.push_voice)
            except Exception as e:
                log.warning("Voice engine unavailable: %s", e)
        self.voice = voice
        self.services = Services(self.workflow, self.companion, voice, lock=self.lock, on_event=self.push_voice)
        self.services.extra_hotkeys = dict(self.extra_hotkeys)
        self.services.start()

    def restart_background(self, reload_speech: bool) -> None:
        if self.services:
            self.services.stop()
        if self.voice:
            self.voice.close()
            self.voice = None
        if reload_speech:
            with self._listener_lock:
                self._listener = None
        threading.Thread(target=self.start_background, daemon=True).start()

    def _after_settings(self, result) -> None:
        """A voice command changed settings: restart what it affects (off the caller's thread)."""
        if result.scheduler_changed:
            self.restart_scheduler()
        if result.background_changed:
            self.restart_background(reload_speech=result.speech_changed)

    def restart_scheduler(self) -> None:
        self.scheduler.shutdown()
        self.scheduler = CompanionScheduler(self.companion.db)
        self.scheduler.start()
        self.workflow.scheduler = self.scheduler


state: State | None = None


@asynccontextmanager
async def lifespan(_app):
    st = get_state()
    threading.Thread(target=st.start_background, daemon=True).start()
    yield
    if st.services:
        st.services.stop()
    st.scheduler.shutdown()


app = FastAPI(title="Lyra", docs_url=None, redoc_url=None, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


def require_token(x_companion_token: str = Header(default="")) -> None:
    if not secrets.compare_digest(x_companion_token, TOKEN):
        raise HTTPException(status_code=403, detail="Missing or wrong token — reload the page.")


def get_state() -> State:
    global state
    if state is None:
        state = State()
    return state


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    html = (STATIC / "index.html").read_text(encoding="utf-8").replace("__TOKEN__", TOKEN)
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


# ---- chat -------------------------------------------------------------------

@app.post("/api/message", dependencies=[Depends(require_token)])
def message(body: dict) -> StreamingResponse:
    """Run a message. If the task needs an answer from the user, an `ask` event is returned;
    the page then sends the same text again with `answers` filled in."""
    text = str(body.get("text", "")).strip()
    answers = {str(k): str(v) for k, v in (body.get("answers") or {}).items()}
    if not text:
        raise HTTPException(400, "Empty message")
    st = get_state()

    def events():
        with st.lock:
            pending: list[tuple[str, list[str] | None]] = []

            def ask(question: str, options: list[str] | None = None) -> str | None:
                if question in answers:
                    answer = answers[question]
                    return (match_option(answer, options) or None) if options else (answer or None)
                pending.append((question, options))
                return None

            st.workflow.ask, st.workflow._confirm = ask, None
            intent, result = st.workflow.run_text(text)
            if pending:
                question, options = pending[0]
                yield _line({"type": "ask", "question": question, "options": options, "text": text})
                return
            if result is not None:
                yield _line({"type": "reply", "intent": intent.name, "text": result or "Done."})
                return
            reply = []
            yield _line({"type": "start", "intent": "chat"})
            for piece in st.companion.reply_stream(text):
                reply.append(piece)
                yield _line({"type": "token", "text": piece})
            st.workflow.context.log(text, "chat", "".join(reply))
            yield _line({"type": "done"})

    return StreamingResponse(events(), media_type="application/x-ndjson")


def _line(obj: dict) -> str:
    return json.dumps(obj, ensure_ascii=False) + "\n"


@app.post("/api/transcribe", dependencies=[Depends(require_token)])
async def transcribe(audio: UploadFile) -> dict:
    data = await audio.read()
    if len(data) < 1000:
        return {"text": ""}
    suffix = Path(audio.filename or "clip.webm").suffix or ".webm"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
        f.write(data)
        path = f.name
    try:
        from faster_whisper import decode_audio

        samples = decode_audio(path, sampling_rate=16_000)
        text = get_state().listener.transcribe(samples)
    finally:
        Path(path).unlink(missing_ok=True)
    return {"text": text}


@app.post("/api/wake/{action}", dependencies=[Depends(require_token)])
def wake_control(action: str) -> dict:
    """The page pauses the background wake listener while it records with the browser mic."""
    services = get_state().services
    wake = services.wake if services else None
    if wake is not None:
        if action == "pause":
            wake.pause()
        elif action == "resume":
            wake.resume()
    return {"wake": wake is not None and not wake.paused, "last_heard": wake.last_heard if wake else ""}


@app.get("/api/events", dependencies=[Depends(require_token)])
def events(after: int = 0) -> dict:
    st = get_state()
    wake = st.services.wake if st.services else None
    from app.runtime import in_call
    from app.voice.voiceprint import get_voiceprint

    voice = st.voice
    return {"events": [e for e in st.events if e["id"] > after],
            "status": {"phase": getattr(voice, "phase", "idle") if voice else "offline",
                       "level": round(getattr(voice, "level", 0.0), 3) if voice else 0.0,
                       "voice_ready": voice is not None,
                       "wake": wake is not None, "wake_paused": bool(wake and wake.paused),
                       "in_call": in_call.is_set(), "voiceprints": len(get_voiceprint().profiles()),
                       "mouse_reading": bool(st.services and st.services.mouse),
                       "monitor": bool(st.services and st.services.monitor),
                       "hotkeys": bool(st.services and st.services.hotkeys),
                       "phrases": get_config().wake_phrases, "name": get_config().assistant.name}}


# ---- settings ---------------------------------------------------------------

def _settings_payload() -> dict:
    cfg = get_config()
    return {
        "settings": settings_service.current_values(),
        "features": settings_service.features(),
        "models": get_llm().available_models(),
        "microphones": _microphones(),
    }


def _microphones() -> list[dict]:
    from app.voice.mic import list_microphones

    return [{"index": name, "name": name} for name in list_microphones()]


# validation lives in app.settings_service (shared with the native Settings window)
_coerce = settings_service.coerce
KEEP_DEFAULT = settings_service.KEEP_DEFAULT


@app.get("/api/settings", dependencies=[Depends(require_token)])
def get_settings() -> dict:
    return _settings_payload()


@app.put("/api/settings", dependencies=[Depends(require_token)])
def put_settings(body: dict) -> dict:
    st = get_state()
    with st.lock:
        result = settings_service.apply(body)
        if result.scheduler_changed:
            st.restart_scheduler()
    if result.background_changed:
        st.restart_background(reload_speech=result.speech_changed)
    return {**_settings_payload(), "errors": result.errors}


@app.post("/api/mic-test", dependencies=[Depends(require_token)])
def mic_test(body: dict) -> dict:
    """Record 4 s from a microphone and report how loud it was and what was heard."""
    import numpy as np

    from app.voice.mic import MicStream
    from app.voice.stt import SAMPLE_RATE

    st = get_state()
    wake = st.services.wake if st.services else None
    if wake:
        wake.pause()
    try:
        with MicStream(body.get("device") or None) as mic:
            audio = mic.record(4)
            mic_name = mic.name
        block = SAMPLE_RATE // 10
        levels = [float(np.sqrt(np.mean(audio[i:i + block] ** 2))) for i in range(0, len(audio) - block, block)]
        text = st.listener.transcribe(audio)
    except Exception as e:
        raise HTTPException(400, f"Microphone error: {e}")
    finally:
        if wake:
            wake.resume()
    loudest = max(levels) if levels else 0.0
    verdict = ("silent — check Windows microphone privacy settings" if loudest < 1e-4 else
               "very quiet — speak closer or pick another mic" if loudest < 0.004 else "good")
    return {"loudest": round(loudest, 4), "noise": round(float(np.median(levels)) if levels else 0, 4),
            "verdict": verdict, "heard": text, "mic": mic_name}


# ---- accounts & contacts --------------------------------------------------------

@app.get("/api/accounts", dependencies=[Depends(require_token)])
def get_accounts() -> dict:
    cfg = get_config()
    profiles = [{"label": a.label, "browser": a.browser, "profile": a.profile, "email": a.email}
                for a in accounts_mod.browser_profiles()]
    platforms = sorted({k for k in accounts_mod.PLATFORMS if k not in ("email", "x", "drive", "calendar")})
    return {"profiles": profiles, "platforms": platforms, "accounts": cfg.accounts or {}, "contacts": cfg.contacts or {}}


@app.put("/api/accounts", dependencies=[Depends(require_token)])
def put_accounts(body: dict) -> dict:
    accounts: dict[str, list[dict]] = {}
    for platform, items in (body.get("accounts") or {}).items():
        if platform not in accounts_mod.PLATFORMS:
            continue
        clean = []
        for a in items or []:
            if not isinstance(a, dict) or not a.get("profile"):
                continue
            clean.append({"label": str(a.get("label") or a["profile"]).strip(), "browser": str(a.get("browser") or "chrome"),
                          "profile": str(a["profile"]), "google_index": int(a.get("google_index") or 0),
                          "email": str(a.get("email") or "")})
        if clean:
            accounts[platform] = clean
    contacts: dict[str, dict] = {}
    for c in body.get("contacts") or []:
        spoken = str(c.get("spoken", "")).strip()
        if not spoken:
            continue
        contacts[spoken] = {"whatsapp": str(c.get("whatsapp") or spoken).strip(), "phone": str(c.get("phone") or "").strip()}
    with get_state().lock:
        save_settings({"accounts": accounts, "contacts": contacts})
    return get_accounts()


# ---- my commands (routines) ---------------------------------------------------

@app.get("/api/commands", dependencies=[Depends(require_token)])
def get_commands() -> dict:
    return {"commands": [{"phrase": k, "steps": v} for k, v in (get_config().routines or {}).items()]}


@app.put("/api/commands", dependencies=[Depends(require_token)])
def put_commands(body: dict) -> dict:
    routines: dict[str, list[str]] = {}
    for item in body.get("commands", []):
        phrase = str(item.get("phrase", "")).strip()
        steps = [str(s).strip() for s in item.get("steps", []) if str(s).strip()]
        if phrase and steps:
            routines[phrase] = steps
    with get_state().lock:
        save_settings({"routines": routines})
    return get_commands()


@app.post("/api/commands/preview", dependencies=[Depends(require_token)])
def preview_command(body: dict) -> dict:
    """Show how each step will be understood, without running anything."""
    out = []
    for step in body.get("steps", []):
        intent = parse(str(step), use_llm=False)
        out.append({"step": step, "intent": intent.name, "args": intent.args})
    return {"steps": out}


# ---- memory, goals, reminders -----------------------------------------------------

@app.get("/api/memory", dependencies=[Depends(require_token)])
def memory() -> dict:
    db = get_state().companion.db
    return {
        "memories": [{"id": m.id, "content": m.content, "category": m.category, "importance": m.importance}
                     for m in db.all_memories()],
        "goals": [{"id": g.id, "title": g.title} for g in db.goals()],
        "reminders": [{"id": r["id"], "text": r["text"], "due_at": r["due_at"]} for r in db.pending_reminders()],
        "prayer": prayer.describe_today() if get_config().feature_on("prayer") else "",
    }


@app.post("/api/memories", dependencies=[Depends(require_token)])
def add_memory(body: dict) -> dict:
    text = str(body.get("content", "")).strip()
    if text:
        get_state().companion.memory.remember(text, importance=4)
    return memory()


@app.delete("/api/memories/{mem_id}", dependencies=[Depends(require_token)])
def delete_memory(mem_id: int) -> dict:
    get_state().companion.memory.forget(mem_id)
    return memory()


@app.post("/api/goals", dependencies=[Depends(require_token)])
def add_goal(body: dict) -> dict:
    title = str(body.get("title", "")).strip()
    if title:
        get_state().companion.db.add_goal(title)
    return memory()


@app.post("/api/goals/{goal_id}/done", dependencies=[Depends(require_token)])
def goal_done(goal_id: int) -> dict:
    get_state().companion.db.set_goal_status(goal_id, "done")
    return memory()


@app.delete("/api/reminders/{reminder_id}", dependencies=[Depends(require_token)])
def cancel_reminder(reminder_id: int) -> dict:
    get_state().companion.db.complete_reminder(reminder_id)
    return memory()


# ---- voice print ---------------------------------------------------------------------------

@app.get("/api/voiceprint", dependencies=[Depends(require_token)])
def voiceprint_status() -> dict:
    from app.voice.voiceprint import MAX_PROFILES, get_voiceprint

    st = get_state()
    return {"profiles": sorted(get_voiceprint().profiles()), "max": MAX_PROFILES, "clips": len(st.enroll_clips),
            "sentences": _enrol_sentences()}


def _enrol_sentences() -> list[str]:
    from app.services import Services

    return Services.ENROL_SENTENCES


@app.post("/api/voiceprint/clip", dependencies=[Depends(require_token)])
def voiceprint_clip() -> dict:
    """Record one enrolment sentence with the same microphone the wake word uses."""
    from app.voice.mic import MicStream
    from app.voice.stt import UtteranceDetector, beep

    st = get_state()
    wake = st.services.wake if st.services else None
    if wake:
        wake.pause()
    try:
        beep("start")
        detector = UtteranceDetector(st.listener.threshold, max_seconds=8, silence_seconds=1.0, wait_seconds=6,
                                     require_speech=False)
        with MicStream() as mic:
            while not detector.done:
                detector.feed(mic.read(1600))
        beep("stop")
        audio = detector.audio()
        heard = st.listener.transcribe(audio)
    finally:
        if wake:
            wake.resume()
    st.enroll_clips = (st.enroll_clips + [audio])[-3:]
    return {"clips": len(st.enroll_clips), "heard": heard}


@app.post("/api/voiceprint/save", dependencies=[Depends(require_token)])
def voiceprint_save(body: dict) -> dict:
    from app.voice.voiceprint import VoiceprintError, get_voiceprint

    st = get_state()
    if len(st.enroll_clips) < 3:
        raise HTTPException(400, "Record all three sentences first.")
    try:
        name = get_voiceprint().enroll(str(body.get("name") or get_config().user.name or "Me"), st.enroll_clips)
    except VoiceprintError as e:
        raise HTTPException(400, str(e))
    st.enroll_clips = []
    return {"saved": name, **voiceprint_status()}


@app.delete("/api/voiceprint/{name}", dependencies=[Depends(require_token)])
def voiceprint_delete(name: str) -> dict:
    from app.voice.voiceprint import get_voiceprint

    get_voiceprint().delete(name)
    return voiceprint_status()


# ---- expert usage, calls, background ---------------------------------------------------------

@app.get("/api/expert", dependencies=[Depends(require_token)])
def expert_status() -> dict:
    import os

    from app.expert import get_expert

    expert = get_expert()
    cfg = get_config().expert
    return {"week": expert.ledger.this_week(), "budget": cfg.weekly_budget_usd, "left": round(expert.budget_left(), 4),
            "provider": cfg.provider, "credentials_set": bool(os.environ.get("ANTHROPIC_API_KEY")),
            "last_backend": expert.last_backend}


@app.post("/api/calls/decide", dependencies=[Depends(require_token)])
def calls_decide(body: dict) -> dict:
    st = get_state()
    if st.services is None or st.services.calls is None or st.services.calls.call is None:
        raise HTTPException(400, "No call in progress.")
    st.services.decide_call(bool(body.get("record")))
    return {"ok": True}


@app.post("/api/listen", dependencies=[Depends(require_token)])
def listen() -> dict:
    """Record one utterance with Lyra's own microphone (the native window has no browser mic)."""
    st = get_state()
    if st.voice is None:
        raise HTTPException(400, "Voice is still starting — try again in a few seconds.")
    wake = st.services.wake if st.services else None
    if wake:
        wake.pause()
    try:
        text = st.voice.listen(wait_seconds=8)
    finally:
        if wake:
            wake.resume()
    return {"text": text}


# ---- voice-first controls -------------------------------------------------------------------------

@app.post("/api/talk", dependencies=[Depends(require_token)])
def talk() -> dict:
    """Start a spoken conversation now (same as saying the wake word or pressing the talk hotkey)."""
    st = get_state()
    if st.voice is None or st.services is None:
        raise HTTPException(400, "Voice is still starting — try again in a few seconds.")
    threading.Thread(target=st.services.talk_hotkey, daemon=True, name="talk").start()
    return {"ok": True}


@app.post("/api/run", dependencies=[Depends(require_token)])
def run_spoken(body: dict) -> dict:
    """Run a command as if it had been spoken; the answer is spoken and appears in the window."""
    text = str(body.get("text", "")).strip()
    if not text:
        raise HTTPException(400, "Nothing to run.")
    st = get_state()
    if st.voice is None or st.services is None:
        raise HTTPException(400, "Voice is still starting — try again in a few seconds.")
    st.services.run_in_background(text)
    return {"ok": True}


@app.post("/api/speak/stop", dependencies=[Depends(require_token)])
def stop_speaking() -> dict:
    from app.voice.tts import get_speaker

    get_speaker().stop(interrupt=True)
    return {"ok": True}


@app.get("/api/voices", dependencies=[Depends(require_token)])
def voices() -> dict:
    from app.voice import voices as voices_mod

    return voices_mod.catalogue()


@app.post("/api/voices/{voice_id}/download", dependencies=[Depends(require_token)])
def download_voice(voice_id: str) -> dict:
    from app.voice import voices as voices_mod

    if voice_id not in voices_mod.BY_ID:
        raise HTTPException(404, "Unknown voice.")
    voices_mod.downloads.start(voice_id)
    return voices_mod.catalogue()


@app.post("/api/voices/{voice_id}/preview", dependencies=[Depends(require_token)])
def preview_voice(voice_id: str) -> dict:
    from app.voice import voices as voices_mod
    from app.voice.tts import get_speaker

    voice = voices_mod.BY_ID.get(voice_id)
    if voice is None:
        raise HTTPException(404, "Unknown voice.")
    speaker = get_speaker()
    if not speaker.available:
        raise HTTPException(400, "Speech isn't available on this computer.")
    speaker.preview(voice)
    ready = voices_mod.is_ready(voice)
    return {"ok": True, "fallback": not ready,
            "message": "" if ready else f"{voice.name} isn't downloaded yet — this preview uses the Windows voice."}


@app.get("/api/startmenu", dependencies=[Depends(require_token)])
def startmenu_get() -> dict:
    from app.tray import start_menu_enabled

    return {"enabled": start_menu_enabled()}


@app.put("/api/startmenu", dependencies=[Depends(require_token)])
def startmenu_put(body: dict) -> dict:
    from app.tray import set_start_menu, start_menu_enabled

    message = set_start_menu(bool(body.get("enabled")))
    return {"enabled": start_menu_enabled(), "message": message}


@app.get("/api/autostart", dependencies=[Depends(require_token)])
def autostart_get() -> dict:
    from app.tray import autostart_enabled

    return {"enabled": autostart_enabled()}


@app.put("/api/autostart", dependencies=[Depends(require_token)])
def autostart_put(body: dict) -> dict:
    from app.tray import autostart_enabled, set_autostart

    message = set_autostart(bool(body.get("enabled")))
    return {"enabled": autostart_enabled(), "message": message}


@app.post("/api/mouse-reading", dependencies=[Depends(require_token)])
def mouse_reading(body: dict) -> dict:
    st = get_state()
    if st.services is None:
        raise HTTPException(400, "Background services aren't running yet.")
    return {"message": st.services.mouse_reading(bool(body.get("on")))}


def port_in_use(host: str, port: int) -> bool:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def enable_crash_log() -> None:
    """Write a stack dump to data/logs/crash.log if the process dies in native code (audio, COM, Tk),
    which otherwise closes the window without any Python error."""
    import faulthandler

    cfg = get_config()
    global _crash_file
    _crash_file = open(cfg.dir(cfg.storage.logs_dir) / "crash.log", "a", encoding="utf-8")
    _crash_file.write(f"\n--- started {datetime.now().isoformat(timespec='seconds')} ---\n")
    _crash_file.flush()
    faulthandler.enable(file=_crash_file, all_threads=True)


_crash_file = None


def run(host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True) -> int:
    import webbrowser

    import uvicorn

    from app.accessibility.pointer import make_dpi_aware

    url = f"http://{host}:{port}"
    name = get_config().assistant.name
    if port_in_use(host, port):
        print(f"\n{name} is already running at {url} (probably in another window or the tray).")
        print("Opening it in the browser. To restart it, close the other window first (or Quit from the tray).\n")
        if open_browser:
            webbrowser.open(url)
        return 0
    make_dpi_aware()
    enable_crash_log()
    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    web = uvicorn.Server(config)
    original_startup = web.startup

    async def startup(*args, **kwargs):
        await original_startup(*args, **kwargs)
        if web.started:  # only now is the page really reachable
            print(f"\n{name} is running at {url}  (press Ctrl+C to stop)\n", flush=True)
            log.info("Web server started at %s", url)
            if open_browser:
                webbrowser.open(url)

    web.startup = startup
    reason = "stopped"
    try:
        web.run()
        if not web.started:
            reason = "could not start (see the error above)"
    except KeyboardInterrupt:
        reason = "stopped with Ctrl+C"
    except Exception as e:
        reason = f"crashed: {type(e).__name__}: {e}"
        log.exception("Web server crashed")
    finally:
        log.info("Web server %s", reason)
        print(f"\n{name} {reason}.", flush=True)
    return 0 if reason.startswith("stopped") else 1

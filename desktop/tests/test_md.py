"""Tests for MD's newer parts: naming, microphone switching, voice print gate, calls,
lock-screen limits, self-diagnosis, expert budget, pointer safety, winget parsing."""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest

from app.command_parser import parse
from app.config import get_config


@pytest.fixture
def cfg(monkeypatch):
    c = get_config()
    monkeypatch.setattr(c, "features", {})
    return c


# ---- naming ------------------------------------------------------------------------------

def test_wake_phrases_follow_assistant_name(cfg, monkeypatch):
    monkeypatch.setattr(cfg.voice, "wake_phrases", [])
    monkeypatch.setattr(cfg.assistant, "name", "MD")
    assert cfg.wake_phrases == ["hey md", "md"]
    monkeypatch.setattr(cfg.assistant, "name", "Zoya")
    assert cfg.wake_phrases == ["hey zoya", "zoya"]
    monkeypatch.setattr(cfg.voice, "wake_phrases", ["oye dost"])
    assert cfg.wake_phrases == ["oye dost"]


# ---- microphone switching -------------------------------------------------------------------------

class FakeRecorder:
    def __init__(self, mic):
        self.mic = mic

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def record(self, numframes):
        if self.mic.broken:
            self.mic.broken = False
            raise RuntimeError("device removed")
        return np.full((numframes, 1), self.mic.level, dtype=np.float32)


class FakeMic:
    def __init__(self, name, level=0.01):
        self.name, self.id, self.level, self.broken = name, name, level, False

    def recorder(self, **kw):
        return FakeRecorder(self)


@pytest.fixture
def fake_audio(monkeypatch):
    from app.voice import mic as mic_mod

    laptop, airpods = FakeMic("Microphone Array (Realtek)"), FakeMic("AirPods Pro Hands-Free")
    state = {"default": laptop, "all": [laptop, airpods]}
    fake = SimpleNamespace(default_microphone=lambda: state["default"], all_microphones=lambda: state["all"])
    monkeypatch.setattr(mic_mod, "_sc", lambda: fake)
    monkeypatch.setattr(mic_mod, "DEFAULT_CHECK_SECONDS", 0.0)
    monkeypatch.setattr(get_config().voice, "input_device", None)
    return mic_mod, state, laptop, airpods


def test_mic_follows_windows_default(fake_audio):
    mic_mod, state, laptop, airpods = fake_audio
    with mic_mod.MicStream() as m:
        m.read(1600)
        assert m.name == laptop.name
        state["default"] = airpods            # AirPods connected
        m.read(1600)
        assert m.name == airpods.name
        state["default"] = laptop             # AirPods taken out
        m.read(1600)
        assert m.name == laptop.name and m.switches == 2


def test_mic_recovers_from_unplug(fake_audio):
    mic_mod, state, laptop, airpods = fake_audio
    with mic_mod.MicStream() as m:
        laptop.broken = True
        block = m.read(1600)
        assert block.shape == (1600,) and float(block.max()) > 0


def test_mic_skips_a_silent_device(fake_audio, monkeypatch):
    mic_mod, state, laptop, airpods = fake_audio
    monkeypatch.setattr(mic_mod, "DEAD_SECONDS", 0.3)
    airpods.level = 0.0                          # connected, but its mic delivers nothing
    state["default"] = airpods
    with mic_mod.MicStream() as m:
        for _ in range(5):
            m.read(1600)
        assert m.name == laptop.name


# ---- voice print ---------------------------------------------------------------------------------

@pytest.fixture
def vp(monkeypatch, tmp_path, cfg):
    from app.voice import voiceprint

    monkeypatch.setattr(voiceprint, "_folder", lambda: tmp_path)
    monkeypatch.setattr(cfg.voice, "voiceprint_enabled", True)
    monkeypatch.setattr(cfg.voice, "voiceprint_threshold", 0.5)
    v = voiceprint.VoicePrint()
    # a clip's "voice" is its first sample value: same value = same speaker
    def embed(audio):
        vec = np.zeros(4, dtype=np.float32)
        vec[int(audio[0])] = 1.0
        vec[3] = 0.2
        return vec / np.linalg.norm(vec)
    monkeypatch.setattr(v, "embed", embed)
    monkeypatch.setattr(voiceprint, "_instance", v)
    return v


def clip(speaker: int) -> np.ndarray:
    return np.full(32000, speaker, dtype=np.float32)


def test_voiceprint_open_until_enrolled(vp):
    assert vp.verify(clip(1)) == (True, None, 1.0)


def test_voiceprint_accepts_only_enrolled(vp):
    vp.enroll("Muheb", [clip(0), clip(0), clip(0)])
    assert vp.verify(clip(0))[0] is True
    assert vp.verify(clip(1))[0] is False


def test_voiceprint_max_two_and_consistency(vp):
    from app.voice.voiceprint import VoiceprintError

    vp.enroll("A", [clip(0)] * 3)
    vp.enroll("B", [clip(1)] * 3)
    with pytest.raises(VoiceprintError):
        vp.enroll("C", [clip(2)] * 3)
    vp.delete("B")
    with pytest.raises(VoiceprintError):       # three clips that don't match each other
        vp.enroll("C", [clip(1), clip(2), clip(1)])


class FakeVoice:
    def __init__(self, heard=("", np.zeros(0)), then=()):
        self.said, self.heard, self.listener = [], heard, None
        self.queue = [heard, *then]  # what each listen hears; silence afterwards

    def say(self, text):
        self.said.append(text)

    def listen_with_audio(self, **kw):
        return self.queue.pop(0) if self.queue else ("", np.zeros(0))


def make_services(voice):
    from app.companion import Companion
    from app.services import Services
    from app.workflow_extended import ExtendedWorkflow

    services = Services(ExtendedWorkflow(Companion(), None), None, voice)
    services.commands = []
    services.run_command = services.commands.append
    return services


def test_wake_gate_rejects_stranger(vp):
    vp.enroll("Me", [clip(0)] * 3)
    voice = FakeVoice()
    services = make_services(voice)
    services.handle_wake("open youtube please", clip(1))
    assert services.commands == [] and "pehchaani" in voice.said[-1]


def test_wake_gate_accepts_owner_and_combines_short_wake(vp, monkeypatch):
    vp.enroll("Me", [clip(0)] * 3)
    monkeypatch.setattr(vp, "speech_seconds", lambda audio: len(audio) / 16000)
    voice = FakeVoice(heard=("what time is it", clip(0)))
    services = make_services(voice)
    services.handle_wake("", clip(0)[:8000])          # "Hey MD" alone: 0.5 s
    assert voice.said[0] in ("Ji?", "Haan ji, boliye.", "Yes?")
    assert services.commands == ["what time is it"]


# ---- calls ---------------------------------------------------------------------------------------

def test_active_call_app():
    from app.calls import active_call_app

    assert active_call_app(["5319275A.WhatsAppDesktop_cv1g1gvanyjgm"]) == "WhatsApp"
    assert active_call_app([r"C:#Program Files#Zoom#bin#Zoom.exe"]) == "Zoom"
    assert active_call_app([r"C:#Python313#python.exe"]) is None
    assert active_call_app([]) is None


def test_call_flow_asks_records_and_debriefs(monkeypatch, tmp_path):
    from app import calls
    from app.runtime import in_call

    apps = iter(["WhatsApp", "WhatsApp", None, None, None, None])
    monkeypatch.setattr(calls, "active_call_app", lambda users=None: next(apps, None))
    recorded = []

    class Rec:
        def __init__(self, folder):
            self.folder = folder

        def start(self):
            recorded.append("start")

        def stop(self):
            recorded.append("stop")
            return self.folder
    monkeypatch.setattr(calls, "CallRecorder", Rec)
    monkeypatch.setattr(calls.CallWatcher, "_folder", lambda self, app: tmp_path / app)
    events, ended = [], threading.Event()
    watcher = calls.CallWatcher(on_start=lambda c: events.append(("start", c.app, in_call.is_set())),
                                on_end=lambda c: (events.append(("end", c.app, c.recorder is not None)), ended.set()),
                                ask_record=lambda c, decide: decide(True))
    monkeypatch.setattr(watcher._stop, "wait", lambda t: time.sleep(0.01))
    monkeypatch.setattr(calls.time, "time", iter(range(0, 1000, 5)).__next__)
    watcher.start()
    assert ended.wait(5)
    watcher.stop()
    assert events == [("start", "WhatsApp", True), ("end", "WhatsApp", True)]
    assert recorded == ["start", "stop"] and not in_call.is_set()


def test_no_recording_without_yes():
    from app.calls import CallState, CallWatcher

    watcher = CallWatcher(lambda c: None, lambda c: None, lambda c, d: None)
    watcher.call = CallState("Zoom")
    watcher.decide(False)
    assert watcher.call.recorder is None
    watcher.decide(True)                       # a late click can't flip an answered question
    assert watcher.call.recorder is None


def test_silent_during_call():
    from app.runtime import in_call
    from app.voice.tts import Speaker

    speaker = Speaker.__new__(Speaker)
    speaker.available = True
    queued = []
    speaker._queue = SimpleNamespace(put=queued.append)
    in_call.set()
    try:
        speaker.say("hello", wait=False)
    finally:
        in_call.clear()
    assert queued == []


def test_action_items():
    from app.calls import action_items

    notes = "Summary: Talked about the order.\nAction items:\n- Send the invoice\n- Call Ali on Friday\nMood: calm"
    assert action_items(notes) == ["Send the invoice", "Call Ali on Friday"]
    assert action_items("Summary: hi\nAction items: none\nMood: ok") == []


# ---- lock screen ---------------------------------------------------------------------------------

def test_locked_screen_limits(monkeypatch, cfg):
    from app import workflow
    from app.companion import Companion
    from app.workflow_extended import ExtendedWorkflow

    monkeypatch.setattr(workflow, "_locked", lambda: True)
    wf = ExtendedWorkflow(Companion(), None)
    assert "locked" in wf.run_text("open notepad")[1]
    assert "locked" in wf.run_text("type hello")[1]
    assert wf.run_text("what time is it")[1].startswith("It's")


# ---- self-diagnosis ---------------------------------------------------------------------------------

def test_recent_errors_from_log(monkeypatch, tmp_path, cfg):
    from app import doctor
    from app.config import ROOT

    logs = tmp_path / "logs"
    logs.mkdir()
    src = ROOT / "app" / "workflow.py"
    (logs / "companion.log").write_text(
        "2026-09-23 INFO something\nTraceback (most recent call last):\n"
        f'  File "{src}", line 10, in execute\n    result = handler(**args)\nKeyError: \'target\'\n'
        "Traceback (most recent call last):\n"
        f'  File "{src}", line 10, in execute\n    result = handler(**args)\nKeyError: \'target\'\n', encoding="utf-8")
    monkeypatch.setattr(cfg.storage, "logs_dir", str(logs))
    errors = doctor.recent_errors()
    assert len(errors) == 1 and errors[0].count == 2
    assert errors[0].message == "KeyError: 'target'" and "workflow.py:10" in errors[0].location
    assert ">" in errors[0].snippet


# ---- expert ------------------------------------------------------------------------------------------

def test_expert_budget_falls_back_to_local(monkeypatch, tmp_path, cfg):
    from app import expert

    monkeypatch.setattr(cfg.expert, "provider", "claude")
    monkeypatch.setattr(cfg.expert, "weekly_budget_usd", 1.0)
    e = expert.Expert.__new__(expert.Expert)
    e.ledger = expert.UsageLedger(tmp_path / "u.db")
    e._client, e.last_backend = None, ""
    monkeypatch.setattr(e, "local_client", lambda: SimpleNamespace(chat=lambda msgs, **k: "local answer"))
    monkeypatch.setattr(e, "_ask_claude", lambda *a: "claude answer")
    assert e.ask("How should I cache Next.js pages?") == "claude answer"
    e.ledger.add("claude-opus-5", "test", 100_000, 40_000)   # $1.50 > $1 limit
    assert e.ask("How should I cache Next.js pages?") == "local answer"
    assert "budget" in e.last_backend


def test_technical_questions_route_to_expert(monkeypatch, cfg):
    from app.companion import Companion
    from app.workflow_extended import ExtendedWorkflow

    wf = ExtendedWorkflow(Companion(), None)
    monkeypatch.setattr(wf, "do_expert", lambda question: "EXPERT:" + question)
    intent, result = wf.run_text("Which database should I use with Next.js for a shop?")
    assert intent.name == "expert" and result.startswith("EXPERT:")
    assert wf.run_text("how are you today")[1] is None      # normal chat stays chat


# ---- pointer, winget, parsing -------------------------------------------------------------------------

def test_risky_click_needs_confirmation(monkeypatch):
    from app.accessibility import pointer

    clicked = []
    control = SimpleNamespace(Name="Delete account", ControlTypeName="ButtonControl",
                              Click=lambda simulateMove=False: clicked.append(1))
    monkeypatch.setattr(pointer, "find_control", lambda q: control)
    monkeypatch.setattr(pointer, "_glide_to", lambda c: (0, 0, 10, 10))
    monkeypatch.setattr("app.overlay.get_overlay", lambda: SimpleNamespace(highlight=lambda *a, **k: None))
    assert pointer.click("delete", confirm=lambda q: False) == "Okay, I didn't click it."
    assert clicked == []
    control.Name = "Next"
    assert pointer.click("next", confirm=lambda q: pytest.fail("no confirmation needed")) == "Clicked 'Next'."


def test_parse_winget_table():
    from app.automation.system import parse_winget_table

    out = ("   - \\ \n"
           "Name                    Id                       Version  Source\n"
           "---------------------------------------------------------------\n"
           "VLC                     XPDM1ZW6815MQM           Unknown  msstore\n"
           "VLC media player        VideoLAN.VLC             3.0.23   winget\n")
    pkgs = parse_winget_table(out)
    assert [(p.name, p.id, p.source) for p in pkgs] == [("VLC", "XPDM1ZW6815MQM", "msstore"),
                                                         ("VLC media player", "VideoLAN.VLC", "winget")]


@pytest.mark.parametrize("said, intent", [
    ("diagnose yourself", "diagnose"), ("kya masla hai", "diagnose"),
    ("give me a blueprint for an online bakery store", "expert"), ("design a database for a school", "expert"),
    ("research best auth for next.js", "research"), ("create a next.js app called shop", "create_project"),
    ("install vlc", "install_app"), ("switch to youtube tab", "switch_tab"), ("yeh kya hai", "describe_pointer"),
    ("yeh kya likha hai", "translate_screen"), ("show me the save button", "show_control"),
    ("save par click karo", "click_control"), ("press ctrl+s", "press_keys"), ("start mouse reading", "mouse_reading"),
    ("enroll my voice", "enroll_voice"), ("record this call", "record_call"), ("call notes", "call_notes"),
])
def test_new_rules(said, intent):
    assert parse(said, use_llm=False).name == intent


# ---- real threads (these bugs only show up when the thread actually runs) --------------------

def test_no_thread_subclass_shadows_thread_internals():
    import inspect
    import re

    from app.accessibility.pointer import MouseReader
    from app.calls import CallWatcher
    from app.mentor.notification_monitor import NotificationMonitor
    from app.voice.wake import WakeListener

    def private(names):
        return {n for n in names if n.startswith("_") and not n.startswith("__")}

    internals = private(set(dir(threading.Thread())) | set(vars(threading.Thread())))
    for cls in (WakeListener, NotificationMonitor, CallWatcher, MouseReader):
        own = private(set(re.findall(r"self\.(_\w+)", inspect.getsource(cls))) | set(vars(cls)))
        assert not (own & internals), f"{cls.__name__} overrides Thread internals: {own & internals}"


def test_wake_listener_thread_dispatches_command(monkeypatch, cfg):
    from app.voice.wake import WakeListener

    monkeypatch.setattr(cfg.voice, "wake_phrases", [])
    monkeypatch.setattr(cfg.assistant, "name", "MD")
    clips = iter([np.ones(16000, dtype=np.float32)])

    class FakeListener:
        def calibrate(self):
            pass

        def record(self, **kw):
            try:
                return next(clips)
            except StopIteration:
                time.sleep(0.05)
                return np.zeros(0, dtype=np.float32)

        def transcribe(self, audio):
            return "Hey MD, what time is it?"

    got, done = [], threading.Event()
    listener = WakeListener(FakeListener(), lambda rest, audio: (got.append(rest), done.set()))
    listener.start()
    try:
        assert done.wait(5), "wake handler never ran"
        assert got == ["what time is it"] and listener.is_alive()
        done.clear()
        listener.trigger()                       # the talk hotkey path
        assert done.wait(5) and got[-1] == "" and listener.is_alive()
    finally:
        listener.stop()

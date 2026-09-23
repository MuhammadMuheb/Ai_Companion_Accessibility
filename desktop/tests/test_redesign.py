"""Lyra redesign: voice library, voice-first window, branding and upgrade from the earlier release."""

from __future__ import annotations

import re
import threading
from collections import deque
from itertools import count
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.config import get_config

STATIC = Path(__file__).resolve().parent.parent / "app" / "web" / "static"


# ---- voice library ---------------------------------------------------------------------------------

def test_catalogue_has_twelve_voices_with_at_least_seven_female():
    from app.voice.voices import VOICES

    assert 10 <= len(VOICES) <= 12
    assert sum(v.gender == "female" for v in VOICES) >= 7
    assert len({v.id for v in VOICES}) == len(VOICES) and len({v.name for v in VOICES}) == len(VOICES)
    assert {v.engine for v in VOICES} == {"neural", "system"}


def test_neural_voices_map_to_piper_paths():
    from app.voice.voices import BY_ID

    assert BY_ID["amy"].hf_path == "en/en_US/amy/medium/en_US-amy-medium"
    assert BY_ID["jenny"].hf_path == "en/en_GB/jenny_dioco/medium/en_GB-jenny_dioco-medium"


def test_unknown_voice_falls_back_to_default():
    from app.voice.voices import DEFAULT_VOICE, get_voice

    assert get_voice("nobody").id == DEFAULT_VOICE and get_voice(None).id == DEFAULT_VOICE


def test_catalogue_payload(monkeypatch):
    from app.voice import voices

    monkeypatch.setattr(get_config().voice, "tts_voice", "zira")
    data = voices.catalogue()
    zira = next(v for v in data["voices"] if v["id"] == "zira")
    assert data["selected"] == "zira" and zira["selected"] and zira["ready"]
    assert data["counts"]["female"] >= 7


def test_voice_setting_is_validated():
    from app import settings_service

    assert settings_service.coerce(str, "jenny", "voice.tts_voice") == "jenny"
    with pytest.raises(ValueError):
        settings_service.coerce(str, "robot", "voice.tts_voice")
    with pytest.raises(ValueError):
        settings_service.coerce(float, "5", "voice.tts_rate")


def test_changing_the_voice_does_not_restart_listening():
    from app import settings_service

    before = get_config().voice.tts_voice
    try:
        result = settings_service.apply({"voice": {"tts_voice": "cori" if before != "cori" else "amy"}})
        assert not result.errors and not result.background_changed and not result.speech_changed
    finally:
        settings_service.apply({"voice": {"tts_voice": before}})


def test_speaker_uses_windows_voice_when_neural_voice_is_missing(monkeypatch):
    from app.voice import tts
    from app.voice.voices import BY_ID

    spoken = []
    speaker = tts.Speaker.__new__(tts.Speaker)
    speaker.rate_override = speaker.volume_override = None
    speaker._current, speaker.last_error = None, ""

    class Failing:
        def speak(self, *a):
            raise FileNotFoundError("not downloaded")

    class System:
        def speak(self, text, voice, rate, volume):
            spoken.append((text, voice.id))

    speaker._engines = {"neural": Failing(), "system": System()}
    speaker._speak_now("hello", BY_ID["jenny"])
    speaker._speak_now("hi", BY_ID["ryan"])
    assert spoken == [("hello", "zira"), ("hi", "david")]


# ---- voice commands ------------------------------------------------------------------------------

@pytest.mark.parametrize("said, name", [("change your voice to Jenny", "jenny"), ("use a female voice", "female"),
                                        ("speak with Ryan's voice", "ryan"), ("cori ki awaaz lagao", "cori")])
def test_set_voice_rules(said, name):
    from app.command_parser import parse

    intent = parse(said, use_llm=False)
    assert intent.name == "set_voice" and intent.args["name"].lower() == name


@pytest.fixture
def wf(monkeypatch):
    from app import settings_service
    from app.companion import Companion
    from app.voice import voices
    from app.workflow_extended import ExtendedWorkflow

    applied, started = [], []
    monkeypatch.setattr(settings_service, "apply", lambda body: applied.append(body) or SimpleNamespace(
        errors={}, speech_changed=False, background_changed=False, scheduler_changed=False))
    monkeypatch.setattr(voices.downloads, "start", lambda vid: started.append(vid) or True)
    w = ExtendedWorkflow(Companion(), None, ask=lambda q, o=None: "Yes")
    w.applied, w.started = applied, started
    return w


def test_set_voice_by_voice(wf):
    reply = wf.run_text("change your voice to Zira")[1]
    assert wf.applied[-1] == {"voice": {"tts_voice": "zira"}} and "Zira" in reply
    wf.run_text("use a male voice")
    assert wf.applied[-1] == {"voice": {"tts_voice": "ryan"}}
    assert "don't have" in wf.run_text("change your voice to Robot")[1]
    assert "Female" in wf.run_text("what voices do you have")[1]


def test_set_voice_downloads_a_missing_neural_voice(wf, monkeypatch):
    from app.voice import voices

    monkeypatch.setattr(voices, "is_ready", lambda v: v.engine == "system")
    reply = wf.run_text("change your voice to Alba")[1]
    assert wf.started == ["alba"] and "Downloading" in reply


# ---- the window: voice-first, no chat box ----------------------------------------------------------

def test_window_has_no_text_chat_or_search():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert 'id="chat-form"' not in html and 'id="text"' not in html and 'type="search"' not in html
    assert "Type a message" not in html and 'role="search"' not in html
    assert 'id="orb"' in html and 'id="voice-grid"' in html


def test_every_element_the_script_uses_exists():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    ids = re.findall(r'id="([^"]+)"', html)
    assert len(ids) == len(set(ids)), "duplicate ids"
    used = set(re.findall(r'\$\("#([\w-]+)"\)', js)) | set(re.findall(r'getElementById\("([\w-]+)"\)', js))
    assert used <= set(ids), f"missing: {used - set(ids)}"


def test_every_tab_controls_a_panel():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    for tab, panel in re.findall(r'role="tab" id="(tab-\w+)" aria-controls="(panel-\w+)"', html):
        assert f'id="{panel}"' in html and f'aria-labelledby="{tab}"' in html


def test_no_legacy_brand_in_the_window():
    for name in ("index.html", "app.js", "style.css"):
        text = (STATIC / name).read_text(encoding="utf-8")
        assert not re.search(r"\bMD\b", text), name
    assert (STATIC / "lyra-logo.png").exists()


@pytest.fixture
def client(monkeypatch):
    from fastapi.testclient import TestClient

    from app.companion import Companion
    from app.web import server
    from app.workflow_extended import ExtendedWorkflow

    st = server.State.__new__(server.State)
    st.lock, st.events, st._ids = threading.Lock(), deque(), count(1)
    st.services = st.voice = st._listener = None
    st.enroll_clips, st.extra_hotkeys = [], {}
    st._listener_lock = threading.Lock()
    st.companion = Companion()
    st.workflow = ExtendedWorkflow(st.companion, None)
    monkeypatch.setattr(server, "state", st)
    c = TestClient(server.app)
    c.headers["X-Companion-Token"] = server.TOKEN
    c.st = st
    return c


def test_voices_api(client, monkeypatch):
    from app.voice import voices

    r = client.get("/api/voices")
    assert r.status_code == 200 and len(r.json()["voices"]) == len(voices.VOICES)
    assert client.post("/api/voices/robot/preview").status_code == 404
    started = []
    monkeypatch.setattr(voices.downloads, "start", started.append)
    assert client.post("/api/voices/kristin/download").status_code == 200 and started == ["kristin"]


def test_talk_and_run_need_the_voice_engine(client):
    assert client.post("/api/talk").status_code == 400
    assert client.post("/api/run", json={"text": "what time is it"}).status_code == 400
    ran = []
    client.st.voice = SimpleNamespace(phase="listening", level=0.4)
    client.st.services = SimpleNamespace(talk_hotkey=lambda: ran.append("talk"), run_in_background=ran.append,
                                         wake=None, mouse=None, monitor=None, hotkeys=None)
    assert client.post("/api/run", json={"text": "what time is it"}).status_code == 200
    status = client.get("/api/events").json()["status"]
    assert status["phase"] == "listening" and status["level"] == 0.4 and ran == ["what time is it"]


def test_events_report_offline_until_voice_starts(client):
    assert client.get("/api/events").json()["status"]["phase"] == "offline"


# ---- branding & upgrade ------------------------------------------------------------------------------

def test_default_name_is_lyra(monkeypatch):
    from app.config import Config

    cfg = Config()
    assert cfg.assistant.name == "Lyra" and cfg.wake_phrases == ["hey lyra", "lyra"]


def test_settings_from_the_earlier_release_upgrade_to_lyra():
    from app.config import _upgrade_legacy

    data = _upgrade_legacy({"assistant": {"name": "MD"}, "voice": {"wake_phrases": ["hey md", "md"]}})
    assert "name" not in data["assistant"] and "wake_phrases" not in data["voice"]
    kept = _upgrade_legacy({"assistant": {"name": "Zoya"}, "voice": {"wake_phrases": ["oye zoya"]}})
    assert kept["assistant"]["name"] == "Zoya" and kept["voice"]["wake_phrases"] == ["oye zoya"]


def test_goodbye_with_the_assistant_name():
    from app.voice.conversation import is_goodbye

    assert is_goodbye("bye Lyra") and is_goodbye("Khuda hafiz Lyra, shukriya") and not is_goodbye("Lyra open notepad")


def test_voice_engine_reports_its_phase():
    from app.accessibility.voice_engine import VoiceEngine

    engine = VoiceEngine.__new__(VoiceEngine)
    engine.phase, engine.level = "idle", 0.0
    engine.echo = False
    engine._level(0.05, 0.01, True)
    assert 0 < engine.level <= 1
    engine.set_phase("thinking")
    assert engine.phase == "thinking" and engine.level == 0.0

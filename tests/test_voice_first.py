"""Voice-first behaviour: initialism wake names, settings by voice, silent call control,
speakable answers, and the invisible (headless) start."""

from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from app.command_parser import parse
from app.config import get_config
from app.voice.wake import find_wake

MD = ["hey md", "md"]


@pytest.mark.parametrize("said, rest", [
    ("Hello MD, what time is it?", "what time is it"),
    ("Hello M.D. open YouTube", "open YouTube"),
    ("Hello Em Dee what time is it", "what time is it"),
    ("Emdee open notepad", "open notepad"),
    ("Hi MD", ""),
])
def test_initialism_wake_name(said, rest):
    assert find_wake(said, MD)[1] == rest


@pytest.mark.parametrize("said", ["I need to see the MD tomorrow about my back", "Em, I think so", "Mad world",
                                  "Hey Ed", "Hello, how are you?"])
def test_initialism_no_false_wake(said):
    assert find_wake(said, MD) is None


@pytest.mark.parametrize("said, intent", [
    ("your name is Nova", "set_assistant_name"), ("call me Muheb", "set_user_name"),
    ("main Karachi mein rehta hoon", "set_city"), ("turn off notifications", "toggle_feature"),
    ("translation chalu karo", "toggle_feature"), ("open your settings", "show_window"),
    ("stop listening for 10 minutes", "pause_listening"), ("notepad band karo", "close_app"),
    ("chrome band kar do", "close_app"),
])
def test_voice_settings_rules(said, intent):
    assert parse(said, use_llm=False).name == intent


@pytest.fixture
def wf(monkeypatch):
    from app import settings_service
    from app.companion import Companion
    from app.workflow_extended import ExtendedWorkflow

    applied = []
    monkeypatch.setattr(settings_service, "apply", lambda body: applied.append(body) or SimpleNamespace(
        errors={}, speech_changed=False, background_changed=False, scheduler_changed=False))
    w = ExtendedWorkflow(Companion(), None, ask=lambda q, o=None: "Yes")
    w.applied = applied
    return w


def test_set_city_fills_prayer_location(wf):
    reply = wf.run_text("I live in Lahore")[1]
    assert "Lahore" in reply
    assert wf.applied[-1] == {"user": {"city": "Lahore", "latitude": 31.5204, "longitude": 74.3587}}


def test_rename_assistant_resets_wake_phrases(wf):
    reply = wf.run_text("your name is Zoya")[1]
    assert "Hey Zoya" in reply
    assert wf.applied[-1] == {"assistant": {"name": "Zoya"}, "voice": {"wake_phrases": ""}}


def test_toggle_feature_by_voice(wf):
    assert "off" in wf.run_text("turn off notifications")[1]
    assert wf.applied[-1] == {"features": {"notifications": False}}
    assert "don't have" in wf.run_text("turn on the lights")[1]
    assert "can't hear you" in wf.run_text("turn off the wake word")[1]


def test_speakable_keeps_code_and_long_text_out_of_speech():
    from app.services import speakable

    spoken, full = speakable("Use Postgres.\n```sql\nCREATE TABLE shop(id int);\n```")
    assert "CREATE" not in spoken and "window" in spoken and full
    spoken, full = speakable("Short answer.")
    assert spoken == "Short answer." and not full
    spoken, full = speakable("Word. " * 300)
    assert len(spoken) < 700 and full


def test_silent_call_commands(monkeypatch):
    from app import services as services_mod
    from app.runtime import in_call

    done, shown = [], []
    fake = SimpleNamespace(lock=threading.Lock(), workflow=SimpleNamespace(execute=lambda i: done.append(i.name) or "Recording this call."),
                           _event=lambda k, t: shown.append(t), CALL_COMMANDS=services_mod.Services.CALL_COMMANDS)
    monkeypatch.setattr("app.overlay.get_overlay", lambda: SimpleNamespace(prompt=lambda *a, **k: shown.append("popup")))
    in_call.set()
    try:
        services_mod.Services._silent_call_command(fake, "record this call", None)
        services_mod.Services._silent_call_command(fake, "open youtube", None)   # ignored during a call
    finally:
        in_call.clear()
    assert done == ["record_call"] and "popup" in shown


def test_voice_answers_are_not_spoken_during_a_call():
    from app.runtime import in_call
    from app.voice.tts import Speaker

    speaker = Speaker.__new__(Speaker)
    speaker.available, queued = True, []
    speaker._queue = SimpleNamespace(put=queued.append)
    in_call.set()
    try:
        speaker.say("I will not be heard on the call", wait=False)
    finally:
        in_call.clear()
    assert queued == []


def test_app_starts_without_any_window(monkeypatch):
    from app import daemon
    from app.web import server

    monkeypatch.setattr(server, "get_state", lambda: SimpleNamespace(extra_hotkeys={}, services=None))
    app = daemon.NovaApp()
    assert app.window is None and not app._want_window.is_set()   # nothing on screen, no WebView
    app.show("settings")                                           # first request: main thread builds it
    assert app._want_window.is_set() and app._pending_tab == "settings"


# ---- conversation after the wake word (no need to repeat "Hello MD") ----
@pytest.mark.parametrize("said", ["bye", "Okay, bye!", "that's all, thanks", "bas", "bas karo", "Allah Hafiz",
                                  "theek hai, shukriya bas", "nothing else", "Khuda hafiz MD"])
def test_goodbye_ends_conversation(said):
    from app.voice.conversation import is_goodbye

    assert is_goodbye(said)


@pytest.mark.parametrize("said", ["bye the way open notepad", "stop the music on youtube", "open notepad",
                                  "bas ki timing kya hai"])
def test_not_goodbye(said):
    from app.voice.conversation import is_goodbye

    assert not is_goodbye(said)


@pytest.mark.parametrize("said, junk", [("you", True), ("...", True), ("the the the the", True), ("12 34 !!", True),
                                        ("ha ha ha ha ha ha ha ha", True), ("what time is it", False),
                                        ("namaz ka time", False), ("open gmail", False)])
def test_gibberish(said, junk):
    from app.voice.conversation import is_gibberish

    assert is_gibberish(said) is junk


def _talk(heard_list):
    import numpy as np

    from app.services import Services

    said, ran = [], []
    queue = [(h, np.zeros(0)) for h in heard_list]
    services = Services.__new__(Services)
    services.voice = SimpleNamespace(say=said.append,
                                     listen_with_audio=lambda **k: queue.pop(0) if queue else ("", np.zeros(0)))
    services.wake, services._greet, services.on_event = None, 0, None
    services.run_command = ran.append
    services.converse()
    return said, ran


def test_conversation_continues_until_goodbye():
    said, ran = _talk(["what time is it", "open notepad", "Allah Hafiz", "never heard"])
    from app.services import FAREWELLS

    assert ran == ["what time is it", "open notepad"] and said[-1] in FAREWELLS


def test_conversation_skips_gibberish_and_ends_on_silence():
    said, ran = _talk(["you", "namaz ka time"])
    assert ran == ["namaz ka time"] and said == []      # silence afterwards: ends quietly


def test_conversation_gives_up_after_repeated_noise():
    said, ran = _talk(["...", "the the the"])
    assert ran == [] and "Samajh nahi" in said[-1]


def test_stop_listening_survives_the_wake_handler():
    from app.voice.wake import WakeListener

    w = WakeListener.__new__(WakeListener)
    w.asleep, w._paused, w._clips = False, threading.Event(), __import__("queue").Queue()
    w.on_wake = lambda rest, audio: w.sleep()     # the user said "stop listening"
    w._dispatch("stop listening", None)
    assert w.paused                               # still asleep after the command finished
    w.trigger = WakeListener.trigger.__get__(w)
    w._triggered = threading.Event()
    w.trigger(); w.resume()
    assert not w.paused                           # the talk key wakes it again

"""Fast unit tests that need neither Ollama nor a microphone.  Run: python -m pytest"""

from __future__ import annotations

import pytest

from app.accessibility.voice_engine import is_yes
from app.automation import executor
from app.command_parser import normalize, parse
from app.memory import vector
from app.memory.models import Memory
from app.memory.store import MemoryStore, worth_extracting
from app.memory_db import MemoryDB
from app.workflow_extended import split_steps


@pytest.mark.parametrize("text, intent, args", [
    ("open notepad", "open", {"target": "notepad"}),
    ("please open gmail for me", "open", {"target": "gmail"}),
    ("notepad kholo", "open", {"target": "notepad"}),
    ("remind me in 10 minutes to drink water", "remind", {"minutes": 10.0, "text": "drink water"}),
    ("remind me to call ammi in 2 hours", "remind", {"minutes": 120.0, "text": "call ammi"}),
    ("set a reminder for tea after 15 minutes", "remind", {"minutes": 15.0, "text": "tea"}),
    ("what time is it", "time", {}),
    ("kitne baje hain", "time", {}),
    ("namaz ka time", "prayer", {}),
    ("start focus for 30 minutes on math", "focus_start", {"minutes": 30, "task": "math"}),
    ("stop focus", "focus_stop", {}),
    ("add goal learn urdu", "add_goal", {"title": "learn urdu"}),
    ("what is on my screen", "describe_screen", {}),
    ("read the screen", "read_screen", {"full": False}),
    ("search youtube for naat", "search", {"query": "naat", "engine": "youtube"}),
    ("type hello world", "type_text", {"text": "hello world"}),
    ("press ctrl+s", "press_keys", {"keys": "ctrl+s"}),
    ("awaaz kam karo", "volume", {"direction": "down"}),
    ("what did i just do", "recent_activity", {}),
    ("remember that my sister is Ayesha", "remember", {"fact": "my sister is Ayesha"}),
    ("close chrome", "close_app", {"name": "chrome"}),
])
def test_rules(text, intent, args):
    got = parse(text, use_llm=False)
    assert got.name == intent
    assert got.args == args


def test_code_request_keeps_full_text():
    got = parse("write a python function to reverse a string", use_llm=False)
    assert got.name == "generate_code"
    assert got.args["request"] == "write a python function to reverse a string"


@pytest.mark.parametrize("text", ["what is photosynthesis?", "how are you", "tell me a joke"])
def test_questions_are_chat(text):
    assert parse(text, use_llm=False).is_chat


def test_normalize_strips_politeness():
    assert normalize("please open notepad for me") == "open notepad"
    assert normalize("Hey, can you open gmail please") == "open gmail"
    assert normalize("what did i just do") == "what did i just do"


def test_split_steps():
    assert split_steps("open notepad then type hello and then press enter") == ["open notepad", "type hello", "press enter"]
    assert split_steps("notepad kholo phir type hello") == ["notepad kholo", "type hello"]


@pytest.mark.parametrize("cmd", [r"Remove-Item C:\x -Recurse", "format c:", "rm -rf /", "shutdown /s", "iex (iwr x)"])
def test_dangerous_commands_blocked(cmd):
    assert executor.is_blocked(cmd)
    assert "won't run" in executor.run(cmd, confirm=lambda q: True)


def test_unsafe_command_needs_confirmation():
    assert not executor.is_safe("notepad")
    assert executor.run("notepad", confirm=lambda q: False) == "Okay, I didn't run it."
    assert executor.is_safe("dir")
    assert not executor.is_safe("dir | Out-File x.txt")


def test_yes_no():
    assert is_yes("haan kar do") is True
    assert is_yes("yes please") is True
    assert is_yes("nahi") is False
    assert is_yes("no don't") is False
    assert is_yes("banana") is None


def test_vector_search_prefers_embeddings():
    a = Memory("User likes chai", embedding=[1.0, 0.0])
    b = Memory("User is a teacher", embedding=[0.0, 1.0])
    hits = vector.search("drink", [0.9, 0.1], [a, b], min_score=0.3)
    assert hits[0][0] is a


def test_keyword_fallback():
    mems = [Memory("User's sister is Ayesha"), Memory("User likes cricket")]
    hits = vector.search("who is my sister", None, mems)
    assert hits and "Ayesha" in hits[0][0].content


class FakeLLM:
    """Embeddings from a tiny bag-of-words so reconciliation can be tested offline."""
    VOCAB = ["chai", "tea", "teacher", "cricket", "sister", "ayesha", "morning"]

    def embed(self, text):
        t = text.lower()
        return [1.0 if w in t else 0.0 for w in self.VOCAB] + [0.01]


def test_memory_reconcile(tmp_path):
    db = MemoryDB(tmp_path / "m.db")
    store = MemoryStore(db, FakeLLM())
    assert store.remember("User drinks chai in the morning")[0] == "added"
    assert store.remember("User drinks chai every morning")[0] == "kept"   # same fact, said again
    assert store.remember("User plays cricket")[0] == "added"
    assert len(db.all_memories()) == 2
    db.close()


def test_worth_extracting():
    assert worth_extracting("I work as a teacher in Lahore")
    assert not worth_extracting("hello")
    assert not worth_extracting("/memories")


def test_db_goals_and_reminders(tmp_path):
    db = MemoryDB(tmp_path / "m.db")
    gid = db.add_goal("Learn Python")
    assert [g.title for g in db.goals()] == ["Learn Python"]
    db.set_goal_status(gid, "done")
    assert db.goals() == []
    db.add_reminder("water", "2000-01-01T00:00:00")
    due = db.due_reminders("2000-01-01T00:00:01")
    assert len(due) == 1
    db.complete_reminder(due[0]["id"])
    assert db.pending_reminders() == []
    db.close()


# ---- speech detection (no microphone needed) --------------------------------

import numpy as np

from app.voice.stt import UtteranceDetector, is_hallucination

BLOCK = 1600


def _feed(detector, blocks):
    for b in blocks:
        detector.feed(b)
        if detector.done:
            break
    return detector


def _level(value, n=1):
    return [np.full(BLOCK, value, dtype="float32") for _ in range(n)]


def test_enter_click_is_not_speech():
    click = np.zeros(BLOCK, dtype="float32")
    click[:80] = 0.3
    d = _feed(UtteranceDetector(0.004, wait_seconds=2), [click] + _level(0.0005, 5) + [click] + _level(0.0005, 30))
    assert not d.started and d.done
    assert d.audio().size == 0


def test_quiet_speech_is_captured_until_silence():
    d = _feed(UtteranceDetector(0.004, silence_seconds=1.0), _level(0.0005, 10) + _level(0.007, 15) + _level(0.0005, 50))
    assert d.started and d.done
    seconds = d.audio().size / 16000
    assert 2.0 <= seconds <= 3.5  # pre-roll + 1.5 s speech + 1 s trailing silence


def test_push_to_talk_returns_audio_without_detected_speech():
    d = _feed(UtteranceDetector(0.004, wait_seconds=2, require_speech=False), _level(0.003, 40))
    assert not d.started and d.audio().size > 0


def test_hallucinations():
    assert is_hallucination(" Thank you.")
    assert is_hallucination("you")
    assert not is_hallucination("open notepad")


# ---- settings overlay, feature switches, custom commands ----------------------

from app import config as config_mod
from app.workflow_extended import phrase_key


def test_phrase_key_matches_spoken_variants():
    assert phrase_key("Work mode.") == phrase_key("please work mode") == "work mode"


def test_settings_overlay_and_reload(tmp_path, monkeypatch):
    monkeypatch.setattr(config_mod, "SETTINGS_FILE", tmp_path / "settings.yaml")
    cfg = config_mod.get_config()
    old = (cfg.user.name, dict(cfg.features), dict(cfg.routines))
    try:
        config_mod.save_settings({"user": {"name": "Tester"}, "features": {"screen": False},
                                  "routines": {"hello": ["say: hi"]}})
        assert cfg.user.name == "Tester"          # same object updated in place
        assert not cfg.feature_on("screen") and cfg.feature_on("apps")
        assert cfg.routines == {"hello": ["say: hi"]}
    finally:
        monkeypatch.setattr(config_mod, "SETTINGS_FILE", tmp_path / "missing.yaml")
        config_mod.reload_config()
        cfg.user.name, cfg.features, cfg.routines = old[0], old[1], old[2]


def test_say_and_wait_steps():
    assert parse("say: Subah bakhair", use_llm=False).args == {"text": "Subah bakhair"}
    assert parse("wait 2 seconds", use_llm=False).args == {"seconds": 2.0}

"""Memory and goals keep themselves up to date when the user changes something by voice."""

from __future__ import annotations

import pytest

from app.memory.store import MemoryStore, goal_title
from app.memory_db import MemoryDB


class BagOfWordsLLM:
    """Offline stand-in: embeddings from shared words; the 'decide' call answers add."""
    VOCAB = ["travel", "dubai", "london", "chai", "tea", "green", "developer", "web", "cricket", "arabic", "learn",
             "bakery", "website", "sister", "ayesha"]

    def embed(self, text):
        t = text.lower()
        return [1.0 if w in t else 0.0 for w in self.VOCAB] + [0.3]

    def ask_json(self, prompt, system=None, max_tokens=250):
        return {"action": "add", "id": None}


@pytest.fixture
def store(tmp_path):
    db = MemoryDB(tmp_path / "m.db")
    yield MemoryStore(db, BagOfWordsLLM())
    db.close()


def test_changed_goal_replaces_old_one(store):
    store.remember("User's goal is to travel to Dubai", "goal", said="My goal is to travel to Dubai")
    action, mem_id = store.remember("User now wants to travel to London instead of Dubai", "goal",
                                    said="I want to travel to London instead of Dubai")
    assert action == "updated"
    assert [m.content for m in store.db.all_memories()] == ["User now wants to travel to London instead of Dubai"]
    assert [g.title for g in store.db.goals()] == ["Travel to London"]
    history = store.db.history()
    assert {h["kind"] for h in history} == {"memory", "goal"}
    assert any(h["old_content"] == "Travel to Dubai" and h["new_content"] == "Travel to London" for h in history)


def test_same_fact_is_kept_once(store):
    store.remember("User is a web developer")
    assert store.remember("User is a web developer")[0] == "kept"
    assert len(store.db.all_memories()) == 1


def test_unrelated_fact_is_added(store):
    store.remember("User's sister is Ayesha", "person")
    assert store.remember("User likes cricket")[0] == "added"
    assert len(store.db.all_memories()) == 2


def test_preference_change(store):
    store.remember("User drinks chai every morning", "preference")
    action, _ = store.remember("User no longer drinks chai and prefers green tea", "preference",
                               said="I no longer drink chai, green tea now")
    assert action == "updated" and len(store.db.all_memories()) == 1


def test_explicit_goal_change_and_done(store):
    store.set_goal("Travel to Dubai")
    store.set_goal("Learn Arabic")
    action, _, old = store.change_goal("travel to Dubai", "Travel to London")
    assert action == "updated" and old == "Travel to Dubai"
    goal_id = next(g.id for g in store.db.goals() if g.title == "Learn Arabic")
    store.remove_goal(goal_id, done=True)
    assert [g.title for g in store.db.goals()] == ["Travel to London"]
    assert store.db.history()[0]["action"] == "done"


def test_forget_keeps_history(store):
    _, mem_id = store.remember("User likes cricket")
    assert store.forget(mem_id)
    assert store.db.all_memories() == []
    assert store.db.history()[0]["action"] == "forgotten"


@pytest.mark.parametrize("fact, title", [
    ("User now wants to travel to London instead of Dubai.", "Travel to London"),
    ("User's goal is to travel to Dubai next year.", "Travel to Dubai next year"),
    ("User wants to learn Urdu", "Learn Urdu"),
    ("User dreams of owning a car", "Owning a car"),
])
def test_goal_titles(fact, title):
    assert goal_title(fact) == title


def test_voice_memory_commands(tmp_path, monkeypatch):
    from app.companion import Companion
    from app.workflow_extended import ExtendedWorkflow

    db = MemoryDB(tmp_path / "w.db")
    companion = Companion(db=db, llm=BagOfWordsLLM())
    companion.memory = MemoryStore(db, BagOfWordsLLM())
    w = ExtendedWorkflow(companion, None, ask=lambda q, o=None: "Yes")
    say = lambda text: w.run_text(text)[1]
    assert say("my goal is to travel to Dubai").startswith("Added")
    assert "already" in say("add goal travel to Dubai")
    assert "London" in say("change my goal from travelling to Dubai to travelling to London")
    assert [g.title for g in db.goals()] == ["travelling to London"]
    say("remember that I like cricket")
    assert [m.content for m in db.all_memories() if "cricket" in m.content] == ["User likes cricket"]
    assert "forgotten" in say("forget that I like cricket")
    assert "became" in say("what was my old goal")
    db.close()

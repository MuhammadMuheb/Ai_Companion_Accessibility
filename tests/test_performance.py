"""Speed-related behaviour: prompt layout that keeps Ollama's cache warm, and the light wake-word pass."""

import numpy as np

from app.companion import HISTORY_TURNS, Companion
from app.memory_db import MemoryDB
from app.voice import wake


class StreamLLM:
    def __init__(self):
        self.calls = []

    def chat_stream(self, messages, **kwargs):
        self.calls.append(messages)
        yield "ok"

    def embed(self, text):
        return None


def test_prompt_prefix_stays_the_same_between_turns(tmp_path):
    db = MemoryDB(tmp_path / "m.db")
    llm = StreamLLM()
    c = Companion(db=db, llm=llm)
    c.memory.extract_in_background = lambda text: None
    c.reply("first question")
    c.reply("second question")
    first, second = llm.calls
    # the fixed system prompt comes first and never contains the clock or memories
    assert first[0] == second[0] and "Current date" not in first[0]["content"]
    # the changing context sits right before the user's message, so the history before it is reused
    assert second[-2]["role"] == "system" and "Current date" in second[-2]["content"]
    assert second[1:3] == [{"role": "user", "content": "first question"}, {"role": "assistant", "content": "ok"}]
    db.close()


def test_history_is_trimmed_in_chunks(tmp_path):
    db = MemoryDB(tmp_path / "m.db")
    c = Companion(db=db, llm=StreamLLM())
    c.memory.extract_in_background = lambda text: None
    for i in range(HISTORY_TURNS + 1):
        c.reply(f"q{i}")
    assert len(c.history) == HISTORY_TURNS  # half the window kept, not a one-turn sliding window
    db.close()


class FakeListener:
    def __init__(self, quick, full):
        self.quick, self.full, self.full_calls = quick, full, 0

    def transcribe_wake(self, audio):
        return self.quick

    def transcribe(self, audio):
        self.full_calls += 1
        return self.full


def spot(listener):
    w = wake.WakeListener(listener, on_wake=lambda rest, audio: None)
    return w._spot(np.zeros(16000, dtype="float32"), 0.82)


def test_full_model_skipped_when_no_wake_word(monkeypatch):
    monkeypatch.setattr(wake, "get_config", lambda: type("C", (), {"wake_phrases": ["hey nova", "nova"]})())
    listener = FakeListener("we are talking about dinner", "unused")
    hit, _ = spot(listener)
    assert hit is None and listener.full_calls == 0


def test_full_model_reads_the_command_after_the_wake_word(monkeypatch):
    monkeypatch.setattr(wake, "get_config", lambda: type("C", (), {"wake_phrases": ["hey nova", "nova"]})())
    listener = FakeListener("hey nova open", "Hey Nova, open YouTube")
    hit, text = spot(listener)
    assert hit is not None and "youtube" in hit[1].lower() and listener.full_calls == 1

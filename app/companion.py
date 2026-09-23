"""The conversational core shared by the text CLI, voice CLI and scheduler."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Iterator

from app.config import get_config
from app.llm import LLMError, OllamaClient, get_llm
from app.logger import get_logger
from app.memory.models import Message
from app.memory.store import MemoryStore
from app.memory_db import MemoryDB, get_db

log = get_logger(__name__)

SYSTEM_PROMPT = """You are {assistant}, a warm, patient personal AI companion running locally on {name}'s computer.
You help with daily life, focus, reminders, learning and computer tasks, and you are accessibility-friendly:
answers may be read aloud, so keep them short, clear and conversational (2-4 sentences unless asked for detail).
Avoid markdown tables and long bullet lists. Reply in the same language the user writes in
(English, Urdu, Hindi or Roman Urdu)."""

# Sent as a separate message right before the user's text. Ollama reuses its cache for the longest
# unchanged prefix of the conversation: when the time and recalled memories were part of the first
# system message, every request re-read the whole prompt (6-9 s on this CPU); now only this short
# tail is new.
CONTEXT_PROMPT = """Current date and time: {now}
{memories}{goals}"""

HISTORY_TURNS = 12


class Companion:
    def __init__(self, db: MemoryDB | None = None, llm: OllamaClient | None = None):
        self.cfg = get_config()
        self.db = db or get_db()
        self.llm = llm or get_llm()
        self.memory = MemoryStore(self.db, self.llm)
        self.session_id = uuid.uuid4().hex[:12]
        self.history: list[dict] = []

    def _system_prompt(self) -> str:
        return SYSTEM_PROMPT.format(assistant=self.cfg.assistant.name, name=self.cfg.user.name)

    def _context_prompt(self, user_text: str) -> str:
        memories = self.memory.recall(user_text)
        mem_block = ""
        if memories:
            mem_block = "\nWhat you remember about the user:\n" + "\n".join(f"- {m.content}" for m in memories) + "\n"
        goals = self.db.goals()
        goal_block = ""
        if goals:
            goal_block = "\nUser's active goals:\n" + "\n".join(f"- {g.title}" for g in goals) + "\n"
        return CONTEXT_PROMPT.format(
            now=datetime.now().strftime("%A, %d %B %Y, %I:%M %p"),
            memories=mem_block,
            goals=goal_block,
        ).strip()

    def _messages(self, user_text: str) -> list[dict]:
        return (
            [{"role": "system", "content": self._system_prompt()}]
            + self.history
            + [{"role": "system", "content": self._context_prompt(user_text)},
               {"role": "user", "content": user_text}]
        )

    def _record(self, user_text: str, reply: str) -> None:
        self.history += [{"role": "user", "content": user_text}, {"role": "assistant", "content": reply}]
        if len(self.history) > HISTORY_TURNS * 2:
            # drop the older half in one go: a window sliding by one turn would change the start of
            # the prompt on every request and defeat Ollama's prompt cache
            self.history = self.history[-HISTORY_TURNS:]
        self.db.add_message(Message("user", user_text, self.session_id))
        self.db.add_message(Message("assistant", reply, self.session_id))
        if self.cfg.feature_on("memory"):
            self.memory.extract_in_background(user_text)

    def reply_stream(self, user_text: str) -> Iterator[str]:
        """Yield the reply piece by piece; history and memory are updated when it finishes."""
        parts: list[str] = []
        try:
            for piece in self.llm.chat_stream(self._messages(user_text)):
                parts.append(piece)
                yield piece
        except LLMError as e:
            log.error("Chat failed: %s", e)
            msg = "Sorry, I can't reach the local AI model right now. Is Ollama running? (ollama serve)"
            parts = [msg]
            yield msg
            return
        self._record(user_text, "".join(parts).strip())

    def reply(self, user_text: str) -> str:
        return "".join(self.reply_stream(user_text)).strip()

    def warm_up(self) -> None:
        """Load the chat model and cache the fixed system prompt, so the first reply starts at once."""
        try:
            self.llm.chat([{"role": "system", "content": self._system_prompt()},
                           {"role": "user", "content": "hi"}], max_tokens=1)
        except LLMError as e:
            log.debug("Warm-up skipped: %s", e)

    def reset(self) -> None:
        self.history.clear()
        self.session_id = uuid.uuid4().hex[:12]

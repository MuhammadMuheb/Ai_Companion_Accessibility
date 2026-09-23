"""Long-term memory that keeps itself up to date.

Facts, preferences, projects and goals are extracted from what the user says and stored in
the local SQLite database indefinitely. Each new fact is reconciled against what Lyra already
knows:

- the same thing again             -> kept once (refreshed)
- a change ("I'm going to London instead of Dubai", "mera goal ab ...", "no longer ...")
                                   -> the old memory is *replaced*, and the change is kept in history
- something new                    -> added

Goals follow the same rules in the goals list, so saying a new goal updates the old one rather
than piling up contradictions. Voice commands ("change my goal from X to Y", "forget that ...",
"remove my goal ...", "I finished ...") are handled by the workflow using the helpers here.
"""

from __future__ import annotations

import re
import threading
from difflib import SequenceMatcher

from app.llm import LLMError, OllamaClient, get_llm
from app.logger import get_logger
from app.memory import vector
from app.memory.models import Memory
from app.memory_db import MemoryDB, get_db

log = get_logger(__name__)

CATEGORIES = {"fact", "preference", "goal", "project", "person", "event", "note"}

EXTRACT_SYSTEM = (
    "You extract long-term facts about the USER from their message, for a personal assistant's memory. "
    "Keep things worth remembering for months: name, job, family, health needs, likes/dislikes, goals and "
    "aspirations, projects they work on, routines, important dates, personal notes. If the user CHANGES something "
    "(\"instead of\", \"no longer\", \"now\", \"ab\"), write the NEW version only. Ignore greetings, questions, "
    "small talk and temporary states. "
    'Reply ONLY with JSON: {"facts": [{"content": "...", "category": "fact|preference|goal|project|person|event|note", '
    '"importance": 1-5}]}. Write each fact as a short third-person sentence starting with "User". '
    'If there is nothing worth remembering reply {"facts": []}.'
)

DECIDE_SYSTEM = (
    "You maintain a personal assistant's memory. Given EXISTING memories and one NEW fact, decide:\n"
    '- "same": the new fact says the same thing as an existing one\n'
    '- "update": the new fact changes or replaces an existing one (e.g. a different destination, a new job, '
    "a changed preference)\n"
    '- "add": the new fact is about something else\n'
    'Reply ONLY with JSON: {"action": "same|update|add", "id": <existing id or null>}'
)

_PERSONAL = re.compile(r"\b(i|i'm|im|i've|my|mine|me|we|our|mera|meri|mere|main|mujhe|hum|hamara)\b", re.I)
# words that signal "this replaces what I said before"
CHANGE_MARKERS = re.compile(
    r"\b(instead|no longer|not anymore|any ?more|changed|change(?:d)? my mind|switch(?:ed)?|now|actually|rather|"
    r"update|replace|ab|badal|nahi raha|nahi rahi|balke|bajaye)\b", re.I)
_STOP = vector._STOP | {"user", "users", "wants", "want", "goal", "goals", "likes", "like", "is", "to", "a", "an", "the",
                        "will", "would", "plans", "plan", "planning", "their", "his", "her", "next", "year", "instead",
                        "of", "now", "no", "longer", "actually"}


def worth_extracting(text: str) -> bool:
    text = text.strip()
    return len(text) >= 12 and bool(_PERSONAL.search(text)) and not text.startswith("/")


def topic_words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]+", text.lower()) if w not in _STOP and len(w) > 2}


def goal_title(fact: str) -> str:
    """'User now wants to travel to London instead of Dubai.' -> 'Travel to London'"""
    t = re.sub(r"\b(?:now|actually|really|also|still)\s+", "", fact.strip(), flags=re.I)
    t = re.sub(r",?\s+(?:instead of|rather than|not)\s+[^,.]+", "", t, flags=re.I)
    t = re.sub(r"^(?:the )?user(?:'s)?\s+(?:main |long-term |new )?(?:goal|aim|dream|aspiration|plan)s?\s+(?:is|are)\s+(?:to\s+)?",
               "", t, flags=re.I)
    t = re.sub(r"^(?:the )?user\s+(?:wants|would like|hopes|plans|aims|dreams|intends|is planning|is going)\s+to\s+",
               "", t, flags=re.I)
    t = re.sub(r"^(?:the )?user\s+(?:dreams|hopes)\s+(?:of|for)\s+", "", t, flags=re.I)
    t = re.sub(r"^(?:the )?user\s+", "", t, flags=re.I).strip().rstrip(".")
    return t[:1].upper() + t[1:] if t else fact


class MemoryStore:
    SAME_EMBED = 0.90     # cosine above this = the same fact
    RELATED_EMBED = 0.62  # above this = about the same topic (maybe an update)
    SAME_KEYWORD = 0.75
    RELATED_KEYWORD = 0.3

    def __init__(self, db: MemoryDB | None = None, llm: OllamaClient | None = None):
        self.db = db or get_db()
        self.llm = llm or get_llm()
        self._lock = threading.Lock()  # background extraction and voice commands may write at once

    # ---- similarity ---------------------------------------------------------------------
    def _embed(self, text: str) -> list[float] | None:
        try:
            return self.llm.embed(text)
        except Exception:
            return None

    def _score(self, text: str, emb, other_text: str, other_emb) -> tuple[float, bool]:
        """(similarity, measured_with_embeddings)"""
        if emb and other_emb and len(emb) == len(other_emb):
            return vector.cosine(emb, other_emb), True
        return vector.keyword_score(text, other_text), False

    def _related(self, content: str, emb, items: list[tuple[int, str, list | None]]) -> list[tuple[float, bool, int, str]]:
        scored = []
        for item_id, text, item_emb in items:
            score, by_emb = self._score(content, emb, text, item_emb)
            shared = topic_words(content) & topic_words(text)
            related = score >= (self.RELATED_EMBED if by_emb else self.RELATED_KEYWORD) or len(shared) >= 1 and score >= 0.45
            if related:
                scored.append((score, by_emb, item_id, text))
        return sorted(scored, reverse=True)[:3]

    def _decide(self, content: str, candidates: list[tuple[float, bool, int, str]], hint: str) -> tuple[str, int | None]:
        """Same / update / add for a new fact vs related existing ones."""
        best_score, by_emb, best_id, best_text = candidates[0]
        if best_score >= (self.SAME_EMBED if by_emb else self.SAME_KEYWORD):
            return "same", best_id
        if CHANGE_MARKERS.search(hint) or CHANGE_MARKERS.search(content):
            # "instead of Dubai": prefer the candidate that the sentence mentions
            mentioned = [c for c in candidates if topic_words(c[3]) & topic_words(hint)]
            return "update", (mentioned or candidates)[0][2]
        listing = "\n".join(f"[{c[2]}] {c[3]}" for c in candidates)
        try:
            data = self.llm.ask_json(f"EXISTING:\n{listing}\n\nNEW: {content}", system=DECIDE_SYSTEM, max_tokens=60)
        except LLMError:
            data = None
        if isinstance(data, dict) and data.get("action") in ("same", "update", "add"):
            ids = {c[2] for c in candidates}
            target = data.get("id") if data.get("id") in ids else best_id
            return data["action"], (None if data["action"] == "add" else target)
        return "add", None

    # ---- write path ---------------------------------------------------------------------
    def remember(self, content: str, category: str = "fact", importance: int = 3, said: str = "") -> tuple[str, int]:
        """Add a fact, or update/refresh the one it repeats or replaces. Returns (action, id)."""
        content = content.strip()
        category = category if category in CATEGORIES else "fact"
        importance = max(1, min(5, int(importance or 3)))
        emb = self._embed(content)
        with self._lock:
            existing = [(m.id, m.content, m.embedding) for m in self.db.all_memories()]
            candidates = self._related(content, emb, existing)
            action, target = self._decide(content, candidates, said or content) if candidates else ("add", None)
            if action in ("same", "update") and target is not None:
                old = self.db.get_memory(target)
                self.db.update_memory(target, content, emb or (old.embedding if old else None),
                                      max(importance, old.importance if old else importance))
                if action == "update" and old and old.content != content:
                    self.db.add_history("memory", target, "updated", old.content, content)
                    log.info("Memory %s changed: %r -> %r", target, old.content, content)
                result = ("updated" if action == "update" else "kept", target)
            else:
                result = ("added", self.db.add_memory(Memory(content, category, importance, emb)))
                log.info("Stored memory %s: %s", result[1], content)
        if category == "goal":
            self.set_goal(goal_title(content), said=said or content)
        return result

    def extract_and_store(self, user_text: str) -> list[tuple[str, int]]:
        if not worth_extracting(user_text):
            return []
        try:
            data = self.llm.ask_json(f"User message:\n{user_text}", system=EXTRACT_SYSTEM)
        except LLMError as e:
            log.warning("Memory extraction failed: %s", e)
            return []
        facts = data.get("facts", []) if isinstance(data, dict) else []
        results = []
        for fact in facts:
            if isinstance(fact, dict) and isinstance(fact.get("content"), str) and len(fact["content"]) > 5:
                results.append(self.remember(fact["content"], fact.get("category", "fact"), fact.get("importance", 3),
                                             said=user_text))
        return results

    def extract_in_background(self, user_text: str) -> None:
        if worth_extracting(user_text):
            threading.Thread(target=self.extract_and_store, args=(user_text,), daemon=True).start()

    # ---- goals --------------------------------------------------------------------------------
    def find_goals(self, query: str, limit: int = 3) -> list[tuple[float, int, str]]:
        goals = self.db.goals()
        if not goals:
            return []
        q_emb = self._embed(query)
        scored = []
        for g in goals:
            g_emb = self._embed(g.title) if q_emb else None
            score, _ = self._score(query, q_emb, g.title, g_emb)
            score = max(score, SequenceMatcher(None, query.lower(), g.title.lower()).ratio() * 0.9)
            if topic_words(query) & topic_words(g.title):
                score += 0.15
            scored.append((score, g.id, g.title))
        return sorted(scored, reverse=True)[:limit]

    def set_goal(self, title: str, said: str = "") -> tuple[str, int]:
        """Add a goal, or update the goal it repeats or replaces."""
        title = title.strip().rstrip(".")
        goals = self.db.goals()
        emb = self._embed(title)
        items = [(g.id, g.title, self._embed(g.title) if emb else None) for g in goals]
        candidates = self._related(title, emb, items)
        action, target = self._decide(title, candidates, said or title) if candidates else ("add", None)
        if action in ("same", "update") and target is not None:
            old = next(g.title for g in goals if g.id == target)
            if action == "update" and old != title:
                self.db.update_goal(target, title)
                self.db.add_history("goal", target, "updated", old, title)
                log.info("Goal %s changed: %r -> %r", target, old, title)
                return "updated", target
            return "kept", target
        return "added", self.db.add_goal(title)

    def change_goal(self, old_query: str | None, new_title: str) -> tuple[str, int | None, str | None]:
        """Explicit "change my goal (from X) to Y". Returns (action, goal_id, old_title)."""
        matches = self.find_goals(old_query or new_title)
        if not matches or (old_query and matches[0][0] < 0.45):
            return "added", self.db.add_goal(new_title), None
        _, goal_id, old = matches[0]
        self.db.update_goal(goal_id, new_title)
        self.db.add_history("goal", goal_id, "updated", old, new_title)
        return "updated", goal_id, old

    def remove_goal(self, goal_id: int, done: bool = False) -> None:
        title = next((g.title for g in self.db.goals() if g.id == goal_id), None)
        self.db.set_goal_status(goal_id, "done" if done else "dropped")
        self.db.add_history("goal", goal_id, "done" if done else "removed", title, None)

    # ---- forgetting ---------------------------------------------------------------------------
    def find_memories(self, query: str, limit: int = 3) -> list[tuple[float, Memory]]:
        memories = self.db.all_memories()
        emb = self._embed(query)
        scored = []
        for m in memories:
            score, _ = self._score(query, emb, m.content, m.embedding)
            if topic_words(query) & topic_words(m.content):
                score += 0.15
            scored.append((score, m))
        return sorted(scored, key=lambda t: t[0], reverse=True)[:limit]

    def forget(self, mem_id: int) -> bool:
        old = self.db.get_memory(mem_id)
        ok = self.db.delete_memory(mem_id)
        if ok and old:
            self.db.add_history("memory", mem_id, "forgotten", old.content, None)
        return ok

    # ---- read path ------------------------------------------------------------------------------
    def recall(self, query: str, top_k: int = 5) -> list[Memory]:
        memories = self.db.all_memories()
        if not memories:
            return []
        embedding = self._embed(query) if any(m.embedding for m in memories) else None
        hits = [m for m, _ in vector.search(query, embedding, memories, top_k=top_k)]
        # always include the most important facts (name, health needs, ...) even if not matched
        core = [m for m in memories if m.importance >= 5 and m not in hits][:3]
        return core + hits

    def all(self) -> list[Memory]:
        return self.db.all_memories()

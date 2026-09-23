"""Similarity search over stored memories.

Embeddings live in SQLite next to each memory; search is brute-force cosine,
which is plenty fast for a personal memory of a few thousand facts and avoids
a heavy vector-database dependency. Falls back to keyword overlap when the
embedding model is unavailable.
"""

from __future__ import annotations

import math
import re

from app.memory.models import Memory

_WORD = re.compile(r"[a-z0-9؀-ۿऀ-ॿ]+")
_STOP = {"the", "a", "an", "is", "are", "am", "i", "my", "me", "to", "of", "and", "in", "on", "for", "it",
         "user", "what", "do", "you", "was", "be", "that", "this", "with", "at", "have", "has"}


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def keywords(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if w not in _STOP and len(w) > 1}


def keyword_score(query: str, text: str) -> float:
    q, t = keywords(query), keywords(text)
    if not q or not t:
        return 0.0
    return len(q & t) / math.sqrt(len(q) * len(t))


def search(
    query: str,
    query_embedding: list[float] | None,
    memories: list[Memory],
    top_k: int = 5,
    min_score: float = 0.35,
) -> list[tuple[Memory, float]]:
    scored: list[tuple[Memory, float]] = []
    for mem in memories:
        if query_embedding and mem.embedding and len(mem.embedding) == len(query_embedding):
            score = cosine(query_embedding, mem.embedding)
            threshold = min_score + 0.2  # embedding scores run higher than keyword overlap
        else:
            score = keyword_score(query, mem.content)
            threshold = min_score
        if score >= threshold:
            # small boost for important memories
            scored.append((mem, score + 0.01 * mem.importance))
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return scored[:top_k]

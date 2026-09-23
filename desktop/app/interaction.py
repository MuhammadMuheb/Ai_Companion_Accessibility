"""Questions the companion asks the user mid-task ("Which account?", "What should the
message say?", "Send it?") and matching spoken/typed answers to the offered choices.

Each interface supplies an `Ask` callback: the text CLI reads input(), the voice engine
speaks and listens, and the web UI turns the question into buttons. Workflows must ask
every question *before* doing anything irreversible, because the web UI answers by
re-running the request with the answers filled in.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Callable

# Ask(question, options) -> the chosen option / typed text, or None if unanswered or cancelled
Ask = Callable[[str, "list[str] | None"], "str | None"]

YES = {"yes", "yeah", "yep", "sure", "ok", "okay", "confirm", "do it", "go ahead", "send", "send it", "haan", "han",
       "ha", "ji", "jee", "ji haan", "theek hai", "thik hai", "kar do", "kardo", "bhej do", "bhejo", "bilkul", "zaroor"}
NO = {"no", "nope", "cancel", "stop", "don't", "dont", "nahi", "nahin", "mat karo", "mat bhejo", "rehne do", "ruko"}

ORDINALS = {
    0: {"1", "one", "first", "pehla", "pehli", "pehle", "ek"},
    1: {"2", "two", "second", "doosra", "dusra", "doosri", "dusri", "do"},
    2: {"3", "three", "third", "teesra", "tisra", "teesri", "teen"},
    3: {"4", "four", "fourth", "chautha", "chauthi", "char", "chaar"},
    4: {"5", "five", "fifth", "panchwa", "paanch"},
}
CANCEL = {"cancel", "none", "nothing", "stop", "rehne do", "chhodo", "kuch nahi", "never mind"}


def words(text: str) -> str:
    return re.sub(r"[^\w\s@.']", " ", (text or "").lower()).strip()


def is_yes(text: str) -> bool | None:
    t = words(text)
    if not t:
        return None
    if any(re.search(rf"\b{re.escape(w)}\b", t) for w in NO):
        return False
    if any(re.search(rf"\b{re.escape(w)}\b", t) for w in YES):
        return True
    return None


def match_option(answer: str, options: list[str], cutoff: float = 0.6) -> str | None:
    """Pick the option the user meant: by name, part of a name, a number or an ordinal word."""
    a = words(answer)
    if not a or not options:
        return None
    if a in CANCEL:
        return None
    lowered = [words(o) for o in options]
    if lowered == ["yes", "no"]:  # "haan bhej do", "nahi rehne do", ...
        answer = is_yes(a)
        return None if answer is None else options[0 if answer else 1]
    for opt, low in zip(options, lowered):
        if a == low:
            return opt
    tokens = set(a.split())
    if not any(set(low.split()) & tokens for low in lowered):
        hits = [i for i, names in ORDINALS.items() if i < len(options) and tokens & names]
        if len(hits) > 1:  # "the second one": "second" wins over the filler "one"
            hits = [i for i in hits if tokens & (ORDINALS[i] - {"one", "ek", "do"})] or hits
        if hits:
            return options[hits[0]]
    # the answer contains an option's name, or an option contains the whole answer
    contained = [o for o, low in zip(options, lowered) if re.search(rf"\b{re.escape(low)}\b", a)]
    if len(contained) == 1:
        return contained[0]
    containing = [o for o, low in zip(options, lowered) if re.search(rf"\b{re.escape(a)}\b", low)]
    if len(containing) == 1:
        return containing[0]
    best, best_score = None, 0.0
    for opt, low in zip(options, lowered):
        score = max(SequenceMatcher(None, a, low).ratio(),
                    max((SequenceMatcher(None, t, w).ratio() for t in tokens for w in low.split()), default=0))
        if score > best_score:
            best, best_score = opt, score
    return best if best_score >= cutoff else None


def no_answer(question: str, options: list[str] | None = None) -> str | None:
    return None


def ask_confirm(ask: Ask, question: str) -> bool:
    answer = ask(question, ["Yes", "No"])
    return bool(answer) and (answer == "Yes" or is_yes(answer) is True)


def console_ask(question: str, options: list[str] | None = None) -> str | None:
    """Text-mode Ask: numbered choices, or free text."""
    try:
        if options:
            print(f"❓ {question}")
            for i, opt in enumerate(options, 1):
                print(f"   {i}. {opt}")
            return match_option(input("   Your choice: "), options)
        answer = input(f"❓ {question} ").strip()
        return answer or None
    except (EOFError, KeyboardInterrupt):
        return None

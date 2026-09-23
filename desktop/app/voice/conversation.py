"""Deciding what to do with each sentence of a spoken conversation (after the wake word):
end it ("bye", "bas shukriya"), skip it (noise / gibberish), or answer it."""

from __future__ import annotations

import re

from app.voice.stt import is_hallucination

GOODBYES = re.compile(
    r"^(?:ok(?:ay)?,? |theek hai,? |acha,? |thanks?,? |thank you,? |shukriya,? )*"
    r"(?:(?:good ?)?bye(?: bye)?|that'?s (?:all|it)|nothing(?: else)?|no(?:,)? (?:thanks|thank you|that'?s all)|"
    r"(?:we'?re|i'?m) done|stop(?: talking)?|goodnight|good night|see you|"
    r"bas(?: karo| kar do| hai| itna hi)?|kuch nahi?|aur kuch nahi?|allah hafiz|khuda hafiz|shukriya bas|"
    r"chalo bas|jao|theek hai bas)"
    r"(?:,? (?:thanks?|thank you|shukriya))*$")


def _clean(text: str) -> str:
    return re.sub(r"[^\w' ]+", " ", text.lower()).strip()


def is_goodbye(text: str) -> bool:
    from app.config import get_config

    t = re.sub(r"\s+", " ", _clean(text))
    # "bye Lyra", "Khuda hafiz Lyra, shukriya": the assistant's own name doesn't change the meaning
    for name in {get_config().assistant.name.lower(), *(core_name(p) for p in get_config().wake_phrases)}:
        if name:
            t = re.sub(rf"(?:^| ){re.escape(name)}(?= |$)", "", t).strip()
    return bool(GOODBYES.match(re.sub(r"\s+", " ", t)))


def core_name(phrase: str) -> str:
    return re.sub(r"^(?:hey|hi|hello|ok|okay|oye) ", "", phrase.lower()).strip()


def is_gibberish(text: str) -> bool:
    """Noise that Whisper turned into words: known hallucinations, no real letters,
    or one token repeated over and over ("the the the", "ha ha ha ha")."""
    t = _clean(text)
    if is_hallucination(t):
        return True
    words = t.split()
    letters = sum(c.isalpha() for c in t)
    if letters < 3 or letters < len(t.replace(" ", "")) * 0.5:
        return True
    if len(words) >= 3 and len(set(words)) == 1:
        return True
    return len(words) >= 6 and len(set(words)) <= len(words) / 4

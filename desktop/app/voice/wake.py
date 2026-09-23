"""Wake word: keep listening in the background and react when the user says the
companion's name ("Hey Companion, what time is it?") — no button needed.

Any custom name works because detection is done on Whisper's transcript: each thing the
user says is transcribed and fuzzy-matched against the wake phrases, also comparing a
simplified "sound skeleton" so accents and spelling differences ("Dost" / "Dhost") still
match. A recorder thread keeps capturing while the previous clip is being transcribed, so
the name isn't missed while the companion is busy.
"""

from __future__ import annotations

import queue
import re
import threading
from difflib import SequenceMatcher
from typing import Callable

import numpy as np

from app.config import get_config
from app.logger import get_logger

log = get_logger(__name__)

_SOUNDS = [("ph", "f"), ("th", "t"), ("dh", "d"), ("kh", "k"), ("gh", "g"), ("sh", "s"), ("ch", "9"),
           ("ck", "k"), ("c", "k"), ("q", "k"), ("w", "v"), ("z", "j"), ("x", "ks"), ("y", "i")]


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", (text or "").lower())).strip()


def sounds(text: str) -> str:
    """Spelling with similar sounds merged but vowels kept: 'Lira' and 'Lyra' become the same."""
    t = normalize(text).replace(" ", "")
    for a, b in _SOUNDS:
        t = t.replace(a, b)
    return t


def skeleton(text: str) -> str:
    """Rough phonetic key: similar sounds merged, vowels dropped (except a leading one), repeats collapsed."""
    t = normalize(text).replace(" ", "")
    for a, b in _SOUNDS:
        t = t.replace(a, b)
    if not t:
        return ""
    head, rest = t[0], re.sub(r"[aeiouh]", "", t[1:])
    return re.sub(r"(.)\1+", r"\1", head + rest)


def edit_similarity(a: str, b: str) -> float:
    """1 - Levenshtein distance / length. Stricter than difflib for near-miss words."""
    if a == b:
        return 1.0
    if not a or not b:
        return 0.0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return 1 - prev[-1] / max(len(a), len(b))


def similarity(a: str, b: str) -> float:
    plain = edit_similarity(normalize(a), normalize(b))
    # same letters once look-alike sounds are merged ("Lira" / "Lyra"), a little below an exact match
    voiced = edit_similarity(sounds(a), sounds(b)) - 0.03
    sa, sb = skeleton(a), skeleton(b)
    if not sa or not sb:
        return plain
    # same sound skeleton ("Kampanion" ~ "Companion", "Dhost" ~ "Dost") is strong evidence;
    # merely similar skeletons ("compassion") are not
    if sa == sb:
        sound = 0.8 + 0.1 * plain if plain >= 0.72 else plain  # "company" also reduces to "kmpn"
    else:
        sound = SequenceMatcher(None, sa, sb).ratio() - 0.15
    return max(plain, sound, voiced)


FILLERS = {"hey", "hi", "hello", "ok", "okay", "oye", "oy", "ae", "ay", "arey", "arre", "yaar", "o"}


def core(text: str) -> str:
    """The distinctive part of a phrase: 'hey dost' -> 'dost' (so 'hey most' doesn't match)."""
    kept = [w for w in normalize(text).split() if w not in FILLERS]
    return " ".join(kept) or normalize(text)


def threshold_for(phrase: str, sensitivity: float) -> float:
    """Short names are easy to hit by accident ("most" ~ "dost"), so they must match more closely."""
    letters = len(normalize(phrase).replace(" ", ""))
    return min(0.97, sensitivity + max(0, 6 - letters) * 0.03)


# How letters sound when a name is an initialism ("AJ" is heard as "A.J.", "Ay Jay", "Ayjay")
LETTER_NAMES = {
    "a": ["a", "ay", "eh"], "b": ["b", "bee", "be"], "c": ["c", "see", "cee", "si"], "d": ["d", "dee", "di", "de"],
    "e": ["e", "ee"], "f": ["f", "ef"], "g": ["g", "gee", "ji"], "h": ["h", "aitch", "ech"], "i": ["i", "eye", "ai"],
    "j": ["j", "jay", "je"], "k": ["k", "kay", "ke"], "l": ["l", "el"], "m": ["m", "em"], "n": ["n", "en"],
    "o": ["o", "oh"], "p": ["p", "pee", "pi"], "q": ["q", "cue", "queue"], "r": ["r", "ar", "are"], "s": ["s", "es", "ess"],
    "t": ["t", "tee", "ti"], "u": ["u", "you", "yu"], "v": ["v", "vee"], "w": ["w"], "x": ["x", "ex"],
    "y": ["y", "why", "wai"], "z": ["z", "zed", "zee"],
}


def _initialisms(phrases: list[str]) -> dict[str, str]:
    """Spoken spellings of short initialism names -> the name: {'ayjay': 'aj', 'ajay': ...}."""
    from itertools import product

    spelled: dict[str, str] = {}
    for phrase in phrases:
        name = core(phrase).replace(" ", "")
        if 2 <= len(name) <= 4 and name.isalpha() and all(ch in LETTER_NAMES for ch in name):
            for parts in product(*(LETTER_NAMES[ch] for ch in name)):
                spelled["".join(parts)] = name
    return spelled


def _merge_spelled(original: list[str], tokens: list[str], spelled: dict[str, str]) -> tuple[list[str], list[str]]:
    """Join 'a j' / 'ay jay' / 'ayjay' into 'aj' so short initialism names can be matched."""
    out_orig, out_tok, i = [], [], 0
    while i < len(tokens):
        joined = None
        for size in (4, 3, 2, 1):  # longest run of pieces that spells the name
            if i + size <= len(tokens) and "".join(tokens[i:i + size]) in spelled:
                joined = (size, spelled["".join(tokens[i:i + size])])
                break
        if joined and (joined[0] > 1 or tokens[i] != joined[1] or len(tokens[i]) > 1):
            size, name = joined
            out_orig.append(" ".join(original[i:i + size]))
            out_tok.append(name)
            i += size
        else:
            out_orig.append(original[i])
            out_tok.append(tokens[i])
            i += 1
    return out_orig, out_tok


def find_wake(transcript: str, phrases: list[str], sensitivity: float = 0.82) -> tuple[str, str] | None:
    """If `transcript` contains a wake phrase near its start, return (phrase, rest of the sentence)."""
    # "A.J." -> "A J" so the letters become separate words that can be joined back together
    original = re.sub(r"\b([A-Za-z])\.(?=\s*[A-Za-z]\b)", r"\1 ", transcript).split()
    tokens = [normalize(w) for w in original]
    keep = [i for i, t in enumerate(tokens) if t]
    tokens = [tokens[i] for i in keep]
    original = [original[i] for i in keep]
    spelled = _initialisms(phrases)
    if spelled:
        original, tokens = _merge_spelled(original, tokens, spelled)
    if not tokens:
        return None
    best = None  # (score, start, size, phrase)
    for phrase in phrases:
        p = normalize(phrase)
        n = len(p.split())
        if not n:
            continue
        for size in {max(1, n - 1), n, n + 1}:
            for start in range(0, len(tokens) - size + 1):
                if start > 2 and len(tokens) > n + 1:
                    break  # the name should come first ("hey dost, ..."), not mid-sentence
                window = " ".join(tokens[start:start + size])
                score = similarity(core(window), core(p))
                if size != n:
                    score -= 0.05  # prefer windows the same length as the phrase
                margin = score - threshold_for(core(p), sensitivity)
                if best is None or margin > best[0]:
                    best = (margin, start, size, phrase)
    if best and best[0] >= 0:
        _, start, size, phrase = best
        rest = " ".join(original[start + size:]).strip(" ,.!?-")
        return phrase, rest
    return None


class WakeListener(threading.Thread):
    """Background loop: record -> transcribe -> look for the wake phrase -> call `on_wake(rest)`.

    `on_wake` runs on this thread and may use the microphone itself (e.g. to listen for the
    command); the recorder is paused meanwhile.
    """

    def __init__(self, listener, on_wake: Callable[[str, "np.ndarray"], None]):
        super().__init__(daemon=True, name="wake-listener")
        self.listener = listener
        self.on_wake = on_wake
        self._stop = threading.Event()
        self.asleep = False  # the user said "stop listening": stays paused after the current command
        self._paused = threading.Event()   # set = don't record (companion is talking/listening itself)
        self._clips: queue.Queue[np.ndarray] = queue.Queue(maxsize=3)
        self._triggered = threading.Event()
        self.last_heard = ""

    # control -----------------------------------------------------------------
    def stop(self) -> None:
        self._stop.set()
        self._drain()
        try:
            self.listener._close_mic()
        except Exception as e:
            log.debug("Failed to close mic on stop: %s", e)

    def pause(self) -> None:
        self._paused.set()
        self._drain()
        try:
            self.listener._close_mic()
        except Exception as e:
            log.debug("Failed to close mic on pause: %s", e)

    def resume(self) -> None:
        self._drain()
        if not self.asleep:  # a short pause ending doesn't undo "stop listening"
            self._paused.clear()

    def trigger(self) -> None:
        """Act as if the wake word was just said (used by the push-to-talk hotkey)."""
        self.asleep = False  # pressing the talk key also ends "stop listening"
        self._triggered.set()

    def sleep(self) -> None:
        self.asleep = True
        self.pause()

    def wake_up(self) -> None:
        self.asleep = False
        self.resume()

    @property
    def paused(self) -> bool:
        return self._paused.is_set()

    def _drain(self) -> None:
        while not self._clips.empty():
            try:
                self._clips.get_nowait()
            except queue.Empty:
                break

    # threads ---------------------------------------------------------------------
    def _record_loop(self) -> None:
        import threading
        failures = 0
        while not self._stop.is_set():
            if self._paused.is_set():
                self._stop.wait(0.2)
                failures = 0
                continue
            record_thread = None
            result_holder = {"audio": None, "error": None}
            try:
                def record_with_timeout():
                    try:
                        result_holder["audio"] = self.listener.record(
                            require_speech=True, wait_seconds=5, max_seconds=10, silence_seconds=0.8)
                    except Exception as e:
                        result_holder["error"] = e
                record_thread = threading.Thread(target=record_with_timeout, daemon=False, name="wake-record-timeout")
                record_thread.start()
                record_thread.join(timeout=15)
                if record_thread.is_alive():
                    log.critical("Microphone read hung for >15s; forcing restart of audio stream")
                    self.listener._close_mic()
                    record_thread.join(timeout=2)
                    failures += 1
                    self._stop.wait(min(30, 2 * failures))
                    continue
                if result_holder["error"]:
                    raise result_holder["error"]
                audio = result_holder["audio"]
                failures = 0
            except Exception as e:
                failures += 1
                if failures == 1 or failures % 30 == 0:
                    hint = (" — Windows is blocking the microphone: Settings > Privacy & security > Microphone "
                            "> 'Let desktop apps access your microphone'") if "80070005" in str(e) else ""
                    log.warning("Wake recorder error (%d in a row): %s%s", failures, e, hint)
                self._stop.wait(min(30, 2 * failures))
                continue
            finally:
                if record_thread and record_thread.is_alive():
                    record_thread.join(timeout=1)
            if audio and audio.size and not self._paused.is_set():
                try:
                    self._clips.put(audio, timeout=1)
                except queue.Full:
                    pass

    def run(self) -> None:
        try:
            self.listener.calibrate()
        except Exception as e:
            log.warning("Mic calibration failed: %s", e)
        threading.Thread(target=self._record_loop, daemon=True, name="wake-recorder").start()
        log.info("Wake listener started for %s", get_config().wake_phrases)
        while not self._stop.is_set():
            if self._triggered.is_set():
                self._triggered.clear()
                self._dispatch("", None)
                continue
            try:
                audio = self._clips.get(timeout=0.3)
            except queue.Empty:
                continue
            cfg = get_config().voice
            if not cfg.wake_enabled or not get_config().feature_on("wake"):
                continue
            try:
                hit, text = self._spot(audio, cfg.wake_sensitivity)
            except Exception as e:
                log.warning("Wake transcription failed: %s", e)
                continue
            if text:
                self.last_heard = text
            if hit:
                log.info("Wake phrase %r heard in %r", hit[0], text)
                self._dispatch(hit[1], audio)

    def _spot(self, audio, sensitivity: float) -> tuple[tuple[str, str] | None, str]:
        """Look for the wake word cheaply: the light model reads only the start of the clip, and the
        full (slower, more accurate) model runs only when the wake word seems to be there."""
        phrases = get_config().wake_phrases
        quick = getattr(self.listener, "transcribe_wake", None)
        if quick is not None:
            first = quick(audio)
            if not first or not find_wake(first, phrases, sensitivity):
                return None, first
        text = self.listener.transcribe(audio)
        if not text:
            return None, ""
        hit = find_wake(text, phrases, sensitivity)
        if hit is None and quick is not None:
            hit = find_wake(first, phrases, sensitivity)
            hit = (hit[0], "") if hit else None  # the rest of a cut-off clip isn't reliable; ask instead
        return hit, text

    def _dispatch(self, rest: str, audio) -> None:
        self.pause()
        try:
            self.on_wake(rest, audio)
        except Exception:
            log.exception("Wake handler failed")
        finally:
            self.resume()

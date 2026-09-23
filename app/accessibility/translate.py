"""English -> Roman Urdu: translate highlighted text, or the text under the mouse pointer.

Selected text is copied with Ctrl+C (the clipboard is restored afterwards). With nothing
selected, the screen around the pointer is read with OCR and the line(s) under the pointer
are used. Translations are cached so asking twice gives the same answer.
"""

from __future__ import annotations

import ctypes
import re
import time
from ctypes import wintypes
from functools import lru_cache

from app.config import get_config
from app.llm import LLMError, OllamaClient, get_llm
from app.logger import audit, get_logger

log = get_logger(__name__)

SYSTEM = """You translate English into Roman Urdu (Urdu written in English letters, the way Pakistanis text on WhatsApp).
Rules: use simple everyday Urdu words; keep technical words, names, numbers and brand names in English;
never use Urdu or Hindi script; do not add anything that is not in the English text.
Format exactly:
Tarjuma: <translation>
Matlab: <one short sentence explaining what it means or what to do, in Roman Urdu>

Examples:
English: Your password will expire in 3 days.
Tarjuma: Aap ka password 3 din mein expire ho jayega.
Matlab: 3 din ke andar naya password bana lein, warna login nahi ho sakega.

English: Please save your work before closing the window.
Tarjuma: Meharbani kar ke window band karne se pehle apna kaam save kar lein.
Matlab: Pehle save karein, warna jo kaam kiya hai woh zaya ho jayega.

English: Click here to verify your email address.
Tarjuma: Apna email address verify karne ke liye yahan click karein.
Matlab: Yeh link aap ka email confirm karne ke liye hai.
"""

MESSAGE_SYSTEM = """Rewrite the user's short message in natural Roman Urdu (Urdu in English letters, WhatsApp style).
Keep the meaning, tone, names and numbers exactly. Reply with ONLY the rewritten message.

Examples:
English: I am on the way, I will reach in 10 minutes.
Roman Urdu: Main raste mein hoon, 10 minute mein pahunch jaunga.

English: I'm busy right now, I will call you later.
Roman Urdu: Main abhi busy hoon, baad mein call karta hoon.

English: Yes I am available tomorrow.
Roman Urdu: Ji haan, main kal free hoon.
"""

# Words that only appear in Roman Urdu (used to decide whether a message needs converting)
ROMAN_URDU = {"hai", "hain", "hoon", "hun", "main", "mein", "mai", "ka", "ki", "ke", "ko", "se", "nahi", "nahin",
              "kya", "aap", "ap", "tum", "raha", "rahi", "rahe", "gaya", "gayi", "ga", "gi", "kar", "karo", "abhi",
              "baad", "pehle", "theek", "thik", "acha", "achha", "haan", "ji", "kal", "aaj", "hum", "mera", "meri",
              "apna", "apni", "wala", "wali", "jaunga", "jaungi", "aaunga", "aaungi", "kab", "kahan", "kyun", "bhi",
              "ghar", "raste", "rasta", "pahunch", "bas", "yaar", "shukriya", "inshallah", "bilkul", "sab", "wapas"}
ENGLISH = {"the", "is", "are", "am", "i", "i'm", "you", "will", "be", "to", "and", "of", "in", "on", "at", "it",
           "my", "your", "we", "can", "have", "has", "this", "that", "for", "with", "not", "yes", "no", "please",
           "coming", "going", "reach", "later", "busy", "available", "tomorrow", "today", "home", "call", "way"}


def looks_roman_urdu(text: str) -> bool:
    tokens = re.findall(r"[a-z']+", text.lower())
    if not tokens:
        return False
    urdu = sum(t in ROMAN_URDU for t in tokens)
    english = sum(t in ENGLISH for t in tokens)
    return urdu > english or (urdu >= 2 and urdu >= english)


_clients: dict[str, OllamaClient] = {}
_NON_LATIN = re.compile(r"[؀-ۿݐ-ݿऀ-ॿﭐ-﷿ﹰ-﻿]")


def _client() -> OllamaClient:
    model = get_config().llm.translate_model
    if not model:
        return get_llm()
    if model not in _clients:
        _clients[model] = OllamaClient(model=model)
        _clients[model].keep_alive = "30m"
    return _clients[model]


def preload() -> None:
    _client().preload()


def is_roman(text: str) -> bool:
    """False if the model slipped into Urdu/Hindi script."""
    return not _NON_LATIN.search(text or "")


@lru_cache(maxsize=256)
def translate(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return "Koi text nahi mila."
    for attempt in range(2):
        reply = _client().ask(f"English: {text[:1500]}", system=SYSTEM, temperature=0.0 if attempt == 0 else 0.3,
                              max_tokens=300).strip()
        # keep only the two expected lines if the model adds chatter
        keep = [ln.strip() for ln in reply.splitlines() if ln.strip().lower().startswith(("tarjuma", "matlab"))]
        reply = "\n".join(keep) if keep else reply
        if reply and is_roman(reply):
            return reply
        log.warning("Translation came back in the wrong script; retrying")
    raise LLMError("the model didn't answer in Roman Urdu — pick another translation model in Settings")


@lru_cache(maxsize=256)
def to_roman_urdu(message: str) -> str:
    """Convert an English message (e.g. Whisper's transcript of Urdu speech) to Roman Urdu."""
    if looks_roman_urdu(message):
        return message
    reply = _client().ask(f"English: {message}\nRoman Urdu:", system=MESSAGE_SYSTEM, temperature=0.0, max_tokens=150)
    reply = reply.strip().splitlines()[0].strip() if reply.strip() else message
    reply = re.sub(r"^(roman urdu|tarjuma)\s*:\s*", "", reply, flags=re.I).strip().strip('"')
    if not reply or not is_roman(reply):
        return message  # better to send the words as spoken than something unreadable
    return reply


# ---- getting the text --------------------------------------------------------------

user32 = ctypes.windll.user32
VK = {"shift": 0x10, "ctrl": 0x11, "alt": 0x12, "lwin": 0x5B, "rwin": 0x5C}
TERMINALS = {"ConsoleWindowClass", "CASCADIA_HOSTING_WINDOW_CLASS", "mintty", "VirtualConsoleClass"}


def _foreground_class() -> str:
    hwnd = user32.GetForegroundWindow()
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def _wait_modifiers_released(timeout: float = 1.5) -> None:
    """After a hotkey like Ctrl+Alt+T, wait until the keys are up so our Ctrl+C isn't Ctrl+Alt+C."""
    end = time.time() + timeout
    while time.time() < end and any(user32.GetAsyncKeyState(v) & 0x8000 for v in VK.values()):
        time.sleep(0.03)


def selected_text() -> str:
    """Copy the current selection and return it ('' if nothing is selected). Restores the clipboard."""
    import pyautogui
    import pyperclip

    if _foreground_class() in TERMINALS:
        return ""  # Ctrl+C would interrupt a running program in a terminal
    _wait_modifiers_released()
    try:
        previous = pyperclip.paste()
    except pyperclip.PyperclipException:
        previous = None
    marker = f"__companion_{time.time_ns()}__"
    pyperclip.copy(marker)
    pyautogui.hotkey("ctrl", "c")
    text = ""
    for _ in range(10):
        time.sleep(0.05)
        current = pyperclip.paste()
        if current != marker:
            text = current
            break
    if previous is not None:
        pyperclip.copy(previous)
    return text.strip()


def cursor_position() -> tuple[int, int]:
    pt = wintypes.POINT()
    # physical pixels, matching the screenshot, even with display scaling
    if not user32.GetPhysicalCursorPos(ctypes.byref(pt)):
        user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


def pick_lines(lines: list[tuple[str, tuple[int, int, int, int]]], x: int, y: int) -> str:
    """Choose the OCR line under (x, y) — or the nearest one — plus the rest of its paragraph."""
    if not lines:
        return ""
    def distance(box):
        x0, y0, x1, y1 = box
        dx = 0 if x0 <= x <= x1 else min(abs(x - x0), abs(x - x1))
        dy = 0 if y0 <= y <= y1 else min(abs(y - y0), abs(y - y1))
        return dy * 3 + dx  # vertical distance matters most
    ordered = sorted(lines, key=lambda l: l[1][1])
    best = min(range(len(ordered)), key=lambda i: distance(ordered[i][1]))
    if distance(ordered[best][1]) > 80:
        return ""
    height = max(8, ordered[best][1][3] - ordered[best][1][1])
    chosen = [best]
    for step in (-1, 1):  # grow the paragraph up and down while lines are close together
        i = best
        while 0 <= i + step < len(ordered) and len(chosen) < 6:
            prev_box, next_box = ordered[i][1], ordered[i + step][1]
            gap = (next_box[1] - prev_box[3]) if step == 1 else (prev_box[1] - next_box[3])
            overlap = min(prev_box[2], next_box[2]) - max(prev_box[0], next_box[0])
            if gap > height * 0.9 or overlap <= 0:
                break
            i += step
            chosen.append(i)
    return " ".join(ordered[i][0] for i in sorted(chosen))


def text_under_cursor() -> str:
    from PIL import ImageGrab

    from app.accessibility.screen import ocr_lines

    x, y = cursor_position()
    left, top = max(0, x - 600), max(0, y - 150)
    image = ImageGrab.grab(bbox=(left, top, x + 600, y + 150), all_screens=True)
    lines = [(t, (b[0] + left, b[1] + top, b[2] + left, b[3] + top)) for t, b in ocr_lines(image)]
    return pick_lines(lines, x, y)


def translate_on_screen() -> tuple[str, str]:
    """(english, roman urdu explanation) for the selection or the text under the pointer."""
    english = selected_text()
    source = "selection"
    if not english:
        english = text_under_cursor()
        source = "pointer"
    if not english:
        return "", ("Mujhe koi English text nahi mila. Text ko select karein, ya mouse ka pointer us par rakh kar "
                    "dobara koshish karein.")
    try:
        result = translate(english)
    except LLMError as e:
        return english, f"Tarjuma nahi ho saka: {e}"
    audit("translate", source=source, chars=len(english))
    return english, result

"""Send WhatsApp messages through the WhatsApp Desktop app.

Safety: the caller confirms the contact and the exact text with the user first. Before
pressing Enter this module also checks, via Windows UI Automation (with OCR as a fallback),
that the open chat's header really shows that contact — if not, the typed text is cleared
and nothing is sent.

Two ways to reach a chat:
- a phone number saved for the contact in Settings -> `whatsapp://send?phone=...` (exact), or
- searching the chat list for the contact's name as saved in WhatsApp.
"""

from __future__ import annotations

import re
import time
from difflib import SequenceMatcher
from urllib.parse import quote

from app.config import get_config
from app.interaction import words
from app.logger import audit, get_logger

log = get_logger(__name__)

PROCESS_NAMES = {"whatsapp.root.exe", "whatsapp.exe"}
SEARCH_NAMES = ("search or start a new chat", "search", "search input textbox")
COMPOSE_PREFIXES = ("type a message", "type a message to", "message")


class WhatsAppError(RuntimeError):
    pass


class MultipleMatches(WhatsAppError):
    def __init__(self, names: list[str]):
        super().__init__("More than one chat matches: " + ", ".join(names))
        self.names = names


def resolve_contact(spoken: str) -> tuple[str, str | None]:
    """Spoken name -> (name as saved in WhatsApp, phone or None) using Settings -> Contacts."""
    contacts = get_config().contacts or {}
    key = words(spoken)
    for alias, info in contacts.items():
        if words(alias) == key or (isinstance(info, dict) and words(str(info.get("whatsapp", ""))) == key):
            info = info if isinstance(info, dict) else {}
            return str(info.get("whatsapp") or alias), (str(info.get("phone")) if info.get("phone") else None)
    # fuzzy: "mama g" -> "mama ji"
    best, score = None, 0.0
    for alias in contacts:
        s = SequenceMatcher(None, key, words(alias)).ratio()
        if s > score:
            best, score = alias, s
    if best and score >= 0.75:
        info = contacts[best] if isinstance(contacts[best], dict) else {}
        return str(info.get("whatsapp") or best), (str(info.get("phone")) if info.get("phone") else None)
    return spoken.strip(), None


def name_matches(expected: str, seen: str, threshold: float = 0.8) -> bool:
    e, s = words(expected), words(seen)
    if not e or not s:
        return False
    if e == s or re.search(rf"\b{re.escape(e)}\b", s):
        return True
    return SequenceMatcher(None, e, s[: len(e) + 3]).ratio() >= threshold


# ---- window handling ---------------------------------------------------------------

def _auto():
    import uiautomation as auto

    return auto


def find_window():
    import psutil

    auto = _auto()
    for w in auto.GetRootControl().GetChildren():
        try:
            if psutil.Process(w.ProcessId).name().lower() in PROCESS_NAMES and w.ControlTypeName == "WindowControl":
                return w
        except (psutil.Error, OSError):
            continue
    return None


def open_whatsapp(timeout: float = 20):
    from app.automation import launcher

    win = find_window()
    if win is None:
        found = launcher.find_app("whatsapp", fuzzy=False)
        if not found:
            raise WhatsAppError("WhatsApp Desktop is not installed.")
        launcher._launch(found[1])
    end = time.time() + timeout
    while time.time() < end:
        win = find_window()
        if win is not None and _search_box(win, wait=0) is not None:
            break
        if win is not None:
            _restore(win)
        time.sleep(0.5)
    if win is None:
        raise WhatsAppError("WhatsApp didn't open.")
    _restore(win)
    return win


def _restore(win) -> None:
    try:
        pattern = win.GetWindowPattern()
        if pattern.WindowVisualState == 2:  # minimized: the chat UI isn't built until it's shown
            pattern.SetWindowVisualState(0)
            time.sleep(1.0)
    except Exception:
        pass
    try:
        win.SetActive()
    except Exception:
        pass


def _walk(control, depth: int = 0, max_depth: int = 90):
    try:
        children = control.GetChildren()
    except Exception:
        return
    for child in children:
        yield child
        if depth < max_depth:
            yield from _walk(child, depth + 1, max_depth)


def _edits(win) -> list:
    return [c for c in _walk(win) if c.ControlTypeName == "EditControl"]


def _search_box(win, wait: float = 5):
    end = time.time() + wait
    while True:
        for e in _edits(win):
            if words(e.Name).startswith(SEARCH_NAMES):
                return e
        if time.time() >= end:
            return None
        time.sleep(0.3)


def _compose_box(win, search, wait: float = 6):
    end = time.time() + wait
    while True:
        for e in _edits(win):
            if search is not None and e.BoundingRectangle.top == search.BoundingRectangle.top:
                continue
            name = words(e.Name)
            if name.startswith(COMPOSE_PREFIXES) or (search is not None and e.BoundingRectangle.top > search.BoundingRectangle.bottom + 200):
                return e
        if time.time() >= end:
            return None
        time.sleep(0.3)


def _paste(text: str) -> None:
    import pyautogui
    import pyperclip

    try:
        previous = pyperclip.paste()
    except pyperclip.PyperclipException:
        previous = None
    pyperclip.copy(text)
    time.sleep(0.05)
    pyautogui.hotkey("ctrl", "v")
    time.sleep(0.2)
    if previous is not None:
        pyperclip.copy(previous)


def _clear_focused() -> None:
    import pyautogui

    pyautogui.hotkey("ctrl", "a")
    pyautogui.press("delete")


# ---- finding and verifying the chat ---------------------------------------------------

def _results(win, search, contact: str) -> list:
    """Clickable chat rows under the search box whose name starts with/contains the contact."""
    top = search.BoundingRectangle.bottom
    right = search.BoundingRectangle.right + 150
    rows, seen = [], set()
    for c in _walk(win):
        if c.ControlTypeName not in ("ListItemControl", "DataItemControl", "ButtonControl", "GroupControl", "TextControl"):
            continue
        r = c.BoundingRectangle
        if r.top <= top or r.left > right or r.height() < 20 or r.height() > 120:
            continue
        first = c.Name.split("\n")[0].split(",")[0].strip()
        if first and name_matches(contact, first) and r.top not in seen:
            seen.add(r.top)
            rows.append((r.top, first, c))
    rows.sort(key=lambda t: t[0])
    return rows


def _header_names(win, compose) -> list[str]:
    """Texts in the chat header (top of the conversation pane)."""
    wr = win.BoundingRectangle
    pane_left = compose.BoundingRectangle.left - 80
    names = []
    for c in _walk(win):
        if c.ControlTypeName not in ("TextControl", "ButtonControl", "GroupControl", "HeaderControl"):
            continue
        r = c.BoundingRectangle
        if r.left >= pane_left and wr.top < r.top < wr.top + 160 and c.Name.strip():
            names.append(c.Name.strip())
    return names


def _header_ocr(win, compose) -> str:
    from PIL import ImageGrab

    from app.accessibility.screen import ocr

    wr = win.BoundingRectangle
    box = (max(compose.BoundingRectangle.left - 80, wr.left), wr.top, wr.right, wr.top + 160)
    try:
        return ocr(ImageGrab.grab(bbox=box, all_screens=True))
    except RuntimeError:
        return ""


def verify_chat(win, compose, contact: str) -> bool:
    if any(name_matches(contact, n) for n in _header_names(win, compose)):
        return True
    text = _header_ocr(win, compose)
    return any(name_matches(contact, line) for line in text.splitlines())


def open_chat(contact: str, phone: str | None = None, prefill: str = ""):
    """Open the chat with `contact`. Returns (window, compose_box, prefilled)."""
    if phone:
        digits = re.sub(r"\D", "", phone)
        import os

        os.startfile(f"whatsapp://send?phone={digits}" + (f"&text={quote(prefill)}" if prefill else ""))
        time.sleep(2.5)
        win = open_whatsapp()
        compose = _compose_box(win, _search_box(win, wait=2))
        if compose is None:
            raise WhatsAppError(f"The chat for {contact} didn't open. Is {phone} on WhatsApp?")
        return win, compose, bool(prefill)

    win = open_whatsapp()
    search = _search_box(win)
    if search is None:
        raise WhatsAppError("I couldn't find WhatsApp's search box. Is WhatsApp logged in?")
    search.Click(simulateMove=False)
    _clear_focused()
    _paste(contact)
    rows = []
    for _ in range(12):  # results appear as you type
        time.sleep(0.4)
        rows = _results(win, search, contact)
        if rows:
            break
    if not rows:
        search.Click(simulateMove=False)
        _clear_focused()
        raise WhatsAppError(f"I couldn't find a chat called {contact} in WhatsApp.")
    exact = [r for r in rows if words(r[1]) == words(contact)]
    distinct = sorted({r[1] for r in rows})
    if not exact and len(distinct) > 1:
        search.Click(simulateMove=False)
        _clear_focused()
        raise MultipleMatches(distinct[:6])
    target = (exact or rows)[0][2]
    target.Click(simulateMove=False)
    compose = _compose_box(win, search)
    if compose is None:
        raise WhatsAppError(f"The chat with {contact} didn't open.")
    return win, compose, False


def send_message(contact: str, message: str, phone: str | None = None, dry_run: bool = False) -> str:
    """Open the chat, type `message` and send it. The user must already have confirmed."""
    import pyautogui

    win, compose, prefilled = open_chat(contact, phone, prefill=message)
    if get_config().whatsapp.verify_contact and not verify_chat(win, compose, contact):
        compose.Click(simulateMove=False)
        _clear_focused()
        raise WhatsAppError(f"I opened a chat but couldn't confirm it is {contact}, so I didn't send anything.")
    compose.Click(simulateMove=False)
    if not prefilled:
        _clear_focused()
        _paste(message)
    if dry_run:
        _clear_focused()
        return f"Dry run: the chat with {contact} is open and verified; nothing was sent."
    pyautogui.press("enter")
    time.sleep(0.8)
    audit("whatsapp_send", contact=contact, chars=len(message))
    return f"Message sent to {contact}."

"""Mouse-pointer awareness and on-screen guidance.

- `describe_pointer()`: what the mouse is on (button/link/text name via UI Automation, OCR
  fallback) — "yeh kya hai?" while pointing.
- `MouseReader`: optional "mouse reading" mode — rest the pointer on something for a moment
  and MD says what it is (like a screen reader's mouse echo).
- `show(query)` / `click(query)`: find a control by name in the front window, glide the mouse
  to it and highlight it; clicking asks first for risky buttons (delete, send, pay...).
"""

from __future__ import annotations

import re
import threading
import time
from difflib import SequenceMatcher

from app.logger import audit, get_logger

log = get_logger(__name__)

INTERACTIVE = {"ButtonControl", "HyperlinkControl", "MenuItemControl", "TabItemControl", "CheckBoxControl",
               "RadioButtonControl", "ComboBoxControl", "EditControl", "ListItemControl", "TreeItemControl",
               "SplitButtonControl", "TextControl", "DataItemControl", "ImageControl"}
RISKY = re.compile(r"\b(delete|remove|erase|discard|send|submit|pay|buy|purchase|order|confirm|uninstall|format|"
                   r"sign out|log ?out|reset|close all|empty|transfer|publish|post)\b", re.I)
KIND = {"ButtonControl": "button", "HyperlinkControl": "link", "MenuItemControl": "menu item", "TabItemControl": "tab",
        "CheckBoxControl": "checkbox", "RadioButtonControl": "option", "ComboBoxControl": "drop-down",
        "EditControl": "text box", "ListItemControl": "list item", "TreeItemControl": "item", "TextControl": "text",
        "ImageControl": "image", "DocumentControl": "page", "WindowControl": "window", "PaneControl": "area"}


def make_dpi_aware() -> None:
    """Use real pixels everywhere so UI Automation, screenshots and mouse moves agree on scaled displays."""
    import ctypes

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (OSError, AttributeError):
        pass


def _auto():
    import uiautomation as auto

    return auto


def _window_of(control) -> str:
    try:
        top = control.GetTopLevelControl()
        return top.Name if top else ""
    except Exception:
        return ""


def element_at(x: int, y: int) -> dict:
    c = _auto().ControlFromPoint(x, y)
    if c is None:
        return {}
    r = c.BoundingRectangle
    return {"name": (c.Name or "").strip(), "type": c.ControlTypeName, "kind": KIND.get(c.ControlTypeName, "item"),
            "window": _window_of(c), "rect": (r.left, r.top, r.right, r.bottom)}


def describe_pointer() -> str:
    from app.accessibility.translate import cursor_position, text_under_cursor

    x, y = cursor_position()
    info = element_at(x, y)
    name = info.get("name", "")
    if name and len(name) < 200 and info.get("type") not in ("PaneControl", "DocumentControl", "WindowControl"):
        where = f" in {info['window']}" if info.get("window") and info["window"] != name else ""
        return f"The mouse is on a {info['kind']}: {name}{where}."
    text = text_under_cursor()
    if text:
        return f"The mouse is on this text: {text}"
    if info.get("window"):
        return f"The mouse is over {info['window']}, but I can't read anything at that spot."
    return "I can't tell what's under the mouse."


# ---- finding and pointing at controls ------------------------------------------------------

def _score(query: str, name: str) -> float:
    q, n = query.lower().strip(), name.lower().strip()
    if not n:
        return 0.0
    if q == n:
        return 1.0
    if re.search(rf"\b{re.escape(q)}\b", n):
        return 0.9 - min(0.3, (len(n) - len(q)) / 200)
    return SequenceMatcher(None, q, n[: len(q) + 10]).ratio() * 0.8


def find_control(query: str, max_nodes: int = 4000):
    """Best-matching visible control in the foreground window."""
    auto = _auto()
    root = auto.GetForegroundControl()
    if root is None:
        return None
    best, best_score, seen = None, 0.0, 0
    stack = [root]
    while stack and seen < max_nodes:
        c = stack.pop()
        seen += 1
        try:
            kids = c.GetChildren()
        except Exception:
            kids = []
        stack.extend(kids)
        if c.ControlTypeName not in INTERACTIVE:
            continue
        try:
            if c.IsOffscreen:
                continue
            r = c.BoundingRectangle
            if r.width() <= 0 or r.height() <= 0:
                continue
        except Exception:
            continue
        score = _score(query, c.Name or "")
        if c.ControlTypeName == "TextControl":
            score -= 0.05  # prefer real controls over labels with the same text
        if score > best_score:
            best, best_score = c, score
    return best if best_score >= 0.6 else None


def _glide_to(control) -> tuple[int, int, int, int]:
    import pyautogui

    r = control.BoundingRectangle
    pyautogui.moveTo((r.left + r.right) // 2, (r.top + r.bottom) // 2, duration=0.6, tween=pyautogui.easeInOutQuad)
    return r.left, r.top, r.right, r.bottom


def show(query: str) -> str:
    from app.overlay import get_overlay

    control = find_control(query)
    if control is None:
        return f"I couldn't find '{query}' in the window in front."
    rect = _glide_to(control)
    get_overlay().highlight(*rect, seconds=3)
    kind = KIND.get(control.ControlTypeName, "item")
    audit("show_control", query=query, name=control.Name)
    return f"Here it is — the {kind} '{control.Name}'. I've moved the mouse onto it."


def click(query: str, confirm=None) -> str:
    from app.overlay import get_overlay

    control = find_control(query)
    if control is None:
        return f"I couldn't find '{query}' in the window in front."
    name = control.Name or query
    rect = _glide_to(control)
    get_overlay().highlight(*rect, seconds=2)
    if RISKY.search(name) and (confirm is None or not confirm(f"Click '{name}'? This may not be reversible.")):
        return "Okay, I didn't click it."
    try:
        control.Click(simulateMove=False)
    except Exception as e:
        return f"I couldn't click it: {e}"
    audit("click_control", name=name)
    return f"Clicked '{name}'."


# ---- mouse reading mode ---------------------------------------------------------------------------

class MouseReader(threading.Thread):
    """Say what's under the pointer when it rests somewhere new for `dwell` seconds."""

    def __init__(self, say, dwell: float = 1.2):
        super().__init__(daemon=True, name="mouse-reader")
        self.say, self.dwell = say, dwell
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        from app.accessibility.translate import cursor_position
        from app.runtime import in_call

        auto = _auto()
        init = auto.UIAutomationInitializerInThread(debug=False)
        last_pos, since, last_said = None, time.time(), ""
        try:
            while not self._stop.is_set():
                pos = cursor_position()
                if pos != last_pos:
                    last_pos, since = pos, time.time()
                elif time.time() - since >= self.dwell and not in_call.is_set():
                    try:
                        info = element_at(*pos)
                    except Exception:
                        info = {}
                    label = info.get("name", "")
                    if label and label != last_said and len(label) < 160:
                        last_said = label
                        self.say(f"{info['kind']}: {label}" if info.get("kind") not in ("text", "item") else label)
                    since = float("inf")  # speak once per resting place
                self._stop.wait(0.2)
        finally:
            del init

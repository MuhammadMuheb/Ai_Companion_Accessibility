"""Keyboard, windows, clipboard, volume and system status."""

from __future__ import annotations

from datetime import datetime

from app.logger import audit

KEY_ALIASES = {
    "control": "ctrl", "escape": "esc", "return": "enter", "windows": "win", "window": "win",
    "page up": "pageup", "page down": "pagedown", "delete": "delete", "back space": "backspace",
    "spacebar": "space", "arrow up": "up", "arrow down": "down", "arrow left": "left", "arrow right": "right",
}


def _gui():
    import pyautogui

    pyautogui.FAILSAFE = True  # slam the mouse into a screen corner to abort
    return pyautogui


def type_text(text: str) -> str:
    gui = _gui()
    if text.isascii():
        gui.write(text, interval=0.02)
    else:  # pyautogui can't type non-ASCII (Urdu/Hindi) — paste it instead
        import pyperclip

        pyperclip.copy(text)
        gui.hotkey("ctrl", "v")
    audit("type_text", chars=len(text))
    return "Typed it."


def press_keys(spec: str) -> str:
    """Press a key or combo, e.g. 'enter', 'ctrl+s', 'alt tab'."""
    spec = spec.lower().strip()
    for spoken, key in KEY_ALIASES.items():
        spec = spec.replace(spoken, key)
    keys = [k for k in spec.replace("+", " ").split() if k]
    if not keys:
        return "Which key?"
    gui = _gui()
    if len(keys) == 1:
        gui.press(keys[0])
    else:
        gui.hotkey(*keys)
    audit("press_keys", keys=keys)
    return f"Pressed {' + '.join(keys)}."


def volume(direction: str, steps: int = 5) -> str:
    key = {"up": "volumeup", "down": "volumedown", "mute": "volumemute"}.get(direction)
    if not key:
        return "Say volume up, down or mute."
    _gui().press(key, presses=1 if key == "volumemute" else steps)
    return f"Volume {direction}."


def list_windows() -> list[str]:
    import pygetwindow

    return [t for t in pygetwindow.getAllTitles() if t.strip()]


def switch_window(name: str) -> str:
    import pygetwindow

    name = name.lower()
    for win in pygetwindow.getAllWindows():
        if win.title and name in win.title.lower():
            try:
                if win.isMinimized:
                    win.restore()
                win.activate()
            except Exception:
                # Windows refuses focus changes sometimes; alt-tab style nudge then retry
                _gui().press("alt")
                win.activate()
            return f"Switched to {win.title}."
    return f"I couldn't find a window called {name}."


def read_clipboard() -> str:
    import pyperclip

    text = pyperclip.paste()
    return text if text else "The clipboard is empty."


def system_status() -> str:
    import psutil

    parts = [f"It's {datetime.now().strftime('%I:%M %p').lstrip('0')}"]
    battery = psutil.sensors_battery()
    if battery:
        plug = "charging" if battery.power_plugged else "on battery"
        parts.append(f"battery {battery.percent:.0f} percent, {plug}")
    parts.append(f"CPU {psutil.cpu_percent(interval=0.5):.0f} percent")
    parts.append(f"memory {psutil.virtual_memory().percent:.0f} percent used")
    disk = psutil.disk_usage("C:\\")
    parts.append(f"{disk.free / 1e9:.0f} gigabytes free on C drive")
    return ", ".join(parts) + "."

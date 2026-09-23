"""System-tray icon for the native Lyra app, plus Windows shortcuts:
"Start with Windows" (Startup folder) and "Add to Start menu" — both per-user, no admin
rights, easy to undo.

While Windows is locked Lyra keeps running (it's a normal program in your session): it
still hears the wake word, reads notifications and reminders aloud and answers questions,
but refuses anything that controls the computer until you unlock (see workflow.LOCKED_OK).
It does not run on the sign-in screen before you log in — that would need a system service
with access to your account, which would weaken Windows' own security.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from app.config import DATA_ROOT, FROZEN, ROOT, get_config
from app.logger import get_logger

log = get_logger(__name__)

PROGRAMS = Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs"
STARTUP = PROGRAMS / "Startup"
SHORTCUT = STARTUP / "Lyra.lnk"
START_MENU = PROGRAMS / "Lyra.lnk"
LAUNCHER = ROOT / "lyra.pyw"
# shortcuts made by the earlier release (before the Lyra rebrand)
LEGACY_STARTUP = STARTUP / "MD Companion.lnk"
LEGACY_START_MENU = PROGRAMS / "MD.lnk"


def pythonw() -> str:
    if FROZEN:
        return sys.executable  # Lyra.exe itself
    exe = Path(sys.executable)
    candidate = exe.with_name("pythonw.exe")
    return str(candidate if candidate.exists() else exe)


def _make_shortcut(path: Path, arguments: str = "") -> str | None:
    """Create a .lnk that runs lyra.pyw with pythonw (no console). Returns an error or None."""
    path.parent.mkdir(parents=True, exist_ok=True)
    ps = ("$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:LYRA_LNK); "
          "$s.TargetPath = $env:LYRA_EXE; $s.Arguments = $env:LYRA_ARGS; "
          "$s.WorkingDirectory = $env:LYRA_DIR; $s.Description = 'Lyra - voice-first AI assistant'; $s.Save()")
    # from source the shortcut runs `pythonw lyra.pyw`; installed, it runs Lyra.exe directly
    args = arguments if FROZEN else f'"{LAUNCHER}"' + (f" {arguments}" if arguments else "")
    env = {**os.environ, "LYRA_LNK": str(path), "LYRA_EXE": pythonw(), "LYRA_ARGS": args,
           "LYRA_DIR": str(DATA_ROOT)}
    proc = subprocess.run(["powershell", "-NoProfile", "-Command", ps], env=env, capture_output=True, text=True)
    if proc.returncode != 0 or not path.exists():
        return proc.stderr[-200:] or "unknown error"
    return None


def migrate_legacy_shortcuts() -> None:
    """Replace shortcuts left by the earlier release with Lyra's own (same settings, new name)."""
    try:
        if LEGACY_STARTUP.exists():
            LEGACY_STARTUP.unlink()
            if not SHORTCUT.exists():
                _make_shortcut(SHORTCUT)
        if LEGACY_START_MENU.exists():
            LEGACY_START_MENU.unlink()
            if not START_MENU.exists():
                _make_shortcut(START_MENU, "--show")
    except OSError as e:
        log.info("Couldn't update old shortcuts: %s", e)


def autostart_enabled() -> bool:
    return SHORTCUT.exists()


def set_autostart(enabled: bool) -> str:
    name = get_config().assistant.name
    if not enabled:
        if SHORTCUT.exists():
            SHORTCUT.unlink()
        return f"{name} won't start automatically any more."
    error = _make_shortcut(SHORTCUT)
    if error:
        return f"Couldn't create the startup shortcut: {error}"
    return f"{name} will start by itself, in the background, when you sign in to Windows."


def start_menu_enabled() -> bool:
    return START_MENU.exists()


def set_start_menu(enabled: bool) -> str:
    if not enabled:
        if START_MENU.exists():
            START_MENU.unlink()
        return "Removed Lyra from the Start menu."
    error = _make_shortcut(START_MENU, "--show")
    return f"Couldn't add to the Start menu: {error}" if error else "Lyra is in the Start menu — search for “Lyra”."


def _icon_image():
    from PIL import Image, ImageDraw

    logo = ROOT / "app" / "web" / "static" / "lyra-logo.png"  # the Lyra brand mark
    if logo.exists():
        return Image.open(logo).convert("RGBA").resize((64, 64), Image.LANCZOS)
    # fallback: the Lyra mark without its gradient (dark tile, violet orbit, star)
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((2, 2, 62, 62), radius=16, fill=(16, 14, 32, 255))
    d.arc((15, 16, 49, 50), start=-30, end=270, fill=(139, 123, 255, 255), width=6)
    d.ellipse((42, 12, 50, 20), fill=(94, 234, 212, 255))
    return img


def start_tray(app):
    """Show the tray icon (runs on its own thread; the native window owns the main thread).
    `app` is a daemon.LyraApp."""
    try:
        import pystray
    except ImportError:
        log.warning("pystray not installed; no tray icon — use Ctrl+Alt+N to open Lyra")
        return None

    def wake():
        s = app.state.services
        return s.wake if s else None

    def toggle_listening(icon, _item):
        w = wake()
        if w:
            (w.wake_up if w.paused else w.sleep)()

    def quit_(icon, _item):
        icon.stop()
        app.quit()

    name = get_config().assistant.name
    menu = pystray.Menu(
        pystray.MenuItem(f"Open {name}", lambda icon, item: app.show("home"), default=True),
        pystray.MenuItem("Talk now", lambda icon, item: app.talk()),
        pystray.MenuItem("Settings", lambda icon, item: app.show("settings")),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(lambda item: "Resume listening" if (wake() and wake().paused) else "Pause listening",
                         toggle_listening, enabled=lambda item: wake() is not None),
        pystray.MenuItem("Start with Windows", lambda icon, item: set_autostart(not autostart_enabled()),
                         checked=lambda item: autostart_enabled()),
        pystray.MenuItem("Show in Start menu", lambda icon, item: set_start_menu(not start_menu_enabled()),
                         checked=lambda item: start_menu_enabled()),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(f"Quit {name}", quit_),
    )
    icon = pystray.Icon("lyra", _icon_image(), f"{name} — AI companion", menu)
    icon.run_detached()
    return icon

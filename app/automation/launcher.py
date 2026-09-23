"""Open Windows applications by name."""

from __future__ import annotations

import os
import shutil
import subprocess
from difflib import get_close_matches
from pathlib import Path

from app.logger import audit, get_logger

log = get_logger(__name__)

# spoken name -> command, URI or executable
KNOWN_APPS = {
    "notepad": "notepad.exe",
    "calculator": "calc.exe",
    "calc": "calc.exe",
    "paint": "mspaint.exe",
    "file explorer": "explorer.exe",
    "explorer": "explorer.exe",
    "files": "explorer.exe",
    "command prompt": "cmd.exe",
    "cmd": "cmd.exe",
    "powershell": "powershell.exe",
    "terminal": "wt.exe",
    "task manager": "taskmgr.exe",
    "settings": "ms-settings:",
    "control panel": "control.exe",
    "chrome": "chrome",
    "google chrome": "chrome",
    "edge": "msedge",
    "microsoft edge": "msedge",
    "firefox": "firefox",
    "word": "winword",
    "excel": "excel",
    "powerpoint": "powerpnt",
    "outlook": "outlook",
    "vs code": "code",
    "vscode": "code",
    "visual studio code": "code",
    "spotify": "spotify:",
    "camera": "microsoft.windows.camera:",
    "clock": "ms-clock:",
    "alarms": "ms-clock:",
    "store": "ms-windows-store:",
    "narrator": "narrator.exe",
    "magnifier": "magnify.exe",
    "on screen keyboard": "osk.exe",
    "snipping tool": "snippingtool.exe",
}

START_MENU_DIRS = [
    Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs",
    Path(os.environ.get("PROGRAMDATA", "C:/ProgramData")) / "Microsoft/Windows/Start Menu/Programs",
]


def _start_menu_shortcuts() -> dict[str, Path]:
    shortcuts: dict[str, Path] = {}
    for folder in START_MENU_DIRS:
        if folder.exists():
            for lnk in folder.rglob("*.lnk"):
                name = lnk.stem.lower()
                if "uninstall" not in name:
                    shortcuts.setdefault(name, lnk)
    return shortcuts


_start_apps_cache: dict[str, str] | None = None


def start_apps() -> dict[str, str]:
    """Installed apps from the Start menu, including Microsoft Store apps: name -> AppUserModelID."""
    global _start_apps_cache
    if _start_apps_cache is None:
        import json

        try:
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command", "Get-StartApps | ConvertTo-Json -Compress"],
                capture_output=True, text=True, timeout=20, encoding="utf-8", errors="replace").stdout
            items = json.loads(out) if out.strip() else []
            items = [items] if isinstance(items, dict) else items
            _start_apps_cache = {i["Name"].lower(): i["AppID"] for i in items if i.get("Name") and i.get("AppID")}
        except (subprocess.SubprocessError, ValueError) as e:
            log.warning("Couldn't list Start apps: %s", e)
            _start_apps_cache = {}
    return _start_apps_cache


def launch_app_id(app_id: str) -> None:
    subprocess.Popen(["explorer.exe", "shell:AppsFolder\\" + app_id], close_fds=True)


def find_app(name: str, fuzzy: bool = True) -> tuple[str, str | Path] | None:
    """(display name, launch target) for a spoken app name, or None."""
    spoken = name.strip().lower().removeprefix("the ").removesuffix(" app").strip()
    if spoken in KNOWN_APPS:
        return spoken, KNOWN_APPS[spoken]
    apps = start_apps()
    if spoken in apps:
        return spoken, "appid:" + apps[spoken]
    shortcuts = _start_menu_shortcuts()
    if spoken in shortcuts:
        return spoken, shortcuts[spoken]
    starts = sorted(k for k in apps if k.startswith(spoken + " ")) or sorted(k for k in shortcuts if k.startswith(spoken))
    if starts:
        key = starts[0]
        return key, ("appid:" + apps[key]) if key in apps else shortcuts[key]
    if fuzzy:
        match = get_close_matches(spoken, list(apps) + list(shortcuts), n=1, cutoff=0.75)
        if match:
            key = match[0]
            return key, ("appid:" + apps[key]) if key in apps else shortcuts[key]
    return None


def _launch(target: str | Path) -> None:
    target = str(target)
    if target.startswith("appid:"):
        launch_app_id(target.removeprefix("appid:"))
    elif target.endswith(":") or target.endswith(".lnk") or Path(target).exists():
        os.startfile(target)
    elif shutil.which(target):
        subprocess.Popen([shutil.which(target)], close_fds=True)
    else:
        # `start` resolves App Paths registrations (chrome, winword, ...)
        subprocess.Popen(["cmd", "/c", "start", "", target], close_fds=True, shell=False)


def open_app(name: str, fuzzy: bool = True) -> str:
    found = find_app(name, fuzzy=fuzzy)
    if found is None:
        return f"I couldn't find an app called {name}."
    spoken, target = found
    try:
        _launch(target)
    except OSError as e:
        log.warning("Failed to open %s: %s", name, e)
        return f"I couldn't open {name}: {e}"
    audit("open_app", name=spoken, target=str(target))
    return f"Opening {spoken}."


def close_app(process_name: str) -> list[str]:
    """Return running processes matching the name (the caller confirms before killing)."""
    import psutil

    name = process_name.lower()
    return sorted({p.info["name"] for p in psutil.process_iter(["name"]) if p.info["name"] and name in p.info["name"].lower()})

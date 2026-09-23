"""Deeper system control: install software with winget (Microsoft Store + community
repository), show files in File Explorer, switch browser tabs, and fall back to an app's
website when the desktop app isn't installed."""

from __future__ import annotations

import re
import subprocess
import threading
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

from app.logger import audit, get_logger

log = get_logger(__name__)

# Desktop app -> web version, used when the app isn't installed
WEB_FALLBACK = {
    "claude": "https://claude.ai", "chatgpt": "https://chatgpt.com", "whatsapp": "https://web.whatsapp.com",
    "spotify": "https://open.spotify.com", "teams": "https://teams.microsoft.com", "microsoft teams": "https://teams.microsoft.com",
    "outlook": "https://outlook.live.com", "word": "https://www.office.com/launch/word", "excel": "https://www.office.com/launch/excel",
    "powerpoint": "https://www.office.com/launch/powerpoint", "vs code": "https://vscode.dev", "vscode": "https://vscode.dev",
    "visual studio code": "https://vscode.dev", "figma": "https://www.figma.com", "notion": "https://www.notion.so",
    "slack": "https://app.slack.com", "discord": "https://discord.com/app", "telegram": "https://web.telegram.org",
    "zoom": "https://app.zoom.us", "canva": "https://www.canva.com", "instagram": "https://www.instagram.com",
    "tiktok": "https://www.tiktok.com", "gemini": "https://gemini.google.com", "photoshop": "https://www.photopea.com",
}


@dataclass
class Package:
    name: str
    id: str
    version: str
    source: str

    def label(self) -> str:
        where = "Microsoft Store" if self.source == "msstore" else "winget"
        return f"{self.name} ({where})"


def _run(args: list[str], timeout: int = 90) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace")


def parse_winget_table(output: str) -> list[Package]:
    """Parse `winget search` output (fixed-width columns under a dashed line)."""
    lines = [ln.rstrip() for ln in output.splitlines()]
    try:
        dash = next(i for i, ln in enumerate(lines) if re.fullmatch(r"-{10,}", ln.strip()))
    except StopIteration:
        return []
    header = lines[dash - 1]
    # the header may be preceded by progress spinner characters
    header = header[max(0, header.find("Name")):] if "Name" in header else header
    offset = len(lines[dash - 1]) - len(header)
    cols = [m.start() + offset for m in re.finditer(r"\S+", header)]
    names = [m.group(0).lower() for m in re.finditer(r"\S+", header)]
    packages = []
    for ln in lines[dash + 1:]:
        if not ln.strip() or len(ln) < cols[1]:
            continue
        fields = {}
        for j, name in enumerate(names):
            start = cols[j]
            end = cols[j + 1] if j + 1 < len(cols) else len(ln)
            fields[name] = ln[start:end].strip()
        if fields.get("id"):
            packages.append(Package(fields.get("name", ""), fields["id"], fields.get("version", ""),
                                    fields.get("source", "winget") or "winget"))
    return packages


def winget_search(query: str, limit: int = 5) -> list[Package]:
    proc = _run(["winget", "search", query, "--accept-source-agreements", "--disable-interactivity"])
    found = parse_winget_table(proc.stdout)
    q = query.lower()
    found.sort(key=lambda p: (-SequenceMatcher(None, q, p.name.lower()).ratio(), p.source != "msstore"))
    return found[:limit]


def winget_install(pkg: Package, on_done=None) -> None:
    """Install in the background; `on_done(message)` is called when it finishes."""
    def work():
        args = ["winget", "install", "--id", pkg.id, "--exact", "--source", pkg.source,
                "--accept-package-agreements", "--accept-source-agreements", "--disable-interactivity"]
        try:
            proc = _run(args, timeout=1800)
            ok = proc.returncode == 0
            msg = f"{pkg.name} is installed." if ok else f"Installing {pkg.name} failed: {(proc.stdout + proc.stderr)[-300:]}"
        except subprocess.TimeoutExpired:
            ok, msg = False, f"Installing {pkg.name} took more than 30 minutes and was stopped."
        audit("winget_install", id=pkg.id, source=pkg.source, ok=ok)
        if on_done:
            on_done(msg)
    threading.Thread(target=work, daemon=True, name="winget").start()


def show_in_explorer(path: str | Path) -> str:
    p = Path(path)
    if not p.exists():
        return f"{p} doesn't exist."
    subprocess.Popen(["explorer", f"/select,{p}"] if p.is_file() else ["explorer", str(p)])
    audit("show_in_explorer", path=str(p))
    return f"Showing {p.name} in File Explorer."


def switch_browser_tab(name: str) -> str | None:
    """Select the browser tab whose title best matches `name`. None if no tab matches."""
    import psutil
    import uiautomation as auto

    from app.mentor.notification_monitor import BROWSER_PROCESSES

    best, best_score, best_win = None, 0.0, None
    for win in auto.GetRootControl().GetChildren():
        try:
            if psutil.Process(win.ProcessId).name().lower() not in BROWSER_PROCESSES:
                continue
        except (psutil.Error, OSError):
            continue
        stack = [(win, 0)]
        while stack:
            c, d = stack.pop()
            try:
                kids = c.GetChildren()
            except Exception:
                continue
            for k in kids:
                if k.ControlTypeName == "TabItemControl":
                    title = k.Name.lower()
                    score = 1.0 if name.lower() in title else SequenceMatcher(None, name.lower(), title[:len(name) + 8]).ratio()
                    if score > best_score:
                        best, best_score, best_win = k, score, win
                elif d < 14 and k.ControlTypeName in ("PaneControl", "GroupControl", "TabControl", "ToolBarControl", "CustomControl"):
                    stack.append((k, d + 1))
    if best is None or best_score < 0.6:
        return None
    try:
        best_win.SetActive()
        try:
            best.GetSelectionItemPattern().Select()
        except Exception:
            best.Click(simulateMove=False)
    except Exception as e:
        return f"I found the tab but couldn't switch to it: {e}"
    audit("switch_tab", title=best.Name)
    return f"Switched to the tab {best.Name}."

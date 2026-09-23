"""Find, read, create and list files in the user's own folders."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from app.logger import audit

HOME = Path.home()
FOLDERS = {
    "desktop": HOME / "Desktop",
    "documents": HOME / "Documents",
    "downloads": HOME / "Downloads",
    "pictures": HOME / "Pictures",
    "music": HOME / "Music",
    "videos": HOME / "Videos",
    "projects": HOME / "Projects",
    "home": HOME,
}
TEXT_EXT = {".txt", ".md", ".py", ".js", ".ts", ".json", ".csv", ".html", ".css", ".yaml", ".yml", ".ini", ".log", ".xml"}


def folder(name: str) -> Path:
    key = name.strip().lower().removesuffix(" folder")
    if key in FOLDERS:
        return FOLDERS[key]
    return Path(os.path.expandvars(os.path.expanduser(name)))


def list_folder(name: str = "desktop", limit: int = 20) -> str:
    path = folder(name)
    if not path.is_dir():
        return f"I can't find the folder {name}."
    items = sorted(path.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
    items = [p for p in items if not p.name.startswith((".", "~$")) and p.name.lower() != "desktop.ini"]
    if not items:
        return f"{path.name} is empty."
    shown = ", ".join(("folder " if p.is_dir() else "") + p.name for p in items[:limit])
    more = f", and {len(items) - limit} more" if len(items) > limit else ""
    return f"{path.name} has {len(items)} items, newest first: {shown}{more}."


def find_files(query: str, roots: list[str] | None = None, limit: int = 10) -> list[Path]:
    query = query.lower()
    matches: list[Path] = []
    for root in roots or ["desktop", "documents", "downloads", "projects"]:
        base = folder(root)
        if not base.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in {"node_modules", "__pycache__", "venv", ".venv"}]
            for f in filenames:
                if query in f.lower():
                    matches.append(Path(dirpath) / f)
                    if len(matches) >= limit:
                        return matches
    return matches


def read_file(path: str | Path, limit: int = 4000) -> str:
    p = Path(path)
    if not p.is_file():
        found = find_files(str(path), limit=1)
        if not found:
            return f"I couldn't find a file called {path}."
        p = found[0]
    if p.suffix.lower() not in TEXT_EXT:
        return f"{p.name} isn't a text file I can read aloud."
    text = p.read_text(encoding="utf-8", errors="replace")
    audit("read_file", path=str(p))
    return text[:limit] + ("... (truncated)" if len(text) > limit else "")


def create_note(text: str, name: str = "") -> str:
    base = FOLDERS["documents"] / "Companion Notes"
    base.mkdir(parents=True, exist_ok=True)
    path = base / f"{name or 'note'}-{datetime.now():%Y%m%d-%H%M%S}.txt"
    path.write_text(text + "\n", encoding="utf-8")
    audit("create_note", path=str(path))
    return f"Saved your note in Documents, Companion Notes, as {path.name}."


def write_file(path: str | Path, content: str, overwrite: bool = False) -> str:
    p = Path(path)
    if p.exists() and not overwrite:
        return f"{p.name} already exists; I didn't overwrite it."
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    audit("write_file", path=str(p))
    return f"Saved {p.name}."

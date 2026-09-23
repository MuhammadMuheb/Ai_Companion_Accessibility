"""Simple git operations, described in plain spoken language."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable

from app.logger import audit


def _git(repo: str | Path, *args: str) -> tuple[int, str]:
    try:
        proc = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, timeout=60,
                              encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return 1, "Git is not installed."
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def is_repo(repo: str | Path) -> bool:
    return _git(repo, "rev-parse", "--is-inside-work-tree")[0] == 0


def status(repo: str | Path = ".") -> str:
    if not is_repo(repo):
        return f"{Path(repo).resolve().name} is not a git repository."
    _, branch = _git(repo, "branch", "--show-current")
    _, porcelain = _git(repo, "status", "--porcelain")
    lines = [ln for ln in porcelain.splitlines() if ln.strip()]
    if not lines:
        return f"On branch {branch}. Everything is committed."
    changed = sum(1 for ln in lines if not ln.startswith("??"))
    new = sum(1 for ln in lines if ln.startswith("??"))
    return f"On branch {branch}: {changed} changed file(s) and {new} new file(s)."


def log(repo: str | Path = ".", n: int = 5) -> str:
    code, out = _git(repo, "log", f"-{n}", "--pretty=format:%s (%cr)")
    return out if code == 0 else "No commits yet."


def commit_all(message: str, repo: str | Path = ".", confirm: Callable[[str], bool] | None = None) -> str:
    if not is_repo(repo):
        return "This folder is not a git repository."
    if confirm is None or not confirm(f"Commit all changes with message '{message}'?"):
        return "Okay, I didn't commit."
    _git(repo, "add", "-A")
    code, out = _git(repo, "commit", "-m", message)
    audit("git_commit", repo=str(repo), message=message, code=code)
    return "Committed." if code == 0 else f"Commit failed: {out[:300]}"

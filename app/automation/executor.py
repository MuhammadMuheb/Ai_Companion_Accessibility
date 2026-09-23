"""Run shell commands on the user's behalf — always shown and confirmed first,
with obviously destructive commands refused outright."""

from __future__ import annotations

import re
import subprocess
from typing import Callable

from app.logger import audit, get_logger

log = get_logger(__name__)

Confirm = Callable[[str], bool]

BLOCKED = [
    r"\bformat\s+[a-z]:", r"\bdiskpart\b", r"\bbcdedit\b", r"\bcipher\s+/w", r"\breg\s+delete\b",
    r"\brd\s+/s\b", r"\brmdir\s+/s\b", r"\bdel\s+.*[/\\*]", r"\brm\s+-rf?\b", r"remove-item\b.*-recurse",
    r"\bshutdown\b", r"\bset-executionpolicy\b", r"\bnet\s+user\b", r"\btakeown\b", r"\bicacls\b",
    r"\bvssadmin\b", r"\bwmic\b.*\bdelete\b", r"invoke-webrequest.*\|\s*iex", r"\biex\b", r"downloadstring",
]

# Read-only commands that are safe to run without asking
SAFE = [r"^(dir|ls|echo|type|where|whoami|hostname|ipconfig|systeminfo|tasklist|ver|date /t|time /t)\b",
        r"^(get-date|get-process|get-childitem|get-location|get-content|test-connection|ping)\b",
        r"^git\s+(status|log|diff|branch)\b", r"^python\s+--version$"]


def is_blocked(command: str) -> bool:
    return any(re.search(p, command, re.I) for p in BLOCKED)


def is_safe(command: str) -> bool:
    return any(re.search(p, command.strip(), re.I) for p in SAFE) and not re.search(r"[|;&>]", command)


def run(command: str, confirm: Confirm | None = None, cwd: str | None = None, timeout: int = 60) -> str:
    command = command.strip()
    if not command:
        return "No command given."
    if is_blocked(command):
        audit("command_blocked", command=command)
        return "I won't run that command because it could damage your system or data."
    if not is_safe(command):
        if confirm is None or not confirm(f"Run this command: {command} ?"):
            return "Okay, I didn't run it."
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True, text=True, timeout=timeout, cwd=cwd, encoding="utf-8", errors="replace",
        )
    except subprocess.TimeoutExpired:
        return f"The command took longer than {timeout} seconds and was stopped."
    audit("run_command", command=command, code=proc.returncode)
    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    if proc.returncode != 0:
        return f"The command failed. {err[:800] or out[:800]}"
    return out[:3000] or "Done. The command produced no output."

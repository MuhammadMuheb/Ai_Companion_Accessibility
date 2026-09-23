"""Self-diagnosis and repair.

`check()` runs health checks (Ollama, models, microphone, speech, OCR, Python packages, disk,
notification database) and `recent_errors()` pulls real tracebacks out of Lyra's own log and
points at the source line. Problems with a known, safe fix (start Ollama, pull a missing model,
pip-install a missing package) are fixed after the user says yes. For anything else Lyra explains
the likely cause and gives the exact commands — it does not rewrite its own code by itself.
"""

from __future__ import annotations

import importlib.util
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from app.config import ROOT, get_config
from app.logger import get_logger

log = get_logger(__name__)

PACKAGES = {  # import name -> pip name, feature
    "faster_whisper": ("faster-whisper", "speech recognition"),
    "pyttsx3": ("pyttsx3", "speaking (Windows voices)"),
    "piper": ("piper-tts", "neural voices"),
    "soundcard": ("soundcard", "microphone switching and call recording"),
    "onnxruntime": ("onnxruntime", "voice print"),
    "kaldi_native_fbank": ("kaldi-native-fbank", "voice print"),
    "uiautomation": ("uiautomation", "browser tabs, WhatsApp, pointer"),
    "pynput": ("pynput", "hotkeys"),
    "pyautogui": ("pyautogui", "mouse and keyboard control"),
    "pystray": ("pystray", "tray icon"),
    "anthropic": ("anthropic", "Claude expert mode"),
    "fastapi": ("fastapi", "Lyra's window (in-process API)"),
    "webview": ("pywebview", "Lyra's native window"),
}


@dataclass
class Issue:
    title: str
    detail: str
    fix_label: str = ""
    fix_command: list[str] = field(default_factory=list)  # safe, known fix (run only after a yes)
    manual: str = ""                                      # what the user can run themselves

    def describe(self) -> str:
        return f"{self.title}: {self.detail}" + (f" Fix: {self.manual}" if self.manual else "")


def _ollama_exe() -> str | None:
    found = shutil.which("ollama")
    if found:
        return found
    default = Path.home() / "AppData/Local/Programs/Ollama/ollama.exe"
    return str(default) if default.exists() else None


def check() -> list[Issue]:
    from app.llm import get_llm

    cfg = get_config()
    issues: list[Issue] = []
    llm = get_llm()
    if not llm.is_available():
        exe = _ollama_exe()
        issues.append(Issue("Ollama isn't running", "I can't reach the local AI model server.",
                            "Start Ollama", [exe, "serve"] if exe else [], manual="ollama serve"))
    else:
        for model in {cfg.llm.chat_model, cfg.llm.embedding_model, cfg.llm.translate_model} - {""}:
            if not llm.has_model(model):
                issues.append(Issue(f"Model {model} is missing", "A configured model isn't downloaded.",
                                    f"Download {model}", ["ollama", "pull", model], manual=f"ollama pull {model}"))
    for module, (pip_name, feature) in PACKAGES.items():
        try:
            found = importlib.util.find_spec(module) is not None
        except (ImportError, ValueError):
            found = False
        if not found:
            issues.append(Issue(f"Python package {pip_name} is missing", f"Needed for {feature}.",
                                f"Install {pip_name}", [sys.executable, "-m", "pip", "install", pip_name],
                                manual=f"python -m pip install {pip_name}"))
    try:
        from app.voice.mic import MicStream

        with MicStream() as mic:
            audio = mic.record(0.5)
        if float(abs(audio).max()) < 1e-5:
            issues.append(Issue("The microphone sends silence", f"{mic.name} gives no sound at all.",
                                manual="Windows Settings > Privacy & security > Microphone: allow desktop apps; "
                                       "check the mic isn't muted."))
    except Exception as e:
        issues.append(Issue("No working microphone", str(e), manual="Plug in or enable a microphone."))
    free_gb = shutil.disk_usage(ROOT).free / 1e9
    if free_gb < 3:
        issues.append(Issue("Disk almost full", f"Only {free_gb:.1f} GB free.", manual="Free up disk space."))
    return issues


def fix(issue: Issue) -> str:
    if not issue.fix_command or not issue.fix_command[0]:
        return f"I can't fix this myself. {issue.manual}"
    try:
        if issue.fix_command[-1] == "serve":  # a server: start it in the background and wait for it
            subprocess.Popen(issue.fix_command, creationflags=subprocess.CREATE_NO_WINDOW)
            from app.llm import get_llm

            for _ in range(20):
                time.sleep(1)
                if get_llm().is_available():
                    return "Ollama is running again."
            return "I started Ollama but it isn't answering yet."
        proc = subprocess.run(issue.fix_command, capture_output=True, text=True, timeout=1800,
                              encoding="utf-8", errors="replace")
        return f"Done: {issue.fix_label}." if proc.returncode == 0 else f"{issue.fix_label} failed: {proc.stderr[-300:]}"
    except Exception as e:
        return f"{issue.fix_label} failed: {e}"


# ---- errors from Lyra's own log -------------------------------------------------------------

_TB_START = re.compile(r"^Traceback \(most recent call last\):")
_FRAME = re.compile(r'File "([^"]+)", line (\d+), in (\S+)')


@dataclass
class LoggedError:
    message: str
    location: str
    snippet: str
    count: int = 1


def recent_errors(max_errors: int = 5) -> list[LoggedError]:
    cfg = get_config()
    path = cfg.dir(cfg.storage.logs_dir) / "companion.log"
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-4000:]
    errors: dict[str, LoggedError] = {}
    i = 0
    while i < len(lines):
        if _TB_START.match(lines[i]):
            frames, j = [], i + 1
            while j < len(lines) and (lines[j].startswith(" ") or not lines[j].strip()):
                m = _FRAME.search(lines[j])
                if m:
                    frames.append(m.groups())
                j += 1
            message = lines[j].strip() if j < len(lines) else "unknown error"
            own = [f for f in frames if str(ROOT) in f[0] or f[0].startswith("app")] or frames
            if own:
                file, line, func = own[-1]
                location = f"{Path(file).relative_to(ROOT) if str(ROOT) in file else file}:{line} in {func}"
                snippet = _snippet(file, int(line))
            else:
                location, snippet = "unknown", ""
            key = f"{location}|{message}"
            if key in errors:
                errors[key].count += 1
            else:
                errors[key] = LoggedError(message, location, snippet)
            i = j
        i += 1
    return list(errors.values())[-max_errors:]


def _snippet(file: str, line: int, context: int = 4) -> str:
    try:
        src = Path(file).read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    lo, hi = max(0, line - 1 - context), min(len(src), line + context)
    return "\n".join(f"{n + 1:4d}{'>' if n + 1 == line else ' '} {src[n]}" for n in range(lo, hi))


def explain_errors(errors: list[LoggedError]) -> str:
    """Ask the expert model for the root cause and the exact fix commands."""
    if not errors:
        return "I found no errors in my log."
    from app.expert import get_expert

    report = "\n\n".join(f"ERROR ({e.count}x): {e.message}\nAT: {e.location}\n{e.snippet}" for e in errors)
    prompt = ("These errors come from the AI companion's own Python code (Windows, Python 3.13). For each: the "
              "likely root cause in one sentence, and the exact fix — a code change or the terminal commands.\n\n" + report)
    return get_expert().ask(prompt, purpose="self-diagnosis", max_tokens=1200)


def summary(issues: list[Issue], errors: list[LoggedError]) -> str:
    if not issues and not errors:
        return "Everything looks healthy: models, microphone, packages and logs are fine."
    parts = []
    if issues:
        parts.append(f"I found {len(issues)} problem(s): " + " ".join(i.describe() for i in issues))
    if errors:
        parts.append(f"My log has {len(errors)} recent error(s), the latest: {errors[-1].message} at {errors[-1].location}.")
    return " ".join(parts)

"""Run a script, and if it fails ask the model to explain the error and suggest a fix."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from app.code.generator import extract_code
from app.llm import get_llm
from app.logger import audit

RUNNERS = {".py": [sys.executable], ".js": ["node"], ".ps1": ["powershell", "-NoProfile", "-File"]}


def run_script(path: str | Path, timeout: int = 60) -> tuple[int, str]:
    p = Path(path)
    runner = RUNNERS.get(p.suffix.lower())
    if runner is None:
        return 1, f"I don't know how to run {p.suffix} files."
    try:
        proc = subprocess.run(runner + [str(p)], capture_output=True, text=True, timeout=timeout,
                              cwd=p.parent, encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        return 1, f"Stopped after {timeout} seconds (it may be waiting for input or looping)."
    except FileNotFoundError as e:
        return 1, f"Couldn't start the runner: {e}"
    audit("run_script", path=str(p), code=proc.returncode)
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def diagnose(code: str, error: str) -> dict:
    reply = get_llm().ask(
        f"This code fails.\n\nCode:\n```\n{code[:6000]}\n```\n\nError:\n{error[-3000:]}\n\n"
        "First explain the cause in 1-2 plain sentences. Then give the full corrected code in one fenced block.",
        temperature=0.1,
        max_tokens=1200,
    )
    fixed, explanation = extract_code(reply)
    return {"explanation": explanation or reply, "fixed_code": fixed if "```" in reply else None}


def debug_file(path: str | Path, apply_fix: bool = False) -> str:
    p = Path(path)
    if not p.is_file():
        return f"I couldn't find {path}."
    code, output = run_script(p)
    if code == 0:
        return f"{p.name} ran successfully. Output: {output[:500] or 'nothing'}"
    result = diagnose(p.read_text(encoding="utf-8", errors="replace"), output)
    msg = f"{p.name} failed. {result['explanation']}"
    if result["fixed_code"]:
        fixed_path = p.with_name(p.stem + "_fixed" + p.suffix)
        if apply_fix:
            fixed_path.write_text(result["fixed_code"], encoding="utf-8")
            msg += f" I saved a corrected version as {fixed_path.name}."
    return msg

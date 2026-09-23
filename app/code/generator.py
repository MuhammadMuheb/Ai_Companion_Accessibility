"""Generate code with the local model and save it under data/code_output."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from app.config import get_config
from app.llm import get_llm
from app.logger import audit

EXTENSIONS = {
    "python": "py", "javascript": "js", "typescript": "ts", "html": "html", "css": "css", "java": "java",
    "c": "c", "c++": "cpp", "cpp": "cpp", "c#": "cs", "csharp": "cs", "go": "go", "rust": "rs", "sql": "sql",
    "bash": "sh", "powershell": "ps1", "php": "php", "kotlin": "kt", "dart": "dart",
}

SYSTEM = (
    "You are an expert programmer. Write complete, working, well-commented {language} code for the request. "
    "Reply with ONE fenced code block only, then one short sentence explaining how to run it."
)

_BLOCK = re.compile(r"```[\w+#-]*\n(.*?)```", re.S)


def detect_language(request: str) -> str:
    text = request.lower()
    for lang in sorted(EXTENSIONS, key=len, reverse=True):
        if re.search(rf"(?<![\w+#]){re.escape(lang)}(?![\w+#])", text):
            return lang
    return "python"


def extract_code(reply: str) -> tuple[str, str]:
    """Split a model reply into (code, explanation)."""
    match = _BLOCK.search(reply)
    if not match:
        return reply.strip(), ""
    return match.group(1).rstrip() + "\n", _BLOCK.sub("", reply).strip()


def slug(text: str, words: int = 5) -> str:
    parts = re.findall(r"[a-z0-9]+", text.lower())
    skip = {"write", "create", "make", "generate", "a", "an", "the", "code", "program", "function", "in", "for", "that", "me"}
    return "_".join([p for p in parts if p not in skip][:words]) or "snippet"


def generate(request: str, language: str | None = None, save: bool = True) -> dict:
    language = language or detect_language(request)
    reply = get_llm().ask(request, system=SYSTEM.format(language=language), temperature=0.2,
                          max_tokens=1200)
    code, explanation = extract_code(reply)
    result = {"language": language, "code": code, "explanation": explanation, "path": None}
    if save:
        cfg = get_config()
        folder = cfg.dir(cfg.storage.code_output_dir)
        path = folder / f"{slug(request)}_{datetime.now():%H%M%S}.{EXTENSIONS.get(language, 'txt')}"
        path.write_text(code, encoding="utf-8")
        result["path"] = str(path)
        audit("generate_code", language=language, path=str(path))
    return result


def explain(code_or_path: str) -> str:
    p = Path(code_or_path)
    code = p.read_text(encoding="utf-8", errors="replace") if p.is_file() else code_or_path
    return get_llm().ask(
        f"Explain what this code does in simple spoken language (3-5 sentences):\n\n{code[:6000]}", temperature=0.2,
        max_tokens=250,
    )

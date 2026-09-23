"""Diagnose the installation: imports every module, checks Ollama/models, the
database, speech output and screen reading.  Run: python test_setup.py"""

from __future__ import annotations

import importlib
import pkgutil
import sys
import tempfile

import main

main._utf8_console()
failures = 0


def step(label: str, fn) -> None:
    global failures
    try:
        detail = fn()
        print(f"✓ {label}{f' — {detail}' if detail else ''}")
    except Exception as e:
        failures += 1
        print(f"✗ {label} — {type(e).__name__}: {e}")


def import_all() -> str:
    import app

    names = [m.name for m in pkgutil.walk_packages(app.__path__, "app.")]
    for name in names:
        importlib.import_module(name)
    return f"{len(names)} modules"


def database() -> str:
    from app.memory_db import MemoryDB

    with tempfile.TemporaryDirectory() as tmp:
        db = MemoryDB(f"{tmp}/t.db")
        db.add_goal("test")
        assert db.goals()[0].title == "test"
        db.close()
    return "SQLite OK"


def speech() -> str:
    from app.voice.tts import get_speaker

    s = get_speaker()
    if not s.available:
        raise RuntimeError("pyttsx3 could not start")
    return "text-to-speech engine ready"


def microphone() -> str:
    import sounddevice as sd

    return sd.query_devices(kind="input")["name"]


def ocr() -> str:
    from PIL import Image, ImageDraw, ImageFont

    from app.accessibility.screen import ocr as run_ocr

    img = Image.new("RGB", (600, 100), "white")
    ImageDraw.Draw(img).text((20, 25), "Companion OCR check", fill="black", font=ImageFont.truetype("arial.ttf", 36))
    text = run_ocr(img)
    assert "OCR" in text, text
    return f"read '{text}'"


print("AI Companion — setup check\n")
step("All modules import", import_all)
step("Database", database)
step("Speech output", speech)
step("Microphone", microphone)
step("Screen reading (OCR)", ocr)
print()
ok = main.check()
print("\nAll good! Run: python main.py   or   python main_accessibility.py" if ok and not failures
      else f"\n{failures} problem(s) found — see above.")
sys.exit(0 if ok and not failures else 1)

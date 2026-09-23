"""Nova — entry point.

    python main.py                    # Nova, invisible in the background (same as nova.pyw)
    python main.py --mode text        # text chat in this terminal
    python main.py --mode basic       # plain chat with slash commands only
    python main.py --mode hybrid      # press Enter to speak or type; replies are spoken
    python main.py --mode voice       # hands-free voice
    python main.py --mode wake        # say the wake name ("hey companion") to talk
    python main.py --mode web         # optional legacy browser interface (not started otherwise)
    python main.py --check            # verify Ollama, models and optional features
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import sys

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")


def _utf8_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass


def check(verbose: bool = True) -> bool:
    from app.config import get_config
    from app.llm import get_llm

    cfg, llm = get_config(), get_llm()
    if not llm.is_available():
        print(f"✗ Ollama is not reachable at {llm.url}. Start it with: ollama serve")
        return False
    print(f"✓ Ollama running at {llm.url}")
    ok = True
    for model, purpose in ((cfg.llm.chat_model, "chat"), (cfg.llm.embedding_model, "memory search")):
        if llm.has_model(model):
            print(f"✓ Model {model} ({purpose})")
        else:
            print(f"✗ Model {model} missing ({purpose}). Run: ollama pull {model}")
            ok = ok and purpose != "chat"  # embeddings are optional (keyword fallback)
    if not verbose:
        return ok
    optional = {
        "faster_whisper": "speech recognition", "pyttsx3": "text-to-speech", "sounddevice": "microphone",
        "pyautogui": "keyboard control", "winrt.windows.media.ocr": "screen reading (Windows OCR)",
        "psutil": "system status", "plyer": "desktop notifications",
    }
    for module, feature in optional.items():
        try:
            found = importlib.util.find_spec(module) is not None
        except ModuleNotFoundError:
            found = False
        print(f"{'✓' if found else '–'} {feature}{'' if found else f'  (pip install -r requirements-FULL.txt)'}")
    from app.accessibility.vision import vision_model
    from app.mentor import prayer

    vm = vision_model()
    print(f"{'✓' if vm else '–'} screen description with images{f' ({vm})' if vm else '  (optional: ollama pull moondream)'}")
    print(f"{'✓' if prayer.is_configured() else '–'} prayer times"
          f"{'' if prayer.is_configured() else '  (set user.latitude / longitude in config.yaml)'}")
    return ok


def main() -> int:
    _utf8_console()
    parser = argparse.ArgumentParser(description="Local AI companion")
    parser.add_argument("--mode", choices=["app", "text", "basic", "hybrid", "voice", "wake", "web"], default="app")
    parser.add_argument("--show", action="store_true", help="app mode: also open Nova's window")
    parser.add_argument("--port", type=int, default=8765, help="web mode: port to listen on")
    parser.add_argument("--no-browser", action="store_true", help="web mode: don't open the browser")
    parser.add_argument("--check", action="store_true", help="check Ollama, models and features, then exit")
    parser.add_argument("--speak", action="store_true", help="text mode: also speak replies")
    args = parser.parse_args()

    if args.check:
        return 0 if check() else 1
    if not check(verbose=False):
        return 1

    if args.mode == "app":
        from app import daemon
        return daemon.run(show_window=args.show)
    if args.mode == "web":
        from app.web import server
        return server.run(port=args.port, open_browser=not args.no_browser)
    elif args.mode == "basic":
        from app import cli
        cli.run()
    elif args.mode == "text":
        from app import cli_enhanced
        cli_enhanced.run(speak=args.speak)
    else:
        from app import voice_cli
        voice_cli.run(mode={"voice": "hands_free", "wake": "wake"}.get(args.mode, "push"))
    return 0


if __name__ == "__main__":
    sys.exit(main())

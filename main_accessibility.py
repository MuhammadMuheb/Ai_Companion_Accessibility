"""AI Companion — voice-first entry point for blind and low-vision users.

    python main_accessibility.py              # say the wake name ("hey companion") — no keys needed
    python main_accessibility.py --push-to-talk # press Enter to talk (or type)
    python main_accessibility.py --hands-free # every sentence is a command
    python main_accessibility.py --mic-test   # check the microphone level (--mic-test 2 for device 2)
"""

from __future__ import annotations

import argparse
import sys

from main import _utf8_console, check


def main() -> int:
    _utf8_console()
    parser = argparse.ArgumentParser(description="Voice-first AI companion")
    parser.add_argument("--hands-free", action="store_true", help="treat everything you say as a command")
    parser.add_argument("--push-to-talk", action="store_true", help="press Enter before speaking")
    parser.add_argument("--quiet", action="store_true", help="don't print text, only speak")
    parser.add_argument("--mic-test", nargs="?", const="default", metavar="DEVICE",
                        help="show a live microphone level meter and exit (optionally for a device number/name)")
    args = parser.parse_args()

    if args.mic_test:
        from app.voice.stt import mic_test
        mic_test(device=None if args.mic_test == "default" else args.mic_test)
        return 0

    if not check(verbose=False):
        try:
            from app.voice.tts import speak
            speak("I can't reach the local AI model. Please start Ollama and try again.")
        except Exception:
            pass
        return 1

    from app.config import get_config
    from app import voice_cli

    cfg = get_config()
    if args.hands_free or cfg.accessibility.voice_only_mode:
        mode = "hands_free"
    elif args.push_to_talk or not (cfg.voice.wake_enabled and cfg.feature_on("wake")):
        mode = "push"
    else:
        mode = "wake"
    voice_cli.run(mode=mode, echo=not args.quiet)
    return 0


if __name__ == "__main__":
    sys.exit(main())

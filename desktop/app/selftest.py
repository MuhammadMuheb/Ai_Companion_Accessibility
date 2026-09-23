"""`Lyra.exe --selftest`: load every part that needs native code or bundled files, write the result to
data/logs/selftest.json and exit — without starting the tray, the microphone loop or speaking.
Used to check a build (and by support: "run Lyra.exe --selftest and send me the file")."""

from __future__ import annotations

import json
import time
import traceback
from pathlib import Path


def _check(results: dict, name: str, fn) -> None:
    start = time.perf_counter()
    try:
        detail = fn()
        results[name] = {"ok": True, "detail": detail, "seconds": round(time.perf_counter() - start, 2)}
    except Exception as e:  # report and keep going
        results[name] = {"ok": False, "error": f"{type(e).__name__}: {e}",
                         "trace": traceback.format_exc(limit=3)}


def run() -> int:
    import numpy as np

    from app.config import DATA_ROOT, FROZEN, ROOT, get_config

    results: dict = {"frozen": FROZEN, "program_dir": str(ROOT), "data_dir": str(DATA_ROOT)}

    def whisper(size):
        def load():
            from faster_whisper import WhisperModel

            from app.voice.stt import whisper_source

            src = whisper_source(size)
            model = WhisperModel(src, device="cpu", compute_type="int8")
            list(model.transcribe(np.zeros(16000, dtype="float32"), beam_size=1)[0])
            return {"bundled": src != size}
        return load

    def voiceprint():
        from app.voice.voiceprint import MODEL_FILE, VoicePrint

        VoicePrint().session  # noqa: B018 — loads the ONNX model
        return {"bundled": (ROOT / "models" / MODEL_FILE).exists()}

    def tts():
        import pyttsx3

        engine = pyttsx3.init()
        voices = engine.getProperty("voices")
        engine.stop()
        return {"voices": len(voices)}

    def neural_voice():
        # load the bundled default voice and synthesise one word (no sound is played)
        from piper import PiperVoice

        from app.voice.voices import DEFAULT_VOICE, get_voice, model_files

        files = model_files(get_voice(DEFAULT_VOICE))
        if files is None:
            raise FileNotFoundError("default voice not bundled")
        voice = PiperVoice.load(str(files[0]), config_path=str(files[1]))
        samples = sum(len(c.audio_float_array) for c in voice.synthesize("Hello."))
        return {"voice": files[0].name, "samples": samples}

    def ocr():
        import winrt.windows.media.ocr as wocr

        return {"engine": wocr.OcrEngine.try_create_from_user_profile_languages() is not None}

    def mics():
        from app.voice.mic import list_microphones

        return {"count": len(list_microphones())}

    def webview():
        import webview as wv

        return {"version": getattr(wv, "__version__", "?")}

    def tray():
        import pystray  # noqa: F401

        from app.tray import _icon_image

        return {"icon": _icon_image().size}

    def api():
        # render the window's page directly: the app's lifespan would start the background services
        from app.web import server

        page = server.index()
        return {"status": page.status_code, "bytes": len(page.body), "routes": len(server.app.routes)}

    def config():
        cfg = get_config()
        return {"assistant": cfg.assistant.name, "wake": cfg.wake_phrases}

    for name, fn in [("config", config), ("whisper_tiny", whisper("tiny")), ("whisper_base", whisper("base")),
                     ("voiceprint", voiceprint), ("tts", tts), ("neural_voice", neural_voice), ("ocr", ocr), ("microphones", mics),
                     ("webview", webview), ("tray", tray), ("window_api", api)]:
        _check(results, name, fn)

    results["all_ok"] = all(v.get("ok") for v in results.values() if isinstance(v, dict))
    out = get_config().dir("data/logs") / "selftest.json"
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(json.dumps(results, indent=2))
    return 0 if results["all_ok"] else 1

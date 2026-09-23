"""AI Companion (Lyra) package."""

import os
import warnings

# CRITICAL: Force WASAPI (Windows audio) to use shared mode, never exclusive. This ensures:
# - Microphone is shareable with calls, meetings, and other apps
# - Speaker is shareable so system sounds and other audio apps work
# - No app can monopolize audio or require a hard restart to recover
os.environ.setdefault("SOUNDDEVICE_EXCLUSIVE", "False")
os.environ.setdefault("WASAPI_EXCLUSIVE", "False")

# soundcard warns about a harmless gap at the start of every recording; keep logs readable
warnings.filterwarnings("ignore", message="data discontinuity in recording")

# Windows ships an older onnxruntime.dll in System32 that the WinRT OCR APIs load. If WinRT is
# imported first, the Python onnxruntime package later binds to that DLL and the process crashes
# (access violation) the first time the voice print runs. Loading our onnxruntime first avoids it.
try:
    import onnxruntime  # noqa: F401
except ImportError:  # voice print is optional
    pass

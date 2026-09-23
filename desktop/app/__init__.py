"""AI Companion (MD) package."""

import warnings

# soundcard warns about a harmless gap at the start of every recording; keep logs readable
warnings.filterwarnings("ignore", message="data discontinuity in recording")

# Windows ships an older onnxruntime.dll in System32 that the WinRT OCR APIs load. If WinRT is
# imported first, the Python onnxruntime package later binds to that DLL and the process crashes
# (access violation) the first time the voice print runs. Loading our onnxruntime first avoids it.
try:
    import onnxruntime  # noqa: F401
except ImportError:  # voice print is optional
    pass

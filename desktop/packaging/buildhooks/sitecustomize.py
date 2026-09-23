# Build-time only: load onnxruntime before WinRT so PyInstaller's import scan doesn't crash.
try:
    import onnxruntime  # noqa: F401
except ImportError:
    pass

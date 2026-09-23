# PyInstaller spec for Lyra.exe — build with packaging\build.ps1 (run from the desktop folder).
# One folder, windowed (no console). Speech models and the default neural voice are bundled so Lyra
# works offline after install.

from PyInstaller.utils.hooks import collect_all, collect_submodules, copy_metadata

ROOT = SPECPATH + "\\.."

datas = [
    (ROOT + "\\app\\web\\static", "app\\web\\static"),
    (ROOT + "\\config.yaml", "."),
    (SPECPATH + "\\models", "models"),
    (SPECPATH + "\\lyra.ico", "."),
]
binaries = []
hiddenimports = collect_submodules("app") + [
    "pyttsx3.drivers", "pyttsx3.drivers.sapi5", "comtypes.client",
    "pystray._win32", "pynput.keyboard._win32", "pynput.mouse._win32",
    "plyer.platforms.win.notification", "plyer.platforms.win.libs.balloontip",
    "webview.platforms.edgechromium", "webview.platforms.winforms",
    "uvicorn.logging", "uvicorn.loops.auto", "uvicorn.protocols.http.auto", "uvicorn.lifespan.on",
]

# packages with native DLLs, data files or plugin discovery
for pkg in ("piper", "faster_whisper", "ctranslate2", "av", "tokenizers", "webview", "clr_loader",
            "pythonnet", "uiautomation", "soundcard", "sounddevice", "soundfile", "kaldi_native_fbank",
            "adhanpy", "tzdata", "winrt", "pyttsx3", "comtypes", "plyer", "pystray", "pynput", "pyautogui",
            "pygetwindow", "pyperclip", "psutil", "anthropic", "httpx", "starlette", "fastapi", "pydantic"):
    try:
        d, b, h = collect_all(pkg)
    except Exception:
        continue
    datas += d
    binaries += b
    hiddenimports += h

for dist in ("apscheduler", "faster-whisper", "fastapi", "pydantic", "anthropic", "httpx", "tokenizers",
             "huggingface-hub", "pywebview", "piper-tts", "tqdm", "requests"):
    try:
        datas += copy_metadata(dist)
    except Exception:
        pass

a = Analysis(
    [ROOT + "\\lyra.pyw"],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["onnxruntime.quantization", "onnxruntime.tools", "onnxruntime.transformers", "tkinter.test", "pytest", "matplotlib", "IPython", "notebook", "scipy", "pandas", "torch"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Lyra",
    icon=SPECPATH + "\\lyra.ico",
    console=False,
    version=SPECPATH + "\\version.txt",
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="Lyra")

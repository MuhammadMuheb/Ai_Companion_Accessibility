# MD — voice-first AI assistant for Windows

```
index.html, assets/     website (static, deployed on Vercel)
desktop/                the MD desktop app (Python) and its installer build
  md.pyw                start MD from source (tray, wake word "Hey MD")
  app/                  assistant code
  tests/                python -m pytest -q   (run inside desktop/)
  packaging/            PyInstaller + Inno Setup -> MD-Setup-<version>.exe
```

## Website

```bash
python -m http.server 8000      # then open http://127.0.0.1:8000
```

Vercel: Root Directory empty, Framework preset **Other**, no build command. `desktop/` is excluded
from the deployment by `.vercelignore`.

## Desktop app from source

```bash
cd desktop
pip install -r requirements-FULL.txt
python main.py --check
pythonw md.pyw
```

## Building the installer

Needs Python with `requirements-FULL.txt` and `pyinstaller`, plus Inno Setup 6
(`winget install JRSoftware.InnoSetup`).

```powershell
powershell -ExecutionPolicy Bypass -File desktop\packaging\build.ps1
```

The result is `desktop\packaging\Output\MD-Setup-<version>.exe`: a per-user installer (no admin
rights) with Whisper and voice-print models bundled. The Ollama models are downloaded by the
installer (optional task) or later from the Start menu entry "MD - Download AI models".
Installed MD keeps its data in `%LOCALAPPDATA%\MD`.

The installer is too large for Git — publish it as a GitHub Release asset and point the website's
download button at it.

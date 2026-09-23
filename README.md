# Nova — voice-first AI companion

Nova is a personal assistant and IT helper you talk to. It comes in two parts:

| | **Nova Desktop** (`nova.pyw`, `app/`) | **Nova Cloud** (`cloud/`) |
|---|---|---|
| Runs on | Your Windows PC, in the tray | Vercel (serverless) |
| AI model | Local, via Ollama (free, private) | Claude, with the site owner's API key |
| Voice | Wake word “Hello Nova”, offline speech recognition, spoken replies | Browser speech recognition + read-aloud |
| Memory | SQLite on your PC (`data/memory.db`), facts update themselves | Saved in the visitor's browser |
| PC control, reminders, prayer times, notifications, WhatsApp, OCR | ✅ | — (a hosted server can't reach your PC) |

The website in `cloud/` is also the download page for the desktop app.

## Nova Desktop

Requirements: Windows 10/11, Python 3.10–3.13, [Ollama](https://ollama.com/download).

```powershell
.\install.ps1          # packages + models + Start menu + start with Windows
```

or manually:

```bash
pip install -r requirements-FULL.txt
ollama pull qwen2.5:1.5b && ollama pull nomic-embed-text && ollama pull gemma3:4b
python main.py --check      # health check
pythonw nova.pyw            # start invisibly in the tray; say "Hello Nova"
```

Settings live in `config.yaml` (and `data/settings.yaml`, written by the settings page and by voice).
See [SETUP_INSTRUCTIONS.md](SETUP_INSTRUCTIONS.md) for every feature.

### Why it's fast on a CPU-only laptop

- The chat prompt is laid out so Ollama can reuse its cache: the fixed persona comes first, the clock
  and recalled memories come last. Re-reading a changed prompt costs 6–9 s on a 2-core i5; a cache hit
  costs 0.2 s.
- Conversation history is trimmed in chunks, not one turn at a time, for the same reason.
- The chat model is kept loaded (`llm.keep_alive: 30m`) and warmed up at start.
- The wake word is spotted by the light `tiny` Whisper model on the first 3 s of each sentence heard
  (`voice.wake_model_size`); the more accurate model runs only when the wake word is there. This used
  to be the biggest CPU drain, competing with the chat model all day.
- Replies stream and are spoken sentence by sentence, so the first words come out right away.

## Nova Cloud (Vercel)

1. Push this repository to GitHub (below).
2. In Vercel: **Add New… → Project → Import** the repository.
3. Set **Root Directory** to `cloud` (Framework preset: *Other*). Vercel then installs only
   `cloud/requirements.txt`, serves `cloud/public/` as static files and `cloud/api/index.py` as the API.
4. Add environment variables (Project → Settings → Environment Variables):

| Variable | Required | Meaning |
|---|---|---|
| `ANTHROPIC_API_KEY` | yes | Key from console.anthropic.com — chat is billed to it |
| `NOVA_ACCESS_CODE` | yes, unless `NOVA_PUBLIC=1` | Visitors type this before chatting, so strangers can't spend your credits |
| `NOVA_PUBLIC` | no | `1` = anyone can chat without a code |
| `NOVA_MODEL` | no | default `claude-opus-5` |
| `NOVA_EFFORT` | no | `low` (default, fastest first word) · `medium` · `high` |
| `NOVA_DOWNLOAD_URL` | no | Link for “Download for Windows”, e.g. a GitHub release `.zip` |
| `NOVA_GITHUB_REPO` | no | `owner/repo`; used for the download link when `NOVA_DOWNLOAD_URL` is empty |
| `NOVA_RATE_LIMIT` | no | Messages per visitor IP per 10 minutes (default 30) |

5. Deploy. `https://<project>.vercel.app/api/health` should return `{"ok": true}`.

Try it locally first:

```bash
pip install -r cloud/requirements.txt uvicorn
python cloud/dev.py --demo   # no API key needed: canned streamed replies on http://127.0.0.1:3000
python cloud/dev.py          # real Claude replies (set ANTHROPIC_API_KEY and NOVA_PUBLIC=1 first)
```

## Git & GitHub

`data/` (memories, conversations, recordings, voice prints, browser profile), `logs/` and `.env` are in
`.gitignore` — they never leave your PC.

```bash
git init
git add .
git commit -m "Nova: desktop companion + Vercel web app"
git branch -M main
git remote add origin https://github.com/<you>/<repo>.git
git push -u origin main
```

For the download button, create a GitHub release (or use the automatic
`https://github.com/<you>/<repo>/archive/refs/heads/main.zip`) and set `NOVA_DOWNLOAD_URL` /
`NOVA_GITHUB_REPO` in Vercel.

## Tests

```bash
python -m pytest -q
```

Tests use temporary storage (see `tests/conftest.py`) and a fake Claude client — no real data is
touched and no API calls are made.

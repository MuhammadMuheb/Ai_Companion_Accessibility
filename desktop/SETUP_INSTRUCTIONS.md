# AI Companion — Setup & Usage

A fully local, voice-first AI companion for Windows. Everything (chat, memory, speech
recognition, text-to-speech and screen reading) runs on your own computer via Ollama.

## 1. Install

```powershell
python setup.py                      # creates folders, installs requirements-FULL.txt, pulls models
# or manually:
pip install -r requirements-FULL.txt # use requirements.txt for text-only chat
ollama pull qwen2.5:1.5b       # chat
ollama pull nomic-embed-text   # memory search
ollama pull gemma3:4b          # English -> Roman Urdu translation
```

Optional:
- `ollama pull moondream` — lets the companion *look* at the screen (image description), not just read its text.
- Tesseract OCR — only needed if Windows OCR is unavailable; Windows' built-in OCR is used by default.

## 2. Configure

Edit `config.yaml`:
- `user.name` — what the companion calls you
- `user.latitude` / `user.longitude` — enables prayer times and reminders (e.g. Karachi: `24.86` / `67.01`)
- `scheduler.prayer_method`, `scheduler.madhab` — calculation method (default Karachi / Hanafi)
- `voice.language` — `en`, `ur`, `hi`, or `""` for auto-detect
- `routines` — named multi-step routines, e.g. saying "work mode"

Environment variables in `.env` (copy `.env.example`) override `config.yaml`.

## 3. Check

```powershell
python test_setup.py     # imports, database, speech, microphone, OCR, Ollama, models
python -m pytest         # unit tests (no Ollama or microphone needed)
```

## 4. Run

### As a Windows app (normal way)

Double-click **`lyra.pyw`** — no terminal, no browser, no server, and **no window**: Lyra starts invisibly
and waits for its name ("Hello Lyra", "Hey Lyra" — whatever you named it). Answers come back by voice.
Its window is only built when you ask for it ("open your window", Ctrl+Alt+N, or the tray menu).
`lyra.pyw --show` (the Start-menu shortcut) opens the window straight away. `python main.py` = invisible start too.

- Lyra lives in the **system tray** (near the clock). Its menu: *Open*, *Talk now*, *Settings*,
  *Pause listening*, *Start with Windows*, *Show in Start menu*, *Quit*.
- **Ctrl+Alt+N** opens Lyra's window from anywhere; launching `lyra.pyw` again while Lyra runs
  just brings the window to the front (only one Lyra ever runs).
- Closing the window keeps Lyra running in the tray; *Quit* in the tray menu stops it.
- Tick **Start with Windows** (tray or Settings → Background) and Lyra starts quietly in the tray
  every time you sign in. **Show in Start menu** lets you find it by searching “Lyra”.
- The window is a native WebView2 window (the Windows component WhatsApp Desktop uses), so screen
  readers such as Narrator and NVDA can read it. Nothing listens on the network: the window calls
  Lyra's functions directly inside the same process.

The window has these sections:

- **Home** — voice only, no chat box. The orb shows what Lyra is doing (waiting for her name,
  listening, thinking, speaking) and live captions show what she heard and said. Just say “Hey Lyra”;
  clicking the orb or `Ctrl+Alt+Space` also starts a conversation. `Esc` stops her speaking.
- **Voice** — pick one of 12 speaking voices (8 female: Amy, Kristin, Lena, Harper, Jenny, Cori,
  Alba, Zira; 4 male: Ryan, Alan, Joe, David), preview them, set speed and volume. Neural voices
  (Piper, offline) download once (~63 MB each; Amy ships with the installer); Zira and David are
  built into Windows. Also here: wake word, microphone, recognition language and voice print.
  By voice: “change your voice to Jenny”, “what voices do you have”.
- **Routines** — your own voice commands: a phrase plus steps (one per line), e.g.
  phrase `work mode` → `open vs code`, `open gmail`, `say: Let's get to work!`, `start focus for 50 minutes`.
  The mic buttons let you say phrases and steps. “Check steps” shows how each step will be understood.
- **Accounts** — which browser profile is which account; how you say your WhatsApp contacts.
- **Memory** — goals, reminders and what Lyra remembers; add to them by voice.
- **Settings** — features on/off, your name and location (for prayer times), AI model, prayer,
  notifications, calls, shortcuts, focus, expert mode, startup. Light/dark theme: the button at
  the bottom of the sidebar.

Settings save automatically to `data/settings.yaml` (they override `config.yaml`).

**Upgrading from the earlier release:** the installer removes it and Lyra moves your memories,
settings and voice prints from `%LOCALAPPDATA%\MD` to `%LOCALAPPDATA%\Lyra` on first start.

The old browser interface still exists for developers (`python main.py --mode web`), but nothing
starts it any more.

### In the terminal

```powershell
python main.py --mode text           # text chat that understands commands
python main.py --speak               # same, and replies are spoken
python main.py --mode hybrid         # press Enter to speak, or type
python main.py --mode voice          # hands-free, always listening
python main.py --mode basic          # plain chat with /slash commands only
python main_accessibility.py         # voice-first (push-to-talk); add --hands-free or --quiet
```

## Talking to Lyra (a conversation, not one command)

Say the name once — "Hello Lyra" — and Lyra answers "Ji?". After every answer it beeps and listens
again, so you can keep talking without repeating its name:

1. You: "Hello Lyra" → Lyra: "Ji?"  2. "what time is it" → answer, beep  3. "open notepad" → done, beep
4. "bas, shukriya" / "bye" / "Allah Hafiz" / "that's all" → "Theek hai, Allah Hafiz." and it goes back to waiting.

- Silence for 6 seconds also ends the conversation quietly.
- Noise or gibberish (coughs, "the the the", Whisper's invented "Thank you") is skipped; twice in a
  row and Lyra says "Samajh nahi aaya" and stops.
- Turn it off with `voice.conversation: false` (then each command needs the name again);
  `voice.follow_up_seconds` changes the 6-second wait.

## Everything by voice (no need to open Settings)

| What | Say |
|---|---|
| Rename Lyra | "your name is Lyra", "call yourself Zoya" (asks first; the wake phrase changes to the new name) |
| Your name / city | "call me Muheb", "I live in Lahore", "main Karachi mein rehta hoon" (fills prayer-time location) |
| Features | "turn off notifications", "enable translation", "whatsapp on karo" |
| Window | "open your window", "open your settings", "show my memory" |
| Quiet | "stop listening for 10 minutes", "go to sleep" (Ctrl+Alt+Space or the tray wakes it again) |

Long answers and code are not read out character by character: Lyra says a short version and puts the
full text in its window. Slow jobs (expert answers, translation, research) get an instant "Ek minute…".

### Long-term memory that updates itself

Lyra keeps facts, preferences, goals, projects and people in `data/memory.db` (local SQLite on this
computer only — not encrypted, nothing uploaded). When you say something that changes an old memory,
it **replaces** it instead of keeping both:

- "My goal is now to travel to London instead of Dubai" / "change my goal from Dubai to London" /
  "mera goal ab London jana hai" → the Dubai goal and memory become London.
- "Actually, I moved to Karachi" → the old city memory is updated.
- Repeating something it already knows is not stored twice.
- "I finished learning Urdu" / "Urdu seekhna ho gaya" → goal marked done. "remove my goal about gym".
- "forget that my sister is Ayesha" / "… bhool jao" (asks to confirm).
- "what changed in my memory?" → the history of updates (old → new), kept in `memory_history`.

### During a call

Lyra stays silent during calls (nothing is spoken into the call). The wake word still works for call
commands only — "Hello Lyra, record this call" / "stop recording" — shown as a small popup. Recording
still asks you every time and reminds you to tell the other person; your own voice saying the command
is heard on the call.

## What you can say

| Area | Examples |
|---|---|
| Apps & web | "open notepad", "notepad kholo", "open gmail", "search youtube for naat", "google weather karachi" |
| Web answers | "search the web for capital of Pakistan", "summarize https://…" |
| Screen | "read the screen", "read the whole screen", "what's on my screen", "take a screenshot", "read clipboard" |
| Keyboard & windows | "type hello world", "press ctrl+s", "switch to chrome", "what windows are open", "volume up", "awaaz kam karo" |
| Reminders & mentor | "remind me in 10 minutes to drink water", "namaz ka time", "start focus for 30 minutes on math", "stop focus", "advice", "morning summary" |
| Goals & memory | "add goal learn urdu", "what are my goals", "remember that my sister is Ayesha", "what do you remember about me" |
| Files & code | "what's on my desktop", "find file budget", "read file notes.txt", "take a note buy milk", "write a python function to …", "debug script.py", "git status" |
| System | "battery status", "what time is it", "kitne baje hain", "run command ipconfig" |
| Multi-step | "open notepad then type hello and then press enter", "work mode" |
| Conversation | anything else — "what did I just do?", "repeat that", or just chat |

In voice mode, anything risky (closing apps, running non-read-only commands, saving
fixed code) is confirmed by asking you "yes or no" first. Destructive shell commands
(format, recursive delete, shutdown, …) are always refused.

## Wake word, accounts, notifications, translation, WhatsApp

**Wake word.** Say “Hey Companion …” (or your own name for it — Settings → Voice → Wake names)
and speak the command, with no button: “Hey Companion, what time is it?”. Saying just the name
gets “Ji?” and it listens. Works with the laptop mic or earphones (Settings → Microphone,
then *Test microphone*). `Ctrl+Alt+Space` starts listening without the name. Runs in
`python main_accessibility.py` (default mode), `python main.py --mode wake` and the web mode.
Pick a name that isn't an everyday word — common words (“dost”, “zara”) wake it by accident.

**Multiple accounts.** “Open YouTube” / “open Gmail” / “open Instagram” / “open Chrome” asks
which account first; each Chrome/Edge profile is one account. Say it directly to skip the
question: “open YouTube on NexTube”, “NexTube wala YouTube”. On the **Accounts & Contacts**
page, tick which profiles belong to each site and name them; for Google sites a second Google
account inside the same profile is chosen with “Google #”.

**Notifications.** Announced as soon as they arrive, from two sources:
1. every notification Windows receives (WhatsApp Desktop, Outlook, Teams, browsers…), even
   while Focus / Do-not-disturb hides the pop-ups;
2. unread counts in *open browser tabs* — Gmail, WhatsApp Web, TikTok, Instagram, Facebook… —
   read from every tab, including background tabs. This works even when the browser's or the
   site's notifications are switched off (on this laptop Chrome's are off).

*Limitation:* if an app's notifications are off in Windows **and** it isn't open in a browser
tab, Windows never receives anything, so there is nothing to catch — e.g. Gmail with no tab open.

**Translate to Roman Urdu.** Select English text anywhere and press `Ctrl+Alt+T` — or just
point the mouse at it and press `Ctrl+Alt+T` (the text under the pointer is read with OCR).
Or say “yeh kya likha hai” / “translate this”. You get *Tarjuma* (translation) and *Matlab*
(what it means / what to do), spoken and shown. Uses `gemma3:4b` (about 30–60 s on this CPU;
smaller models gave wrong translations in testing and are not recommended for this).

**WhatsApp.** “Mama ji ko WhatsApp karo ke main raste mein hoon”, “send a WhatsApp message to
Ali saying I will call later”, “open WhatsApp, go to Mama Ji chat and send I am available”.
English dictation is converted to Roman Urdu; the companion **always reads the message back and
asks before sending**, then opens the chat in WhatsApp Desktop, checks the chat's name on screen,
and only then presses Enter. Add contacts on the Accounts & Contacts page (a phone number opens
the exact chat; otherwise WhatsApp is searched by the saved name, and if several chats match you
are asked which one).

## Lyra: background, calls, voice print, pointer, system control, expert mode

**Name.** The assistant is called *Lyra*; change it under Settings → About you and the wake word
follows (“Hey Zoya”). Custom wake names can still be listed under Settings → Voice.

**Microphone switching.** Lyra records through Windows' own audio system and follows the Windows
default microphone: connect AirPods and it moves to them, take them out and it returns to the
laptop mic — mid-sentence, without restarting. If a mic sends pure silence for 3 s it tries another.

**Voice print (max 2 people).** Settings → Voice print → record three sentences → Save. From then
on only enrolled voices can wake Lyra (strangers get “main sirf pehchaani hui awaaz par kaam karti
hoon”). “Hey Lyra” alone is too short to recognise a voice, so Lyra checks the name *plus* the
command together. It's a convenience filter, not strong security, and never unlocks anything.

**Calls.** When WhatsApp, Teams, Zoom, Skype, Discord, Phone Link or a Meet/Zoom/Teams browser tab
starts using the microphone, Lyra goes silent (no speech, no wake word) and a small pop-up asks
*Record & take notes / No* (it closes as “No” after 30 s). Recording is never automatic, and the
pop-up reminds you that everyone on the call must agree — recording people without their consent
is illegal in many places. Both sides are recorded (your mic + the computer's sound). When the call
ends Lyra saves `data/calls/<date>/me.wav, them.wav, transcript.txt, notes.md`, tells you the summary
and action items (offers to add them as goals), asks how the call went and suggests water / a break.

**Running in the background.** Double-click `lyra.pyw` (tray icon with Open /
Pause listening / Start with Windows / Quit), or tick *Start Lyra … when I sign in* in Settings. While
Windows is **locked** Lyra keeps listening and can answer questions, set reminders, read
notifications and prayer times, but refuses anything that controls the computer (opening apps,
typing, files, messages) until you unlock. After sleep it restarts listening by itself. It does not
run on the sign-in screen before you log in — that would require a system service with access to
your account and would weaken Windows' own lock.

**Screen & mouse.** “Yeh kya hai?” / “what is this?” — says what's under the pointer. “Start mouse
reading” — rest the pointer anywhere and Lyra names it. “Show me the Save button” — glides the mouse
there and outlines it. “Click Send” — clicks it (asks first for risky buttons: delete, send, pay…).

**System control.** “Install VLC” / “install Spotify from the Microsoft Store” (winget; asks which
package and confirms), “show budget.xlsx in Explorer”, “switch to the YouTube tab”, “open Claude”
(opens claude.ai if the desktop app isn't installed), “run command …” (confirmed).

**Self-diagnosis.** “Diagnose yourself” / “kya masla hai” — checks Ollama, models, microphone,
packages, disk and Lyra's own log; offers safe fixes (start Ollama, download a model, pip-install a
package) and explains logged errors with the source line and the exact commands to run. Lyra does
not rewrite its own code by itself.

**Expert mode.** Web-development, UI/UX, database and architecture questions (“blueprint for an
online bakery”, “design a database for a school”, “research best auth for Next.js”) go to expert
mode. Default is the local model (free, private, ~1–2 min per answer on this CPU). For top-quality
answers choose *Claude* under Settings → Expert, set `ANTHROPIC_API_KEY` in Windows yourself, and
set a weekly US$ limit — Lyra counts every request's tokens and falls back to the local model when
the week's budget is used. “Create a Next.js app called shop” scaffolds a project in
`~/Projects` (confirmed) and opens it in VS Code.

**Not included:** a phone app. The browser page works at phone size, but reaching it from a phone
means exposing Lyra (which can control this computer) on your network and serving HTTPS for the
microphone — a separate, security-sensitive piece of work.

## Where things are stored

- `data/memory.db` — conversations, memories, goals, check-ins, reminders (SQLite)
- `data/conversations/` — daily activity log used for "what did I just do?"
- `data/code_output/` — generated code
- `data/screenshots/` — screenshots
- `data/logs/companion.log`, `data/logs/audit.jsonl` — app log and a record of every action taken

## Performance note

On a CPU-only machine phi3:mini produces ~3 words per second, so long answers and screen
descriptions take 20–60 s. Replies are capped by `llm.max_tokens`. For faster responses try
a smaller model, e.g. `ollama pull qwen2.5:1.5b` and set `llm.chat_model: "qwen2.5:1.5b"`.

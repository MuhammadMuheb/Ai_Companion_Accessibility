"use strict";

document.documentElement.classList.add("js");
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/* ---------- nav: scrolled state, mobile menu, active link ---------- */
const nav = $("#nav");
const onScroll = () => nav.classList.toggle("scrolled", scrollY > 12);
addEventListener("scroll", onScroll, { passive: true });
onScroll();

const menuBtn = $("#menu-btn");
const links = $("#nav-links");
function setMenu(open) {
  links.classList.toggle("open", open);
  menuBtn.setAttribute("aria-expanded", String(open));
  menuBtn.setAttribute("aria-label", open ? "Close menu" : "Open menu");
}
menuBtn.addEventListener("click", () => setMenu(!links.classList.contains("open")));
links.addEventListener("click", (e) => { if (e.target.closest("a")) setMenu(false); });
addEventListener("keydown", (e) => { if (e.key === "Escape") setMenu(false); });

const navMap = new Map($$(".nav-links a").map((a) => [a.getAttribute("href").slice(1), a]));
const sectionSpy = new IntersectionObserver((entries) => {
  entries.forEach((en) => {
    if (!en.isIntersecting) return;
    navMap.forEach((a) => a.classList.remove("active"));
    navMap.get(en.target.id)?.classList.add("active");
  });
}, { rootMargin: "-45% 0px -50% 0px" });
navMap.forEach((_, id) => { const s = document.getElementById(id); if (s) sectionSpy.observe(s); });

/* ---------- theme ---------- */
$("#theme-btn").addEventListener("click", () => {
  const root = document.documentElement;
  const isLight = root.dataset.theme ? root.dataset.theme === "light" : matchMedia("(prefers-color-scheme: light)").matches;
  root.dataset.theme = isLight ? "dark" : "light";
  try { localStorage.setItem("nova.theme", root.dataset.theme); } catch { /* storage blocked */ }
});

/* ---------- reveal + counters ---------- */
function countUp(el) {
  const to = parseFloat(el.dataset.to);
  const dec = parseInt(el.dataset.dec || "0", 10);
  if (reduceMotion) return;
  const start = performance.now();
  const dur = 1100;
  const tick = (now) => {
    const t = Math.min(1, (now - start) / dur);
    el.textContent = (to * (1 - Math.pow(1 - t, 3))).toFixed(dec);
    if (t < 1) requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
}
const revealer = new IntersectionObserver((entries) => {
  entries.forEach((en) => {
    if (!en.isIntersecting) return;
    en.target.classList.add("in-view");
    $$(".count", en.target).forEach(countUp);
    revealer.unobserve(en.target);
  });
}, { threshold: 0.12 });
$$(".reveal").forEach((el) => revealer.observe(el));

/* ---------- card spotlight ---------- */
$$(".card").forEach((card) => {
  card.addEventListener("pointermove", (e) => {
    const r = card.getBoundingClientRect();
    card.style.setProperty("--mx", `${e.clientX - r.left}px`);
    card.style.setProperty("--my", `${e.clientY - r.top}px`);
  });
});

/* ---------- equalizer bars ---------- */
function makeBars(el, count, prefix = "") {
  const frag = document.createDocumentFragment();
  for (let i = 0; i < count; i++) {
    const b = document.createElement("i");
    const h = 25 + Math.round(Math.abs(Math.sin(i * 0.7)) * 70);
    b.style.setProperty("--h", h + "%");
    if (prefix) b.style.animationDelay = `${(i % 9) * -0.13}s`;
    frag.appendChild(b);
  }
  el.appendChild(frag);
}
makeBars($("#feature-bars"), 36, "eq");
const wave = $("#wave");
makeBars(wave, 18);
const waveBars = $$("i", wave);

/* ---------- hero: looping voice session ---------- */
const SESSIONS = [
  [
    { who: "you", text: "Hello Nova, open Gmail on my work account." },
    { who: "sys", text: "open → gmail · profile “Work”" },
    { who: "nova", text: "Opening Gmail in your Work profile. You have 3 unread emails." },
  ],
  [
    { who: "you", text: "Remind me in 20 minutes to call Ammi." },
    { who: "sys", text: "reminder · 20 min · “call Ammi”" },
    { who: "nova", text: "Done — I’ll remind you at 4:35 to call Ammi." },
  ],
  [
    { who: "you", text: "Namaz kab hai?" },
    { who: "sys", text: "prayer times · Karachi · Hanafi" },
    { who: "nova", text: "Asr 4:39 par hai — 22 minute baqi hain. 10 minute pehle reminder aa jayega." },
  ],
];
const transcript = $("#transcript");
const orb = $("#orb");
const orbState = $("#orb-state");
let waveTimer = null;

function animateWave(level) {
  clearInterval(waveTimer);
  if (!level) { waveBars.forEach((b) => (b.style.height = "20%")); return; }
  waveTimer = setInterval(() => {
    waveBars.forEach((b) => (b.style.height = 15 + Math.random() * level + "%"));
  }, 110);
}

function addLine(line) {
  const li = document.createElement("li");
  li.className = line.who;
  if (line.who === "sys") {
    li.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12l5 5 9-10"/></svg>';
    li.append(line.text);
  } else {
    li.textContent = line.text;
  }
  transcript.appendChild(li);
  return li;
}

async function typeInto(li, text) {
  li.textContent = "";
  for (let i = 0; i < text.length; i += 2) {
    li.textContent = text.slice(0, i + 2);
    await sleep(22);
  }
}

async function runSessions() {
  if (reduceMotion) { SESSIONS[0].forEach(addLine); return; }
  for (let s = 0; ; s = (s + 1) % SESSIONS.length) {
    transcript.innerHTML = "";
    for (const line of SESSIONS[s]) {
      if (line.who === "you") {
        orb.classList.remove("speaking"); orbState.textContent = "Listening…"; animateWave(80);
        await sleep(900);
        const li = addLine({ who: "you", text: "" });
        await typeInto(li, line.text);
        animateWave(0); orbState.textContent = "Thinking…";
        await sleep(600);
      } else if (line.who === "sys") {
        addLine(line);
        await sleep(700);
      } else {
        orb.classList.add("speaking"); orbState.textContent = "Speaking…"; animateWave(55);
        const li = addLine({ who: "nova", text: "" });
        await typeInto(li, line.text);
        await sleep(1400);
        animateWave(0); orb.classList.remove("speaking"); orbState.textContent = "Listening…";
      }
    }
    await sleep(2200);
  }
}
// start the demo only while it's on screen
let demoStarted = false;
new IntersectionObserver((en) => {
  if (en[0].isIntersecting && !demoStarted) { demoStarted = true; runSessions(); }
}).observe($(".hero-visual"));

/* ---------- commands explorer ---------- */
const ICON = {
  apps: '<path d="M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z"/>',
  mem: '<path d="M12 3a6 6 0 0 0-4 10.5V17h8v-3.5A6 6 0 0 0 12 3zM9 21h6"/>',
  time: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  eye: '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
  msg: '<path d="M21 12a9 9 0 0 1-13.5 7.8L3 21l1.2-4.5A9 9 0 1 1 21 12z"/>',
};
const COMMANDS = [
  { id: "apps", label: "Apps & web", icon: ICON.apps, items: [
    { say: "Open VS Code", tag: "open", reply: "Opening Visual Studio Code.", meta: ["rule match", "no AI call needed"] },
    { say: "Open Gmail on work account", tag: "accounts", reply: "Opening Gmail in your Work Chrome profile.", meta: ["browser profile", "multi-account"] },
    { say: "Search YouTube for Python tutorials", tag: "search", reply: "Here are YouTube results for Python tutorials.", meta: ["search", "YouTube"] },
    { say: "Work mode", tag: "routine", reply: "Starting Work mode: VS Code, Gmail, and a 50-minute focus session.", meta: ["routine", "3 steps"] },
    { say: "Awaaz kam karo", tag: "volume", reply: "Volume down.", meta: ["Roman Urdu", "system"] },
  ]},
  { id: "memory", label: "Memory & goals", icon: ICON.mem, items: [
    { say: "Remember that my sister's name is Ayesha", tag: "remember", reply: "Got it — your sister is Ayesha.", meta: ["memory · person"] },
    { say: "What do you remember about me?", tag: "recall", reply: "Your name is Ali, your sister is Ayesha, and you want to learn Python.", meta: ["local SQLite"] },
    { say: "My new goal is to run 5K", tag: "goal", reply: "Added your goal: run 5K. I’ll ask about it in the morning check-in.", meta: ["goals", "check-ins"] },
    { say: "Forget that I live in Lahore", tag: "forget", reply: "Okay, I’ve forgotten that you live in Lahore.", meta: ["memory · delete"] },
  ]},
  { id: "time", label: "Reminders & focus", icon: ICON.time, items: [
    { say: "Remind me in 10 minutes to drink water", tag: "remind", reply: "I’ll remind you at 3:25 to drink water.", meta: ["scheduler"] },
    { say: "Start focus for 50 minutes", tag: "focus", reply: "Focus started for 50 minutes. I’ll tell you if YouTube or Instagram opens.", meta: ["distraction watch"] },
    { say: "Namaz kab hai?", tag: "prayer", reply: "Maghrib 6:21 par hai — 1 ghanta 5 minute baqi.", meta: ["adhan · Karachi method"] },
    { say: "Brief me", tag: "morning", reply: "Good morning! Two reminders today, and your goal is to learn Python.", meta: ["daily summary"] },
  ]},
  { id: "screen", label: "Screen & accessibility", icon: ICON.eye, items: [
    { say: "Read the screen", tag: "OCR", reply: "The window says: “Invoice 2041, due 30 September, total 18,500 rupees.”", meta: ["Windows OCR", "offline"] },
    { say: "What's under the mouse?", tag: "pointer", reply: "The mouse is on the “Submit” button.", meta: ["UI Automation"] },
    { say: "Translate this into Roman Urdu", tag: "translate", reply: "“Aap ka parcel kal pohanch jayega.”", meta: ["gemma3:4b", "Ctrl Alt T"] },
    { say: "How is my laptop?", tag: "status", reply: "Battery 64%, CPU 18%, 7 GB of memory free.", meta: ["system status"] },
  ]},
  { id: "msg", label: "WhatsApp & notifications", icon: ICON.msg, items: [
    { say: "Mama Ji ko WhatsApp karo ke main late aaunga", tag: "send", reply: "Sending to Mama Ji: “Main late aaunga.” Should I send it?", meta: ["confirms first", "checks chat name"] },
    { say: "Open Ayesha's chat", tag: "chat", reply: "Opening your WhatsApp chat with Ayesha.", meta: ["WhatsApp Desktop"] },
    { say: "Repeat that", tag: "repeat", reply: "WhatsApp from Mama Ji: “Khana kha liya?”", meta: ["notifications"] },
  ]},
];

const tabsEl = $("#cmd-tabs");
const listEl = $("#cmd-list");
const prevYou = $("#prev-you");
const prevNova = $("#prev-nova");
const prevMeta = $("#prev-meta");

function showCommand(item, btn) {
  $$(".cmd", listEl).forEach((b) => b.setAttribute("aria-pressed", String(b === btn)));
  prevYou.textContent = "“" + item.say + "”";
  prevNova.textContent = item.reply;
  prevMeta.innerHTML = "";
  item.meta.forEach((m) => { const s = document.createElement("span"); s.textContent = m; prevMeta.appendChild(s); });
  [prevYou.parentElement, prevNova.parentElement].forEach((b) => { b.style.animation = "none"; void b.offsetWidth; b.style.animation = ""; });
}

function showCategory(cat) {
  $$(".tab", tabsEl).forEach((t) => {
    const on = t.dataset.id === cat.id;
    t.setAttribute("aria-selected", String(on));
    t.tabIndex = on ? 0 : -1;
  });
  listEl.setAttribute("aria-labelledby", "tab-" + cat.id);
  listEl.innerHTML = "";
  cat.items.forEach((item, i) => {
    const li = document.createElement("li");
    const b = document.createElement("button");
    b.type = "button";
    b.className = "cmd";
    b.innerHTML = "<span></span><small></small>";
    b.firstChild.textContent = "“" + item.say + "”";
    b.lastChild.textContent = item.tag;
    b.addEventListener("click", () => showCommand(item, b));
    li.appendChild(b);
    listEl.appendChild(li);
    if (i === 0) showCommand(item, b);
  });
}

COMMANDS.forEach((cat, i) => {
  const t = document.createElement("button");
  t.type = "button";
  t.className = "tab";
  t.id = "tab-" + cat.id;
  t.dataset.id = cat.id;
  t.setAttribute("role", "tab");
  t.innerHTML = `<svg viewBox="0 0 24 24" aria-hidden="true">${cat.icon}</svg>`;
  t.append(cat.label);
  t.addEventListener("click", () => showCategory(cat));
  t.addEventListener("keydown", (e) => {
    if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
    const next = COMMANDS[(i + (e.key === "ArrowRight" ? 1 : COMMANDS.length - 1)) % COMMANDS.length];
    showCategory(next);
    $("#tab-" + next.id).focus();
  });
  tabsEl.appendChild(t);
});
showCategory(COMMANDS[0]);

/* ---------- memory demo ---------- */
const memSaid = $("#mem-said");
const memTarget = $("#mem-target");
const memText = $(".md-text", memTarget);
const memHistory = $("#mem-history");
let memBusy = false;
async function playMemory() {
  if (memBusy) return;
  memBusy = true;
  memSaid.classList.remove("show");
  memTarget.classList.remove("changing", "changed");
  memText.textContent = "User is travelling to Dubai next month.";
  memHistory.hidden = true;
  await sleep(300);
  memSaid.classList.add("show");
  await sleep(1100);
  memTarget.classList.add("changing");
  await sleep(700);
  memText.style.opacity = "0";
  await sleep(250);
  memText.textContent = "User is travelling to London next month.";
  memText.style.opacity = "1";
  memTarget.classList.replace("changing", "changed");
  await sleep(400);
  memHistory.hidden = false;
  memBusy = false;
}
$("#mem-play").addEventListener("click", playMemory);
new IntersectionObserver((en, obs) => {
  if (en[0].isIntersecting) { playMemory(); obs.disconnect(); }
}, { threshold: 0.5 }).observe($(".memory-demo"));

/* ---------- docs ---------- */
const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
// tiny highlighter: the comment is split off first, and spans use unquoted class names so the
// string rule can't match the markup it produced
function highlight(code, lang) {
  return code.split("\n").map((line) => {
    const hash = line.search(/(^|\s)#/);
    let body = hash >= 0 ? line.slice(0, hash) : line;
    const comment = hash >= 0 ? line.slice(hash) : "";
    body = esc(body);
    if (lang === "yaml") {
      body = body
        .replace(/^(\s*-?\s*)([\w .]+)(:)/, "$1<span class=k>$2</span>$3")
        .replace(/"([^"]*)"/g, '<span class=s>"$1"</span>')
        .replace(/:\s(\d+(?:\.\d+)?|true|false|null)\b/g, ": <span class=n>$1</span>");
    } else {
      body = body.replace(/^(PS&gt;)\s/, "<span class=p>$1 </span>").replace(/^(✓)/, "<span class=s>$1</span>");
    }
    return body + (comment ? `<span class=c>${esc(comment)}</span>` : "");
  }).join("\n");
}

const DOCS = {
  install: {
    file: "PowerShell", lang: "sh",
    note: "The installer only adds what’s missing, so it’s safe to run again after an update.",
    code: `# 1. Unzip Nova, then from its folder:
PS> .\\install.ps1

# or step by step
PS> pip install -r requirements-FULL.txt
PS> ollama pull qwen2.5:1.5b
PS> ollama pull nomic-embed-text
PS> ollama pull gemma3:4b

# start invisibly in the tray, then say "Hello Nova"
PS> pythonw nova.pyw`,
  },
  check: {
    file: "PowerShell", lang: "sh",
    note: "Run the check any time something seems off — or just ask Nova to “check yourself”.",
    code: `PS> python main.py --check
✓ Ollama running at http://localhost:11434
✓ Model qwen2.5:1.5b (chat)
✓ Model nomic-embed-text (memory search)
✓ speech recognition
✓ text-to-speech
✓ microphone
✓ screen reading (Windows OCR)
✓ desktop notifications
– prayer times  (set user.latitude / longitude)`,
  },
  config: {
    file: "config.yaml", lang: "yaml",
    note: "Changes made by voice or on the settings page are saved to data/settings.yaml and override this file.",
    code: `assistant:
  name: "Nova"             # the wake word follows the name

llm:
  chat_model: "qwen2.5:1.5b"
  max_tokens: 400
  keep_alive: "30m"        # keep the model loaded

voice:
  stt_model_size: "base"   # understands your commands
  wake_model_size: "tiny"  # listens for the wake word all day
  language: "en"           # "en", "ur", "hi" or "" to auto-detect
  wake_sensitivity: 0.82
  conversation: true       # keep listening until "bye"

scheduler:
  prayer_method: "KARACHI"
  madhab: "HANAFI"
  prayer_reminder_minutes_before: 10`,
  },
  routines: {
    file: "config.yaml", lang: "yaml",
    note: "Say the routine’s name and Nova runs every step in order.",
    code: `routines:
  work mode:
    - open vs code
    - open gmail
    - start focus for 50 minutes
  good night:
    - what are my goals
    - volume down

focus:
  default_minutes: 25
  distractions: ["youtube", "instagram", "netflix"]`,
  },
  hotkeys: {
    file: "config.yaml", lang: "yaml",
    note: "Hotkeys work from any app, even when Nova’s window is closed.",
    code: `hotkeys:
  enabled: true
  talk: "<ctrl>+<alt>+<space>"    # talk without the wake word
  translate: "<ctrl>+<alt>+t"     # selected text -> Roman Urdu
  open_window: "<ctrl>+<alt>+n"   # open Nova's window`,
  },
};

const codeBody = $("#code-body");
const codeFile = $("#code-file");
const codeNote = $("#code-note");
let currentDoc = "install";
function showDoc(key) {
  currentDoc = key;
  const d = DOCS[key];
  codeFile.textContent = d.file;
  codeBody.innerHTML = highlight(d.code, d.lang);
  codeNote.textContent = d.note;
  $$(".doc-link").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.doc === key)));
}
$$(".doc-link").forEach((b) => b.addEventListener("click", () => showDoc(b.dataset.doc)));
showDoc("install");

const copyBtn = $("#copy-btn");
copyBtn.addEventListener("click", async () => {
  const text = DOCS[currentDoc].code.split("\n").map((l) => l.replace(/^PS> /, "")).join("\n");
  const label = $("span", copyBtn);
  try {
    await navigator.clipboard.writeText(text);
    label.textContent = "Copied";
  } catch {
    label.textContent = "Press Ctrl+C";
    const range = document.createRange();
    range.selectNodeContents(codeBody);
    getSelection().removeAllRanges();
    getSelection().addRange(range);
  }
  setTimeout(() => (label.textContent = "Copy"), 1600);
});

/* ---------- footer ---------- */
$("#year").textContent = new Date().getFullYear();

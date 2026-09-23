"use strict";

const TOKEN = document.querySelector('meta[name="token"]').content;
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

// In the native Nova window there is no server: calls go through the in-process bridge.
const NATIVE = !!window.NOVA_NATIVE;
const bridgeReady = NATIVE ? new Promise(resolve => {
  if (window.pywebview && window.pywebview.api) resolve();
  else window.addEventListener("pywebviewready", () => resolve(), { once: true });
}) : null;

async function api(path, { method = "GET", body, raw = false } = {}) {
  if (NATIVE) {
    await bridgeReady;
    const r = await window.pywebview.api.request(method, path, body === undefined ? null : body);
    if (r.status >= 400) {
      let detail = `Error ${r.status}`;
      try { detail = JSON.parse(r.body).detail || detail; } catch {}
      throw new Error(detail);
    }
    return raw ? new Response(r.body, { status: r.status }) : JSON.parse(r.body);
  }
  const opts = { method, headers: { "X-Companion-Token": TOKEN } };
  if (body instanceof FormData) opts.body = body;
  else if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(path, opts);
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch {}
    throw new Error(detail);
  }
  return raw ? res : res.json();
}

function store(key, value) {
  try {
    if (value === undefined) return localStorage.getItem(key);
    localStorage.setItem(key, value);
  } catch { return null; }
}

// ---------------------------------------------------------------- tabs
const tabs = $$('[role="tab"]');
function selectTab(tab) {
  tabs.forEach(t => {
    const on = t === tab;
    t.setAttribute("aria-selected", on);
    t.tabIndex = on ? 0 : -1;
    $("#" + t.getAttribute("aria-controls")).hidden = !on;
  });
  store("tab", tab.id);
  const loaders = { "tab-commands": loadCommands, "tab-settings": loadSettings, "tab-memory": loadMemory,
                    "tab-accounts": loadAccounts };
  loaders[tab.id]?.();
  if (tab.id === "tab-chat") $("#text").focus();
}
tabs.forEach((t, i) => {
  t.addEventListener("click", () => selectTab(t));
  t.addEventListener("keydown", e => {
    const d = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
    if (d) { const n = tabs[(i + d + tabs.length) % tabs.length]; n.focus(); selectTab(n); }
  });
});

// ---------------------------------------------------------------- speech out
const speech = {
  voice: null,
  rate: parseFloat(store("rate") || "1"),
  enabled: () => $("#speak-toggle").checked,
  say(text, { force = false } = {}) {
    if (!("speechSynthesis" in window) || (!force && !this.enabled())) return Promise.resolve();
    const clean = String(text).replace(/https?:\/\/\S+/g, "link").replace(/[*_`#>|]+/g, "").trim();
    if (!clean) return Promise.resolve();
    return new Promise(resolve => {
      const u = new SpeechSynthesisUtterance(clean);
      if (this.voice) u.voice = this.voice;
      u.rate = this.rate;
      u.onend = u.onerror = resolve;
      speechSynthesis.speak(u);
    });
  },
  stop() { if ("speechSynthesis" in window) speechSynthesis.cancel(); },
  idle() {
    return new Promise(resolve => {
      const check = () => (!speechSynthesis.speaking && !speechSynthesis.pending) ? resolve() : setTimeout(check, 150);
      check();
    });
  },
};
function loadVoices() {
  if (!("speechSynthesis" in window)) return;
  const voices = speechSynthesis.getVoices();
  const sel = $("#voice-select");
  const saved = store("voice");
  sel.innerHTML = "";
  voices.forEach(v => sel.add(new Option(`${v.name} (${v.lang})`, v.name)));
  speech.voice = voices.find(v => v.name === saved) || voices.find(v => /en[-_]/i.test(v.lang)) || voices[0] || null;
  if (speech.voice) sel.value = speech.voice.name;
}
if ("speechSynthesis" in window) { speechSynthesis.onvoiceschanged = loadVoices; loadVoices(); }
$("#voice-select").addEventListener("change", e => {
  speech.voice = speechSynthesis.getVoices().find(v => v.name === e.target.value) || null;
  store("voice", e.target.value);
  speech.say("This is how I sound.", { force: true });
});
$("#rate").value = speech.rate;
$("#rate").addEventListener("change", e => { speech.rate = parseFloat(e.target.value); store("rate", e.target.value); });

// ---------------------------------------------------------------- speech in
function beep(freq, ms) {
  try {
    const ctx = beep.ctx || (beep.ctx = new AudioContext());
    const o = ctx.createOscillator(), g = ctx.createGain();
    o.frequency.value = freq; g.gain.value = 0.15;
    o.connect(g).connect(ctx.destination);
    o.start(); o.stop(ctx.currentTime + ms / 1000);
  } catch {}
}

const recorder = {
  stream: null, busy: false, stopRequested: false,
  async open() {
    if (!this.stream) {
      this.stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
    }
    return this.stream;
  },
  /** Record one utterance: stops after ~1.3 s of quiet once speech started, at 15 s, or on stop(). */
  async record({ onLevel, waitMs = 8000, maxMs = 15000 } = {}) {
    const stream = await this.open();
    const ctx = new AudioContext();
    const src = ctx.createMediaStreamSource(stream);
    const analyser = ctx.createAnalyser();
    analyser.fftSize = 2048;
    src.connect(analyser);
    const buf = new Float32Array(analyser.fftSize);
    const mime = MediaRecorder.isTypeSupported("audio/webm;codecs=opus") ? "audio/webm;codecs=opus" : "";
    const rec = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
    const chunks = [];
    rec.ondataavailable = e => e.data.size && chunks.push(e.data);
    this.stopRequested = false;

    beep(880, 120);
    await new Promise(r => setTimeout(r, 150)); // don't record our own beep
    rec.start(250);
    const t0 = performance.now();
    let noise = null, started = false, loudRun = 0, quietMs = 0;
    await new Promise(resolve => {
      const tick = setInterval(() => {
        analyser.getFloatTimeDomainData(buf);
        let sum = 0; for (const x of buf) sum += x * x;
        const level = Math.sqrt(sum / buf.length);
        const elapsed = performance.now() - t0;
        if (elapsed < 300) { noise = noise === null ? level : Math.min(noise, level); onLevel?.(level * 8, "speak"); return; }
        const threshold = Math.max(0.01, (noise || 0) * 3);
        const loud = level > threshold;
        onLevel?.(Math.min(1, level * 8), started ? "hearing" : "speak");
        if (!started) {
          loudRun = loud ? loudRun + 1 : 0;
          if (loudRun >= 2) started = true;
          else if (elapsed > waitMs) stop();
        } else {
          quietMs = loud ? 0 : quietMs + 100;
          if (quietMs >= 1300) stop();
        }
        if (elapsed > maxMs || this.stopRequested) stop();
      }, 100);
      const stop = () => { clearInterval(tick); rec.stop(); };
      rec.onstop = resolve;
    });
    ctx.close();
    beep(520, 90);
    onLevel?.(0, "");
    return new Blob(chunks, { type: rec.mimeType || "audio/webm" });
  },
  stop() { this.stopRequested = true; },
};

async function listenOnce(statusEl = $("#mic-status")) {
  if (NATIVE) {  // Nova's own microphone (the one the wake word uses); it beeps when to speak
    if (recorder.busy) return "";
    recorder.busy = true;
    speech.stop();
    $("#mic").classList.add("listening");
    statusEl.textContent = "Listening… speak after the beep";
    try {
      const { text } = await api("/api/listen", { method: "POST" });
      statusEl.textContent = text ? "" : "I didn't catch that — try again.";
      return text;
    } catch (e) {
      statusEl.textContent = e.message;
      return "";
    } finally {
      recorder.busy = false;
      $("#mic").classList.remove("listening");
    }
  }
  if (recorder.busy) { recorder.stop(); return ""; }
  recorder.busy = true;
  speech.stop();
  api("/api/wake/pause", { method: "POST" }).catch(() => {});
  const mic = $("#mic");
  mic.classList.add("listening");
  mic.setAttribute("aria-pressed", "true");
  try {
    statusEl.textContent = "Listening… speak now";
    const blob = await recorder.record({
      onLevel: (lvl, state) => {
        mic.style.setProperty("--level", lvl.toFixed(2));
        if (state === "hearing") statusEl.textContent = "Hearing you…";
      },
    });
    statusEl.textContent = "Understanding…";
    mic.classList.add("busy");
    const fd = new FormData();
    fd.append("audio", blob, "speech.webm");
    const { text } = await api("/api/transcribe", { method: "POST", body: fd });
    statusEl.textContent = text ? "" : "I didn't catch that — try again.";
    return text;
  } catch (e) {
    statusEl.textContent = e.name === "NotAllowedError"
      ? "Microphone blocked — allow it in the browser's address bar." : "Mic error: " + e.message;
    return "";
  } finally {
    api("/api/wake/resume", { method: "POST" }).catch(() => {});
    recorder.busy = false;
    mic.classList.remove("listening", "busy");
    mic.setAttribute("aria-pressed", "false");
    mic.style.setProperty("--level", 0);
  }
}

// ---------------------------------------------------------------- chat
const log = $("#log");
function addMsg(kind, text, tag) {
  const div = document.createElement("div");
  div.className = "msg " + kind;
  if (tag) { const t = document.createElement("span"); t.className = "tag"; t.textContent = tag; div.append(t); }
  const body = document.createElement("span");
  body.textContent = text;
  div.append(body);
  log.append(div);
  log.scrollTop = log.scrollHeight;
  return body;
}

let pending = null; // { text, answers, question, options } while waiting for the user's answer
let lastFromVoice = false;

async function sendMessage(text, { answers = {}, fromVoice = false } = {}) {
  text = text.trim();
  if (!text) return;
  lastFromVoice = fromVoice;
  if (!Object.keys(answers).length) addMsg("user", text);
  const out = addMsg("ai", "…", appName);
  let full = "", spoken = 0;
  const speakReady = (final) => {
    // speak finished sentences while the rest is still being written
    const re = /[^.!?۔\n]+[.!?۔\n]+/g;
    re.lastIndex = spoken;
    let m;
    while ((m = re.exec(full))) { speech.say(m[0]); spoken = re.lastIndex; }
    if (final && spoken < full.length) { speech.say(full.slice(spoken)); spoken = full.length; }
  };
  try {
    const res = await api("/api/message", { method: "POST", body: { text, answers }, raw: true });
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let nl;
      while ((nl = buffer.indexOf("\n")) >= 0) {
        const line = buffer.slice(0, nl); buffer = buffer.slice(nl + 1);
        if (!line.trim()) continue;
        const ev = JSON.parse(line);
        if (ev.type === "token") { full += ev.text; out.textContent = full; speakReady(false); }
        else if (ev.type === "reply") { full = ev.text; out.textContent = full; speech.say(full); }
        else if (ev.type === "ask") { out.textContent = ev.question; askUser(ev.question, ev.options, ev.text, answers); return; }
        log.scrollTop = log.scrollHeight;
      }
    }
    speakReady(true);
  } catch (e) {
    out.textContent = "Error: " + e.message;
    speech.say("Sorry, something went wrong.");
  }
  if ($("#auto-listen").checked && lastFromVoice) {
    await speech.idle();
    talk();
  }
}

const YES = /\b(yes|yeah|yep|sure|ok|okay|confirm|go ahead|do it|send|haan|han|ha|ji|jee|theek hai|thik hai|kar do|bhej do|bilkul|zaroor)\b/i;
const NO = /\b(no|nope|cancel|stop|don'?t|nahi|nahin|mat karo|mat bhejo|rehne do)\b/i;

function matchSpoken(answer, options) {
  const a = answer.toLowerCase().replace(/[^\w\s@.]/g, " ").trim();
  if (!a) return null;
  if (options.length === 2 && options[0] === "Yes") return NO.test(a) ? "No" : YES.test(a) ? "Yes" : null;
  return options.find(o => o.toLowerCase() === a)
    || options.find(o => a.includes(o.toLowerCase()))
    || options.find(o => o.toLowerCase().includes(a)) || answer; // the server matches fuzzily too
}

async function askUser(question, options, text, answers) {
  pending = { text, answers, question, options };
  $("#ask-q").textContent = question;
  const box = $("#ask-options");
  box.innerHTML = "";
  const form = $("#ask-form");
  form.hidden = !!options;
  (options || []).forEach((opt, i) => {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = opt === "Yes" ? "Yes, do it" : opt;
    if (i === 0) b.className = "primary";
    b.onclick = () => answer(opt);
    box.append(b);
  });
  $("#ask").hidden = false;
  (options ? box.querySelector("button") : $("#ask-input")).focus();
  const spoken = options && options.length > 2 ? `${question}` : question + (options ? " Say yes or no." : "");
  await speech.say(spoken, { force: lastFromVoice });
  if (lastFromVoice && pending && pending.question === question) {
    const heard = await listenOnce();
    if (!heard || !pending || pending.question !== question) return;
    answer(options ? matchSpoken(heard, options) || heard : heard);
  }
}
function answer(value) {
  if (!pending) return;
  const { text, answers, question, options } = pending;
  pending = null;
  $("#ask").hidden = true;
  if (value === null || value === undefined || value === "" || (options && value === "No")) {
    addMsg("ai", "Okay, I won't do it.", appName);
    speech.say("Okay, I won't do it.");
    return;
  }
  addMsg("user", value);
  sendMessage(text, { answers: { ...answers, [question]: value }, fromVoice: lastFromVoice });
}
$("#ask-form").addEventListener("submit", e => { e.preventDefault(); answer($("#ask-input").value.trim()); $("#ask-input").value = ""; });
$("#ask-cancel").addEventListener("click", () => answer(null));

$("#chat-form").addEventListener("submit", e => {
  e.preventDefault();
  const input = $("#text");
  const text = input.value;
  input.value = "";
  sendMessage(text);
});

async function talk() {
  const text = await listenOnce();
  if (text) sendMessage(text, { fromVoice: true });
}
$("#mic").addEventListener("click", talk);
$("#stop-speaking").addEventListener("click", () => speech.stop());

document.addEventListener("keydown", e => {
  if (e.ctrlKey && e.code === "Space") { e.preventDefault(); talk(); }
  else if (e.key === "Escape") { speech.stop(); if (recorder.busy) recorder.stop(); }
});

// dictation into any input: <button class="dictate" data-target="id">
async function dictateInto(el, { append = false } = {}) {
  const status = document.createElement("span");
  const text = await listenOnce(status);
  if (!text) { toast(status.textContent || "Didn't catch that."); return; }
  if (append && el.value.trim()) el.value = el.value.replace(/\s*$/, "\n") + text;
  else el.value = text;
  el.dispatchEvent(new Event("input"));
  el.focus();
}
document.addEventListener("click", e => {
  const b = e.target.closest(".dictate");
  if (b) dictateInto($("#" + b.dataset.target));
});

// ---------------------------------------------------------------- notifications
let lastEvent = 0;
let appName = "Nova";
$("#call-record").onclick = () => { api("/api/calls/decide", { method: "POST", body: { record: true } }).catch(e => toast(e.message)); $("#call-banner").hidden = true; };
$("#call-skip").onclick = () => { api("/api/calls/decide", { method: "POST", body: { record: false } }).catch(() => {}); $("#call-banner").hidden = true; };
function toast(text) {
  const t = $("#toast");
  t.textContent = text;
  t.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (t.hidden = true), 8000);
}
async function pollEvents() {
  try {
    const { events, status } = await api(`/api/events?after=${lastEvent}`);
    for (const ev of events) {
      lastEvent = Math.max(lastEvent, ev.id);
      if (ev.kind === "heard") addMsg("user", "🎤 " + ev.message);
      else if (ev.kind === "said") addMsg("ai", ev.message, `${appName} (voice)`);
      else if (ev.kind === "note") addMsg("note", ev.message);
      else if (ev.kind === "call") { $("#call-q").textContent = ev.message; $("#call-banner").hidden = false; }
      else {
        addMsg("note", `🔔 ${ev.time} — ${ev.title}: ${ev.message}`);
        toast(`${ev.title}: ${ev.message}`);
        if (ev.speak) speech.say(`${ev.title}. ${ev.message}`, { force: true });
      }
    }
    const parts = [];
    if (status.name && status.name !== appName) { appName = status.name; $("#app-name").textContent = appName; document.title = appName; }
    if (!status.in_call) $("#call-banner").hidden = true;
    if (status.in_call) parts.push("📞 on a call — staying quiet");
    else if (status.wake) parts.push(status.wake_paused ? "🎙 wake word paused" : `🎙 say “${status.phrases[0]}” to talk`);
    if (status.voiceprints) parts.push(`🔒 ${status.voiceprints} trusted voice${status.voiceprints > 1 ? "s" : ""}`);
    if (status.monitor) parts.push("🔔 watching notifications");
    if (status.hotkeys) parts.push("⌨ Ctrl+Alt+T translate");
    $("#bg-status").textContent = parts.join(" · ");
  } catch {}
}
setInterval(pollEvents, 2000);

// ---------------------------------------------------------------- my commands
const commandsEl = $("#commands");
function commandCard(phrase = "", steps = []) {
  const node = $("#command-tpl").content.firstElementChild.cloneNode(true);
  const phraseEl = $(".phrase", node), stepsEl = $(".steps", node);
  phraseEl.value = phrase;
  stepsEl.value = steps.join("\n");
  $(".dictate-phrase", node).onclick = () => dictateInto(phraseEl);
  $(".dictate-step", node).onclick = () => dictateInto(stepsEl, { append: true });
  $(".delete", node).onclick = () => { node.remove(); $("#commands-status").textContent = "Deleted — press Save to keep this change."; };
  $(".check-steps", node).onclick = async () => {
    const { steps } = await api("/api/commands/preview", { method: "POST", body: { steps: stepLines(stepsEl) } });
    const ul = $(".preview", node);
    ul.innerHTML = "";
    steps.forEach(s => {
      const li = document.createElement("li");
      li.textContent = s.intent === "chat"
        ? `✗ “${s.step}” — not a command I know; it would be sent to the AI as a question`
        : `✓ “${s.step}” → ${s.intent}${Object.keys(s.args).length ? " " + JSON.stringify(s.args) : ""}`;
      if (s.intent === "chat") li.className = "chat";
      ul.append(li);
    });
  };
  $(".try", node).onclick = () => {
    if (!phraseEl.value.trim()) return toast("Give the command a phrase first.");
    saveCommands().then(() => { selectTab($("#tab-chat")); sendMessage(phraseEl.value); });
  };
  return node;
}
const stepLines = el => el.value.split("\n").map(s => s.trim()).filter(Boolean);

async function loadCommands() {
  const { commands } = await api("/api/commands");
  commandsEl.innerHTML = "";
  if (!commands.length) commandsEl.append(commandCard());
  commands.forEach(c => commandsEl.append(commandCard(c.phrase, c.steps)));
}
async function saveCommands() {
  const commands = $$(".command", commandsEl).map(n => ({ phrase: $(".phrase", n).value.trim(), steps: stepLines($(".steps", n)) }))
    .filter(c => c.phrase && c.steps.length);
  await api("/api/commands", { method: "PUT", body: { commands } });
  $("#commands-status").textContent = `Saved ${commands.length} command(s).`;
}
$("#add-command").onclick = () => { const n = commandCard(); commandsEl.append(n); $(".phrase", n).focus(); };
$("#save-commands").onclick = () => saveCommands().catch(e => ($("#commands-status").textContent = "Error: " + e.message));

// ---------------------------------------------------------------- settings
const form = $("#settings-form");
async function loadSettings() {
  const data = await api("/api/settings");
  const models = $("#model-select");
  models.innerHTML = "";
  const current = data.settings.llm.chat_model;
  const names = data.models.filter(m => !/embed/.test(m));
  if (!names.includes(current)) names.unshift(current);
  names.forEach(m => models.add(new Option(m, m)));
  const tmodels = $("#translate-model-select");
  tmodels.innerHTML = "";
  tmodels.add(new Option("Same as chat model", ""));
  names.forEach(m => tmodels.add(new Option(m, m)));
  const mics = $("#mic-select");
  mics.innerHTML = "";
  mics.add(new Option("Windows default microphone", ""));
  data.microphones.forEach(m => mics.add(new Option(m.name, String(m.index))));
  fillSettings(data.settings);
  loadVoiceprint();
  loadExpertUsage();
  api("/api/autostart").then(r => ($("#autostart").checked = r.enabled)).catch(() => {});
  api("/api/startmenu").then(r => ($("#startmenu").checked = r.enabled)).catch(() => {});
  const feats = $("#features");
  feats.innerHTML = "";
  data.features.forEach(f => {
    const label = document.createElement("label");
    label.innerHTML = `<input type="checkbox" data-feature="${f.key}"> <span></span>`;
    $("input", label).checked = f.enabled;
    $("span", label).textContent = f.label;
    feats.append(label);
  });
}
function fillSettings(settings) {
  for (const [section, values] of Object.entries(settings)) {
    for (const [key, value] of Object.entries(values)) {
      const el = form.elements[`${section}.${key}`];
      if (!el) continue;
      if (el.type === "checkbox") el.checked = !!value;
      else if (el.tagName === "SELECT" && value !== null && value !== undefined && ![...el.options].some(o => o.value === String(value)))
        { el.add(new Option(String(value), String(value))); el.value = String(value); }
      else el.value = Array.isArray(value) ? value.join(", ") : (value ?? "");
    }
  }
}

function showFieldErrors(errors) {
  $$(".field-error", form).forEach(n => n.remove());
  $$("[aria-invalid]", form).forEach(el => el.removeAttribute("aria-invalid"));
  const entries = Object.entries(errors || {});
  for (const [field, message] of entries) {
    const el = form.elements[field];
    if (!el) continue;
    el.setAttribute("aria-invalid", "true");
    const note = document.createElement("span");
    note.className = "field-error";
    note.id = `err-${field.replace(".", "-")}`;
    note.textContent = message;
    el.setAttribute("aria-describedby", note.id);
    el.insertAdjacentElement("afterend", note);
  }
  if (entries.length) form.elements[entries[0][0]]?.focus();
  return entries.length;
}

form.addEventListener("submit", async e => {
  e.preventDefault();
  const body = { features: {} };
  for (const el of form.elements) {
    if (el.dataset.feature) { body.features[el.dataset.feature] = el.checked; continue; }
    if (!el.name || !el.name.includes(".")) continue;
    const [section, key] = el.name.split(".");
    (body[section] ||= {})[key] = el.type === "checkbox" ? el.checked : el.value;
  }
  const status = $("#settings-status");
  const button = form.querySelector('button[type="submit"]');
  button.disabled = true;           // no double submits while the first save is running
  status.textContent = "Saving…";
  try {
    const result = await api("/api/settings", { method: "PUT", body });
    fillSettings(result.settings);  // show what was actually stored (e.g. "24,86" becomes 24.86)
    const bad = showFieldErrors(result.errors);
    if (bad) {
      status.textContent = `Saved — but ${bad} ${bad > 1 ? "fields need" : "field needs"} fixing (marked in red) — ${bad > 1 ? "those" : "it"} kept the old value.`;
      speech.say(`Saved, but ${bad} ${bad > 1 ? "fields need" : "field needs"} fixing.`);
    } else {
      status.textContent = "Saved ✓";
      speech.say("Settings saved.");
    }
  } catch (err) {
    status.textContent = "Couldn't save: " + err.message + " — nothing was changed. Try again, or reload the page.";
  } finally {
    button.disabled = false;
  }
});

// ---------------------------------------------------------------- memory & goals
function fillList(ul, items, render, emptyText) {
  ul.innerHTML = "";
  if (!items.length) { const li = document.createElement("li"); li.className = "empty"; li.textContent = emptyText; ul.append(li); }
  items.forEach(item => ul.append(render(item)));
}
function row(text, buttonText, onClick, cls = "") {
  const li = document.createElement("li");
  const span = document.createElement("span");
  span.textContent = text;
  const b = document.createElement("button");
  b.textContent = buttonText;
  b.className = cls;
  b.setAttribute("aria-label", `${buttonText}: ${text}`);
  b.onclick = onClick;
  li.append(span, b);
  return li;
}
function renderMemory(data) {
  $("#prayer-line").textContent = data.prayer || "Prayer times are off or not set up (see Settings).";
  fillList($("#goals"), data.goals, g => row(g.title, "Done ✓", async () => renderMemory(await api(`/api/goals/${g.id}/done`, { method: "POST" }))), "No goals yet.");
  fillList($("#reminders"), data.reminders, r => row(`${r.text} — ${new Date(r.due_at).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}`,
    "Cancel", async () => renderMemory(await api(`/api/reminders/${r.id}`, { method: "DELETE" }))), "No pending reminders.");
  fillList($("#memories"), data.memories, m => row(m.content, "Forget", async () => renderMemory(await api(`/api/memories/${m.id}`, { method: "DELETE" })), "danger"),
    "I don't remember anything about you yet.");
}
async function loadMemory() { renderMemory(await api("/api/memory")); }
$("#goal-form").addEventListener("submit", async e => {
  e.preventDefault();
  const input = $("#goal-input");
  if (!input.value.trim()) return;
  renderMemory(await api("/api/goals", { method: "POST", body: { title: input.value } }));
  input.value = "";
});
$("#memory-form").addEventListener("submit", async e => {
  e.preventDefault();
  const input = $("#memory-input");
  if (!input.value.trim()) return;
  renderMemory(await api("/api/memories", { method: "POST", body: { content: input.value } }));
  input.value = "";
});

// ---------------------------------------------------------------- mic test
$("#mic-test").addEventListener("click", async () => {
  const out = $("#mic-test-result");
  out.textContent = "Recording… speak now for 4 seconds.";
  beep(880, 120);
  try {
    const r = await api("/api/mic-test", { method: "POST", body: { device: form.elements["voice.input_device"].value } });
    out.textContent = `Loudest ${r.loudest} (room ${r.noise}) — ${r.verdict}. Heard: “${r.heard || "nothing"}”`;
    speech.say(r.heard ? `I heard: ${r.heard}` : "I didn't hear any words.", { force: true });
  } catch (e) { out.textContent = e.message; }
});

// ---------------------------------------------------------------- accounts & contacts
let accountData = null;
async function loadAccounts() {
  accountData = await api("/api/accounts");
  const box = $("#accounts");
  box.innerHTML = "";
  if (!accountData.profiles.length) { box.textContent = "No Chrome / Edge / Brave profiles found."; return; }
  accountData.platforms.forEach(platform => {
    const node = $("#platform-tpl").content.firstElementChild.cloneNode(true);
    node.dataset.platform = platform;
    $("legend", node).textContent = platform.replace(/\b\w/g, c => c.toUpperCase());
    const configured = accountData.accounts[platform] || [];
    accountData.profiles.forEach(prof => {
      const saved = configured.find(a => a.profile === prof.profile && a.browser === prof.browser);
      const row = document.createElement("div");
      row.className = "row profile-row";
      row.innerHTML = `<label class="check"><input type="checkbox" class="use"> <span></span></label>
        <label class="grow"><span class="sr-only">Name to say</span><input class="label" placeholder="Name you'll say"></label>
        <label title="For Google sites: which signed-in Google account inside this profile (0 = first)">Google #
          <input class="gindex" type="number" min="0" max="9" value="0" style="width:5em"></label>`;
      $("span", row).textContent = prof.label + (prof.email ? ` — ${prof.email}` : "");
      $(".use", row).checked = !!saved;
      $(".label", row).value = saved ? saved.label : prof.label;
      $(".gindex", row).value = saved ? saved.google_index || 0 : 0;
      Object.assign(row.dataset, { profile: prof.profile, browser: prof.browser, email: prof.email || "" });
      $(".profiles", node).append(row);
    });
    box.append(node);
  });
  const contacts = $("#contacts");
  contacts.innerHTML = "";
  const entries = Object.entries(accountData.contacts || {});
  if (!entries.length) contacts.append(contactRow());
  entries.forEach(([spoken, c]) => contacts.append(contactRow(spoken, c.whatsapp, c.phone)));
}
function contactRow(spoken = "", wa = "", phone = "") {
  const node = $("#contact-tpl").content.firstElementChild.cloneNode(true);
  $(".spoken", node).value = spoken;
  $(".wa-name", node).value = wa;
  $(".phone", node).value = phone || "";
  $(".dictate-contact", node).onclick = () => dictateInto($(".spoken", node));
  $(".delete", node).onclick = () => node.remove();
  return node;
}
$("#add-contact").onclick = () => { const n = contactRow(); $("#contacts").append(n); $(".spoken", n).focus(); };
$("#save-accounts").onclick = async () => {
  const accounts = {};
  $$(".platform", $("#accounts")).forEach(node => {
    const rows = $$(".profile-row", node).filter(r => $(".use", r).checked);
    if (rows.length) accounts[node.dataset.platform] = rows.map(r => ({
      label: $(".label", r).value.trim(), profile: r.dataset.profile, browser: r.dataset.browser,
      email: r.dataset.email, google_index: parseInt($(".gindex", r).value || "0", 10) }));
  });
  const contacts = $$(".contact", $("#contacts")).map(n => ({
    spoken: $(".spoken", n).value.trim(), whatsapp: $(".wa-name", n).value.trim(), phone: $(".phone", n).value.trim() }))
    .filter(c => c.spoken);
  try {
    await api("/api/accounts", { method: "PUT", body: { accounts, contacts } });
    $("#accounts-status").textContent = "Saved ✓";
    speech.say("Saved.");
  } catch (e) { $("#accounts-status").textContent = "Error: " + e.message; }
};

// ---------------------------------------------------------------- voice print
async function loadVoiceprint() {
  const vp = await api("/api/voiceprint");
  fillList($("#vp-profiles"), vp.profiles, name => row(`🔒 ${name}`, "Remove", async () => {
    await api(`/api/voiceprint/${encodeURIComponent(name)}`, { method: "DELETE" }); loadVoiceprint(); }, "danger"),
    "No voices enrolled — everyone can wake me.");
  $("#vp-enrol").hidden = vp.profiles.length >= vp.max;
  const ol = $("#vp-sentences");
  ol.innerHTML = "";
  vp.sentences.forEach((sentence, i) => {
    const li = document.createElement("li");
    li.innerHTML = `<span></span> <button type="button">🎤 Record</button> <span class="muted small"></span>`;
    li.firstChild.textContent = `“${sentence}”`;
    const [btn, out] = [li.querySelector("button"), li.querySelectorAll("span")[1]];
    btn.onclick = async () => {
      btn.disabled = true; out.textContent = "Recording… speak after the beep";
      try { const r = await api("/api/voiceprint/clip", { method: "POST" }); out.textContent = `✓ heard “${r.heard || "…"}” (${r.clips}/3)`; }
      catch (e) { out.textContent = e.message; }
      btn.disabled = false;
    };
    ol.append(li);
  });
}
$("#vp-save").onclick = async () => {
  try {
    const r = await api("/api/voiceprint/save", { method: "POST", body: { name: $("#vp-name").value } });
    $("#vp-status").textContent = `Saved “${r.saved}”. I'll now only respond to enrolled voices.`;
    speech.say("Voice saved.", { force: true });
    loadVoiceprint();
  } catch (e) { $("#vp-status").textContent = e.message; }
};

// ---------------------------------------------------------------- expert usage & background
async function loadExpertUsage() {
  try {
    const r = await api("/api/expert");
    $("#expert-usage").textContent = `This week: $${r.week.cost.toFixed(2)} of $${r.budget} used (${r.week.requests} Claude requests). ` +
      (r.provider === "claude" && !r.credentials_set ? "ANTHROPIC_API_KEY is not set in this session." : "");
  } catch {}
}
$("#autostart").addEventListener("change", async e => {
  try { const r = await api("/api/autostart", { method: "PUT", body: { enabled: e.target.checked } }); $("#bg-message").textContent = r.message; e.target.checked = r.enabled; }
  catch (err) { $("#bg-message").textContent = err.message; }
});
$("#startmenu").addEventListener("change", async e => {
  try { const r = await api("/api/startmenu", { method: "PUT", body: { enabled: e.target.checked } }); $("#bg-message").textContent = r.message; e.target.checked = r.enabled; }
  catch (err) { $("#bg-message").textContent = err.message; }
});
$("#mouse-reading").addEventListener("change", async e => {
  try { const r = await api("/api/mouse-reading", { method: "POST", body: { on: e.target.checked } }); $("#bg-message").textContent = r.message; }
  catch (err) { $("#bg-message").textContent = err.message; e.target.checked = false; }
});

// ---------------------------------------------------------------- start
$("#speak-toggle").checked = store("speak") !== "0";
$("#speak-toggle").addEventListener("change", e => store("speak", e.target.checked ? "1" : "0"));
$("#auto-listen").checked = store("autolisten") === "1";
$("#auto-listen").addEventListener("change", e => store("autolisten", e.target.checked ? "1" : "0"));
addMsg("ai", "Assalam-o-Alaikum! Say “Hey Nova”, press the 🎤 button (or Ctrl+Space), or type below.", "Nova");
selectTab($("#" + (store("tab") || "tab-chat")) || tabs[0]);

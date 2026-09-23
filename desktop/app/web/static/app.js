"use strict";
/* Lyra's window — a voice-first mirror of what Lyra hears and says, plus her settings.
   There is no chat box: all conversation happens by voice (wake word, the orb, or Ctrl+Space)
   and every sound comes from Lyra's own speaker, so the selected voice is always the one heard. */

const TOKEN = document.querySelector('meta[name="token"]').content;
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

// In the native window there is no server: calls go through the in-process bridge.
const NATIVE = !!window.LYRA_NATIVE;
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
  if (body !== undefined) {
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
  return null;
}

function toast(text, ms = 6000) {
  const t = $("#toast");
  t.textContent = text;
  t.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (t.hidden = true), ms);
}

let appName = "Lyra";
let wakePhrase = "hey lyra";
const titleCase = s => s.replace(/\b\w/g, c => c.toUpperCase());

function applyName(name, phrases) {
  appName = name || "Lyra";
  if (phrases && phrases.length) wakePhrase = phrases[0];
  $("#app-name").textContent = appName;
  $("#caption-name").textContent = appName;
  $("#orb-label").textContent = `Talk to ${appName}`;
  document.title = appName;
  $$(".app-name").forEach(n => (n.textContent = appName));
  $$(".wake-name").forEach(n => (n.textContent = titleCase(wakePhrase)));
}

// ---------------------------------------------------------------- theme
const THEMES = ["system", "light", "dark"];
function applyTheme(theme) {
  const root = document.documentElement;
  if (theme === "system") root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", theme);
  const btn = $("#theme-toggle");
  const label = { system: "Theme: follow system", light: "Theme: light", dark: "Theme: dark" }[theme];
  btn.setAttribute("aria-label", label);
  btn.title = label;
  $("use", btn).setAttribute("href", { system: "#i-auto", light: "#i-sun", dark: "#i-moon" }[theme]);
}
let theme = THEMES.includes(store("theme")) ? store("theme") : "system";
applyTheme(theme);
$("#theme-toggle").addEventListener("click", () => {
  theme = THEMES[(THEMES.indexOf(theme) + 1) % THEMES.length];
  store("theme", theme);
  applyTheme(theme);
});

// ---------------------------------------------------------------- navigation
const tabs = $$('[role="tab"]');
const loaders = {
  "tab-voice": () => { loadVoiceSettings(); loadVoices(); },
  "tab-routines": () => loadCommands(),
  "tab-settings": () => loadSettings(),
  "tab-memory": () => loadMemory(),
  "tab-accounts": () => loadAccounts(),
};
function selectTab(tab, { focus = false } = {}) {
  if (!tab) return;
  tabs.forEach(t => {
    const on = t === tab;
    t.setAttribute("aria-selected", on);
    t.tabIndex = on ? 0 : -1;
    $("#" + t.getAttribute("aria-controls")).hidden = !on;
  });
  store("tab", tab.id);
  $("#main").scrollTop = 0;
  if (focus) tab.focus();
  Promise.resolve(loaders[tab.id]?.()).catch(e => toast(e.message));
}
tabs.forEach((t, i) => {
  t.addEventListener("click", () => selectTab(t));
  t.addEventListener("keydown", e => {
    const d = ["ArrowDown", "ArrowRight"].includes(e.key) ? 1 : ["ArrowUp", "ArrowLeft"].includes(e.key) ? -1 : 0;
    if (d) { e.preventDefault(); selectTab(tabs[(i + d + tabs.length) % tabs.length], { focus: true }); }
    else if (e.key === "Home") { e.preventDefault(); selectTab(tabs[0], { focus: true }); }
    else if (e.key === "End") { e.preventDefault(); selectTab(tabs[tabs.length - 1], { focus: true }); }
  });
});
// called by the native app ("open your settings", tray menu, Ctrl+Alt+N)
window.lyraShow = name => {
  const legacy = { chat: "home", commands: "routines" };
  selectTab($("#tab-" + (legacy[name] || name)) || tabs[0]);
};
window.selectTab = selectTab;

// ---------------------------------------------------------------- home: live voice state
const orb = $("#orb");
const PHASES = {
  offline:   ["Waking up…", "Loading speech recognition — this takes a few seconds."],
  idle:      [() => `Say “${titleCase(wakePhrase)}”`, "I'm listening for my name. Just speak — no clicks or typing needed."],
  nowake:    ["Ready when you are", () => `The wake word is off. Click the orb or press Ctrl+Alt+Space to talk.`],
  paused:    ["Listening is paused", "Click the orb or press Ctrl+Alt+Space to talk — that also turns listening back on."],
  call:      ["On a call", "I'm staying quiet. You can still say my name and “record this call”."],
  listening: ["Listening…", "Go ahead, I'm all ears."],
  thinking:  ["Thinking…", "One moment."],
  speaking:  ["Speaking", "Press Esc to stop me."],
};
let lastPhase = "";
function setPhase(phase) {
  if (phase === lastPhase) return;
  lastPhase = phase;
  orb.dataset.phase = ["nowake"].includes(phase) ? "idle" : phase;
  const [title, hint] = PHASES[phase] || PHASES.idle;
  $("#phase-title").textContent = typeof title === "function" ? title() : title;
  $("#phase-hint").textContent = typeof hint === "function" ? hint() : hint;
}

function setStatus(state, text) {
  const pill = $("#status-pill");
  pill.dataset.state = state;
  $("#status-text").textContent = text;
}

function greeting(name) {
  const h = new Date().getHours();
  const part = h < 5 ? "Good night" : h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening";
  return name && name !== "User" ? `${part}, ${name}` : part;
}

orb.addEventListener("click", async () => {
  if (["listening", "thinking", "speaking"].includes(lastPhase)) { stopSpeaking(); return; }
  try { await api("/api/talk", { method: "POST" }); setPhase("listening"); }
  catch (e) { toast(e.message); }
});
async function stopSpeaking() { try { await api("/api/speak/stop", { method: "POST" }); } catch {} }
document.addEventListener("keydown", e => {
  if (e.key === "Escape") stopSpeaking();
  else if (e.ctrlKey && e.code === "Space") { e.preventDefault(); orb.click(); }
});

// conversation captions + history (read-only)
const historyEl = $("#history");
const convo = [];
function addHistory(kind, text) {
  if (!text) return;
  convo.push({ kind, text, time: new Date() });
  while (convo.length > 40) convo.shift();
  const empty = $(".empty", historyEl);
  if (empty) empty.remove();
  const li = document.createElement("li");
  li.className = kind;
  const who = document.createElement("span");
  who.className = "who";
  who.textContent = kind === "you" ? "You" : kind === "lyra" ? appName : "Note";
  const body = document.createElement("span");
  body.className = "body";
  body.textContent = text;
  li.append(who, body);
  historyEl.append(li);
  while (historyEl.children.length > 40) historyEl.firstElementChild.remove();
  historyEl.scrollTop = historyEl.scrollHeight;
}
function caption(kind, text) {
  const box = $("#caption");
  const line = $(kind === "you" ? "#caption-you" : "#caption-lyra");
  $(".text", line).textContent = text;
  line.hidden = false;
  if (kind === "you") $("#caption-lyra").hidden = true;  // a new question clears the old answer
  box.hidden = false;
}
$("#clear-history").addEventListener("click", () => {
  convo.length = 0;
  historyEl.innerHTML = '<li class="empty">Your conversation appears here as you talk.</li>';
  $("#caption").hidden = true;
});

// calls
$("#call-record").onclick = () => { api("/api/calls/decide", { method: "POST", body: { record: true } }).catch(e => toast(e.message)); $("#call-banner").hidden = true; };
$("#call-skip").onclick = () => { api("/api/calls/decide", { method: "POST", body: { record: false } }).catch(() => {}); $("#call-banner").hidden = true; };

// polling: fast while the window is visible so the orb follows the voice closely
let lastEvent = 0;
let polling = false;
let userName = "";
async function pollEvents() {
  if (polling) return;
  polling = true;
  try {
    const { events, status } = await api(`/api/events?after=${lastEvent}`);
    for (const ev of events) {
      lastEvent = Math.max(lastEvent, ev.id);
      if (ev.kind === "heard") { caption("you", ev.message); addHistory("you", ev.message); }
      else if (ev.kind === "said") { caption("lyra", ev.message); addHistory("lyra", ev.message); }
      else if (ev.kind === "note") addHistory("note", ev.message);
      else if (ev.kind === "call") { $("#call-q").textContent = ev.message; $("#call-banner").hidden = false; }
      else { addHistory("note", `${ev.title}: ${ev.message}`); toast(`${ev.title}: ${ev.message}`); }
    }
    if (status.name !== appName || (status.phrases || [])[0] !== wakePhrase) { applyName(status.name, status.phrases); lastPhase = ""; }
    if (!status.in_call) $("#call-banner").hidden = true;

    let phase = status.phase;
    if (phase === "offline") setStatus("offline", "Starting…");
    else if (status.in_call) { phase = "call"; setStatus("call", "On a call — quiet"); }
    else if (phase !== "idle") setStatus("busy", titleCase(phase));
    else if (!status.wake) { phase = "nowake"; setStatus("ready", "Ready"); }
    else if (status.wake_paused) { phase = "paused"; setStatus("paused", "Listening paused"); }
    else setStatus("ready", `Say “${titleCase(wakePhrase)}”`);
    setPhase(phase);
    orb.style.setProperty("--level", (status.level || 0).toFixed(3));
  } catch {
    setStatus("offline", "Reconnecting…");
  } finally {
    polling = false;
  }
}
function schedulePoll() {
  const fast = document.visibilityState === "visible" && !$("#panel-home").hidden;
  setTimeout(() => pollEvents().finally(schedulePoll), fast ? 250 : 1500);
}

// ---------------------------------------------------------------- dictation (for routine phrases and names)
async function listenInto(el, button, { append = false } = {}) {
  button?.classList.add("listening");
  button?.setAttribute("aria-busy", "true");
  try {
    const { text } = await api("/api/listen", { method: "POST" });
    if (!text) { toast("I didn't catch that — try again."); return; }
    el.value = append && el.value.trim() ? el.value.replace(/\s*$/, "\n") + text : text;
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.focus();
  } catch (e) {
    toast(e.message);
  } finally {
    button?.classList.remove("listening");
    button?.removeAttribute("aria-busy");
  }
}

// ---------------------------------------------------------------- settings forms (autosave)
let settingsData = null;
function rangeFill(el) {
  const min = parseFloat(el.min || 0), max = parseFloat(el.max || 1);
  el.style.setProperty("--pct", `${((parseFloat(el.value) - min) / (max - min)) * 100}%`);
  const out = el.dataset.out && document.getElementById(el.dataset.out);
  if (!out) return;
  const v = parseFloat(el.value);
  out.textContent = {
    x: `${v.toFixed(2)}×`, pct: `${Math.round(v * 100)}%`, sec: `${v} s`, num: v.toFixed(2),
    sens: v < 0.75 ? "Relaxed" : v < 0.87 ? "Balanced" : "Strict",
  }[el.dataset.format] || String(v);
}

function fillForm(form, settings) {
  for (const [section, values] of Object.entries(settings)) {
    for (const [key, value] of Object.entries(values)) {
      const el = form.elements[`${section}.${key}`];
      if (!el || el === document.activeElement) continue;
      if (el.type === "checkbox") el.checked = !!value;
      else if (el.tagName === "SELECT") {
        const v = value === null || value === undefined ? "" : String(value);
        if (![...el.options].some(o => o.value === v)) el.add(new Option(v || "Default", v));
        el.value = v;
      } else el.value = Array.isArray(value) ? value.join(", ") : (value ?? "");
      if (el.type === "range") rangeFill(el);
    }
  }
}

function showFieldErrors(form, errors) {
  $$(".field-error", form).forEach(n => n.remove());
  $$("[aria-invalid]", form).forEach(el => { el.removeAttribute("aria-invalid"); el.removeAttribute("aria-describedby"); });
  for (const [field, message] of Object.entries(errors || {})) {
    const el = form.elements[field];
    if (!el) continue;
    el.setAttribute("aria-invalid", "true");
    const note = document.createElement("span");
    note.className = "field-error";
    note.id = `err-${field.replace(".", "-")}`;
    note.textContent = message.charAt(0).toUpperCase() + message.slice(1) + ".";
    el.setAttribute("aria-describedby", note.id);
    el.insertAdjacentElement("afterend", note);
  }
  return Object.keys(errors || {}).length;
}

function saveState(formName, kind, text) {
  $$(`.save-state[data-form="${formName}"]`).forEach(el => { el.dataset.kind = kind; el.textContent = text; });
}

async function saveField(form, body) {
  const name = form.dataset.form;
  saveState(name, "busy", "Saving…");
  try {
    const result = await api("/api/settings", { method: "PUT", body });
    settingsData = result;
    fillForm(form, result.settings);
    const bad = showFieldErrors(form, result.errors);
    saveState(name, bad ? "error" : "ok", bad ? "Check the highlighted field" : "Saved");
    if (result.settings.assistant) applyName(result.settings.assistant.name, null);
    return result;
  } catch (e) {
    saveState(name, "error", `Couldn't save — ${e.message}`);
    throw e;
  }
}

function bindAutosave(form) {
  form.addEventListener("submit", e => e.preventDefault());
  form.addEventListener("input", e => { if (e.target.type === "range") rangeFill(e.target); });
  form.addEventListener("change", e => {
    const el = e.target;
    if (el.dataset.feature) { saveField(form, { features: { [el.dataset.feature]: el.checked } }).catch(() => {}); return; }
    if (!el.name || !el.name.includes(".")) return;
    const [section, key] = el.name.split(".");
    saveField(form, { [section]: { [key]: el.type === "checkbox" ? el.checked : el.value } }).catch(() => {});
  });
}
bindAutosave($("#voice-form"));
bindAutosave($("#settings-form"));

async function fetchSettings() {
  settingsData = await api("/api/settings");
  const s = settingsData.settings;
  userName = s.user.name;
  $("#greeting").textContent = greeting(userName);
  return settingsData;
}

// ---- voice page
async function loadVoiceSettings() {
  const data = await fetchSettings();
  const mics = $("#mic-select");
  mics.innerHTML = "";
  mics.add(new Option("Windows default microphone", ""));
  data.microphones.forEach(m => mics.add(new Option(m.name, String(m.index))));
  fillForm($("#voice-form"), data.settings);
  loadVoiceprint().catch(() => {});
}

let voiceFilter = "all";
let voiceData = null;
let voicePoll = null;
const HUES = { amy: 262, kristin: 318, lessac: 200, harper: 168, jenny: 290, cori: 230, alba: 20, zira: 250,
               ryan: 210, alan: 180, joe: 30, david: 240 };

function renderVoices() {
  const grid = $("#voice-grid");
  const tpl = $("#voice-card-tpl");
  const { voices, counts } = voiceData;
  $("#voice-summary").textContent = `${voices.length} voices · ${counts.female} female · ${counts.male} male`;
  if (grid.children.length !== voices.length) {
    grid.innerHTML = "";
    voices.forEach(v => {
      const node = tpl.content.firstElementChild.cloneNode(true);
      node.dataset.id = v.id;
      const input = $("input", node);
      input.value = v.id;
      input.id = `voice-${v.id}`;
      input.addEventListener("change", () => chooseVoice(v.id));
      $(".avatar", node).textContent = v.name[0];
      $(".avatar", node).style.setProperty("--hue", HUES[v.id] ?? 250);
      $(".voice-name", node).textContent = v.name;
      $(".voice-accent", node).textContent = `${v.gender === "female" ? "Female" : "Male"} · ${v.accent}`;
      $(".voice-style", node).textContent = v.style;
      const preview = $(".preview", node);
      preview.setAttribute("aria-label", `Preview ${v.name}`);
      preview.addEventListener("click", () => previewVoice(v.id, preview));
      const dl = $(".download", node);
      dl.setAttribute("aria-label", `Download ${v.name} (${v.size_mb} MB)`);
      dl.addEventListener("click", async () => {
        try { voiceData = await api(`/api/voices/${v.id}/download`, { method: "POST" }); renderVoices(); }
        catch (e) { toast(e.message); }
      });
      grid.append(node);
    });
  }
  voices.forEach(v => {
    const node = grid.querySelector(`[data-id="${v.id}"]`);
    node.classList.toggle("selected", v.selected);
    $("input", node).checked = v.selected;
    node.hidden = voiceFilter !== "all" && v.gender !== voiceFilter;
    const badge = $(".badge", node);
    const bar = $(".progress", node);
    badge.className = "badge";
    if (v.engine === "system") { badge.textContent = "Windows voice"; badge.classList.add("ready"); }
    else if (v.downloading !== null && v.downloading !== undefined) badge.textContent = `Downloading ${Math.round(v.downloading * 100)}%`;
    else if (v.error) { badge.textContent = "Download failed"; badge.classList.add("error"); badge.title = v.error; }
    else if (v.ready) { badge.textContent = "Neural · ready"; badge.classList.add("ready"); }
    else badge.textContent = `${v.size_mb} MB`;
    const downloading = v.downloading !== null && v.downloading !== undefined;
    $(".download", node).hidden = v.engine !== "neural" || v.ready || downloading || !voiceData.neural_engine;
    $(".preview", node).hidden = !$(".download", node).hidden;
    bar.hidden = !downloading;
    if (!bar.hidden) $("span", bar).style.width = `${Math.max(3, v.downloading * 100)}%`;
  });
  const sel = voices.find(v => v.selected);
  const note = $("#voice-note");
  if (!voiceData.neural_engine) note.textContent = "Neural voices need the piper-tts package (pip install piper-tts). The Windows voices work now.";
  else if (sel && !sel.ready) {
    const fallback = sel.gender === "male" ? "David" : "Zira";
    const downloading = sel.downloading !== null && sel.downloading !== undefined;
    note.textContent = downloading ? `${sel.name} is downloading — until it's ready I speak with the ${fallback} Windows voice.`
      : sel.error ? `${sel.name} couldn't download. Check the internet connection and press Download again.`
      : `${sel.name} isn't on this computer yet — press Download (${sel.size_mb} MB). Until then I speak with ${fallback}.`;
  }
  else note.textContent = "Neural voices run entirely on this computer once downloaded.";
  const busy = voices.some(v => v.downloading !== null && v.downloading !== undefined);
  clearTimeout(voicePoll);
  if (busy && !$("#panel-voice").hidden) voicePoll = setTimeout(loadVoices, 700);
}

async function loadVoices() {
  voiceData = await api("/api/voices");
  renderVoices();
}

async function chooseVoice(id) {
  const v = voiceData.voices.find(x => x.id === id);
  voiceData.voices.forEach(x => (x.selected = x.id === id));
  renderVoices();
  try {
    await saveField($("#voice-form"), { voice: { tts_voice: id } });
    if (v && !v.ready && v.engine === "neural") voiceData = await api(`/api/voices/${id}/download`, { method: "POST" });
    renderVoices();
    const r = await api(`/api/voices/${id}/preview`, { method: "POST" });
    if (r.message) toast(r.message);
  } catch (e) { toast(e.message); }
}

async function previewVoice(id, button) {
  button.setAttribute("aria-busy", "true");
  try {
    const r = await api(`/api/voices/${id}/preview`, { method: "POST" });
    if (r.message) toast(r.message);
  } catch (e) { toast(e.message); }
  setTimeout(() => button.removeAttribute("aria-busy"), 1200);
}

$$(".segmented [data-filter]").forEach(b => b.addEventListener("click", () => {
  voiceFilter = b.dataset.filter;
  $$(".segmented [data-filter]").forEach(x => x.setAttribute("aria-pressed", x === b));
  if (voiceData) renderVoices();
}));

$("#mic-test").addEventListener("click", async e => {
  const btn = e.currentTarget, out = $("#mic-test-result");
  btn.setAttribute("aria-busy", "true");
  btn.disabled = true;
  out.textContent = "Recording — speak now for 4 seconds…";
  try {
    const r = await api("/api/mic-test", { method: "POST", body: { device: $("#voice-form").elements["voice.input_device"].value } });
    out.textContent = `Level: ${r.verdict}. Heard: “${r.heard || "nothing"}”`;
  } catch (err) { out.textContent = err.message; }
  btn.disabled = false;
  btn.removeAttribute("aria-busy");
});

// ---- voice print
function listRow(text, buttonText, onClick, { danger = false } = {}) {
  const li = document.createElement("li");
  const span = document.createElement("span");
  span.textContent = text;
  const b = document.createElement("button");
  b.type = "button";
  b.className = `btn btn-text btn-sm${danger ? " danger" : ""}`;
  b.textContent = buttonText;
  b.setAttribute("aria-label", `${buttonText}: ${text}`);
  b.onclick = onClick;
  li.append(span, b);
  return li;
}
function fillList(ul, items, render, emptyText) {
  ul.innerHTML = "";
  if (!items.length) { const li = document.createElement("li"); li.className = "empty"; li.textContent = emptyText; ul.append(li); }
  items.forEach(item => ul.append(render(item)));
}

async function loadVoiceprint() {
  const vp = await api("/api/voiceprint");
  fillList($("#vp-profiles"), vp.profiles, name => listRow(name, "Remove", async () => {
    await api(`/api/voiceprint/${encodeURIComponent(name)}`, { method: "DELETE" }); loadVoiceprint();
  }, { danger: true }), "No voices enrolled yet — everyone can talk to me.");
  $("#vp-enrol").hidden = vp.profiles.length >= vp.max;
  const ol = $("#vp-sentences");
  ol.innerHTML = "";
  vp.sentences.forEach(sentence => {
    const li = document.createElement("li");
    const text = document.createElement("span");
    text.textContent = `“${sentence}”`;
    const row = document.createElement("div");
    row.className = "row";
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "btn btn-secondary btn-sm";
    btn.innerHTML = '<svg class="icon"><use href="#i-mic"/></svg>Record';
    const out = document.createElement("span");
    out.className = "hint";
    btn.onclick = async () => {
      btn.disabled = true; out.textContent = "Recording — speak after the beep…";
      try { const r = await api("/api/voiceprint/clip", { method: "POST" }); out.textContent = `✓ Heard “${r.heard || "…"}” (${r.clips}/3)`; }
      catch (e) { out.textContent = e.message; }
      btn.disabled = false;
    };
    row.append(btn, out);
    li.append(text, row);
    ol.append(li);
  });
}
$("#vp-save").onclick = async () => {
  try {
    const r = await api("/api/voiceprint/save", { method: "POST", body: { name: $("#vp-name").value } });
    $("#vp-status").textContent = `Saved “${r.saved}”. I'll now only respond to enrolled voices.`;
    loadVoiceprint();
  } catch (e) { $("#vp-status").textContent = e.message; }
};

// ---- settings page
async function loadSettings() {
  const data = await fetchSettings();
  const names = data.models.filter(m => !/embed/.test(m));
  const current = data.settings.llm.chat_model;
  if (!names.includes(current)) names.unshift(current);
  const models = $("#model-select");
  models.innerHTML = "";
  names.forEach(m => models.add(new Option(m, m)));
  const tmodels = $("#translate-model-select");
  tmodels.innerHTML = "";
  tmodels.add(new Option("Same as conversation model", ""));
  names.forEach(m => tmodels.add(new Option(m, m)));
  const feats = $("#features");
  feats.innerHTML = "";
  data.features.forEach(f => {
    const label = document.createElement("label");
    label.className = "switch-row";
    const text = document.createElement("span");
    const name = document.createElement("span");
    name.className = "switch-label";
    name.textContent = f.label;
    text.append(name);
    const input = document.createElement("input");
    input.type = "checkbox";
    input.setAttribute("role", "switch");
    input.dataset.feature = f.key;
    input.checked = f.enabled;
    label.append(text, input);
    feats.append(label);
  });
  fillForm($("#settings-form"), data.settings);
  loadExpertUsage();
  api("/api/autostart").then(r => ($("#autostart").checked = r.enabled)).catch(() => {});
  api("/api/startmenu").then(r => ($("#startmenu").checked = r.enabled)).catch(() => {});
}

async function loadExpertUsage() {
  try {
    const r = await api("/api/expert");
    $("#expert-usage").textContent = `This week: $${r.week.cost.toFixed(2)} of $${r.budget} (${r.week.requests} Claude requests).` +
      (r.provider === "claude" && !r.credentials_set ? " ANTHROPIC_API_KEY isn't set for this session." : "");
  } catch {}
}
function bindToggle(id, path, method, key) {
  $(id).addEventListener("change", async e => {
    e.stopPropagation();
    try {
      const r = await api(path, { method, body: { [key]: e.target.checked } });
      $("#bg-message").textContent = r.message;
      if ("enabled" in r) e.target.checked = r.enabled;
    } catch (err) { $("#bg-message").textContent = err.message; e.target.checked = !e.target.checked; }
  });
}
bindToggle("#autostart", "/api/autostart", "PUT", "enabled");
bindToggle("#startmenu", "/api/startmenu", "PUT", "enabled");
bindToggle("#mouse-reading", "/api/mouse-reading", "POST", "on");

// ---------------------------------------------------------------- routines
const commandsEl = $("#commands");
const stepLines = el => el.value.split("\n").map(s => s.trim()).filter(Boolean);
function commandCard(phrase = "", steps = []) {
  const node = $("#command-tpl").content.firstElementChild.cloneNode(true);
  const phraseEl = $(".phrase", node), stepsEl = $(".steps", node);
  phraseEl.value = phrase;
  stepsEl.value = steps.join("\n");
  const dirty = () => { $("#commands-status").dataset.kind = ""; $("#commands-status").textContent = "Unsaved changes"; };
  phraseEl.addEventListener("input", dirty);
  stepsEl.addEventListener("input", dirty);
  $(".dictate-phrase", node).onclick = e => listenInto(phraseEl, e.currentTarget);
  $(".dictate-step", node).onclick = e => listenInto(stepsEl, e.currentTarget, { append: true });
  $(".delete", node).onclick = () => { node.remove(); dirty(); };
  $(".check-steps", node).onclick = async () => {
    const { steps } = await api("/api/commands/preview", { method: "POST", body: { steps: stepLines(stepsEl) } });
    const ul = $(".preview-list", node);
    ul.innerHTML = "";
    steps.forEach(s => {
      const li = document.createElement("li");
      li.textContent = s.intent === "chat"
        ? `? “${s.step}” — not a command I know; I'd answer it as a question`
        : `✓ “${s.step}” → ${s.intent.replace(/_/g, " ")}`;
      if (s.intent === "chat") li.className = "chat";
      ul.append(li);
    });
  };
  $(".try", node).onclick = async () => {
    if (!phraseEl.value.trim()) { toast("Give the routine a phrase first."); phraseEl.focus(); return; }
    try {
      await saveCommands();
      await api("/api/run", { method: "POST", body: { text: phraseEl.value } });
      selectTab($("#tab-home"));
    } catch (e) { toast(e.message); }
  };
  return node;
}
async function loadCommands() {
  const { commands } = await api("/api/commands");
  commandsEl.innerHTML = "";
  if (!commands.length) commandsEl.append(commandCard());
  commands.forEach(c => commandsEl.append(commandCard(c.phrase, c.steps)));
  $("#commands-status").textContent = "";
}
async function saveCommands() {
  const commands = $$(".command", commandsEl).map(n => ({ phrase: $(".phrase", n).value.trim(), steps: stepLines($(".steps", n)) }))
    .filter(c => c.phrase && c.steps.length);
  const status = $("#commands-status");
  await api("/api/commands", { method: "PUT", body: { commands } });
  status.dataset.kind = "ok";
  status.textContent = `Saved ${commands.length} routine${commands.length === 1 ? "" : "s"}`;
}
$("#add-command").onclick = () => { const n = commandCard(); commandsEl.append(n); $(".phrase", n).focus(); };
$("#save-commands").onclick = () => saveCommands().catch(e => { $("#commands-status").dataset.kind = "error"; $("#commands-status").textContent = e.message; });

// ---------------------------------------------------------------- accounts & contacts
async function loadAccounts() {
  const data = await api("/api/accounts");
  const box = $("#accounts");
  box.innerHTML = "";
  if (!data.profiles.length) {
    const p = document.createElement("p");
    p.className = "hint";
    p.textContent = "No Chrome, Edge or Brave profiles found on this computer.";
    box.append(p);
  }
  data.profiles.length && data.platforms.forEach(platform => {
    const node = $("#platform-tpl").content.firstElementChild.cloneNode(true);
    node.dataset.platform = platform;
    $("legend", node).textContent = titleCase(platform);
    const configured = data.accounts[platform] || [];
    data.profiles.forEach(prof => {
      const saved = configured.find(a => a.profile === prof.profile && a.browser === prof.browser);
      const row = document.createElement("div");
      row.className = "profile-row";
      row.innerHTML = `<label class="check"><input type="checkbox" class="use"> <span></span></label>
        <label><span class="sr-only">Name you'll say</span><input class="label" placeholder="Name you'll say"></label>
        <label title="For Google sites: which signed-in Google account in this profile (0 = first)"><span class="sr-only">Google account number</span>
          <input class="gindex" type="number" min="0" max="9" value="0"></label>`;
      $(".check span", row).textContent = prof.label + (prof.email ? ` — ${prof.email}` : "");
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
  const entries = Object.entries(data.contacts || {});
  if (!entries.length) contacts.append(contactRow());
  entries.forEach(([spoken, c]) => contacts.append(contactRow(spoken, c.whatsapp, c.phone)));
  $("#accounts-status").textContent = "";
}
function contactRow(spoken = "", wa = "", phone = "") {
  const node = $("#contact-tpl").content.firstElementChild.cloneNode(true);
  $(".spoken", node).value = spoken;
  $(".wa-name", node).value = wa;
  $(".phone", node).value = phone || "";
  $(".dictate-contact", node).onclick = e => listenInto($(".spoken", node), e.currentTarget);
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
  const status = $("#accounts-status");
  try {
    await api("/api/accounts", { method: "PUT", body: { accounts, contacts } });
    status.dataset.kind = "ok";
    status.textContent = "Saved";
  } catch (e) { status.dataset.kind = "error"; status.textContent = e.message; }
};

// ---------------------------------------------------------------- memory
function renderMemory(data) {
  $("#prayer-line").textContent = data.prayer || "Prayer times are off or your location isn't set — say “I live in Karachi”.";
  fillList($("#goals"), data.goals, g => listRow(g.title, "Mark done",
    async () => renderMemory(await api(`/api/goals/${g.id}/done`, { method: "POST" }))), "No goals yet.");
  fillList($("#reminders"), data.reminders, r => listRow(
    `${r.text} — ${new Date(r.due_at).toLocaleString([], { weekday: "short", hour: "numeric", minute: "2-digit" })}`,
    "Cancel", async () => renderMemory(await api(`/api/reminders/${r.id}`, { method: "DELETE" }))), "No reminders pending.");
  fillList($("#memories"), data.memories, m => listRow(m.content, "Forget",
    async () => renderMemory(await api(`/api/memories/${m.id}`, { method: "DELETE" })), { danger: true }),
    "Nothing yet — I learn as we talk.");
}
async function loadMemory() { renderMemory(await api("/api/memory")); }

// ---------------------------------------------------------------- start
$("#greeting").textContent = greeting("");
applyName("Lyra", ["hey lyra"]);
setPhase("offline");
fetchSettings().catch(() => {});
selectTab($("#" + (store("tab") || "tab-home")) || tabs[0]);
pollEvents().finally(schedulePoll);
document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") pollEvents(); });

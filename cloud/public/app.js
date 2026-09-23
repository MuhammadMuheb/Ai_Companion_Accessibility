"use strict";

// ---- storage (per browser; the page works without it) -------------------------------------
const store = {
  get(key, fallback) {
    try { const v = localStorage.getItem("nova." + key); return v === null ? fallback : JSON.parse(v); }
    catch { return fallback; }
  },
  set(key, value) { try { localStorage.setItem("nova." + key, JSON.stringify(value)); } catch { /* private mode */ } },
};

const MAX_HISTORY = 40;
const $ = (id) => document.getElementById(id);
const els = {
  log: $("log"), empty: $("empty"), input: $("composer-input"), form: $("composer"), send: $("send-btn"),
  mic: $("mic-btn"), speak: $("speak-btn"), clear: $("clear-btn"), statusDot: $("status-dot"),
  statusText: $("status-text"), memoryBtn: $("memory-btn"), memoryCount: $("memory-count"),
  memoryDialog: $("memory-dialog"), memoryList: $("memory-list"), memoryInput: $("memory-input"),
  memoryAdd: $("memory-add"), codeDialog: $("code-dialog"), codeInput: $("code-input"),
  download: $("download-btn"), downloadNote: $("download-note"), theme: $("theme-btn"),
};

let history = store.get("chat", []);
let memories = store.get("memories", []);
let speakOn = store.get("speak", false);
let config = { chat_enabled: false, access_code_required: true, download_url: "" };
let busy = false;

// ---- rendering ------------------------------------------------------------------------------
function escapeHtml(s) {
  return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// Just enough markdown for chat: fenced code, inline code, bold. Everything is escaped first.
function renderMarkdown(text) {
  const parts = text.split(/```(\w*)\n?([\s\S]*?)(?:```|$)/g);
  let html = "";
  for (let i = 0; i < parts.length; i += 3) {
    html += escapeHtml(parts[i])
      .replace(/`([^`\n]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
    if (i + 2 < parts.length) html += `<pre><code>${escapeHtml(parts[i + 2])}</code></pre>`;
  }
  return html;
}

function addBubble(role, text) {
  els.empty.hidden = true;
  const div = document.createElement("div");
  div.className = "msg " + role;
  if (role === "assistant") div.innerHTML = renderMarkdown(text);
  else div.textContent = text;
  els.log.appendChild(div);
  els.log.scrollTop = els.log.scrollHeight;
  return div;
}

function renderHistory() {
  els.log.querySelectorAll(".msg").forEach((m) => m.remove());
  els.empty.hidden = history.length > 0;
  history.forEach((m) => addBubble(m.role, m.content));
}

function setStatus(kind, text) {
  els.statusDot.className = "dot " + kind;
  els.statusText.textContent = text;
}

// ---- memory ---------------------------------------------------------------------------------
function saveMemories() {
  store.set("memories", memories);
  els.memoryCount.textContent = memories.length;
  els.memoryList.innerHTML = "";
  if (!memories.length) {
    els.memoryList.innerHTML = '<li class="none">Nothing yet. Say “remember that …” in the chat or add one below.</li>';
    return;
  }
  memories.forEach((m, i) => {
    const li = document.createElement("li");
    li.textContent = m;
    const del = document.createElement("button");
    del.type = "button";
    del.setAttribute("aria-label", "Forget: " + m);
    del.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6 6 18"/></svg>';
    del.onclick = () => { memories.splice(i, 1); saveMemories(); };
    li.appendChild(del);
    els.memoryList.appendChild(li);
  });
}

function addMemory(text) {
  const fact = text.trim().replace(/[.!]+$/, "");
  if (!fact) return;
  memories = memories.filter((m) => m.toLowerCase() !== fact.toLowerCase());
  memories.push(fact);
  memories = memories.slice(-50);
  saveMemories();
}

// "remember that I ..." / "yaad rakho ke ..." — saved locally, then still answered by Nova
const REMEMBER = /^(?:please\s+)?(?:remember|note)\s+(?:that\s+)?(.{3,})$|^yaad\s+rakh(?:o|na)\s+(?:ke|ki|k)?\s*(.{3,})$/i;

// ---- speech out -----------------------------------------------------------------------------
let spoken = 0;
function speakNew(text, final) {
  if (!speakOn || !("speechSynthesis" in window)) return;
  const rest = text.slice(spoken);
  // speak whole sentences as soon as they are complete, so the first one starts right away
  const match = final ? rest : (rest.match(/^[\s\S]*[.!?؟।\n]\s/) || [""])[0];
  if (!match.trim()) return;
  spoken += match.length;
  const clean = match.replace(/```[\s\S]*?```/g, " code block ").replace(/[*_`#>]/g, "");
  const u = new SpeechSynthesisUtterance(clean);
  u.lang = document.documentElement.lang || navigator.language;
  speechSynthesis.speak(u);
}

function setSpeak(on) {
  speakOn = on;
  store.set("speak", on);
  els.speak.setAttribute("aria-pressed", String(on));
  if (!on && "speechSynthesis" in window) speechSynthesis.cancel();
}

// ---- chat -----------------------------------------------------------------------------------
async function askCode() {
  els.codeInput.value = "";
  els.codeDialog.showModal();
  return new Promise((resolve) => {
    els.codeDialog.addEventListener("close", () => {
      const ok = els.codeDialog.returnValue === "ok" && els.codeInput.value.trim();
      if (ok) store.set("code", els.codeInput.value.trim());
      resolve(Boolean(ok));
    }, { once: true });
  });
}

async function send(text) {
  text = text.trim();
  if (!text || busy) return;
  if (config.access_code_required && !store.get("code", "")) {
    if (!(await askCode())) return;
  }
  const remembered = text.match(REMEMBER);
  if (remembered) addMemory(remembered[1] || remembered[2]);

  busy = true;
  els.send.disabled = true;
  els.input.value = "";
  autosize();
  history.push({ role: "user", content: text });
  history = history.slice(-MAX_HISTORY);
  if (history[0].role !== "user") history.shift();
  store.set("chat", history);
  addBubble("user", text);
  const bubble = addBubble("assistant", "");
  bubble.classList.add("typing");
  spoken = 0;
  if ("speechSynthesis" in window) speechSynthesis.cancel();

  let reply = "";
  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Nova-Access-Code": store.get("code", "") },
      body: JSON.stringify({
        messages: history,
        memories,
        local_time: new Date().toLocaleString(undefined, { dateStyle: "full", timeStyle: "short" }),
      }),
    });
    if (res.status === 401) {
      store.set("code", "");
      throw new Error("Wrong access code — try again.");
    }
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(typeof err.detail === "string" ? err.detail : `Server error (${res.status}).`);
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const events = buffer.split("\n\n");
      buffer = events.pop();
      for (const ev of events) {
        if (!ev.startsWith("data: ")) continue;
        const data = JSON.parse(ev.slice(6));
        if (data.error) throw new Error(data.error);
        if (data.t) {
          reply += data.t;
          bubble.innerHTML = renderMarkdown(reply);
          els.log.scrollTop = els.log.scrollHeight;
          speakNew(reply, false);
        }
      }
    }
    speakNew(reply, true);
  } catch (e) {
    const msg = e instanceof TypeError ? "Couldn't reach the server — check your connection." : e.message;
    if (!reply) { bubble.remove(); history.pop(); store.set("chat", history); }
    addBubble("error", msg);
  } finally {
    bubble.classList.remove("typing");
    if (reply.trim()) {
      history.push({ role: "assistant", content: reply.trim() });
      store.set("chat", history);
    }
    busy = false;
    els.send.disabled = false;
    els.input.focus();
  }
}

// ---- voice input (browser speech recognition: Chrome, Edge, Safari) --------------------------
const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
let recognizer = null;
function toggleMic() {
  if (!Recognition) {
    addBubble("error", "Voice input needs Chrome, Edge or Safari. The desktop app has full offline voice.");
    return;
  }
  if (recognizer) { recognizer.stop(); return; }
  recognizer = new Recognition();
  recognizer.lang = navigator.language || "en-US";
  recognizer.interimResults = true;
  recognizer.continuous = false;
  let finalText = "";
  recognizer.onresult = (ev) => {
    let interim = "";
    for (let i = ev.resultIndex; i < ev.results.length; i++) {
      if (ev.results[i].isFinal) finalText += ev.results[i][0].transcript;
      else interim += ev.results[i][0].transcript;
    }
    els.input.value = (finalText + interim).trim();
    autosize();
  };
  recognizer.onerror = (ev) => {
    if (ev.error === "not-allowed") addBubble("error", "Microphone access was blocked. Allow it in the address bar.");
  };
  recognizer.onend = () => {
    recognizer = null;
    els.mic.setAttribute("aria-pressed", "false");
    if (finalText.trim()) send(finalText);
  };
  els.mic.setAttribute("aria-pressed", "true");
  recognizer.start();
}

// ---- wiring ---------------------------------------------------------------------------------
function autosize() {
  els.input.style.height = "auto";
  els.input.style.height = Math.min(els.input.scrollHeight, 180) + "px";
}

els.form.addEventListener("submit", (e) => { e.preventDefault(); send(els.input.value); });
els.input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(els.input.value); }
});
els.input.addEventListener("input", autosize);
els.mic.addEventListener("click", toggleMic);
els.speak.addEventListener("click", () => setSpeak(!speakOn));
els.clear.addEventListener("click", () => {
  history = [];
  store.set("chat", history);
  if ("speechSynthesis" in window) speechSynthesis.cancel();
  renderHistory();
  els.input.focus();
});
document.querySelectorAll(".suggestion").forEach((b) => b.addEventListener("click", () => send(b.textContent)));
els.memoryBtn.addEventListener("click", () => els.memoryDialog.showModal());
els.memoryAdd.addEventListener("click", () => { addMemory(els.memoryInput.value); els.memoryInput.value = ""; els.memoryInput.focus(); });
els.memoryInput.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); els.memoryAdd.click(); } });
els.theme.addEventListener("click", () => {
  const dark = document.documentElement.dataset.theme
    ? document.documentElement.dataset.theme === "dark"
    : matchMedia("(prefers-color-scheme: dark)").matches;
  document.documentElement.dataset.theme = dark ? "light" : "dark";
  try { localStorage.setItem("nova.theme", document.documentElement.dataset.theme); } catch { /* ignore */ }
});

async function loadConfig() {
  try {
    const res = await fetch("/api/config");
    config = await res.json();
    if (config.chat_enabled) setStatus("ok", "Online · replies stream instantly");
    else setStatus("err", "Chat is not set up on this server yet");
  } catch {
    setStatus("err", "Offline — the server can't be reached");
  }
  if (config.download_url) {
    els.download.href = config.download_url;
  } else {
    els.download.setAttribute("aria-disabled", "true");
    els.downloadNote.textContent = "Download link not configured yet (set NOVA_DOWNLOAD_URL).";
  }
}

setSpeak(speakOn);
saveMemories();
renderHistory();
loadConfig();

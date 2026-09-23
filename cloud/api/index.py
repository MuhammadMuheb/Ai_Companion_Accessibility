"""Nova Cloud — the hosted API behind the web chat (runs as one Vercel Python function).

The desktop app stays local (Ollama, microphone, tray, automation). This function only gives the
web page a fast chat with Claude, using the deployer's own ANTHROPIC_API_KEY.

Environment variables (set them in Vercel > Project > Settings > Environment Variables):
    ANTHROPIC_API_KEY   required for chat
    NOVA_ACCESS_CODE    visitors must type this code before chatting — protects your API bill
    NOVA_PUBLIC         "1" allows chat without an access code (anyone can spend your credits)
    NOVA_MODEL          default claude-opus-5
    NOVA_EFFORT         low | medium | high (default low: shortest wait before the first word)
    NOVA_DOWNLOAD_URL   where the "Download for Windows" button points (e.g. a GitHub release .zip)
    NOVA_GITHUB_REPO    owner/repo — used for the download link when NOVA_DOWNLOAD_URL is empty
    NOVA_RATE_LIMIT     messages per IP per 10 minutes (default 30; per server instance, best effort)
"""

from __future__ import annotations

import hmac
import json
import os
import time
from collections import defaultdict, deque
from datetime import datetime, timezone

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

MAX_MESSAGES = 40
MAX_CHARS = 4000
MAX_MEMORIES = 50
RATE_WINDOW = 600

# Stable text first so the API's prompt cache can reuse it; per-user details go in a second block.
SYSTEM_PROMPT = """You are Nova, a warm, patient personal AI companion and a sharp IT expert.
You help with daily life, focus, learning, programming and computer problems. Answers may be read
aloud, so keep them short, clear and conversational (2-4 sentences) unless the user asks for detail
or for code. Use markdown code blocks only for code. Reply in the language the user writes in
(English, Urdu, Hindi or Roman Urdu).

This is the web version. You cannot open apps, read the screen or send messages here; when the user
asks for that, say it works in the Nova desktop app (the Download section of this page)."""


class ChatMessage(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(min_length=1, max_length=MAX_CHARS)


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1, max_length=MAX_MESSAGES)
    memories: list[str] = Field(default_factory=list, max_length=MAX_MEMORIES)
    local_time: str = Field(default="", max_length=80)


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, "").strip() or default


def download_url() -> str:
    url = env("NOVA_DOWNLOAD_URL")
    if url:
        return url
    repo = env("NOVA_GITHUB_REPO")
    return f"https://github.com/{repo}/archive/refs/heads/main.zip" if repo else ""


def access_required() -> bool:
    return env("NOVA_PUBLIC") != "1"


def check_access(code: str | None) -> None:
    if not access_required():
        return
    expected = env("NOVA_ACCESS_CODE")
    if not expected:
        raise HTTPException(503, "Chat is switched off: the owner hasn't set NOVA_ACCESS_CODE (or NOVA_PUBLIC=1).")
    if not code or not hmac.compare_digest(code.encode(), expected.encode()):
        raise HTTPException(401, "Wrong access code.")


_hits: dict[str, deque] = defaultdict(deque)


def check_rate(ip: str) -> None:
    limit = int(env("NOVA_RATE_LIMIT", "30"))
    now = time.monotonic()
    hits = _hits[ip]
    while hits and now - hits[0] > RATE_WINDOW:
        hits.popleft()
    if len(hits) >= limit:
        raise HTTPException(429, "Too many messages — wait a few minutes.")
    hits.append(now)


def build_request(body: ChatRequest) -> dict:
    messages = [m.model_dump() for m in body.messages]
    if messages[0]["role"] != "user" or messages[-1]["role"] != "user":
        raise HTTPException(422, "The conversation must start and end with a user message.")
    when = body.local_time or datetime.now(timezone.utc).strftime("%A, %d %B %Y (UTC)")
    context = f"User's local date and time: {when}"
    memories = [m.strip()[:300] for m in body.memories if m.strip()]
    if memories:
        context += "\nWhat you remember about the user (they saved these):\n" + "\n".join(f"- {m}" for m in memories)
    return {
        "model": env("NOVA_MODEL", "claude-opus-5"),
        "max_tokens": 8000,
        "system": [
            {"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": context},
        ],
        "messages": messages,
        "output_config": {"effort": env("NOVA_EFFORT", "low")},
        # if the model declines, the API retries on Anthropic's recommended fallback model
        "betas": ["server-side-fallback-2026-07-01"],
        "fallbacks": "default",
    }


_client = None


def get_client():
    global _client
    if _client is None:
        import anthropic

        _client = anthropic.Anthropic()
    return _client


def sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def stream_reply(params: dict):
    import anthropic

    try:
        with get_client().beta.messages.stream(**params) as stream:
            for text in stream.text_stream:
                yield sse({"t": text})
            final = stream.get_final_message()
        if final.stop_reason == "refusal":
            yield sse({"error": "Nova can't help with that request."})
        yield sse({"done": True, "stop": final.stop_reason})
    except anthropic.AuthenticationError:
        yield sse({"error": "The server's ANTHROPIC_API_KEY is missing or invalid."})
    except anthropic.RateLimitError:
        yield sse({"error": "The AI service is busy right now — try again in a moment."})
    except anthropic.APIStatusError as e:
        yield sse({"error": f"AI service error ({e.status_code})."})
    except anthropic.APIConnectionError:
        yield sse({"error": "Couldn't reach the AI service."})


app = FastAPI(title="Nova Cloud", docs_url=None, redoc_url=None, openapi_url=None)


@app.get("/api/config")
def config() -> dict:
    return {
        "chat_enabled": bool(env("ANTHROPIC_API_KEY")) and (not access_required() or bool(env("NOVA_ACCESS_CODE"))),
        "access_code_required": access_required(),
        "download_url": download_url(),
        "model": env("NOVA_MODEL", "claude-opus-5"),
    }


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


@app.post("/api/chat")
def chat(body: ChatRequest, request: Request, x_nova_access_code: str | None = Header(default=None)):
    check_access(x_nova_access_code)
    forwarded = request.headers.get("x-forwarded-for", "")
    check_rate(forwarded.split(",")[0].strip() or (request.client.host if request.client else "?"))
    params = build_request(body)
    return StreamingResponse(stream_reply(params), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

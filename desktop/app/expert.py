"""Expert mode: senior-engineer answers for web development, UI/UX, databases and
architecture, plus research from the web and project scaffolding.

Backends:
- "local" (default): the best installed Ollama model — free and private, but a small CPU
  model is nowhere near "top-tier"; answers are shorter and less reliable.
- "claude": Claude via the Anthropic API, if you have set up credentials yourself
  (ANTHROPIC_API_KEY or `ant auth login`). Lyra never asks for or stores the key.

A weekly spending cap (Settings → Expert) is enforced from the token counts the API reports;
once it is reached Lyra falls back to the local model until the next week starts.
"""

from __future__ import annotations

import re
import sqlite3
import threading
from datetime import date, timedelta

from app.config import get_config
from app.llm import LLMError, OllamaClient, get_llm
from app.logger import get_logger

log = get_logger(__name__)

EXPERT_SYSTEM = """You are Lyra's expert mode: a principal software engineer, web architect and product designer.
Expertise: Next.js (App Router, Server Components, Server Actions), React, TypeScript, Tailwind, UI/UX and
accessibility (WCAG), Node, Python/FastAPI, REST/GraphQL, PostgreSQL/MySQL/SQLite/MongoDB/Redis schema
design, auth, caching, testing, CI/CD, Docker, Vercel/cloud deployment, performance and security.
Answer like a senior reviewer: state the recommendation first, then the reasoning and trade-offs.
For "blueprint"/"architecture" requests use sections: Goal, Stack (with why), Architecture, Data model,
Key screens/UX, API, Security, Deployment, Build steps. Give runnable commands and code when useful.
If something is uncertain or version-dependent, say so. The user may be listening rather than reading,
so keep prose plain and put code in fenced blocks."""

# Words that mark a question as technical enough for expert mode
TECH = re.compile(
    r"\b(next\.?js|react|vue|svelte|angular|typescript|javascript|node|tailwind|css|html|ui|ux|figma|design system|"
    r"database|schema|sql|postgres|mysql|mongodb|redis|prisma|supabase|firebase|api|rest|graphql|backend|frontend|"
    r"architecture|blueprint|microservice|docker|kubernetes|deploy|vercel|aws|auth|oauth|jwt|seo|performance|"
    r"framework|full.?stack|web ?app|website|landing page|component|state management|caching|devops|ci/cd)\b", re.I)

PRICES = {  # USD per million tokens (input, output) — Anthropic list prices
    "claude-opus-5": (5.0, 25.0), "claude-opus-5-5": (4.0, 20.0), "claude-sonnet-5": (2.0, 10.0),
    "claude-haiku-4-5": (1.0, 5.0), "claude-fable-5-1": (10.0, 50.0),
}
LOCAL_PREFERENCE = ("qwen2.5-coder:7b", "qwen2.5:7b", "llama3.1:8b", "gemma3:4b", "qwen2.5:3b")


def is_technical(text: str) -> bool:
    return bool(TECH.search(text))


def week_start(today: date | None = None) -> str:
    today = today or date.today()
    return (today - timedelta(days=today.weekday())).isoformat()


class UsageLedger:
    """Per-week token and cost totals, stored in the memory database."""

    def __init__(self, path=None):
        cfg = get_config()
        self.path = path or cfg.path(cfg.storage.memory_db)
        self._lock = threading.Lock()
        with sqlite3.connect(self.path) as con:
            con.execute("CREATE TABLE IF NOT EXISTS api_usage (week TEXT, model TEXT, purpose TEXT, "
                        "input_tokens INTEGER, output_tokens INTEGER, cost REAL, at TEXT DEFAULT CURRENT_TIMESTAMP)")

    def add(self, model: str, purpose: str, input_tokens: int, output_tokens: int) -> float:
        pin, pout = PRICES.get(model, (5.0, 25.0))
        cost = input_tokens / 1e6 * pin + output_tokens / 1e6 * pout
        with self._lock, sqlite3.connect(self.path) as con:
            con.execute("INSERT INTO api_usage (week, model, purpose, input_tokens, output_tokens, cost) VALUES (?,?,?,?,?,?)",
                        (week_start(), model, purpose, input_tokens, output_tokens, cost))
        return cost

    def this_week(self) -> dict:
        with sqlite3.connect(self.path) as con:
            row = con.execute("SELECT COALESCE(SUM(input_tokens),0), COALESCE(SUM(output_tokens),0), "
                              "COALESCE(SUM(cost),0), COUNT(*) FROM api_usage WHERE week=?", (week_start(),)).fetchone()
        return {"input_tokens": row[0], "output_tokens": row[1], "cost": round(row[2], 4), "requests": row[3]}


class Expert:
    def __init__(self):
        self.ledger = UsageLedger()
        self._client = None
        self.last_backend = "local"

    # ---- backend selection ---------------------------------------------------------
    def budget_left(self) -> float:
        return get_config().expert.weekly_budget_usd - self.ledger.this_week()["cost"]

    def _claude(self):
        if self._client is None:
            import anthropic

            self._client = anthropic.Anthropic()
        return self._client

    def use_claude(self) -> bool:
        cfg = get_config().expert
        return cfg.provider == "claude" and self.budget_left() > 0

    def local_client(self) -> OllamaClient:
        chosen = get_config().expert.local_model
        installed = get_llm().available_models()
        if not chosen:
            chosen = next((m for m in LOCAL_PREFERENCE if m in installed), get_llm().model)
        client = OllamaClient(model=chosen)
        client.keep_alive = "30m"
        return client

    # ---- asking ----------------------------------------------------------------------
    def ask(self, prompt: str, system: str = EXPERT_SYSTEM, max_tokens: int = 1500, purpose: str = "expert",
            history: list[dict] | None = None) -> str:
        messages = list(history or []) + [{"role": "user", "content": prompt}]
        if self.use_claude():
            try:
                return self._ask_claude(messages, system, purpose)
            except Exception as e:  # network/credentials problem: still answer, locally
                log.warning("Claude request failed, using the local model: %s", e)
                self.last_backend = f"local (Claude unavailable: {type(e).__name__})"
        elif get_config().expert.provider == "claude":
            self.last_backend = "local (weekly Claude budget used up)"
        else:
            self.last_backend = "local"
        local = self.local_client()
        return local.chat([{"role": "system", "content": system}] + messages, temperature=0.3,
                          max_tokens=min(max_tokens, 700))  # a CPU model writes ~3-5 words a second

    def _ask_claude(self, messages: list[dict], system: str, purpose: str) -> str:
        import anthropic

        cfg = get_config().expert
        kwargs = {"output_config": {"effort": cfg.effort}} if not cfg.claude_model.startswith("claude-haiku") else {}
        if cfg.claude_model in ("claude-opus-5", "claude-fable-5-1"):
            # if a safety classifier declines, the API retries on a suitable model by itself
            kwargs |= {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}
        try:
            response = self._claude().beta.messages.create(
                model=cfg.claude_model,
                max_tokens=16000,
                system=system,
                messages=messages,
                **kwargs,
            )
        except anthropic.AuthenticationError as e:
            raise LLMError("Claude credentials are missing or invalid — set ANTHROPIC_API_KEY or run `ant auth login`") from e
        cost = self.ledger.add(response.model, purpose, response.usage.input_tokens, response.usage.output_tokens)
        self.last_backend = f"Claude ({response.model}, ${cost:.3f})"
        if response.stop_reason == "refusal":
            return "Claude declined to answer that request."
        return "".join(b.text for b in response.content if b.type == "text").strip()

    # ---- research ----------------------------------------------------------------------
    def research(self, topic: str) -> str:
        """Search the web, read the top pages and write a sourced brief."""
        from app.accessibility import web_executor

        results = web_executor.web_search(topic, max_results=5)
        if not results:
            return "I couldn't find anything on the web for that."
        sources = []
        for r in results[:3]:
            try:
                title, text = web_executor.page_text(r["url"], limit=6000)
            except Exception:
                title, text = r["title"], r["snippet"]
            sources.append(f"SOURCE: {title or r['title']} ({r['url']})\n{text[:4000]}")
        prompt = (f"Research question: {topic}\n\n" + "\n\n".join(sources) +
                  "\n\nWrite a brief: key findings, recommendations, and cite sources by name.")
        return self.ask(prompt, purpose="research", max_tokens=1500)


_expert: Expert | None = None


def get_expert() -> Expert:
    global _expert
    if _expert is None:
        _expert = Expert()
    return _expert


# ---- project scaffolding ---------------------------------------------------------------------

SCAFFOLDS = {
    "nextjs": ("Next.js (TypeScript, Tailwind, App Router)",
               "npx --yes create-next-app@latest {name} --ts --tailwind --eslint --app --src-dir --use-npm --yes"),
    "react": ("React + Vite (TypeScript)", "npm create vite@latest {name} -- --template react-ts"),
    "vue": ("Vue + Vite (TypeScript)", "npm create vite@latest {name} -- --template vue-ts"),
    "astro": ("Astro", "npm create astro@latest {name} -- --template basics --yes --no-git --install"),
}


def scaffold_kind(text: str) -> str | None:
    t = text.lower()
    if "next" in t:
        return "nextjs"
    for key in ("react", "vue", "astro"):
        if key in t:
            return key
    return None

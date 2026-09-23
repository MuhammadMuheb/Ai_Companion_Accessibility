"""Thin wrapper around the local Ollama HTTP API."""

from __future__ import annotations

import json
import re
from typing import Iterator

import requests

from app.config import get_config
from app.logger import get_logger

log = get_logger(__name__)


class LLMError(RuntimeError):
    pass


class OllamaClient:
    def __init__(self, url: str | None = None, model: str | None = None, embed_model: str | None = None):
        cfg = get_config().llm
        self.url = (url or cfg.ollama_url).rstrip("/")
        self.model = model or cfg.chat_model
        self.embed_model = embed_model or cfg.embedding_model
        self.temperature = cfg.temperature
        self.max_tokens = cfg.max_tokens
        self.timeout = cfg.timeout
        self.keep_alive: str | None = getattr(cfg, "keep_alive", None) or None  # e.g. "30m" keeps the model loaded

    # ---- health -------------------------------------------------------
    def available_models(self) -> list[str]:
        try:
            r = requests.get(f"{self.url}/api/tags", timeout=5)
            r.raise_for_status()
            return [m["name"] for m in r.json().get("models", [])]
        except requests.RequestException:
            return []

    def is_available(self) -> bool:
        return bool(self.available_models())

    def has_model(self, name: str) -> bool:
        names = self.available_models()
        return any(n == name or n.split(":")[0] == name.split(":")[0] for n in names)

    # ---- chat ---------------------------------------------------------
    def _options(self, temperature: float | None, max_tokens: int | None) -> dict:
        return {
            "temperature": self.temperature if temperature is None else temperature,
            "num_predict": max_tokens or self.max_tokens,
        }

    def chat(self, messages: list[dict], temperature: float | None = None, json_mode: bool = False,
             max_tokens: int | None = None) -> str:
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": self._options(temperature, max_tokens),
        }
        if json_mode:
            payload["format"] = "json"
        if self.keep_alive:
            payload["keep_alive"] = self.keep_alive
        try:
            r = requests.post(f"{self.url}/api/chat", json=payload, timeout=self.timeout)
            r.raise_for_status()
        except requests.RequestException as e:
            raise LLMError(f"Ollama request failed: {e}") from e
        return r.json()["message"]["content"]

    def chat_stream(self, messages: list[dict], temperature: float | None = None,
                    max_tokens: int | None = None) -> Iterator[str]:
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "options": self._options(temperature, max_tokens),
        }
        if self.keep_alive:
            payload["keep_alive"] = self.keep_alive
        try:
            with requests.post(f"{self.url}/api/chat", json=payload, stream=True, timeout=self.timeout) as r:
                r.raise_for_status()
                for line in r.iter_lines():
                    if not line:
                        continue
                    chunk = json.loads(line)
                    if "error" in chunk:
                        raise LLMError(chunk["error"])
                    piece = chunk.get("message", {}).get("content", "")
                    if piece:
                        yield piece
                    if chunk.get("done"):
                        break
        except requests.RequestException as e:
            raise LLMError(f"Ollama request failed: {e}") from e

    def preload(self) -> None:
        """Load the model into memory now so the first real request is fast."""
        try:
            body = {"model": self.model, "prompt": "", "keep_alive": self.keep_alive or "10m"}
            requests.post(f"{self.url}/api/generate", json=body, timeout=self.timeout)
        except requests.RequestException as e:
            log.debug("Preload of %s failed: %s", self.model, e)

    def ask(self, prompt: str, system: str | None = None, **kwargs) -> str:
        messages = [{"role": "system", "content": system}] if system else []
        messages.append({"role": "user", "content": prompt})
        return self.chat(messages, **kwargs)

    def ask_json(self, prompt: str, system: str | None = None, max_tokens: int = 250) -> dict | list | None:
        """Ask for a JSON answer; returns None if the model output can't be parsed."""
        raw = self.ask(prompt, system=system, temperature=0.1, json_mode=True, max_tokens=max_tokens)
        return parse_json(raw)

    # ---- embeddings ---------------------------------------------------
    def embed(self, text: str) -> list[float] | None:
        try:
            r = requests.post(
                f"{self.url}/api/embed",
                json={"model": self.embed_model, "input": text},
                timeout=self.timeout,
            )
            r.raise_for_status()
            vectors = r.json().get("embeddings") or []
            return vectors[0] if vectors else None
        except requests.RequestException as e:
            log.warning("Embedding failed (%s); falling back to keyword search", e)
            return None


def parse_json(raw: str):
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        pass
    match = re.search(r"(\{.*\}|\[.*\])", raw or "", re.S)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            return None
    return None


_client: OllamaClient | None = None


def get_llm() -> OllamaClient:
    global _client
    if _client is None:
        _client = OllamaClient()
    return _client

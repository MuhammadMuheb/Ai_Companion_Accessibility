"""Describe what's on screen for a blind or low-vision user.

Uses an Ollama vision model (e.g. `ollama pull moondream`) when one is installed;
otherwise reads the screen text with OCR and asks the chat model to summarise it.
"""

from __future__ import annotations

import base64
import io

import requests

from app.accessibility import screen
from app.llm import LLMError, get_llm
from app.logger import get_logger

log = get_logger(__name__)

VISION_MODELS = ("moondream", "llava-phi3", "llava", "llama3.2-vision", "qwen2.5vl", "gemma3")

SUMMARY_SYSTEM = (
    "You help a blind user understand their computer screen. You are given text read from the screen by OCR. "
    "In 2-4 short sentences say which app or page this is and the most important content or buttons. "
    "Do not invent anything that is not in the text."
)


def vision_model() -> str | None:
    llm = get_llm()
    names = llm.available_models()
    for candidate in VISION_MODELS:
        for name in names:
            if name.split(":")[0] == candidate:
                return name
    return None


def _describe_with_vision(model: str, image, question: str) -> str:
    image = image.copy()
    image.thumbnail((1280, 1280))
    buf = io.BytesIO()
    image.convert("RGB").save(buf, format="JPEG", quality=85)
    llm = get_llm()
    r = requests.post(
        f"{llm.url}/api/chat",
        json={
            "model": model,
            "stream": False,
            "messages": [{"role": "user", "content": question, "images": [base64.b64encode(buf.getvalue()).decode()]}],
        },
        timeout=llm.timeout,
    )
    r.raise_for_status()
    return r.json()["message"]["content"].strip()


def describe_screen(question: str = "", window_only: bool = True) -> str:
    image = screen.capture(window_only=window_only)
    question = question or "Describe this screen briefly for a blind user: what app it is and what is important."
    model = vision_model()
    if model:
        try:
            return _describe_with_vision(model, image, question)
        except (requests.RequestException, KeyError) as e:
            log.warning("Vision model failed, falling back to OCR: %s", e)
    try:
        text = screen.ocr(image)
    except RuntimeError as e:
        return str(e)
    if not text:
        return "I couldn't find any readable text on the screen."
    try:
        return get_llm().ask(f"Question: {question}\n\nScreen text:\n{text[:4000]}", system=SUMMARY_SYSTEM, temperature=0.2, max_tokens=150)
    except LLMError:
        return "Here is the text on screen: " + text[:600]

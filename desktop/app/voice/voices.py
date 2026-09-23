"""Lyra's voice library: twelve curated speaking voices (eight female, four male).

Two engines stand behind the catalogue:

- ``neural``  — Piper neural text-to-speech (offline, ONNX). Natural, studio-quality voices. Each
  voice is one ~60 MB model, downloaded once on demand into ``data/voices`` (the installer bundles
  the default voice, so Lyra speaks out of the box with no internet).
- ``system``  — the voices built into Windows (SAPI). Instant and always available; used as the
  fallback whenever a neural voice isn't downloaded yet or can't load.

The catalogue is the single source of truth for the Voice settings page, the ``voice.tts_voice``
setting and the speaker (``app.voice.tts``).
"""

from __future__ import annotations

import json
import sys
import threading
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path

from app.config import ROOT, get_config
from app.logger import get_logger

log = get_logger(__name__)

HF_BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main"
DEFAULT_VOICE = "amy"
SAMPLE_TEXT = "Hi, I'm {name}. Just say my name whenever you need me — I'm always listening."


@dataclass(frozen=True)
class Voice:
    id: str             # stable key stored in settings (voice.tts_voice)
    name: str           # display name
    gender: str         # "female" | "male"
    accent: str         # e.g. "US English"
    style: str          # one-line character description shown on the card
    engine: str         # "neural" | "system"
    model: str = ""     # neural: Piper voice key, e.g. "en_US-amy-medium"; system: SAPI name fragment
    size_mb: int = 0    # neural download size

    @property
    def hf_path(self) -> str:
        """Folder of a Piper voice on Hugging Face: en/en_US/amy/medium/en_US-amy-medium."""
        lang_region, speaker, quality = self.model.split("-", 2)
        return f"{lang_region.split('_')[0]}/{lang_region}/{speaker}/{quality}/{self.model}"


VOICES: tuple[Voice, ...] = (
    # ---- female ------------------------------------------------------------------------------
    Voice("amy", "Amy", "female", "US English", "Warm and friendly — the signature Lyra voice",
          "neural", "en_US-amy-medium", 63),
    Voice("kristin", "Kristin", "female", "US English", "Clear, bright and upbeat",
          "neural", "en_US-kristin-medium", 63),
    Voice("lessac", "Lena", "female", "US English", "Crisp studio narrator, very articulate",
          "neural", "en_US-lessac-medium", 63),
    Voice("harper", "Harper", "female", "US English", "Soft, calm and reassuring",
          "neural", "en_US-hfc_female-medium", 63),
    Voice("jenny", "Jenny", "female", "UK English", "Gentle, polished and expressive",
          "neural", "en_GB-jenny_dioco-medium", 63),
    Voice("cori", "Cori", "female", "UK English", "Elegant, measured and precise",
          "neural", "en_GB-cori-medium", 63),
    Voice("alba", "Alba", "female", "Scottish", "Lilting, relaxed and personable",
          "neural", "en_GB-alba-medium", 63),
    Voice("zira", "Zira", "female", "US English", "Built into Windows — instant, no download",
          "system", "Zira"),
    # ---- male --------------------------------------------------------------------------------
    Voice("ryan", "Ryan", "male", "US English", "Confident, rich and natural",
          "neural", "en_US-ryan-medium", 63),
    Voice("alan", "Alan", "male", "UK English", "Composed, deep and professional",
          "neural", "en_GB-alan-medium", 63),
    Voice("joe", "Joe", "male", "US English", "Relaxed, casual and easygoing",
          "neural", "en_US-joe-medium", 63),
    Voice("david", "David", "male", "US English", "Built into Windows — instant, no download",
          "system", "David"),
)
BY_ID = {v.id: v for v in VOICES}


def get_voice(voice_id: str | None) -> Voice:
    return BY_ID.get(voice_id or "", BY_ID[DEFAULT_VOICE])


# ---- where neural voices live ----------------------------------------------------------------

def user_dir() -> Path:
    """Downloaded voices (writable, per user)."""
    return get_config().dir("data/voices")


def bundled_dirs() -> list[Path]:
    """Voices shipped with the app: packaging/models/voices from source, models/voices when installed."""
    dirs = [ROOT / "packaging" / "models" / "voices", ROOT / "models" / "voices"]
    base = getattr(sys, "_MEIPASS", None)
    if base:
        dirs.insert(0, Path(base) / "models" / "voices")
    return dirs


def model_files(voice: Voice) -> tuple[Path, Path] | None:
    """(model.onnx, model.onnx.json) if the voice is on disk."""
    if voice.engine != "neural":
        return None
    for folder in [user_dir(), *bundled_dirs()]:
        onnx = folder / f"{voice.model}.onnx"
        cfg = folder / f"{voice.model}.onnx.json"
        if onnx.exists() and cfg.exists() and onnx.stat().st_size > 1_000_000:
            return onnx, cfg
    return None


def neural_engine_available() -> bool:
    try:
        import piper  # noqa: F401
    except Exception:
        return False
    return True


def is_ready(voice: Voice) -> bool:
    if voice.engine == "system":
        return True
    return model_files(voice) is not None


# ---- downloads ----------------------------------------------------------------------------------

class Downloads:
    """Background downloads with progress, one per voice."""

    def __init__(self):
        self._lock = threading.Lock()
        self.progress: dict[str, float] = {}   # voice id -> 0..1 while downloading
        self.errors: dict[str, str] = {}

    def start(self, voice_id: str) -> bool:
        voice = BY_ID.get(voice_id)
        if voice is None or voice.engine != "neural" or is_ready(voice):
            return False
        with self._lock:
            if voice_id in self.progress:
                return True
            self.progress[voice_id] = 0.0
            self.errors.pop(voice_id, None)
        threading.Thread(target=self._run, args=(voice,), daemon=True, name=f"voice-dl-{voice_id}").start()
        return True

    def _run(self, voice: Voice) -> None:
        folder = user_dir()
        try:
            base = self._resolve(voice)
            self._fetch(f"{base}.onnx.json", folder / f"{voice.model}.onnx.json", voice.id, weight=(0.0, 0.01))
            self._fetch(f"{base}.onnx", folder / f"{voice.model}.onnx", voice.id, weight=(0.01, 1.0))
            log.info("Voice %s downloaded", voice.model)
        except Exception as e:
            log.warning("Voice download failed for %s: %s", voice.model, e)
            self.errors[voice.id] = "Download failed — check the internet connection and try again."
            for suffix in (".onnx", ".onnx.json"):
                (folder / f"{voice.model}{suffix}.part").unlink(missing_ok=True)
        finally:
            with self._lock:
                self.progress.pop(voice.id, None)

    @staticmethod
    def _resolve(voice: Voice) -> str:
        """URL of the voice without its extension. The catalogue path is tried first; if Hugging Face
        moved it, the official voices.json index is consulted."""
        url = f"{HF_BASE}/{voice.hf_path}"
        try:
            req = urllib.request.Request(f"{url}.onnx.json", method="HEAD")
            urllib.request.urlopen(req, timeout=15).close()
            return url
        except Exception:
            with urllib.request.urlopen(f"{HF_BASE}/voices.json", timeout=30) as r:
                index = json.load(r)
            files = index[voice.model]["files"]
            onnx = next(p for p in files if p.endswith(".onnx"))
            return f"{HF_BASE}/{onnx[: -len('.onnx')]}"

    def _fetch(self, url: str, dest: Path, voice_id: str, weight: tuple[float, float]) -> None:
        part = dest.with_name(dest.name + ".part")
        lo, hi = weight
        with urllib.request.urlopen(url, timeout=30) as r, open(part, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            done = 0
            while chunk := r.read(1 << 16):
                f.write(chunk)
                done += len(chunk)
                if total:
                    self.progress[voice_id] = lo + (hi - lo) * done / total
        part.replace(dest)


downloads = Downloads()


# ---- what the Voice page shows ------------------------------------------------------------------

def catalogue() -> dict:
    cfg = get_config().voice
    selected = get_voice(cfg.tts_voice).id
    neural_ok = neural_engine_available()
    items = []
    for v in VOICES:
        item = asdict(v)
        item["ready"] = is_ready(v) and (v.engine == "system" or neural_ok)
        item["downloading"] = downloads.progress.get(v.id)
        item["error"] = downloads.errors.get(v.id, "")
        item["selected"] = v.id == selected
        items.append(item)
    return {"voices": items, "selected": selected, "rate": cfg.tts_rate, "volume": cfg.tts_volume,
            "neural_engine": neural_ok,
            "counts": {"female": sum(v.gender == "female" for v in VOICES),
                       "male": sum(v.gender == "male" for v in VOICES)}}

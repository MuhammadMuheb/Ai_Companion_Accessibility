"""Speaker verification ("voice print"): only enrolled voices may wake the companion.

Uses the WeSpeaker ResNet34 model (ONNX, ~26 MB, trained on VoxCeleb) to turn a few seconds of
speech into a 256-number voice embedding; two clips from the same person have a high cosine
similarity. Up to MAX_PROFILES voices can be enrolled. With no profiles enrolled, verification
is off and every voice is accepted.

This is a convenience filter, not strong security: a good recording of an enrolled voice can
fool it. It is never used to unlock anything.
"""

from __future__ import annotations

import json
import re
import threading
from datetime import datetime
from pathlib import Path

import numpy as np

from app.config import get_config
from app.logger import get_logger

log = get_logger(__name__)

MODEL_REPO = "Wespeaker/wespeaker-voxceleb-resnet34-LM"
MODEL_FILE = "voxceleb_resnet34_LM.onnx"
MAX_PROFILES = 2
SAMPLE_RATE = 16_000
MIN_SECONDS = 1.0  # shorter clips ("Hey MD" alone) are not reliable; combine with the command


class VoiceprintError(RuntimeError):
    pass


def _folder() -> Path:
    cfg = get_config()
    return cfg.dir("data/voiceprints")


def _fbank(audio: np.ndarray) -> np.ndarray:
    import kaldi_native_fbank as knf

    opts = knf.FbankOptions()
    opts.frame_opts.samp_freq = SAMPLE_RATE
    opts.frame_opts.dither = 0.0
    opts.frame_opts.window_type = "hamming"
    opts.frame_opts.frame_length_ms = 25
    opts.frame_opts.frame_shift_ms = 10
    opts.mel_opts.num_bins = 80
    opts.energy_floor = 0.0
    fb = knf.OnlineFbank(opts)
    fb.accept_waveform(SAMPLE_RATE, (audio.astype(np.float32) * 32768.0).tolist())
    fb.input_finished()
    feats = np.stack([fb.get_frame(i) for i in range(fb.num_frames_ready)]) if fb.num_frames_ready else np.zeros((0, 80))
    return (feats - feats.mean(axis=0, keepdims=True)).astype(np.float32)  # per-utterance mean normalisation


class VoicePrint:
    def __init__(self):
        self._session = None
        self._lock = threading.Lock()

    @property
    def session(self):
        with self._lock:
            if self._session is None:
                import onnxruntime as ort

                from app.config import ROOT

                bundled = ROOT / "models" / MODEL_FILE  # shipped with the installer
                if bundled.exists():
                    path = str(bundled)
                else:
                    from huggingface_hub import hf_hub_download

                    path = hf_hub_download(MODEL_REPO, MODEL_FILE)
                self._session = ort.InferenceSession(path, providers=["CPUExecutionProvider"])
            return self._session

    def embed(self, audio: np.ndarray) -> np.ndarray:
        audio = _speech_only(audio)
        if audio.size < SAMPLE_RATE * MIN_SECONDS:
            raise VoiceprintError("Need at least a second of speech.")
        feats = _fbank(audio)
        emb = self.session.run(None, {"feats": feats[None, :, :]})[0][0]
        return emb / (np.linalg.norm(emb) + 1e-9)

    # ---- profiles ---------------------------------------------------------------
    def profiles(self) -> dict[str, np.ndarray]:
        out = {}
        for f in sorted(_folder().glob("*.npy")):
            out[f.stem] = np.load(f)
        return out

    def enroll(self, name: str, clips: list[np.ndarray]) -> str:
        name = re.sub(r"[^\w -]", "", name).strip()[:30] or "Me"
        existing = self.profiles()
        if name not in existing and len(existing) >= MAX_PROFILES:
            raise VoiceprintError(f"Only {MAX_PROFILES} voices can be enrolled. Delete one first.")
        embs = [self.embed(c) for c in clips]
        if len(embs) > 1:  # clips of one person should agree with each other
            sims = [float(a @ b) for i, a in enumerate(embs) for b in embs[i + 1:]]
            if min(sims) < 0.35:
                raise VoiceprintError("The recordings don't sound like the same voice — please record again in a quiet place.")
        mean = np.mean(embs, axis=0)
        mean /= np.linalg.norm(mean) + 1e-9
        np.save(_folder() / f"{name}.npy", mean)
        (_folder() / f"{name}.json").write_text(json.dumps({"enrolled": datetime.now().isoformat(timespec="seconds"),
                                                            "clips": len(clips)}), encoding="utf-8")
        return name

    def delete(self, name: str) -> bool:
        found = False
        for ext in (".npy", ".json"):
            f = _folder() / f"{name}{ext}"
            if f.exists():
                f.unlink()
                found = True
        return found

    def active(self) -> bool:
        return get_config().voice.voiceprint_enabled and bool(self.profiles())

    @staticmethod
    def speech_seconds(audio: np.ndarray) -> float:
        return _speech_only(audio).size / SAMPLE_RATE

    def verify(self, audio: np.ndarray) -> tuple[bool, str | None, float]:
        """(accepted, best matching profile, score). Accepts everyone when no profile is enrolled."""
        profiles = self.profiles()
        if not profiles or not get_config().voice.voiceprint_enabled:
            return True, None, 1.0
        try:
            emb = self.embed(audio)
        except VoiceprintError:
            return False, None, 0.0
        name, score = max(((n, float(emb @ p)) for n, p in profiles.items()), key=lambda t: t[1])
        accepted = score >= get_config().voice.voiceprint_threshold
        log.info("Voiceprint: best %s %.2f -> %s", name, score, "accepted" if accepted else "rejected")
        return accepted, name, score


def _speech_only(audio: np.ndarray, block: int = 400) -> np.ndarray:
    """Drop near-silent 25 ms blocks so pauses don't dilute the voice embedding."""
    audio = np.asarray(audio, dtype=np.float32)
    if audio.size < block * 4:
        return audio
    n = audio.size // block
    frames = audio[: n * block].reshape(n, block)
    energy = np.sqrt((frames ** 2).mean(axis=1))
    keep = energy > max(1e-4, np.percentile(energy, 30) * 1.5)
    return frames[keep].reshape(-1) if keep.sum() >= 40 else audio


_instance: VoicePrint | None = None


def get_voiceprint() -> VoicePrint:
    global _instance
    if _instance is None:
        _instance = VoicePrint()
    return _instance

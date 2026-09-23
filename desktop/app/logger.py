"""Application logging plus an append-only JSONL audit trail of actions taken."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from logging.handlers import RotatingFileHandler

from app.config import get_config

_configured = False


def setup_logging() -> None:
    global _configured
    if _configured:
        return
    cfg = get_config()
    log_dir = cfg.dir(cfg.storage.logs_dir)
    root = logging.getLogger()
    root.setLevel(cfg.log_level.upper())
    handler = RotatingFileHandler(log_dir / "companion.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.addHandler(handler)
    # Keep noisy third-party libraries quiet
    for name in ("urllib3", "apscheduler", "faster_whisper", "comtypes"):
        logging.getLogger(name).setLevel(logging.WARNING)
    _configured = True


def get_logger(name: str) -> logging.Logger:
    setup_logging()
    return logging.getLogger(name)


def audit(action: str, **details) -> None:
    """Record something the companion did (opened an app, ran a command, ...)."""
    cfg = get_config()
    path = cfg.dir(cfg.storage.logs_dir) / "audit.jsonl"
    entry = {"time": datetime.now().isoformat(timespec="seconds"), "action": action, **details}
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")

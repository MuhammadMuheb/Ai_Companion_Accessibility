"""Multi-step requests ("open notepad then type hello") and named routines from config.yaml."""

from __future__ import annotations

import re
import time

from app.command_parser import Intent, normalize, parse
from app.config import get_config
from app.workflow import Workflow

_SPLIT = re.compile(r"\s*(?:,?\s*and then|,?\s*then|,?\s*uske baad|,?\s*phir|;)\s+", re.I)

# give an app time to open before typing into it
SETTLE_AFTER = {"open": 2.0, "switch_window": 0.8, "press_keys": 0.3}


def phrase_key(text: str) -> str:
    """Canonical form of a spoken trigger phrase: lowercase, no politeness words or punctuation."""
    return re.sub(r"[^\w\s]", "", normalize(text).lower()).strip()


def split_steps(text: str) -> list[str]:
    return [s for s in _SPLIT.split(text.strip()) if s]


class ExtendedWorkflow(Workflow):
    def plan(self, text: str) -> list[Intent] | None:
        """Return a list of steps if `text` is a routine or a multi-step command, else None."""
        routines = {phrase_key(k): v for k, v in (get_config().routines or {}).items()}
        key = phrase_key(text)
        for candidate in (key, key.removeprefix("start ").removeprefix("run ").removesuffix(" routine").strip()):
            if candidate in routines:
                return [parse(step) for step in routines[candidate] if step.strip()]
        parts = split_steps(text)
        if len(parts) < 2:
            return None
        steps = [parse(p) for p in parts]
        if any(step.is_chat for step in steps):
            return None  # not a clean chain of commands — let the model handle it as conversation
        return steps

    def run_text(self, text: str) -> tuple[Intent, str | None]:
        """Parse and execute. Returns (intent, result); result None means 'chat with the model'."""
        steps = self.plan(text)
        if steps:
            results = []
            for step in steps:
                results.append(self.execute(step) or "")
                time.sleep(SETTLE_AFTER.get(step.name, 0))
            return Intent("multi_step", {"steps": [s.name for s in steps]}, text), " ".join(r for r in results if r)
        intent = parse(text)
        if intent.is_chat and get_config().feature_on("expert") and get_config().expert.route_technical:
            from app.expert import is_technical

            if is_technical(text):
                intent = Intent("expert", {"question": text}, text)
        return intent, self.execute(intent)

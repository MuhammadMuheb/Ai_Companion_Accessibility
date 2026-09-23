"""Focus sessions (Pomodoro-style) with gentle distraction nudges.

Nothing is blocked or changed on the system; during a session the active window
title is checked once a minute and the user is nudged if it looks distracting.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta

from app.config import get_config
from app.logger import get_logger
from app.mentor.notifications import notify

log = get_logger(__name__)


def active_window_title() -> str:
    try:
        import pygetwindow
    except ImportError:
        return ""
    try:
        win = pygetwindow.getActiveWindow()
        return win.title if win else ""
    except Exception:
        return ""


class FocusSession:
    CHECK_EVERY = 60  # seconds

    def __init__(self):
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.task = ""
        self.ends_at: datetime | None = None
        self.nudges = 0

    @property
    def active(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, minutes: int | None = None, task: str = "") -> str:
        if self.active:
            return f"A focus session is already running until {self.ends_at:%I:%M %p}."
        minutes = minutes or get_config().focus.default_minutes
        self.task, self.nudges = task, 0
        self.ends_at = datetime.now() + timedelta(minutes=minutes)
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        what = f" on '{task}'" if task else ""
        return f"Focus session started{what} for {minutes} minutes, until {self.ends_at:%I:%M %p}. You've got this!"

    def stop(self) -> str:
        if not self.active:
            return "No focus session is running."
        self._stop.set()
        self._thread.join(timeout=2)
        return "Focus session stopped."

    def status(self) -> str:
        if not self.active:
            return "No focus session is running."
        left = max(0, int((self.ends_at - datetime.now()).total_seconds() // 60))
        return f"Focus session: {left} minute(s) left."

    def _run(self) -> None:
        distractions = [d.lower() for d in get_config().focus.distractions]
        while not self._stop.is_set():
            if datetime.now() >= self.ends_at:
                notify("Focus complete", "Great work! Time for a 5 minute break — stretch and drink some water.")
                return
            title = active_window_title().lower()
            if title and any(d in title for d in distractions):
                self.nudges += 1
                notify("Stay focused", "That looks like a distraction. Let's get back to your task.")
            self._stop.wait(min(self.CHECK_EVERY, max(1, (self.ends_at - datetime.now()).total_seconds())))


_session = FocusSession()


def get_session() -> FocusSession:
    return _session

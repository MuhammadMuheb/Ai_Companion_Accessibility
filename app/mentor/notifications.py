"""Deliver alerts to the user: console, desktop toast (if plyer is installed) and any
registered listeners — the voice CLI registers text-to-speech so alerts are spoken."""

from __future__ import annotations

import sys
import threading
from typing import Callable

from app.logger import get_logger

log = get_logger(__name__)

Listener = Callable[[str, str], None]
_listeners: list[Listener] = []
_print_lock = threading.Lock()


def add_listener(fn: Listener) -> None:
    if fn not in _listeners:
        _listeners.append(fn)


def remove_listener(fn: Listener) -> None:
    if fn in _listeners:
        _listeners.remove(fn)


def _desktop_toast(title: str, message: str) -> None:
    try:
        from plyer import notification
    except ImportError:
        return
    try:
        notification.notify(title=title, message=message[:250], app_name="AI Companion", timeout=10)
    except Exception as e:  # plyer backends raise a variety of errors
        log.debug("Desktop notification failed: %s", e)


def notify(title: str, message: str, console: bool = True, toast: bool = True) -> None:
    """`toast=False` for alerts that came *from* a Windows notification, so we don't echo them back."""
    log.info("Notify: %s — %s", title, message)
    if console:
        with _print_lock:
            sys.stdout.write(f"\n\a🔔 {title}: {message}\n")
            sys.stdout.flush()
    if toast:
        _desktop_toast(title, message)
    for fn in list(_listeners):
        try:
            fn(title, message)
        except Exception as e:
            log.warning("Notification listener failed: %s", e)

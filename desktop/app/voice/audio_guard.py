"""Audio access guard: ensures microphone and speaker are NEVER exclusively held.

This module enforces that:
- All audio streams use shared/non-exclusive mode
- No component can monopolize audio
- Calls, meetings, and other apps can always access audio
- Audio is released immediately when not needed
"""

from __future__ import annotations

import atexit
import os
import threading
from typing import Callable

from app.logger import get_logger

log = get_logger(__name__)

_audio_lock = threading.RLock()
_held_by = {}  # type: dict[str, str]  # resource -> component holding it
_cleanup_handlers: list[Callable[[], None]] = []


def _enforce_shared_only():
    """Reject any attempt to use exclusive mode."""
    if os.environ.get("SOUNDDEVICE_EXCLUSIVE", "False").lower() == "true":
        log.critical("FATAL: Exclusive audio mode detected! Calls and meetings will be blocked.")
        os.environ["SOUNDDEVICE_EXCLUSIVE"] = "False"
    if os.environ.get("WASAPI_EXCLUSIVE", "False").lower() == "true":
        log.critical("FATAL: Exclusive WASAPI mode detected! Calls and meetings will be blocked.")
        os.environ["WASAPI_EXCLUSIVE"] = "False"


def require_shared_mode(component: str, resource: str) -> None:
    """Check that `component` is not requesting exclusive audio access.

    Args:
        component: Name of the component (e.g., "wake_listener", "speech_engine")
        resource: Name of the resource (e.g., "microphone", "speaker")

    Raises:
        RuntimeError: If exclusive mode is detected
    """
    _enforce_shared_only()
    with _audio_lock:
        if resource in _held_by and _held_by[resource] != component:
            holder = _held_by[resource]
            log.warning("%s wants %s but %s is using it; should release first", component, resource, holder)
        _held_by[resource] = component


def release_audio(component: str, resource: str) -> None:
    """Notify that `component` is releasing `resource`.

    Args:
        component: Name of the component
        resource: Name of the resource
    """
    with _audio_lock:
        if _held_by.get(resource) == component:
            del _held_by[resource]
            log.debug("%s released %s", component, resource)


def add_cleanup_handler(fn: Callable[[], None]) -> None:
    """Register a function to clean up audio resources at shutdown."""
    _cleanup_handlers.append(fn)


def _cleanup_all():
    """Called at exit to ensure all audio resources are released."""
    for fn in _cleanup_handlers:
        try:
            fn()
        except Exception as e:
            log.debug("Cleanup handler failed: %s", e)
    log.info("Audio resources cleaned up at shutdown")


atexit.register(_cleanup_all)


def audio_health_check() -> dict[str, str]:
    """Return current audio state for diagnostics.

    Returns:
        Dict with "microphone" and "speaker" keys showing who's using them (if anyone)
    """
    with _audio_lock:
        return {
            "microphone": _held_by.get("microphone", "idle"),
            "speaker": _held_by.get("speaker", "idle"),
            "exclusive_mode_enforced": os.environ.get("SOUNDDEVICE_EXCLUSIVE") == "False",
        }

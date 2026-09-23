"""Live system state shared across the app: is a call in progress, is the screen locked."""

from __future__ import annotations

import ctypes
import threading

in_call = threading.Event()   # set while a call is using the microphone — Nova stays silent


def screen_locked() -> bool:
    """True on the Windows lock/sign-in screen (the secure desktop has taken over input)."""
    user32 = ctypes.windll.user32
    DESKTOP_SWITCHDESKTOP = 0x0100
    hdesk = user32.OpenInputDesktop(0, False, DESKTOP_SWITCHDESKTOP)
    if not hdesk:
        return True
    try:
        return not user32.SwitchDesktop(hdesk)
    finally:
        user32.CloseDesktop(hdesk)

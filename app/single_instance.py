"""Only one Nova at a time. A second launch (double-clicking Nova again, the Start-menu
shortcut, the startup shortcut) doesn't start a second copy — it asks the running one to
show its window and exits. Uses Windows named kernel objects: no ports, no network."""

from __future__ import annotations

import ctypes
import threading
from ctypes import wintypes
from typing import Callable

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.CreateMutexW.restype = wintypes.HANDLE
kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.CreateEventW.restype = wintypes.HANDLE
kernel32.CreateEventW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.OpenEventW.restype = wintypes.HANDLE
kernel32.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.SetEvent.argtypes = [wintypes.HANDLE]
kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
kernel32.WaitForSingleObject.restype = wintypes.DWORD
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

ERROR_ALREADY_EXISTS = 183
EVENT_MODIFY_STATE = 0x0002
WAIT_OBJECT_0 = 0


class SingleInstance:
    """`with SingleInstance("Nova") as inst:` — inst.primary is False if Nova already runs."""

    def __init__(self, name: str = "NovaCompanion"):
        self.mutex_name = f"Local\\{name}.Instance"
        self.event_name = f"Local\\{name}.ShowWindow"
        self._mutex = None
        self._event = None
        self.primary = False
        self._stop = threading.Event()

    def __enter__(self) -> "SingleInstance":
        self._mutex = kernel32.CreateMutexW(None, False, self.mutex_name)
        self.primary = ctypes.get_last_error() != ERROR_ALREADY_EXISTS
        if self.primary:
            # auto-reset event another launch can signal
            self._event = kernel32.CreateEventW(None, False, False, self.event_name)
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        for handle in (self._event, self._mutex):
            if handle:
                kernel32.CloseHandle(handle)
        self._event = self._mutex = None

    def signal_running(self) -> bool:
        """From a second launch: ask the running instance to show itself."""
        handle = kernel32.OpenEventW(EVENT_MODIFY_STATE, False, self.event_name)
        if not handle:
            return False
        try:
            return bool(kernel32.SetEvent(handle))
        finally:
            kernel32.CloseHandle(handle)

    def on_show_request(self, callback: Callable[[], None]) -> None:
        """In the running instance: call `callback` whenever another launch signals."""
        def wait():
            while not self._stop.is_set() and self._event:
                if kernel32.WaitForSingleObject(self._event, 500) == WAIT_OBJECT_0:
                    callback()
        threading.Thread(target=wait, daemon=True, name="show-window-listener").start()

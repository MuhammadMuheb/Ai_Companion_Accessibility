"""Small on-screen UI drawn by Nova itself: a corner pop-up with buttons, and a coloured
frame that highlights part of the screen. Runs one Tk event loop on its own thread; all
calls from other threads are queued onto it."""

from __future__ import annotations

import queue
import threading
from typing import Callable

from app.logger import get_logger

log = get_logger(__name__)


class Overlay:
    def __init__(self):
        self._jobs: queue.Queue = queue.Queue()
        self._ready = threading.Event()
        self.available = True
        threading.Thread(target=self._loop, daemon=True, name="overlay").start()
        self._ready.wait(5)

    def _loop(self) -> None:
        try:
            import tkinter as tk

            self.tk = tk
            self.root = tk.Tk()
            self.root.withdraw()
        except Exception as e:
            log.warning("On-screen pop-ups unavailable: %s", e)
            self.available = False
            self._ready.set()
            return
        self._ready.set()

        def pump():
            while True:
                try:
                    job = self._jobs.get_nowait()
                except queue.Empty:
                    break
                try:
                    job()
                except Exception as e:
                    log.warning("Overlay job failed: %s", e)
            self.root.after(40, pump)

        pump()
        self.root.mainloop()

    def _run(self, fn) -> None:
        if self.available:
            self._jobs.put(fn)

    def call(self, fn) -> None:
        """Run `fn` on the UI thread (all Tk work must happen there)."""
        self._run(fn)

    def call_sync(self, fn, timeout: float | None = None):
        """Run `fn` on the UI thread and wait for its result (from a worker thread)."""
        if threading.current_thread().name == "overlay":
            return fn()
        done, box = threading.Event(), {}

        def job():
            try:
                box["value"] = fn()
            except Exception as e:
                box["error"] = e
            finally:
                done.set()
        self._run(job)
        if not done.wait(timeout):
            raise TimeoutError("UI didn't respond")
        if "error" in box:
            raise box["error"]
        return box.get("value")

    # ---- corner prompt ------------------------------------------------------------------
    def prompt(self, title: str, message: str, buttons: list[str], on_choice: Callable[[str | None], None],
               timeout: float = 25, note: str = "") -> None:
        """Small always-on-top card in the bottom-right corner. Calls on_choice(button) or
        on_choice(None) on timeout. It doesn't take keyboard focus away from the user's call."""
        def build():
            tk = self.tk
            win = tk.Toplevel(self.root)
            win.overrideredirect(True)
            win.attributes("-topmost", True)
            try:
                win.attributes("-toolwindow", True)
            except tk.TclError:
                pass
            bg, fg = "#1f2937", "#f9fafb"
            win.configure(bg=bg, padx=14, pady=12)
            tk.Label(win, text=title, bg=bg, fg=fg, font=("Segoe UI Semibold", 11)).pack(anchor="w")
            tk.Label(win, text=message, bg=bg, fg=fg, font=("Segoe UI", 10), justify="left",
                     wraplength=300).pack(anchor="w", pady=(4, 6))
            if note:
                tk.Label(win, text=note, bg=bg, fg="#fbbf24", font=("Segoe UI", 9), justify="left",
                         wraplength=300).pack(anchor="w", pady=(0, 8))
            row = tk.Frame(win, bg=bg)
            row.pack(anchor="e")
            done = {"v": False}

            def choose(value):
                if done["v"]:
                    return
                done["v"] = True
                win.destroy()
                threading.Thread(target=on_choice, args=(value,), daemon=True).start()

            for i, label in enumerate(buttons):
                tk.Button(row, text=label, command=lambda v=label: choose(v), relief="flat", padx=10, pady=3,
                          bg="#10b981" if i == 0 else "#374151", fg="white", activebackground="#059669",
                          font=("Segoe UI", 10)).pack(side="left", padx=(6, 0))
            win.update_idletasks()
            sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
            w, h = win.winfo_reqwidth(), win.winfo_reqheight()
            win.geometry(f"+{sw - w - 24}+{sh - h - 72}")
            win.after(int(timeout * 1000), lambda: choose(None))
            self._current = (win, choose)

        self._run(build)

    def dismiss(self) -> None:
        def close():
            current = getattr(self, "_current", None)
            if current:
                current[1](None)
        self._run(close)

    # ---- highlight --------------------------------------------------------------------------
    def highlight(self, left: int, top: int, right: int, bottom: int, seconds: float = 2.5,
                  color: str = "#f59e0b") -> None:
        """Draw a thick frame around a screen rectangle (four thin topmost windows, so clicks pass
        through the middle)."""
        def build():
            tk = self.tk
            t = 4
            bars = [(left - t, top - t, right - left + 2 * t, t), (left - t, bottom, right - left + 2 * t, t),
                    (left - t, top, t, bottom - top), (right, top, t, bottom - top)]
            wins = []
            for x, y, w, h in bars:
                bar = tk.Toplevel(self.root)
                bar.overrideredirect(True)
                bar.attributes("-topmost", True)
                bar.configure(bg=color)
                bar.geometry(f"{max(1, w)}x{max(1, h)}+{x}+{y}")
                wins.append(bar)
            self.root.after(int(seconds * 1000), lambda: [b.destroy() for b in wins])

        self._run(build)


_overlay: Overlay | None = None


def get_overlay() -> Overlay:
    global _overlay
    if _overlay is None:
        _overlay = Overlay()
    return _overlay

"""MD as a headless Windows background app — no web server, no browser, no terminal,
and no window unless you ask for one.

One process holds everything: the companion, scheduler, voice (wake word, speech), the
background services (notifications, calls, hotkeys, watchdog) and a system-tray icon. MD
starts invisible and is used by voice ("Hello MD ..."). Its window (chat, settings, commands,
accounts, memory) is only created the first time you open it from the tray, with Ctrl+Alt+N, or
by launching MD again — until then not even the WebView is loaded.

The window is a WebView2 control (the Windows component WhatsApp Desktop and Teams use), so it
works with screen readers such as Narrator and NVDA. It is *not* a browser tab and nothing
listens on the network: the page calls MD's functions directly inside this process (the API
routes are invoked in-process, without any socket or port).

Start: `md.pyw` (double-click, no console), the Start-menu / startup shortcut, or `python main.py`.
Launching MD again while it runs just brings its window to the front.
"""

from __future__ import annotations

import base64
import os
import threading
from datetime import datetime
from pathlib import Path

from app.config import get_config
from app.logger import get_logger

log = get_logger(__name__)

STATIC = Path(__file__).parent / "web" / "static"


class Bridge:
    """Exposed to the page as `window.pywebview.api`. Each call runs the matching API route
    in-process (starlette's in-memory transport: no socket, no port)."""

    def __init__(self):
        from fastapi.testclient import TestClient

        from app.web import server

        # no `with` block: the web app's own start-up (which would start a second set of
        # background services) is not run; the daemon starts them itself
        self._client = TestClient(server.app, raise_server_exceptions=False)
        self._client.headers["X-Companion-Token"] = server.TOKEN

    def request(self, method: str, path: str, body=None) -> dict:
        try:
            if isinstance(body, dict) and "__file" in body:
                f = body["__file"]
                files = {f["field"]: (f["name"], base64.b64decode(f["b64"]), f.get("type") or "application/octet-stream")}
                r = self._client.request(method, path, files=files)
            elif body is None:
                r = self._client.request(method, path)
            else:
                r = self._client.request(method, path, json=body)
            return {"status": r.status_code, "body": r.text}
        except Exception as e:
            log.exception("Bridge call %s %s failed", method, path)
            return {"status": 500, "body": f'{{"detail": "{type(e).__name__}: {e}"}}'}


def page_html() -> str:
    """The UI as one self-contained document (CSS and JS inlined, token filled in)."""
    from app.web import server

    html = (STATIC / "index.html").read_text(encoding="utf-8").replace("__TOKEN__", server.TOKEN)
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    html = html.replace('<link rel="stylesheet" href="/static/style.css">', f"<style>\n{css}\n</style>")
    html = html.replace('<script src="/static/app.js"></script>',
                        f"<script>window.MD_NATIVE = true;</script>\n<script>\n{js}\n</script>")
    return html


class MDApp:
    def __init__(self):
        from app.web import server

        self.state = server.get_state()           # companion, scheduler, workflow, voice, services
        self.state.extra_hotkeys = {"open_window": self.show}
        self.window = None
        self._want_window = threading.Event()
        self._pending_tab: str | None = None
        self._quitting = False

    # ---- window (created on first use) ------------------------------------------------------
    def show(self, tab: str | None = None) -> None:
        """Open MD's window — from the tray, Ctrl+Alt+N, a second launch, or a voice command."""
        if self.window is None:
            self._pending_tab = tab
            self._want_window.set()      # the main thread creates the window
            return
        self.window.show()
        self.window.restore()
        self.window.on_top = True
        self.window.on_top = False
        if tab:
            self.window.evaluate_js(f"selectTab(document.getElementById('tab-{tab}'))")

    def _on_closing(self):
        if self._quitting:
            return True
        self.window.hide()   # closing the window keeps MD running invisibly
        return False

    def _on_loaded(self):
        if self._pending_tab:
            self.window.evaluate_js(f"selectTab(document.getElementById('tab-{self._pending_tab}'))")
            self._pending_tab = None

    def run_ui_when_needed(self) -> None:
        """Main thread: stay idle (no UI at all) until the window is first requested, then run it."""
        import webview

        while not self._want_window.wait(1.0):
            if self._quitting:
                return
        name = get_config().assistant.name
        self.window = webview.create_window(name, html=page_html(), js_api=Bridge(), width=940, height=780,
                                            min_size=(480, 420), text_select=True)
        self.window.events.closing += self._on_closing
        self.window.events.loaded += self._on_loaded
        storage = get_config().dir("data/webview")  # remembers the page's own preferences (voice, tab)
        log.info("Opening MD's window")
        webview.start(gui="edgechromium", private_mode=False, storage_path=str(storage))

    # ---- lifecycle -------------------------------------------------------------------------------
    def start_background(self) -> None:
        threading.Thread(target=self.state.start_background, daemon=True, name="md-start").start()

    def talk(self) -> None:
        services = self.state.services
        if services is not None:
            threading.Thread(target=services.talk_hotkey, daemon=True).start()

    def quit(self) -> None:
        self._quitting = True
        try:
            if self.state.services:
                self.state.services.stop()
            self.state.scheduler.shutdown()
        finally:
            log.info("MD stopped from the tray")
            os._exit(0)


def run(show_window: bool = False) -> int:
    """Entry point: single instance, tray icon, background services; the window only on request."""
    from app.accessibility.pointer import make_dpi_aware
    from app.single_instance import SingleInstance
    from app.tray import start_tray

    with SingleInstance() as instance:
        if not instance.primary:
            instance.signal_running()  # already running: bring its window up and leave
            log.info("MD already running; asked it to show its window")
            return 0
        make_dpi_aware()
        _enable_crash_log()
        app = MDApp()
        app.start_background()
        instance.on_show_request(app.show)   # launching MD again = "show me the window"
        start_tray(app)
        log.info("MD running invisibly in the background (no window, no server)")
        if show_window:
            app.show()
        app.run_ui_when_needed()
    return 0


def _enable_crash_log() -> None:
    """Native crashes (audio, COM, WebView) leave no Python error; dump stacks to data/logs/crash.log."""
    import faulthandler

    cfg = get_config()
    global _crash_file
    _crash_file = open(cfg.dir(cfg.storage.logs_dir) / "crash.log", "a", encoding="utf-8")
    _crash_file.write(f"\n--- started {datetime.now().isoformat(timespec='seconds')} ---\n")
    _crash_file.flush()
    faulthandler.enable(file=_crash_file, all_threads=True)


_crash_file = None

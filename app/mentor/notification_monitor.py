"""Watch for new notifications anywhere on the laptop and alert the user straight away.

Two sources, because neither alone catches everything:

1. Windows notification database — every toast Windows receives from any app (WhatsApp
   Desktop, Outlook, Teams, browsers with notifications allowed...). Captured even while
   Focus / Do-not-disturb hides the pop-ups.
2. Browser tabs and window titles — sites put unread counts in the tab title, e.g.
   "(3) WhatsApp", "Inbox (12) - you@gmail.com - Gmail", "(2) TikTok". Read with UI
   Automation from every Chrome/Edge/Brave tab, including background tabs. This works even
   when the browser's or the site's notifications are switched off, as long as the site is
   open in a tab.

Limitation: if an app's notifications are switched off in Windows Settings *and* it doesn't
show a count in its window/tab title, Windows never receives anything and it can't be caught.
"""

from __future__ import annotations

import os
import re
import sqlite3
import threading
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from app.config import get_config
from app.logger import get_logger
from app.mentor.notifications import notify

log = get_logger(__name__)

WPN_DB = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/Windows/Notifications/wpndatabase.db"
BROWSER_PROCESSES = {"chrome.exe", "msedge.exe", "brave.exe", "opera.exe", "vivaldi.exe"}
OWN_APPS = ("python", "ai companion", "plyer")

PLATFORMS = [
    ("WhatsApp", r"whatsapp"), ("Gmail", r"gmail|mail\.google"), ("Outlook", r"outlook"), ("TikTok", r"tiktok"),
    ("Instagram", r"instagram"), ("Messenger", r"messenger"), ("Facebook", r"facebook"),
    ("YouTube", r"youtube"), ("LinkedIn", r"linkedin"), ("X", r"\bx\b|twitter"), ("Telegram", r"telegram"),
    ("Discord", r"discord"), ("Slack", r"slack"), ("Teams", r"\bteams\b"), ("Snapchat", r"snapchat"),
]
_COUNT_PREFIX = re.compile(r"^\s*\((\d+)\+?\)\s*")
_COUNT_ANY = re.compile(r"\((\d+)\+?\)")


@dataclass
class Alert:
    app: str
    title: str
    body: str = ""
    source: str = "windows"

    def spoken(self, read_content: bool) -> tuple[str, str]:
        heading = self.app if not self.title or self.title.lower() == self.app.lower() else f"{self.app} — {self.title}"
        if read_content and self.body:
            return heading, self.body
        return heading, "New notification" if self.source == "windows" else self.body


# ---- 1. Windows notification database ----------------------------------------------

def parse_toast(payload: bytes | str) -> tuple[str, str, str]:
    """(title, body, attribution) from a toast's XML payload."""
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8", errors="replace")
    try:
        root = ET.fromstring(payload)
    except ET.ParseError:
        return "", "", ""
    texts, attribution = [], ""
    for el in root.iter("text"):
        value = "".join(el.itertext()).strip()
        if not value:
            continue
        if el.get("placement") == "attribution":
            attribution = value
        else:
            texts.append(value)
    return (texts[0] if texts else ""), " ".join(texts[1:]), attribution


def app_name(primary_id: str, attribution: str = "") -> str:
    from app.automation.launcher import start_apps

    by_id = {v.lower(): k for k, v in start_apps().items()}
    name = by_id.get(primary_id.lower())
    if not name:
        base = primary_id.split("!")[0]
        base = re.sub(r"_[a-z0-9]{13}$", "", base)          # package family hash
        base = base.split(".")[-1] if "." in base else base  # 5319275A.WhatsAppDesktop -> WhatsAppDesktop
        name = re.sub(r"Desktop$", "", base) or primary_id
    name = name.title() if name.islower() else name
    name = next((p for p, pattern in PLATFORMS if re.search(pattern, name, re.I)), name)
    if attribution:
        site = re.sub(r"^(?:via|from)\s+", "", attribution, flags=re.I)
        for platform, pattern in PLATFORMS:
            if re.search(pattern, site, re.I):
                return platform
        return f"{name} ({site})"
    return name


class WindowsToastSource:
    def __init__(self, db_path: Path = WPN_DB):
        self.db_path = db_path
        self.last_order: int | None = None

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True, timeout=2)

    def poll(self) -> list[Alert]:
        if not self.db_path.exists():
            return []
        try:
            con = self._connect()
            try:
                if self.last_order is None:  # don't announce what was already there at start-up
                    self.last_order = con.execute("SELECT COALESCE(MAX([Order]), 0) FROM Notification").fetchone()[0]
                    return []
                rows = con.execute(
                    "SELECT n.[Order], h.PrimaryId, n.Payload FROM Notification n "
                    "JOIN NotificationHandler h ON h.RecordId = n.HandlerId "
                    "WHERE n.Type = 'toast' AND n.[Order] > ? ORDER BY n.[Order]", (self.last_order,)).fetchall()
            finally:
                con.close()
        except sqlite3.Error as e:
            log.debug("Notification DB busy: %s", e)
            return []
        alerts = []
        ignore = [a.lower() for a in get_config().notifications.ignore_apps]
        for order, primary_id, payload in rows:
            self.last_order = max(self.last_order, order)
            pid = (primary_id or "").lower()
            if any(own in pid for own in OWN_APPS) or any(i in pid for i in ignore):
                continue
            title, body, attribution = parse_toast(payload or b"")
            if not title and not body:
                continue
            alerts.append(Alert(app_name(primary_id or "", attribution), title, body, "windows"))
        return alerts


# ---- 2. Browser tabs and window titles ---------------------------------------------------

def parse_title(title: str) -> tuple[str, int, str] | None:
    """('Gmail', 12, 'Inbox - you@gmail.com - Gmail') for a title that carries an unread count."""
    platform = next((p for p, pattern in PLATFORMS if re.search(pattern, title, re.I)), None)
    if not platform:
        return None
    m = _COUNT_PREFIX.match(title) or _COUNT_ANY.search(title)
    if not m:
        return platform, 0, title.strip()
    key = (title[: m.start()] + title[m.end():]).strip(" -|")
    return platform, int(m.group(1)), re.sub(r"\s{2,}", " ", key)


def account_hint(key: str) -> str:
    m = re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", key)
    return m.group(0) if m else ""


class TitleSource:
    """Unread counts from browser tab titles and top-level window titles."""

    def __init__(self):
        self.counts: dict[str, int] | None = None

    def _titles(self) -> list[str]:
        import psutil
        import uiautomation as auto

        titles: list[str] = []
        for win in auto.GetRootControl().GetChildren():
            try:
                name = win.Name
                proc = psutil.Process(win.ProcessId).name().lower()
            except (psutil.Error, OSError, Exception):
                continue
            if proc in BROWSER_PROCESSES:
                titles.extend(self._tab_names(win))
            elif name:
                titles.append(name)
        return titles

    @staticmethod
    def _tab_names(win, depth: int = 0) -> list[str]:
        names: list[str] = []
        if depth > 14:
            return names
        try:
            children = win.GetChildren()
        except Exception:
            return names
        for child in children:
            kind = child.ControlTypeName
            if kind == "TabItemControl":
                names.append(child.Name)
            elif kind in ("PaneControl", "GroupControl", "TabControl", "ToolBarControl", "CustomControl"):
                names.extend(TitleSource._tab_names(child, depth + 1))
        return names

    def poll(self) -> list[Alert]:
        try:
            titles = self._titles()
        except Exception as e:
            log.debug("Title scan failed: %s", e)
            return []
        current: dict[str, int] = {}
        platforms: dict[str, str] = {}
        for title in titles:
            parsed = parse_title(title)
            if parsed:
                platform, count, key = parsed
                current[key] = max(count, current.get(key, 0))
                platforms[key] = platform
        if self.counts is None:
            self.counts = current
            return []
        alerts = []
        for key, count in current.items():
            before = self.counts.get(key, 0)
            if count > before:
                who = account_hint(key)
                new = count - before
                body = f"{new} new" + (f" ({count} unread)" if count != new else "") + (f" for {who}" if who else "")
                alerts.append(Alert(platforms[key], "", body, "tabs"))
        # keep counts of tabs that disappeared briefly (tab reloads), forget them only when they drop
        self.counts = {**{k: v for k, v in self.counts.items() if k not in current}, **current}
        return alerts


# ---- the monitor -------------------------------------------------------------------------

class NotificationMonitor(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True, name="notification-monitor")
        self._stop = threading.Event()
        self.toasts = WindowsToastSource()
        self.titles = TitleSource()
        self._recent: dict[tuple, float] = {}

    def stop(self) -> None:
        self._stop.set()

    def _deliver(self, alert: Alert) -> None:
        key = (alert.app, alert.title, alert.body)
        now = time.time()
        if now - self._recent.get(key, 0) < 30:  # the same toast is sometimes stored twice
            return
        self._recent = {k: t for k, t in self._recent.items() if now - t < 120}
        self._recent[key] = now
        heading, message = alert.spoken(get_config().notifications.read_content)
        notify(heading, message, toast=False)

    def run(self) -> None:
        try:
            import uiautomation as auto

            init = auto.UIAutomationInitializerInThread(debug=False)
        except Exception:
            init = None
        tick = 0
        try:
            while not self._stop.is_set():
                cfg = get_config()
                n = cfg.notifications
                if n.enabled and cfg.feature_on("notifications"):
                    if n.windows_toasts:
                        for alert in self.toasts.poll():
                            self._deliver(alert)
                    # tab scans cost ~1 s of CPU, so run them less often than the database check
                    if n.browser_tabs and tick % 3 == 0:
                        for alert in self.titles.poll():
                            self._deliver(alert)
                tick += 1
                self._stop.wait(max(1.0, float(n.poll_seconds)))
        finally:
            if init is not None:
                del init

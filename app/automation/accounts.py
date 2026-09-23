"""Open websites in the right account.

Each Chrome / Edge / Brave *profile* is a separate signed-in identity, so "which account"
maps to "which browser profile". Accounts can be labelled per platform in Settings
(e.g. YouTube → "NexTube" = Chrome "Profile 5"); for Google sites a second Google
account signed in inside the same profile can be chosen with `google_index`.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from app.config import get_config
from app.interaction import match_option, words
from app.logger import audit, get_logger

log = get_logger(__name__)

LOCAL = Path(os.environ.get("LOCALAPPDATA", ""))
BROWSERS = {
    "chrome": {"data": LOCAL / "Google/Chrome/User Data",
               "exe": ["chrome", r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                       r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
                       str(LOCAL / "Google/Chrome/Application/chrome.exe")]},
    "edge": {"data": LOCAL / "Microsoft/Edge/User Data",
             "exe": ["msedge", r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                     r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"]},
    "brave": {"data": LOCAL / "BraveSoftware/Brave-Browser/User Data",
              "exe": ["brave", r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
                      str(LOCAL / "BraveSoftware/Brave-Browser/Application/brave.exe")]},
}

# Platforms where people commonly have more than one account
PLATFORMS = {
    "gmail": "https://mail.google.com/mail/u/{g}/",
    "email": "https://mail.google.com/mail/u/{g}/",
    "youtube": "https://www.youtube.com/?authuser={g}",
    "youtube studio": "https://studio.youtube.com/?authuser={g}",
    "google drive": "https://drive.google.com/drive/u/{g}/",
    "drive": "https://drive.google.com/drive/u/{g}/",
    "google calendar": "https://calendar.google.com/calendar/u/{g}/r",
    "calendar": "https://calendar.google.com/calendar/u/{g}/r",
    "instagram": "https://www.instagram.com/",
    "facebook": "https://www.facebook.com/",
    "messenger": "https://www.messenger.com/",
    "tiktok": "https://www.tiktok.com/",
    "twitter": "https://x.com/",
    "x": "https://x.com/",
    "linkedin": "https://www.linkedin.com/",
    "whatsapp web": "https://web.whatsapp.com/",
    "outlook": "https://outlook.live.com/mail/",
    "chatgpt": "https://chatgpt.com/",
    "canva": "https://www.canva.com/",
}
GOOGLE = {"gmail", "email", "youtube", "youtube studio", "google drive", "drive", "google calendar", "calendar"}


@dataclass
class Account:
    label: str
    browser: str = "chrome"
    profile: str = "Default"
    google_index: int = 0
    email: str = ""

    def describe(self) -> str:
        return self.label + (f" ({self.email})" if self.email and self.email.lower() not in self.label.lower() else "")


def browser_exe(browser: str) -> str | None:
    for candidate in BROWSERS.get(browser, {}).get("exe", []):
        found = shutil.which(candidate) or (candidate if Path(candidate).is_file() else None)
        if found:
            return found
    return None


def browser_profiles(browser: str | None = None) -> list[Account]:
    """Profiles of installed Chromium browsers, read from their 'Local State' file."""
    found: list[Account] = []
    for name, info in BROWSERS.items():
        if browser and name != browser:
            continue
        state = info["data"] / "Local State"
        if not state.is_file() or not browser_exe(name):
            continue
        try:
            cache = json.loads(state.read_text(encoding="utf-8")).get("profile", {}).get("info_cache", {})
        except (OSError, json.JSONDecodeError) as e:
            log.warning("Couldn't read %s profiles: %s", name, e)
            continue
        for directory, meta in cache.items():
            label = meta.get("name") or directory
            if name != "chrome":
                label += f" ({name.title()})"
            found.append(Account(label=label, browser=name, profile=directory, email=meta.get("user_name", "")))
    return found


def platform_key(target: str) -> str | None:
    t = words(target).removeprefix("the ").removesuffix(" app").removesuffix(" account").strip()
    if t in PLATFORMS:
        return t
    for key in sorted(PLATFORMS, key=len, reverse=True):
        if re.fullmatch(rf"{re.escape(key)}(\.com)?", t):
            return key
    return None


def accounts_for(platform: str) -> list[Account]:
    """Accounts configured for this platform in Settings, else every browser profile."""
    configured = (get_config().accounts or {}).get(platform) or []
    if not configured and platform == "email":
        configured = (get_config().accounts or {}).get("gmail") or []
    if configured:
        return [Account(label=str(a.get("label") or a.get("profile") or "Account"),
                        browser=str(a.get("browser") or "chrome"), profile=str(a.get("profile") or "Default"),
                        google_index=int(a.get("google_index") or 0), email=str(a.get("email") or ""))
                for a in configured if isinstance(a, dict)]
    return browser_profiles()


_ACCOUNT_SPLIT = re.compile(
    r"^(?P<site>.+?)\s+(?:on|with|from|using|in|for|of|par|pe|se|wala|wali|wale)\s+(?:my\s+|the\s+)?(?P<acct>.+?)"
    r"(?:\s+(?:account|profile|id|channel|wala|wali|wale))?$", re.I)


def split_target(target: str) -> tuple[str, str | None]:
    """'youtube on nextube account' -> ('youtube', 'nextube'); 'gmail' -> ('gmail', None)."""
    m = _ACCOUNT_SPLIT.match(target.strip())
    if m and platform_key(m["site"]):
        return m["site"], m["acct"]
    # "<account> wala youtube" / "nextube youtube"
    for key in sorted(PLATFORMS, key=len, reverse=True):
        m2 = re.fullmatch(rf"(?P<acct>.+?)\s+(?:wala\s+|wali\s+|ka\s+|ki\s+|ke\s+|account\s+)?{re.escape(key)}", target.strip(), re.I)
        if m2:
            return key, m2["acct"]
    return target, None


def find_account(spoken: str, accounts: list[Account]) -> Account | None:
    options = [a.label for a in accounts]
    choice = match_option(spoken, options)
    if choice is None:  # try matching the e-mail address or profile folder name too
        by_email = {a.email.split("@")[0]: a for a in accounts if a.email}
        email_choice = match_option(spoken, list(by_email))
        if email_choice:
            return by_email[email_choice]
        return None
    return next(a for a in accounts if a.label == choice)


def url_for(platform: str, account: Account) -> str:
    return PLATFORMS[platform].format(g=account.google_index if platform in GOOGLE else 0)


def open_in_profile(url: str | None, account: Account) -> str | None:
    exe = browser_exe(account.browser)
    if not exe:
        raise RuntimeError(f"{account.browser} is not installed")
    subprocess.Popen([exe, f"--profile-directory={account.profile}"] + ([url] if url else []), close_fds=True)
    audit("open_url_in_profile", url=url, browser=account.browser, profile=account.profile)
    return url

"""Open websites and searches in the user's default browser."""

from __future__ import annotations

import re
import webbrowser
from urllib.parse import quote_plus

from app.logger import audit

SITES = {
    "gmail": "https://mail.google.com",
    "email": "https://mail.google.com",
    "youtube": "https://www.youtube.com",
    "google": "https://www.google.com",
    "whatsapp": "https://web.whatsapp.com",
    "facebook": "https://www.facebook.com",
    "maps": "https://maps.google.com",
    "google maps": "https://maps.google.com",
    "calendar": "https://calendar.google.com",
    "drive": "https://drive.google.com",
    "github": "https://github.com",
    "chatgpt": "https://chatgpt.com",
    "claude": "https://claude.ai",
    "wikipedia": "https://www.wikipedia.org",
    "news": "https://news.google.com",
    "quran": "https://quran.com",
}

_DOMAIN = re.compile(r"^[\w-]+(\.[\w-]+)+(/\S*)?$")


def resolve(site: str) -> str | None:
    site = site.strip().lower().rstrip(".")
    if site in SITES:
        return SITES[site]
    if site.startswith(("http://", "https://")):
        return site
    if _DOMAIN.match(site.replace(" ", "")):
        return "https://" + site.replace(" ", "")
    return None


def open_url(url: str) -> str:
    webbrowser.open(url)
    audit("open_url", url=url)
    return url


def open_site(site: str) -> str:
    url = resolve(site)
    if not url:
        return search(site)
    open_url(url)
    return f"Opening {site}."


def search(query: str, engine: str = "google") -> str:
    if engine == "youtube":
        open_url(f"https://www.youtube.com/results?search_query={quote_plus(query)}")
    else:
        open_url(f"https://www.google.com/search?q={quote_plus(query)}")
    return f"Searching {engine} for {query}."

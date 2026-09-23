"""Fetch and read web content aloud without needing to look at a browser:
quick web answers (DuckDuckGo, falling back to Wikipedia) and plain-text extraction of any page."""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser
from urllib.parse import parse_qs, unquote, urlparse

import requests

from app.llm import LLMError, get_llm
from app.logger import audit, get_logger

log = get_logger(__name__)

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AIcompanion/1.0"}


class _TextExtractor(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "nav", "footer", "header", "form"}

    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self.title = ""
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag in {"p", "br", "li", "h1", "h2", "h3", "h4", "div", "tr"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self._skip:
            self.parts.append(data)

    def text(self) -> str:
        raw = "".join(self.parts)
        lines = [re.sub(r"\s+", " ", ln).strip() for ln in raw.splitlines()]
        return "\n".join(ln for ln in lines if len(ln) > 2)


def page_text(url: str, limit: int = 20_000) -> tuple[str, str]:
    """Return (title, readable text) of a web page."""
    r = requests.get(url, headers=HEADERS, timeout=20)
    r.raise_for_status()
    parser = _TextExtractor()
    parser.feed(r.text)
    audit("fetch_page", url=url)
    return parser.title.strip(), parser.text()[:limit]


def web_search(query: str, max_results: int = 5) -> list[dict]:
    """Search the web; returns [{title, url, snippet}]. DuckDuckGo first, Wikipedia if it's blocked."""
    try:
        results = _duckduckgo(query, max_results)
    except requests.RequestException as e:
        log.info("DuckDuckGo search failed: %s", e)
        results = []
    if not results:
        results = _wikipedia(query, min(max_results, 3))
    audit("web_search", query=query, results=len(results))
    return results


def _wikipedia(query: str, max_results: int = 3) -> list[dict]:
    api = "https://en.wikipedia.org/w/api.php"
    r = requests.get(api, params={"action": "query", "list": "search", "srsearch": query, "format": "json",
                                  "srlimit": max_results}, headers=HEADERS, timeout=20)
    r.raise_for_status()
    titles = [hit["title"] for hit in r.json().get("query", {}).get("search", [])]
    if not titles:
        return []
    r = requests.get(api, params={"action": "query", "prop": "extracts", "exintro": 1, "explaintext": 1,
                                  "exsentences": 4, "titles": "|".join(titles), "format": "json"},
                     headers=HEADERS, timeout=20)
    r.raise_for_status()
    pages = {p["title"]: p.get("extract", "") for p in r.json().get("query", {}).get("pages", {}).values()}
    return [{"title": f"{t} (Wikipedia)", "url": f"https://en.wikipedia.org/wiki/{t.replace(' ', '_')}",
             "snippet": pages.get(t, "")} for t in titles]


def _duckduckgo(query: str, max_results: int) -> list[dict]:
    r = requests.post("https://html.duckduckgo.com/html/", data={"q": query}, headers=HEADERS, timeout=20)
    r.raise_for_status()
    if r.status_code != 200:  # 202 = bot challenge page
        return []
    results = []
    blocks = re.findall(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>(.*?)(?=<a[^>]+class="result__a"|$)',
                        r.text, re.S)
    for href, title, rest in blocks:
        snippet = re.search(r'class="result__snippet"[^>]*>(.*?)</a>', rest, re.S)
        url = href
        if "uddg=" in href:
            url = unquote(parse_qs(urlparse(href).query).get("uddg", [href])[0])
        clean = lambda s: html.unescape(re.sub(r"<[^>]+>", "", s)).strip()
        results.append({"title": clean(title), "url": url, "snippet": clean(snippet.group(1)) if snippet else ""})
        if len(results) >= max_results:
            break
    return results


def answer_from_web(question: str) -> str:
    try:
        results = web_search(question)
    except requests.RequestException as e:
        return f"I couldn't search the web: {e}"
    if not results:
        return "I didn't find anything on the web for that."
    context = "\n".join(f"- {r['title']}: {r['snippet']}" for r in results)
    try:
        return get_llm().ask(
            f"Question: {question}\n\nSearch results:\n{context}",
            system="Answer the question in 2-3 short sentences using only the search results. Mention the source site name.",
            temperature=0.2,
            max_tokens=150,
        )
    except LLMError:
        return "Top result: " + results[0]["title"] + ". " + results[0]["snippet"]


def summarize_page(url: str) -> str:
    try:
        title, text = page_text(url)
    except requests.RequestException as e:
        return f"I couldn't open that page: {e}"
    if not text:
        return f"The page '{title}' has no readable text."
    try:
        summary = get_llm().ask(
            f"Page title: {title}\n\n{text[:6000]}",
            system="Summarise this web page in 3-4 short spoken sentences for a blind user.",
            temperature=0.2,
            max_tokens=200,
        )
    except LLMError:
        summary = text[:500]
    return f"{title}. {summary}" if title else summary

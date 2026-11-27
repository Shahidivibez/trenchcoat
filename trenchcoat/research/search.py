"""Zero-key web search: DuckDuckGo HTML + multilingual Google/Bing News RSS,
with optional Serper.dev / Tavily boosters when keys exist.

Every provider fails soft: a blocked provider never kills a scout run.
"""

from __future__ import annotations

import html as html_lib
import re
import time
import urllib.parse
from dataclasses import dataclass
from xml.etree import ElementTree

import httpx

from ..config import Config
from ..store import Store

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"

# language → (Google News hl, gl, ceid) presets for local-language hunting
LOCALE_PRESETS = {
    "en": ("en", "US", "US:en"),
    "de": ("de", "DE", "DE:de"),
    "fr": ("fr", "FR", "FR:fr"),
    "es": ("es", "ES", "ES:es"),
    "it": ("it", "IT", "IT:it"),
    "pt": ("pt-BR", "BR", "BR:pt-419"),
    "tr": ("tr", "TR", "TR:tr"),
    "ko": ("ko", "KR", "KR:ko"),
    "ja": ("ja", "JP", "JP:ja"),
    "zh": ("zh-CN", "CN", "CN:zh-Hans"),
    "hi": ("hi", "IN", "IN:hi"),
    "pl": ("pl", "PL", "PL:pl"),
    "nl": ("nl", "NL", "NL:nl"),
    "sv": ("sv", "SE", "SE:sv"),
}


@dataclass
class SearchResult:
    url: str
    title: str
    snippet: str = ""
    source: str = ""  # which provider found it
    published: str | None = None
    language: str = "en"
    query: str = ""

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def _clean_ddg_url(href: str) -> str:
    # DDG wraps results in /l/?uddg=<encoded>
    if href.startswith("//"):
        href = "https:" + href
    parsed = urllib.parse.urlparse(href)
    if "duckduckgo.com" in parsed.netloc and parsed.path.startswith("/l/"):
        qs = urllib.parse.parse_qs(parsed.query)
        if "uddg" in qs:
            return qs["uddg"][0]
    return href


def ddg_search(query: str, limit: int = 8, timeout: float = 20) -> list[SearchResult]:
    """DuckDuckGo HTML endpoint — no key, no JS. Fails soft on blocks."""
    out: list[SearchResult] = []
    try:
        resp = httpx.post(
            "https://html.duckduckgo.com/html/",
            data={"q": query, "kl": "wt-wt"},
            headers={"User-Agent": UA, "Content-Type": "application/x-www-form-urlencoded"},
            timeout=timeout, follow_redirects=True,
        )
        if resp.status_code != 200:
            return out
        body = resp.text
        # result blocks: <a rel="nofollow" class="result__a" href="...">Title</a> ... snippet
        for m in re.finditer(
            r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', body, re.S
        ):
            url = _clean_ddg_url(html_lib.unescape(m.group(1)))
            title = re.sub(r"<[^>]+>", "", m.group(2))
            title = html_lib.unescape(title).strip()
            if not url.startswith("http") or "duckduckgo.com" in url:
                continue
            out.append(SearchResult(url=url, title=title, source="ddg", query=query))
            if len(out) >= limit:
                break
        # snippets (best-effort, aligned by order)
        snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', body, re.S)
        for i, s in enumerate(snippets[: len(out)]):
            out[i].snippet = html_lib.unescape(re.sub(r"<[^>]+>", "", s)).strip()
    except httpx.HTTPError:
        pass
    return out


def google_news_rss(query: str, lang: str = "en", limit: int = 8,
                    timeout: float = 20) -> list[SearchResult]:
    hl, gl, ceid = LOCALE_PRESETS.get(lang, LOCALE_PRESETS["en"])
    url = (
        "https://news.google.com/rss/search?"
        + urllib.parse.urlencode({"q": query, "hl": hl, "gl": gl, "ceid": ceid})
    )
    return _parse_rss(url, "gnews", lang, query, limit, timeout)


def bing_news_rss(query: str, lang: str = "en", limit: int = 8,
                  timeout: float = 20) -> list[SearchResult]:
    mkt = {"de": "de-DE", "fr": "fr-FR", "es": "es-ES", "it": "it-IT",
           "pt": "pt-BR", "tr": "tr-TR", "ko": "ko-KR", "ja": "ja-JP"}.get(lang, "en-US")
    url = "https://www.bing.com/news/search?" + urllib.parse.urlencode(
        {"q": query, "format": "rss", "mkt": mkt}
    )
    return _parse_rss(url, "bing", lang, query, limit, timeout)


def _parse_rss(url: str, source: str, lang: str, query: str, limit: int,
               timeout: float) -> list[SearchResult]:
    out: list[SearchResult] = []
    try:
        resp = httpx.get(url, headers={"User-Agent": UA}, timeout=timeout, follow_redirects=True)
        if resp.status_code != 200:
            return out
        root = ElementTree.fromstring(resp.content)
        for item in root.iter("item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            desc = re.sub(r"<[^>]+>", "", item.findtext("description") or "").strip()
            pub = item.findtext("pubDate")
            if not link:
                continue
            out.append(SearchResult(url=link, title=html_lib.unescape(title),
                                    snippet=html_lib.unescape(desc)[:500], source=source,
                                    published=pub, language=lang, query=query))
            if len(out) >= limit:
                break
    except (httpx.HTTPError, ElementTree.ParseError):
        pass
    return out


def serper_search(cfg: Config, query: str, limit: int = 8) -> list[SearchResult]:
    if not cfg.serper_api_key:
        return []
    try:
        resp = httpx.post(
            "https://google.serper.dev/search",
            headers={"X-API-KEY": cfg.serper_api_key, "Content-Type": "application/json"},
            json={"q": query, "num": limit}, timeout=20,
        )
        if resp.status_code != 200:
            return []
        data = resp.json()
        return [
            SearchResult(url=r.get("link", ""), title=r.get("title", ""),
                         snippet=r.get("snippet", ""), source="serper",
                         published=r.get("date"), query=query)
            for r in data.get("organic", [])[:limit] if r.get("link")
        ]
    except (httpx.HTTPError, ValueError):
        return []


def tavily_search(cfg: Config, query: str, limit: int = 8) -> list[SearchResult]:
    if not cfg.tavily_api_key:
        return []
    try:
        resp = httpx.post(
            "https://api.tavily.com/search",
            json={"api_key": cfg.tavily_api_key, "query": query,
                  "max_results": limit, "search_depth": "basic"},
            timeout=25,
        )
        if resp.status_code != 200:
            return []
        return [
            SearchResult(url=r.get("url", ""), title=r.get("title", ""),
                         snippet=r.get("content", "")[:500], source="tavily", query=query)
            for r in resp.json().get("results", [])[:limit] if r.get("url")
        ]
    except (httpx.HTTPError, ValueError):
        return []


def search_all(cfg: Config, store: Store, query: str, lang: str = "en",
               limit: int = 8, use_rss: bool = True) -> list[SearchResult]:
    """Fan out across providers, dedupe by URL, keep provider diversity."""
    results: list[SearchResult] = []
    seen: set[str] = set()

    def add(rs: list[SearchResult]) -> None:
        for r in rs:
            key = r.url.split("#")[0].rstrip("/")
            if key and key not in seen:
                seen.add(key)
                results.append(r)

    add(ddg_search(query, limit))
    if use_rss:
        add(google_news_rss(query, lang, limit))
        add(bing_news_rss(query, lang, limit))
    add(serper_search(cfg, query, limit))
    add(tavily_search(cfg, query, limit))

    if not results:
        store.log("search-empty", f"No results for {query!r} (lang={lang})")
    time.sleep(0.4)  # politeness between queries
    return results[: limit * 2]

"""Primary-source extraction: trafilatura for HTML, pypdf for PDFs.

Returns clean text + metadata (title, publish date, sitename). The publish date
matters: the judge presumes facts public before the forecaster's view are priced.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from pathlib import Path

import httpx

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
MAX_BYTES = 8 * 1024 * 1024


@dataclass
class Extracted:
    url: str
    final_url: str
    title: str
    text: str
    published_at: str | None
    sitename: str
    content_type: str
    is_pdf: bool = False
    fetch_blocked: bool = False
    needs_browser: bool = False
    error: str | None = None

    @property
    def ok(self) -> bool:
        return bool(self.text and len(self.text) > 200 and not self.fetch_blocked)


def fetch_and_extract(url: str, timeout: float = 25) -> Extracted:
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True,
                          headers={"User-Agent": UA}) as client:
            resp = client.get(url)
    except httpx.HTTPError as e:
        return Extracted(url, url, "", "", None, "", "", error=str(e), needs_browser=True)

    ctype = resp.headers.get("content-type", "")
    final_url = str(resp.url)
    if resp.status_code in (401, 403, 429, 503):
        return Extracted(url, final_url, "", "", None, "", ctype,
                         fetch_blocked=True, needs_browser=True,
                         error=f"HTTP {resp.status_code}")
    if resp.status_code >= 400:
        return Extracted(url, final_url, "", "", None, "", ctype,
                         error=f"HTTP {resp.status_code}")

    content = resp.content[:MAX_BYTES]
    if "pdf" in ctype.lower() or final_url.lower().endswith(".pdf"):
        return _extract_pdf(url, final_url, content)
    return _extract_html(url, final_url, content)


def _extract_html(url: str, final_url: str, content: bytes) -> Extracted:
    try:
        import trafilatura

        html = content.decode("utf-8", errors="replace")
        text = trafilatura.extract(
            html, include_comments=False, include_tables=True,
            favor_precision=True, url=final_url,
        ) or ""
        meta = trafilatura.extract_metadata(html)
        title = (meta.title if meta else "") or ""
        date = (meta.date if meta else None)
        sitename = (meta.sitename if meta else "") or ""
        needs_browser = len(text) < 300 and bool(re.search(r"<script|__NEXT_DATA__|ng-app", html[:20000]))
        return Extracted(url, final_url, title, text, date, sitename, "text/html",
                         needs_browser=needs_browser)
    except Exception as e:  # trafilatura/lxml edge cases
        return Extracted(url, final_url, "", "", None, "", "text/html", error=str(e))


def _extract_pdf(url: str, final_url: str, content: bytes) -> Extracted:
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(content))
        pages = reader.pages[:20]  # judge reads at most 20 pages
        text = "\n".join((p.extract_text() or "") for p in pages)
        info = reader.metadata or {}
        title = str(info.get("/Title") or "") or Path(final_url).name
        return Extracted(url, final_url, title, text.strip(), None, "", "application/pdf", is_pdf=True)
    except Exception as e:
        return Extracted(url, final_url, "", "", None, "", "application/pdf",
                         is_pdf=True, error=str(e))


def save_pdf_attachment(content: bytes, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(content)
    return dest


def download_file(url: str, dest: Path, timeout: float = 30) -> Path | None:
    """Download a primary-source file (PDF/CSV) for attachment."""
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True,
                          headers={"User-Agent": UA}) as client:
            resp = client.get(url)
        if resp.status_code != 200 or len(resp.content) > 10 * 1024 * 1024:
            return None
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(resp.content)
        return dest
    except httpx.HTTPError:
        return None

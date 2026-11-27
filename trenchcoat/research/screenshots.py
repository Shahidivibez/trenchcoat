"""Playwright headless-Chromium screenshots.

Critical edge: Delphi's Judge opens each link once with a basic HTTP fetch — no
browser, no JS. X/Reddit/JS-heavy sites fail, and the judge only sees the URL.
For those we attach a screenshot that shows the URL and the capture date, and
paste the key text into the note itself.

Degrades gracefully when Playwright or Chromium is unavailable (e.g. CI without
the browser install step): the scout logs it and keeps going with text-only.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from ..config import Config
from ..store import Store

_BANNER_JS = """
(url) => {
    const d = document.createElement('div');
    d.style.cssText = 'position:fixed;top:0;left:0;right:0;z-index:2147483647;' +
        'background:#111;color:#fff;font:12px/1.5 monospace;padding:6px 10px;' +
        'border-bottom:2px solid #e74c3c;';
    d.textContent = 'URL: ' + url + '  |  Captured: ' + new Date().toISOString() + '  |  Trenchcoat evidence capture';
    document.body.appendChild(d);
}
"""


def browser_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            exe = p.chromium.executable_path
            return bool(exe and Path(exe).exists())
    except Exception:
        return False


def capture(url: str, dest: Path, cfg: Config, store: Store,
            timeout_ms: int = 30000) -> Path | None:
    """Screenshot a page with a URL+timestamp banner. Returns path or None."""
    if not cfg.playwright_enabled:
        return None
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        store.log("browser-missing", "Playwright not installed; skipping screenshot (pip install playwright && playwright install chromium)")
        return None

    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            page.wait_for_timeout(2500)  # let JS render
            try:
                page.evaluate(_BANNER_JS, url)
            except Exception:
                pass  # banner is best-effort; note carries URL+date regardless
            page.screenshot(path=str(dest), full_page=False)
            browser.close()
        store.log("screenshot", f"Captured {url} -> {dest.name}")
        return dest
    except Exception as e:
        store.log("browser-fail", f"Screenshot failed for {url}: {type(e).__name__}: {e}")
        return None


def capture_text(url: str, cfg: Config, store: Store, timeout_ms: int = 30000) -> str | None:
    """Render a JS page and return visible text (for pasting key lines into the note)."""
    if not cfg.playwright_enabled:
        return None
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            page.wait_for_timeout(2500)
            text = page.inner_text("body")
            browser.close()
            return text[:20000]
    except Exception as e:
        store.log("browser-fail", f"Text render failed for {url}: {e}")
        return None


def stamp() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")

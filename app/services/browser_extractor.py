from __future__ import annotations

from app.core.config import settings
from app.models.schemas import UrlContent


def extract_url_content_with_browser(url: str) -> UrlContent:
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ModuleNotFoundError as exc:
        raise RuntimeError("Playwright is not installed in the active environment") from exc

    timeout = max(3000, settings.content_agent_browser_timeout_ms)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.set_default_timeout(timeout)
            page.goto(url, wait_until="domcontentloaded")
            try:
                page.wait_for_load_state("networkidle", timeout=timeout)
            except PlaywrightTimeoutError:
                pass
            title = (page.title() or "Untitled").strip() or "Untitled"
            text = page.evaluate(
                """
                () => {
                  const root = document.querySelector('main') || document.querySelector('article') || document.body;
                  return (root?.innerText || document.body?.innerText || '').trim();
                }
                """
            )
            cleaned = " ".join((text or "").split())
            return UrlContent(url=url, title=title, text=cleaned[:120000])
        finally:
            browser.close()

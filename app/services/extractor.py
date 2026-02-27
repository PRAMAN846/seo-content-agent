from __future__ import annotations

import httpx
import trafilatura
from bs4 import BeautifulSoup

from app.models.schemas import UrlContent


async def _fetch_html(url: str) -> str:
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.text


def _extract_with_trafilatura(html: str, url: str) -> tuple[str, str]:
    text = trafilatura.extract(html, include_comments=False, include_tables=False) or ""
    metadata = trafilatura.extract_metadata(html, default_url=url)
    title = metadata.title if metadata and metadata.title else "Untitled"
    return title, text


def _extract_with_bs4(html: str) -> tuple[str, str]:
    soup = BeautifulSoup(html, "html.parser")
    title = (soup.title.string or "Untitled") if soup.title else "Untitled"

    for bad in soup(["script", "style", "noscript", "svg"]):
        bad.decompose()

    text = "\n".join(line.strip() for line in soup.get_text("\n").splitlines() if line.strip())
    return title, text


async def extract_url_content(url: str) -> UrlContent:
    html = await _fetch_html(url)
    title, text = _extract_with_trafilatura(html, url)

    if len(text.split()) < 150:
        title, text = _extract_with_bs4(html)

    return UrlContent(url=url, title=title, text=text[:120000])

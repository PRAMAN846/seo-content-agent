from __future__ import annotations

from urllib.parse import parse_qs, quote_plus, unquote, urlparse

import httpx
from bs4 import BeautifulSoup

from app.core.config import settings


def _normalize_url(url: str) -> str:
    cleaned = (url or "").strip()
    if not cleaned:
        return ""
    if cleaned.startswith("//"):
        cleaned = f"https:{cleaned}"
    return cleaned


def _clean_search_results(urls: list[str], *, limit: int) -> list[str]:
    seen: set[str] = set()
    cleaned_urls: list[str] = []
    for raw in urls:
        cleaned = _normalize_url(raw)
        if not cleaned.startswith(("http://", "https://")):
            continue
        if "duckduckgo.com" in cleaned and "uddg=" in cleaned:
            parsed = urlparse(cleaned)
            target = parse_qs(parsed.query).get("uddg", [""])[0]
            cleaned = unquote(target or "")
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            cleaned_urls.append(cleaned)
        if len(cleaned_urls) >= limit:
            break
    return cleaned_urls


def _search_with_serper(query: str, limit: int) -> list[str]:
    response = httpx.post(
        "https://google.serper.dev/search",
        headers={
            "X-API-KEY": settings.serper_api_key or "",
            "Content-Type": "application/json",
        },
        json={"q": query, "num": limit},
        timeout=20.0,
    )
    response.raise_for_status()
    payload = response.json()
    organic = payload.get("organic") or []
    urls = [item.get("link", "") for item in organic if item.get("link")]
    return _clean_search_results(urls, limit=limit)


def _search_with_duckduckgo(query: str, limit: int) -> list[str]:
    response = httpx.get(
        f"https://duckduckgo.com/html/?q={quote_plus(query)}",
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=20.0,
        follow_redirects=True,
    )
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    candidates: list[str] = []
    for anchor in soup.select("a.result__a, a.result-link, a[href]"):
        href = anchor.get("href", "").strip()
        if href:
            candidates.append(href)
    return _clean_search_results(candidates, limit=limit)


def search_public_web(query: str, limit: int | None = None) -> list[str]:
    if not settings.content_agent_search_enabled:
        return []

    final_limit = max(1, limit or settings.content_agent_search_result_count)
    cleaned_query = (query or "").strip()
    if not cleaned_query:
        return []

    if settings.serper_api_key:
        try:
            return _search_with_serper(cleaned_query, final_limit)
        except Exception:
            pass

    try:
        return _search_with_duckduckgo(cleaned_query, final_limit)
    except Exception:
        return []

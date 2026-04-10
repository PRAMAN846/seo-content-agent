from __future__ import annotations

import asyncio
import re
from typing import Optional

import httpx

from app.core.config import settings
from app.models.schemas import ArticleSummary, UrlContent
from app.services.browser_extractor import extract_url_content_with_browser
from app.services.content_agent_search import search_public_web
from app.services.extractor import extract_url_content
from app.services.source_collector import extract_urls_from_text
from app.services.summarizer import summarize_article

SITEMAP_LOC_RE = re.compile(r"<loc>\s*(.*?)\s*</loc>", re.IGNORECASE)


def _unique_urls(urls: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for url in urls:
        cleaned = (url or "").strip().rstrip("/ ")
        if cleaned and cleaned.startswith(("http://", "https://")) and cleaned not in seen:
            seen.add(cleaned)
            ordered.append(cleaned)
    return ordered


def parse_urls_from_field(raw_value: Optional[str]) -> list[str]:
    if not raw_value:
        return []
    direct = extract_urls_from_text(raw_value)
    if direct:
        return _unique_urls(direct)
    parts = [
        piece.strip()
        for piece in re.split(r"[\n,]", raw_value)
        if piece.strip().startswith(("http://", "https://"))
    ]
    return _unique_urls(parts)


async def _fetch_sitemap_urls(sitemap_url: str, limit: int = 2) -> list[str]:
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        response = await client.get(sitemap_url)
        response.raise_for_status()
        body = response.text
    urls = _unique_urls(SITEMAP_LOC_RE.findall(body))
    return urls[:limit]


async def _extract_many(urls: list[str]) -> tuple[list[UrlContent], list[str]]:
    extracted: list[UrlContent] = []
    errors: list[str] = []
    for url in urls:
        try:
            result = await extract_url_content(url)
            if len((result.text or "").split()) < 120 and settings.content_agent_browser_fallback_enabled:
                try:
                    browser_result = await asyncio.to_thread(extract_url_content_with_browser, url)
                    if len((browser_result.text or "").split()) > len((result.text or "").split()):
                        result = browser_result
                except Exception as browser_exc:
                    errors.append(f"{url} -> browser fallback failed: {browser_exc}")
            extracted.append(result)
            continue
        except Exception as exc:
            if settings.content_agent_browser_fallback_enabled:
                try:
                    browser_result = await asyncio.to_thread(extract_url_content_with_browser, url)
                    extracted.append(browser_result)
                    continue
                except Exception as browser_exc:
                    errors.append(f"{url} -> fetch failed: {exc}; browser fallback failed: {browser_exc}")
                    continue
            errors.append(f"{url} -> {exc}")
    return extracted, errors


def _summarize_many(extracted: list[UrlContent]) -> list[ArticleSummary]:
    return [summarize_article(article) for article in extracted]


def _build_research_markdown(
    *,
    search_query: str,
    search_urls: list[str],
    urls: list[str],
    extracted: list[UrlContent],
    summaries: list[ArticleSummary],
    errors: list[str],
) -> str:
    lines = ["# Research packet"]
    if search_query:
        lines.append(f"- Search query: {search_query}")
    if search_urls:
        lines.append(f"- Search-discovered URLs: {len(search_urls)}")
    lines.append(f"- URLs requested: {len(urls)}")
    lines.append(f"- URLs extracted successfully: {len(extracted)}")
    if errors:
        lines.append(f"- URL fetch errors: {len(errors)}")
    if search_urls:
        lines.append("")
        lines.append("## Search results used")
        lines.extend(f"- {url}" for url in search_urls)
    for article, summary in zip(extracted, summaries):
        lines.append("")
        lines.append(f"## {article.title}")
        lines.append(f"- Source URL: {article.url}")
        preview = " ".join((article.text or "").split())[:500]
        if preview:
            lines.append(f"- Source preview: {preview}")
        lines.append("")
        lines.append(summary.summary or "No summary available.")
    if errors:
        lines.append("")
        lines.append("## Fetch issues")
        lines.extend(f"- {error}" for error in errors)
    return "\n".join(lines)


def build_research_packet(
    *,
    prompt: str,
    brand_name: Optional[str] = None,
    default_target_country: Optional[str] = None,
    product_page_urls: Optional[str] = None,
    approved_internal_urls: Optional[str] = None,
    sitemap_url: Optional[str] = None,
) -> tuple[str, dict]:
    search_query_parts = [prompt.strip()]
    country = (default_target_country or "").strip()
    if country and country.lower() not in prompt.lower():
        search_query_parts.append(country)
    search_query = " ".join(part for part in search_query_parts if part).strip()
    search_urls = search_public_web(search_query, limit=3) if search_query else []

    candidate_urls = []
    candidate_urls.extend(search_urls)
    candidate_urls.extend(extract_urls_from_text(prompt or ""))
    candidate_urls.extend(parse_urls_from_field(product_page_urls))
    candidate_urls.extend(parse_urls_from_field(approved_internal_urls))
    candidate_urls = _unique_urls(candidate_urls)

    sitemap_urls: list[str] = []
    sitemap_errors: list[str] = []
    sitemap_url_clean = (sitemap_url or "").strip()
    if sitemap_url_clean:
        try:
            sitemap_urls = asyncio.run(_fetch_sitemap_urls(sitemap_url_clean, limit=2))
        except Exception as exc:
            sitemap_errors.append(f"{sitemap_url_clean} -> {exc}")

    urls = _unique_urls(candidate_urls + sitemap_urls)[:6]
    if not urls:
        return "", {"search_query": search_query, "search_urls": search_urls, "urls": [], "fetched_count": 0, "summary_count": 0, "errors": []}

    extracted, fetch_errors = asyncio.run(_extract_many(urls))
    summaries = _summarize_many(extracted) if extracted else []
    errors = [*sitemap_errors, *fetch_errors]
    markdown = _build_research_markdown(
        search_query=search_query,
        search_urls=search_urls,
        urls=urls,
        extracted=extracted,
        summaries=summaries,
        errors=errors,
    )
    metadata = {
        "brand_name": (brand_name or "").strip(),
        "search_query": search_query,
        "search_urls": search_urls,
        "urls": urls,
        "fetched_count": len(extracted),
        "summary_count": len(summaries),
        "errors": errors,
    }
    return markdown, metadata

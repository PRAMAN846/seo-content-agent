from __future__ import annotations

import asyncio
from typing import Tuple

from app.core.config import settings
from app.models.schemas import ArticleSummary, UrlContent
from app.services.extractor import extract_url_content
from app.services.seo_analyzer import analyze_summaries
from app.services.source_collector import collect_seed_urls
from app.services.summarizer import summarize_article
from app.services.url_validator import select_top_urls


async def build_source_analysis(
    query: str,
    seed_urls: list[str],
    ai_citations_text: str,
    ai_overview_text: str,
) -> Tuple[list[str], list[UrlContent], list[ArticleSummary], str]:
    candidates = collect_seed_urls(
        query=query,
        seed_urls=seed_urls,
        ai_citations_text=ai_citations_text,
        ai_overview_text=ai_overview_text,
    )
    top_urls = select_top_urls(candidates, settings.max_urls)
    if not top_urls:
        raise ValueError("No qualifying URLs found. Provide seed URLs or citation text containing links.")

    results = await asyncio.gather(
        *[extract_url_content(url) for url in top_urls],
        return_exceptions=True,
    )

    extracted: list[UrlContent] = []
    for result in results:
        if isinstance(result, Exception):
            continue
        extracted.append(result)

    if not extracted:
        raise ValueError("Could not extract content from selected URLs.")

    summaries = [summarize_article(article) for article in extracted]
    seo_analysis = analyze_summaries(query, summaries)
    return top_urls, extracted, summaries, seo_analysis

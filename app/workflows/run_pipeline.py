from __future__ import annotations

import asyncio

from app.core.config import settings
from app.models.schemas import RunArtifacts
from app.models.store import run_store
from app.services.exporter_google import export_to_local_doc
from app.services.extractor import extract_url_content
from app.services.seo_analyzer import analyze_summaries
from app.services.source_collector import collect_seed_urls
from app.services.summarizer import summarize_article
from app.services.url_validator import select_top_urls
from app.services.writer import write_article


async def process_run(
    run_id: str,
    query: str,
    seed_urls: list[str],
    ai_citations_text: str,
    ai_overview_text: str,
) -> None:
    run_store.update(run_id, status="running")

    try:
        candidates = collect_seed_urls(
            query=query,
            seed_urls=seed_urls,
            ai_citations_text=ai_citations_text,
            ai_overview_text=ai_overview_text,
        )
        top_urls = select_top_urls(candidates, settings.max_urls)

        if not top_urls:
            raise ValueError("No qualifying URLs found. Provide seed URLs or citation text containing links.")

        extracted = []
        tasks = [extract_url_content(url) for url in top_urls]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, Exception):
                continue
            extracted.append(result)

        if not extracted:
            raise ValueError("Could not extract content from selected URLs.")

        summaries = [summarize_article(a) for a in extracted]
        analysis = analyze_summaries(query, summaries)
        article = write_article(query, analysis)
        export_link = export_to_local_doc(query, article)

        artifacts = RunArtifacts(
            sources=top_urls,
            extracted_articles=extracted,
            summaries=summaries,
            seo_analysis=analysis,
            article_markdown=article,
            export_link=export_link,
        )

        run_store.update(run_id, status="completed", artifacts=artifacts)
    except Exception as exc:  # noqa: BLE001
        run_store.update(run_id, status="failed", error=str(exc))

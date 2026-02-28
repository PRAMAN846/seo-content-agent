from __future__ import annotations

from app.models.schemas import BriefArtifacts
from app.models.store import run_store
from app.services.brief_builder import build_brief, build_brief_from_query
from app.workflows.source_analysis import build_source_analysis


async def process_brief(
    brief_id: str,
    query: str,
    seed_urls: list[str],
    ai_citations_text: str,
    ai_overview_text: str,
) -> None:
    run_store.update_brief(brief_id, status="running", stage="collecting_sources", progress_percent=10, error=None)

    try:
        try:
            top_urls, extracted, summaries, seo_analysis = await build_source_analysis(
                query=query,
                seed_urls=seed_urls,
                ai_citations_text=ai_citations_text,
                ai_overview_text=ai_overview_text,
            )
            run_store.update_brief(brief_id, stage="building_brief", progress_percent=78)
            brief_markdown = build_brief(query, summaries, seo_analysis)
        except ValueError:
            top_urls, extracted, summaries = [], [], []
            seo_analysis = "No competitor sources were provided. This brief is based on the query only and should be reviewed."
            run_store.update_brief(brief_id, stage="building_brief", progress_percent=78)
            brief_markdown = build_brief_from_query(query)

        artifacts = BriefArtifacts(
            sources=top_urls,
            extracted_articles=extracted,
            summaries=summaries,
            seo_analysis=seo_analysis,
            brief_markdown=brief_markdown,
        )
        run_store.update_brief(
            brief_id,
            status="completed",
            stage="completed",
            progress_percent=100,
            artifacts=artifacts,
        )
    except Exception as exc:  # noqa: BLE001
        run_store.update_brief(
            brief_id,
            status="failed",
            stage="failed",
            progress_percent=100,
            error=str(exc),
        )

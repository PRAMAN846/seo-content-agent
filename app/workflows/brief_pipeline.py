from __future__ import annotations

from app.models.schemas import BriefArtifacts
from app.models.store import run_store
from app.services.brief_builder import (
    build_brief_from_query_with_customization,
    build_brief_with_customization,
)
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
        brief_record = run_store.get_brief_by_id(brief_id)
        user_settings = run_store.get_user_settings(brief_record.user_id) if brief_record else None
        brand_name = user_settings.brand_name if user_settings else ""
        brand_url = user_settings.brand_url if user_settings else ""
        prompt_override = user_settings.brief_prompt_override if user_settings else ""

        try:
            top_urls, extracted, summaries, seo_analysis = await build_source_analysis(
                query=query,
                seed_urls=seed_urls,
                ai_citations_text=ai_citations_text,
                ai_overview_text=ai_overview_text,
            )
            run_store.update_brief(brief_id, stage="building_brief", progress_percent=78)
            brief_markdown = build_brief_with_customization(
                query,
                summaries,
                seo_analysis,
                brand_name,
                brand_url,
                prompt_override,
            )
        except ValueError:
            top_urls, extracted, summaries = [], [], []
            seo_analysis = "No competitor sources were provided. This brief is based on the query only and should be reviewed."
            run_store.update_brief(brief_id, stage="building_brief", progress_percent=78)
            brief_markdown = build_brief_from_query_with_customization(
                query,
                brand_name,
                brand_url,
                prompt_override,
            )

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

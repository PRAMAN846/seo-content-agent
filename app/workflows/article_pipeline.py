from __future__ import annotations

from app.models.schemas import ArticleArtifacts
from app.models.store import run_store
from app.services.brief_builder import (
    build_brief_from_query_with_customization,
    build_brief_with_customization,
)
from app.services.exporter_google import export_to_local_doc
from app.services.writer import write_article_from_brief_with_customization
from app.workflows.source_analysis import build_source_analysis


async def process_article_from_brief(article_id: str, query: str, source_brief_id: str, brief_markdown: str) -> None:
    run_store.update_article(article_id, status="running", stage="writing_article", progress_percent=15, error=None)

    try:
        article_record = run_store.get_article_by_id(article_id)
        user_settings = run_store.get_user_settings(article_record.user_id) if article_record else None
        article_markdown = write_article_from_brief_with_customization(
            query,
            brief_markdown,
            user_settings.brand_name if user_settings else "",
            user_settings.brand_url if user_settings else "",
            user_settings.writer_prompt_override if user_settings else "",
        )
        run_store.update_article(article_id, stage="exporting_output", progress_percent=90)
        export_link = export_to_local_doc(query or "content-article", article_markdown)
        artifacts = ArticleArtifacts(
            source_brief_id=source_brief_id,
            source_brief_markdown=brief_markdown,
            article_markdown=article_markdown,
            export_link=export_link,
        )
        run_store.update_article(
            article_id,
            status="completed",
            stage="completed",
            progress_percent=100,
            artifacts=artifacts,
        )
    except Exception as exc:  # noqa: BLE001
        run_store.update_article(article_id, status="failed", stage="failed", progress_percent=100, error=str(exc))


async def process_article_from_custom_brief(article_id: str, query: str, brief_markdown: str) -> None:
    await process_article_from_brief(article_id, query, "", brief_markdown)


async def process_quick_draft(
    article_id: str,
    query: str,
    seed_urls: list[str],
    ai_citations_text: str,
    ai_overview_text: str,
) -> None:
    run_store.update_article(article_id, status="running", stage="collecting_sources", progress_percent=10, error=None)

    try:
        article_record = run_store.get_article_by_id(article_id)
        user_settings = run_store.get_user_settings(article_record.user_id) if article_record else None
        brand_name = user_settings.brand_name if user_settings else ""
        brand_url = user_settings.brand_url if user_settings else ""
        brief_prompt_override = user_settings.brief_prompt_override if user_settings else ""
        writer_prompt_override = user_settings.writer_prompt_override if user_settings else ""
        try:
            _, _, summaries, seo_analysis = await build_source_analysis(
                query=query,
                seed_urls=seed_urls,
                ai_citations_text=ai_citations_text,
                ai_overview_text=ai_overview_text,
            )
            run_store.update_article(article_id, stage="building_internal_brief", progress_percent=72)
            brief_markdown = build_brief_with_customization(
                query,
                summaries,
                seo_analysis,
                brand_name,
                brand_url,
                brief_prompt_override,
            )
        except ValueError:
            run_store.update_article(article_id, stage="building_internal_brief", progress_percent=72)
            brief_markdown = build_brief_from_query_with_customization(
                query,
                brand_name,
                brand_url,
                brief_prompt_override,
            )
        run_store.update_article(article_id, stage="writing_article", progress_percent=84)

        article_markdown = write_article_from_brief_with_customization(
            query,
            brief_markdown,
            brand_name,
            brand_url,
            writer_prompt_override,
        )
        run_store.update_article(article_id, stage="exporting_output", progress_percent=95)
        export_link = export_to_local_doc(query or "quick-draft", article_markdown)
        artifacts = ArticleArtifacts(
            source_brief_id=None,
            source_brief_markdown=brief_markdown,
            article_markdown=article_markdown,
            export_link=export_link,
        )
        run_store.update_article(
            article_id,
            status="completed",
            stage="completed",
            progress_percent=100,
            artifacts=artifacts,
        )
    except Exception as exc:  # noqa: BLE001
        run_store.update_article(article_id, status="failed", stage="failed", progress_percent=100, error=str(exc))

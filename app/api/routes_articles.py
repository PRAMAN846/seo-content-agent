from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException

from app.core.auth import get_current_user
from app.models.schemas import ArticleArtifacts, ArticleCreateRequest, ArticleRecord, UserPublic
from app.models.store import run_store
from app.workflows.article_pipeline import (
    process_article_from_brief,
    process_article_from_custom_brief,
    process_quick_draft,
)

router = APIRouter(prefix="/api/articles", tags=["articles"])


@router.get("", response_model=list[ArticleRecord])
def list_articles(current_user: UserPublic = Depends(get_current_user)) -> list[ArticleRecord]:
    return run_store.list_articles(user_id=current_user.id)


@router.post("", response_model=ArticleRecord)
async def create_article(
    payload: ArticleCreateRequest,
    current_user: UserPublic = Depends(get_current_user),
) -> ArticleRecord:
    if payload.mode == "from_brief":
        if not payload.brief_id:
            raise HTTPException(status_code=400, detail="brief_id is required for from_brief mode")
        brief = run_store.get_brief(user_id=current_user.id, brief_id=payload.brief_id)
        if not brief:
            raise HTTPException(status_code=404, detail="Brief not found")
        brief_markdown = brief.artifacts.brief_markdown.strip()
        if not brief_markdown:
            raise HTTPException(status_code=400, detail="Brief content is empty")

        initial_artifacts = ArticleArtifacts(
            source_brief_id=brief.id,
            source_brief_markdown=brief_markdown,
        )
        article = run_store.create_article(user_id=current_user.id, payload=payload, artifacts=initial_artifacts)
        asyncio.create_task(
            process_article_from_brief(
                article_id=article.id,
                query=brief.query,
                source_brief_id=brief.id,
                brief_markdown=brief_markdown,
            )
        )
        return article

    if payload.mode == "from_custom_brief":
        custom_brief = payload.custom_brief_markdown.strip()
        if not custom_brief:
            raise HTTPException(status_code=400, detail="custom_brief_markdown is required")
        if not payload.query.strip():
            raise HTTPException(status_code=400, detail="query is required for custom brief mode")

        initial_artifacts = ArticleArtifacts(source_brief_markdown=custom_brief)
        article = run_store.create_article(user_id=current_user.id, payload=payload, artifacts=initial_artifacts)
        asyncio.create_task(
            process_article_from_custom_brief(
                article_id=article.id,
                query=payload.query.strip(),
                brief_markdown=custom_brief,
            )
        )
        return article

    if payload.mode == "quick_draft":
        if not payload.query.strip():
            raise HTTPException(status_code=400, detail="query is required for quick draft mode")

        article = run_store.create_article(user_id=current_user.id, payload=payload)
        asyncio.create_task(
            process_quick_draft(
                article_id=article.id,
                query=payload.query.strip(),
                seed_urls=payload.seed_urls,
                ai_citations_text=payload.ai_citations_text,
                ai_overview_text=payload.ai_overview_text,
            )
        )
        return article

    raise HTTPException(status_code=400, detail="Unsupported article mode")


@router.get("/{article_id}", response_model=ArticleRecord)
def get_article(article_id: str, current_user: UserPublic = Depends(get_current_user)) -> ArticleRecord:
    article = run_store.get_article(user_id=current_user.id, article_id=article_id)
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")
    return article

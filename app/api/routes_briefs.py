from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException

from app.core.auth import get_current_user
from app.models.schemas import BriefCreateRequest, BriefRecord, BriefUpdateRequest, UserPublic
from app.models.store import run_store
from app.workflows.brief_pipeline import process_brief

router = APIRouter(prefix="/api/briefs", tags=["briefs"])


@router.get("", response_model=list[BriefRecord])
def list_briefs(current_user: UserPublic = Depends(get_current_user)) -> list[BriefRecord]:
    return run_store.list_briefs(user_id=current_user.id)


@router.post("", response_model=BriefRecord)
async def create_brief(
    payload: BriefCreateRequest,
    current_user: UserPublic = Depends(get_current_user),
) -> BriefRecord:
    brief = run_store.create_brief(user_id=current_user.id, payload=payload)
    asyncio.create_task(
        process_brief(
            brief_id=brief.id,
            query=payload.query,
            seed_urls=payload.seed_urls,
            ai_citations_text=payload.ai_citations_text,
            ai_overview_text=payload.ai_overview_text,
        )
    )
    return brief


@router.get("/{brief_id}", response_model=BriefRecord)
def get_brief(brief_id: str, current_user: UserPublic = Depends(get_current_user)) -> BriefRecord:
    brief = run_store.get_brief(user_id=current_user.id, brief_id=brief_id)
    if not brief:
        raise HTTPException(status_code=404, detail="Brief not found")
    return brief


@router.patch("/{brief_id}", response_model=BriefRecord)
def update_brief(
    brief_id: str,
    payload: BriefUpdateRequest,
    current_user: UserPublic = Depends(get_current_user),
) -> BriefRecord:
    brief = run_store.get_brief(user_id=current_user.id, brief_id=brief_id)
    if not brief:
        raise HTTPException(status_code=404, detail="Brief not found")

    artifacts = brief.artifacts.model_copy(update={"brief_markdown": payload.brief_markdown})
    updated = run_store.update_brief(brief_id, artifacts=artifacts, stage="edited_draft")
    if not updated:
        raise HTTPException(status_code=500, detail="Unable to update brief")
    return updated

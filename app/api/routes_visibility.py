from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.auth import get_current_user
from app.models.schemas import (
    UserPublic,
    VisibilityCompetitor,
    VisibilityCompetitorCreateRequest,
    VisibilityDeleteResponse,
    VisibilityJobRecord,
    VisibilityOverviewResponse,
    VisibilityProfile,
    VisibilityProfileUpdateRequest,
    VisibilityPromptBulkCreateRequest,
    VisibilityPromptListCreateRequest,
    VisibilityPromptListRecord,
    VisibilityPromptListRunRequest,
    VisibilityPromptRecord,
    VisibilityReport,
    VisibilitySubtopicCreateRequest,
    VisibilitySubtopicRecord,
    VisibilityTopicCreateRequest,
    VisibilityTopicRecord,
)
from app.models.store import run_store
from app.services.visibility_tracker import (
    build_visibility_overview,
    build_visibility_report,
    run_visibility_prompt_list_job,
)

router = APIRouter(prefix="/api/visibility", tags=["visibility"])


@router.get("/overview", response_model=VisibilityOverviewResponse)
def get_visibility_overview(current_user: UserPublic = Depends(get_current_user)) -> VisibilityOverviewResponse:
    return build_visibility_overview(current_user.id)


@router.put("/profile", response_model=VisibilityProfile)
def update_visibility_profile(
    payload: VisibilityProfileUpdateRequest,
    current_user: UserPublic = Depends(get_current_user),
) -> VisibilityProfile:
    return run_store.update_visibility_profile(
        current_user.id,
        brand_name=payload.brand_name.strip(),
        brand_url=payload.brand_url.strip(),
        default_schedule_frequency=payload.default_schedule_frequency,
    )


@router.post("/competitors", response_model=VisibilityCompetitor)
def create_visibility_competitor(
    payload: VisibilityCompetitorCreateRequest,
    current_user: UserPublic = Depends(get_current_user),
) -> VisibilityCompetitor:
    return run_store.create_visibility_competitor(
        current_user.id,
        name=payload.name.strip(),
        domain=payload.domain.strip(),
    )


@router.post("/topics", response_model=VisibilityTopicRecord)
def create_visibility_topic(
    payload: VisibilityTopicCreateRequest,
    current_user: UserPublic = Depends(get_current_user),
) -> VisibilityTopicRecord:
    return run_store.create_visibility_topic(current_user.id, name=payload.name.strip())


@router.post("/subtopics", response_model=VisibilitySubtopicRecord)
def create_visibility_subtopic(
    payload: VisibilitySubtopicCreateRequest,
    current_user: UserPublic = Depends(get_current_user),
) -> VisibilitySubtopicRecord:
    return run_store.create_visibility_subtopic(
        current_user.id,
        topic_id=payload.topic_id,
        name=payload.name.strip(),
    )


@router.post("/lists", response_model=VisibilityPromptListRecord)
def create_visibility_prompt_list(
    payload: VisibilityPromptListCreateRequest,
    current_user: UserPublic = Depends(get_current_user),
) -> VisibilityPromptListRecord:
    return run_store.create_visibility_prompt_list(
        current_user.id,
        subtopic_id=payload.subtopic_id,
        name=payload.name.strip(),
        schedule_frequency=payload.schedule_frequency,
    )


@router.post("/prompts/bulk", response_model=list[VisibilityPromptRecord])
def create_visibility_prompts(
    payload: VisibilityPromptBulkCreateRequest,
    current_user: UserPublic = Depends(get_current_user),
) -> list[VisibilityPromptRecord]:
    created = run_store.create_visibility_prompts(
        current_user.id,
        prompt_list_id=payload.prompt_list_id,
        prompts=[item.strip() for item in payload.prompts if item.strip()],
    )
    if not created:
        raise HTTPException(status_code=400, detail="Provide at least one valid prompt.")
    return created


@router.post("/lists/{prompt_list_id}/run", response_model=VisibilityJobRecord)
async def run_visibility_prompt_list(
    prompt_list_id: str,
    payload: VisibilityPromptListRunRequest,
    current_user: UserPublic = Depends(get_current_user),
) -> VisibilityJobRecord:
    prompt_list = run_store.get_visibility_prompt_list(current_user.id, prompt_list_id)
    context = run_store.get_visibility_prompt_list_context(prompt_list_id)
    if not prompt_list or not context or context["user_id"] != current_user.id:
        raise HTTPException(status_code=404, detail="Prompt list not found")
    if not prompt_list.prompts:
        raise HTTPException(status_code=400, detail="Add prompts to the list before running it.")

    job = run_store.create_visibility_job(
        current_user.id,
        topic_id=context["topic_id"],
        subtopic_id=context["subtopic_id"],
        prompt_list_id=prompt_list_id,
        provider=payload.provider.strip() or "openai",
        model=payload.model.strip() or "gpt-5-mini",
        surface=payload.surface,
        run_source=payload.run_source,
        total_prompts=len(prompt_list.prompts),
    )
    asyncio.create_task(run_visibility_prompt_list_job(job.id))
    return job


@router.get("/jobs/{job_id}", response_model=VisibilityJobRecord)
def get_visibility_job(job_id: str, current_user: UserPublic = Depends(get_current_user)) -> VisibilityJobRecord:
    job = run_store.get_visibility_job(current_user.id, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Visibility job not found")
    return job


@router.get("/reports", response_model=VisibilityReport)
def get_visibility_report(
    level: str = Query(default="all"),
    entity_id: str = Query(default="all"),
    current_user: UserPublic = Depends(get_current_user),
) -> VisibilityReport:
    return build_visibility_report(current_user.id, level=level, entity_id=entity_id)


@router.delete("/competitors/{competitor_id}", response_model=VisibilityDeleteResponse)
def delete_visibility_competitor(
    competitor_id: str,
    current_user: UserPublic = Depends(get_current_user),
) -> VisibilityDeleteResponse:
    deleted = run_store.delete_visibility_competitor(current_user.id, competitor_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Competitor not found")
    return VisibilityDeleteResponse(deleted=True, entity_type="competitor", entity_id=competitor_id)


@router.delete("/topics/{topic_id}", response_model=VisibilityDeleteResponse)
def delete_visibility_topic(
    topic_id: str,
    current_user: UserPublic = Depends(get_current_user),
) -> VisibilityDeleteResponse:
    deleted = run_store.delete_visibility_topic(current_user.id, topic_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Topic not found")
    return VisibilityDeleteResponse(deleted=True, entity_type="topic", entity_id=topic_id)


@router.delete("/subtopics/{subtopic_id}", response_model=VisibilityDeleteResponse)
def delete_visibility_subtopic(
    subtopic_id: str,
    current_user: UserPublic = Depends(get_current_user),
) -> VisibilityDeleteResponse:
    deleted = run_store.delete_visibility_subtopic(current_user.id, subtopic_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Subtopic not found")
    return VisibilityDeleteResponse(deleted=True, entity_type="subtopic", entity_id=subtopic_id)


@router.delete("/lists/{prompt_list_id}", response_model=VisibilityDeleteResponse)
def delete_visibility_prompt_list(
    prompt_list_id: str,
    current_user: UserPublic = Depends(get_current_user),
) -> VisibilityDeleteResponse:
    deleted = run_store.delete_visibility_prompt_list(current_user.id, prompt_list_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Prompt list not found")
    return VisibilityDeleteResponse(deleted=True, entity_type="prompt_list", entity_id=prompt_list_id)


@router.delete("/prompts/{prompt_id}", response_model=VisibilityDeleteResponse)
def delete_visibility_prompt(
    prompt_id: str,
    current_user: UserPublic = Depends(get_current_user),
) -> VisibilityDeleteResponse:
    deleted = run_store.delete_visibility_prompt(current_user.id, prompt_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Prompt not found")
    return VisibilityDeleteResponse(deleted=True, entity_type="prompt", entity_id=prompt_id)

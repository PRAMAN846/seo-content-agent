from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.auth import get_current_user
from app.models.schemas import (
    UserPublic,
    VisibilityCompetitor,
    VisibilityCompetitorCreateRequest,
    VisibilityDeleteResponse,
    VisibilityJobRecord,
    VisibilityProjectCreateRequest,
    VisibilityProjectRecord,
    VisibilityProjectsResponse,
    VisibilityProjectSummary,
    VisibilityProjectUpdateRequest,
    VisibilityProjectWorkspaceResponse,
    VisibilityPromptGeneratorRequest,
    VisibilityPromptGeneratorResponse,
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
    build_visibility_projects,
    generate_visibility_prompt_suggestions,
    build_visibility_report,
    build_visibility_workspace,
    cancel_visibility_job,
    run_visibility_prompt_list_job,
)

router = APIRouter(prefix="/api/visibility", tags=["visibility"])


def _parse_optional_date(value: Optional[str], end_of_day: bool = False) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.") from exc
    if len(value) == 10 and end_of_day:
        return parsed.replace(hour=23, minute=59, second=59)
    return parsed


@router.get("/projects", response_model=VisibilityProjectsResponse)
def get_visibility_projects(current_user: UserPublic = Depends(get_current_user)) -> VisibilityProjectsResponse:
    return build_visibility_projects(current_user.id)


@router.post("/projects", response_model=VisibilityProjectRecord)
def create_visibility_project(
    payload: VisibilityProjectCreateRequest,
    current_user: UserPublic = Depends(get_current_user),
) -> VisibilityProjectRecord:
    return run_store.create_visibility_project(
        current_user.id,
        name=payload.name.strip(),
        brand_name=payload.brand_name.strip(),
        brand_url=payload.brand_url.strip(),
        default_target_country=payload.default_target_country.strip(),
        target_audience_notes=payload.target_audience_notes.strip(),
        brand_positioning=payload.brand_positioning.strip(),
        editorial_voice=payload.editorial_voice.strip(),
        editorial_quality_bar=payload.editorial_quality_bar.strip(),
        sitemap_url=payload.sitemap_url.strip(),
        approved_domains=payload.approved_domains.strip(),
        approved_internal_urls=payload.approved_internal_urls.strip(),
        product_page_urls=payload.product_page_urls.strip(),
        visual_guidelines=payload.visual_guidelines.strip(),
        allow_standard_skill_updates=payload.allow_standard_skill_updates,
        default_schedule_frequency=payload.default_schedule_frequency,
    )


@router.get("/projects/{project_id}/workspace", response_model=VisibilityProjectWorkspaceResponse)
def get_visibility_workspace(
    project_id: str,
    start_date: Optional[str] = Query(default=None),
    end_date: Optional[str] = Query(default=None),
    current_user: UserPublic = Depends(get_current_user),
) -> VisibilityProjectWorkspaceResponse:
    try:
        return build_visibility_workspace(
            current_user.id,
            project_id=project_id,
            start_date=_parse_optional_date(start_date),
            end_date=_parse_optional_date(end_date, end_of_day=True),
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/projects/{project_id}/prompt-generator", response_model=VisibilityPromptGeneratorResponse)
def generate_visibility_prompts(
    project_id: str,
    payload: VisibilityPromptGeneratorRequest,
    current_user: UserPublic = Depends(get_current_user),
) -> VisibilityPromptGeneratorResponse:
    try:
        return generate_visibility_prompt_suggestions(current_user.id, project_id=project_id, payload=payload)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.put("/projects/{project_id}", response_model=VisibilityProjectRecord)
def update_visibility_project(
    project_id: str,
    payload: VisibilityProjectUpdateRequest,
    current_user: UserPublic = Depends(get_current_user),
) -> VisibilityProjectRecord:
    updated = run_store.update_visibility_project(
        current_user.id,
        project_id,
        name=payload.name.strip(),
        brand_name=payload.brand_name.strip(),
        brand_url=payload.brand_url.strip(),
        default_target_country=payload.default_target_country.strip(),
        target_audience_notes=payload.target_audience_notes.strip(),
        brand_positioning=payload.brand_positioning.strip(),
        editorial_voice=payload.editorial_voice.strip(),
        editorial_quality_bar=payload.editorial_quality_bar.strip(),
        sitemap_url=payload.sitemap_url.strip(),
        approved_domains=payload.approved_domains.strip(),
        approved_internal_urls=payload.approved_internal_urls.strip(),
        product_page_urls=payload.product_page_urls.strip(),
        visual_guidelines=payload.visual_guidelines.strip(),
        allow_standard_skill_updates=payload.allow_standard_skill_updates,
        default_schedule_frequency=payload.default_schedule_frequency,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Project not found")
    return updated


@router.delete("/projects/{project_id}", response_model=VisibilityDeleteResponse)
def delete_visibility_project(
    project_id: str,
    current_user: UserPublic = Depends(get_current_user),
) -> VisibilityDeleteResponse:
    deleted = run_store.delete_visibility_project(current_user.id, project_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Project not found")
    return VisibilityDeleteResponse(deleted=True, entity_type="project", entity_id=project_id)


@router.post("/projects/{project_id}/competitors", response_model=VisibilityCompetitor)
def create_visibility_competitor(
    project_id: str,
    payload: VisibilityCompetitorCreateRequest,
    current_user: UserPublic = Depends(get_current_user),
) -> VisibilityCompetitor:
    return run_store.create_visibility_competitor(
        current_user.id,
        project_id=project_id,
        name=payload.name.strip(),
        domain=payload.domain.strip(),
    )


@router.post("/topics", response_model=VisibilityTopicRecord)
def create_visibility_topic(
    payload: VisibilityTopicCreateRequest,
    current_user: UserPublic = Depends(get_current_user),
) -> VisibilityTopicRecord:
    return run_store.create_visibility_topic(current_user.id, project_id=payload.project_id, name=payload.name.strip())


@router.post("/subtopics", response_model=VisibilitySubtopicRecord)
def create_visibility_subtopic(
    payload: VisibilitySubtopicCreateRequest,
    current_user: UserPublic = Depends(get_current_user),
) -> VisibilitySubtopicRecord:
    return run_store.create_visibility_subtopic(
        current_user.id,
        project_id=payload.project_id,
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
        project_id=payload.project_id,
        subtopic_id=payload.subtopic_id,
        name=payload.name.strip(),
        schedule_frequency=payload.schedule_frequency,
    )


@router.post("/prompts/bulk", response_model=list[VisibilityPromptRecord])
def create_visibility_prompts(
    payload: VisibilityPromptBulkCreateRequest,
    current_user: UserPublic = Depends(get_current_user),
) -> list[VisibilityPromptRecord]:
    prompt_list = run_store.get_visibility_prompt_list(current_user.id, payload.prompt_list_id)
    if not prompt_list:
        raise HTTPException(status_code=404, detail="Prompt list not found")
    created = run_store.create_visibility_prompts(
        current_user.id,
        project_id=prompt_list.project_id,
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
        project_id=context["project_id"],
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


@router.post("/jobs/{job_id}/cancel", response_model=VisibilityJobRecord)
def cancel_visibility_job_route(job_id: str, current_user: UserPublic = Depends(get_current_user)) -> VisibilityJobRecord:
    try:
        job = cancel_visibility_job(current_user.id, job_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not job:
        raise HTTPException(status_code=404, detail="Visibility job not found")
    return job


@router.get("/reports", response_model=VisibilityReport)
def get_visibility_report(
    project_id: str = Query(...),
    level: str = Query(default="all"),
    entity_id: str = Query(default="all"),
    start_date: Optional[str] = Query(default=None),
    end_date: Optional[str] = Query(default=None),
    current_user: UserPublic = Depends(get_current_user),
) -> VisibilityReport:
    return build_visibility_report(
        current_user.id,
        project_id=project_id,
        level=level,
        entity_id=entity_id,
        start_date=_parse_optional_date(start_date),
        end_date=_parse_optional_date(end_date, end_of_day=True),
    )


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

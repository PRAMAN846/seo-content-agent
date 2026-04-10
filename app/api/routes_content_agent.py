from __future__ import annotations

import asyncio
import json
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.core.auth import get_current_user
from app.models.schemas import (
    ContentAgentRunApproveRequest,
    ContentAgentRunApproveResponse,
    ContentAgentRunCancelResponse,
    ContentAgentRunContinueRequest,
    ContentAgentRunContinueResponse,
    ContentAgentRunCreateRequest,
    ContentAgentRunExportRequest,
    ContentAgentRunExportResponse,
    ContentAgentRunRecord,
    ContentAgentRunStartResponse,
    ContentAgentRunArchiveResponse,
    ContentAgentRunUpdateRequest,
    ContentAgentRunUpdateResponse,
    ContentAgentRunsResponse,
    UserPublic,
)
from app.models.store import run_store
from app.services.content_agent import (
    approve_content_agent_run,
    cancel_content_agent_run,
    continue_content_agent_run,
    export_content_agent_run,
    get_content_agent_run,
    list_content_agent_runs,
    start_content_agent_run,
)

router = APIRouter(prefix="/api/content-agent", tags=["content-agent"])


@router.get("/projects/{project_id}/runs", response_model=ContentAgentRunsResponse)
def get_project_content_agent_runs(
    project_id: str,
    current_user: UserPublic = Depends(get_current_user),
) -> ContentAgentRunsResponse:
    return ContentAgentRunsResponse(runs=list_content_agent_runs(current_user.id, project_id))


@router.post("/projects/{project_id}/runs", response_model=ContentAgentRunStartResponse)
def create_content_agent_run(
    project_id: str,
    payload: ContentAgentRunCreateRequest,
    current_user: UserPublic = Depends(get_current_user),
) -> ContentAgentRunStartResponse:
    try:
        run = start_content_agent_run(
            current_user=current_user,
            project_id=project_id,
            prompt=payload.prompt.strip(),
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return ContentAgentRunStartResponse(run=run)


@router.patch("/runs/{run_id}", response_model=ContentAgentRunUpdateResponse)
def update_single_content_agent_run(
    run_id: str,
    payload: ContentAgentRunUpdateRequest,
    current_user: UserPublic = Depends(get_current_user),
) -> ContentAgentRunUpdateResponse:
    run = run_store.rename_content_agent_run(
        current_user.id,
        run_id,
        title=payload.title.strip(),
    )
    if not run:
        raise HTTPException(status_code=404, detail="Content Agent run not found")
    return ContentAgentRunUpdateResponse(run=run)


@router.get("/runs/{run_id}", response_model=ContentAgentRunRecord)
def get_single_content_agent_run(
    run_id: str,
    current_user: UserPublic = Depends(get_current_user),
) -> ContentAgentRunRecord:
    run = get_content_agent_run(current_user.id, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Content Agent run not found")
    return run


@router.get("/runs/{run_id}/stream")
async def stream_single_content_agent_run(
    run_id: str,
    request: Request,
    current_user: UserPublic = Depends(get_current_user),
) -> StreamingResponse:
    run = get_content_agent_run(current_user.id, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Content Agent run not found")

    async def event_generator():
        last_signature = ""
        last_keepalive = 0.0
        while True:
            if await request.is_disconnected():
                break
            refreshed = get_content_agent_run(current_user.id, run_id)
            if not refreshed:
                yield "event: error\ndata: {\"detail\":\"Content Agent run not found\"}\n\n"
                break
            payload = refreshed.model_dump(mode="json")
            signature = json.dumps(payload, sort_keys=True)
            if signature != last_signature:
                yield f"event: run\ndata: {json.dumps(payload)}\n\n"
                last_signature = signature
                last_keepalive = time.monotonic()
                if refreshed.status in {"completed", "failed", "cancelled"}:
                    break
            elif time.monotonic() - last_keepalive >= 15:
                yield ": keepalive\n\n"
                last_keepalive = time.monotonic()
            await asyncio.sleep(0.9)

    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }
    return StreamingResponse(event_generator(), media_type="text/event-stream", headers=headers)


@router.post("/runs/{run_id}/continue", response_model=ContentAgentRunContinueResponse)
def continue_single_content_agent_run(
    run_id: str,
    payload: ContentAgentRunContinueRequest,
    current_user: UserPublic = Depends(get_current_user),
) -> ContentAgentRunContinueResponse:
    try:
        run = continue_content_agent_run(
            current_user=current_user,
            run_id=run_id,
            prompt=payload.prompt.strip(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return ContentAgentRunContinueResponse(run=run)


@router.post("/runs/{run_id}/approve", response_model=ContentAgentRunApproveResponse)
def approve_single_content_agent_run(
    run_id: str,
    payload: ContentAgentRunApproveRequest,
    current_user: UserPublic = Depends(get_current_user),
) -> ContentAgentRunApproveResponse:
    try:
        run = approve_content_agent_run(
            current_user=current_user,
            run_id=run_id,
            note=payload.note.strip(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return ContentAgentRunApproveResponse(run=run)


@router.post("/runs/{run_id}/cancel", response_model=ContentAgentRunCancelResponse)
def cancel_single_content_agent_run(
    run_id: str,
    current_user: UserPublic = Depends(get_current_user),
) -> ContentAgentRunCancelResponse:
    try:
        run = cancel_content_agent_run(
            current_user=current_user,
            run_id=run_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return ContentAgentRunCancelResponse(run=run)


@router.post("/runs/{run_id}/archive", response_model=ContentAgentRunArchiveResponse)
def archive_single_content_agent_run(
    run_id: str,
    current_user: UserPublic = Depends(get_current_user),
) -> ContentAgentRunArchiveResponse:
    run = get_content_agent_run(current_user.id, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Content Agent run not found")
    if run.status in {"queued", "running", "cancel_requested", "awaiting_approval"}:
        raise HTTPException(status_code=400, detail="Stop or finish this run before archiving it")
    archived = run_store.archive_content_agent_run(current_user.id, run_id)
    if not archived:
        raise HTTPException(status_code=404, detail="Content Agent run not found")
    return ContentAgentRunArchiveResponse(run_id=run_id, archived=True)


@router.post("/runs/{run_id}/export", response_model=ContentAgentRunExportResponse)
def export_single_content_agent_run(
    run_id: str,
    payload: ContentAgentRunExportRequest,
    current_user: UserPublic = Depends(get_current_user),
) -> ContentAgentRunExportResponse:
    try:
        run = export_content_agent_run(
            current_user=current_user,
            run_id=run_id,
            export_format=payload.format,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return ContentAgentRunExportResponse(run=run)

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException

from app.core.auth import get_current_user
from app.models.schemas import RunCreateRequest, RunRecord, UserPublic
from app.models.store import run_store
from app.workflows.run_pipeline import process_run

router = APIRouter(prefix="/api", tags=["runs"])


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/runs", response_model=list[RunRecord])
def list_runs(current_user: UserPublic = Depends(get_current_user)) -> list[RunRecord]:
    return run_store.list_runs(user_id=current_user.id)


@router.post("/runs", response_model=RunRecord)
async def create_run(
    payload: RunCreateRequest,
    current_user: UserPublic = Depends(get_current_user),
) -> RunRecord:
    run = run_store.create_run(user_id=current_user.id, payload=payload)
    asyncio.create_task(
        process_run(
            run_id=run.id,
            query=payload.query,
            seed_urls=payload.seed_urls,
            ai_citations_text=payload.ai_citations_text,
            ai_overview_text=payload.ai_overview_text,
        )
    )
    return run


@router.get("/runs/{run_id}", response_model=RunRecord)
def get_run(run_id: str, current_user: UserPublic = Depends(get_current_user)) -> RunRecord:
    run = run_store.get_run(user_id=current_user.id, run_id=run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return run

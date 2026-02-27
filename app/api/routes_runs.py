from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException

from app.models.schemas import RunCreateRequest, RunRecord
from app.models.store import run_store
from app.workflows.run_pipeline import process_run

router = APIRouter(prefix="/api", tags=["runs"])


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/runs", response_model=list[RunRecord])
def list_runs() -> list[RunRecord]:
    return run_store.list()


@router.post("/runs", response_model=RunRecord)
async def create_run(payload: RunCreateRequest) -> RunRecord:
    run = run_store.create(query=payload.query)
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
def get_run(run_id: str) -> RunRecord:
    run = run_store.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return run

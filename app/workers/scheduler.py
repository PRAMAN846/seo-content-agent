from __future__ import annotations

import asyncio
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.models.store import run_store
from app.workflows.run_pipeline import process_run

logger = logging.getLogger(__name__)


async def _retry_stuck_runs() -> None:
    for run in run_store.list():
        if run.status == "queued":
            logger.info("Retrying queued run %s", run.id)
            asyncio.create_task(
                process_run(
                    run_id=run.id,
                    query=run.query,
                    seed_urls=[],
                    ai_citations_text="",
                    ai_overview_text="",
                )
            )


def start_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler()
    scheduler.add_job(_retry_stuck_runs, "interval", minutes=2)
    scheduler.start()
    return scheduler

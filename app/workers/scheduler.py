import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.services.visibility_tracker import run_due_visibility_schedules

logger = logging.getLogger(__name__)


async def _retry_stuck_runs() -> None:
    # Legacy queued-run retry is disabled while the app transitions
    # to separate brief/article agents with direct background dispatch.
    return None


async def _run_visibility_schedules() -> None:
    await run_due_visibility_schedules()


def start_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler()
    scheduler.add_job(_retry_stuck_runs, "interval", minutes=2)
    scheduler.add_job(_run_visibility_schedules, "interval", minutes=30)
    scheduler.start()
    return scheduler

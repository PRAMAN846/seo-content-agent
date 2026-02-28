import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

logger = logging.getLogger(__name__)


async def _retry_stuck_runs() -> None:
    # Legacy queued-run retry is disabled while the app transitions
    # to separate brief/article agents with direct background dispatch.
    return None


def start_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler()
    scheduler.add_job(_retry_stuck_runs, "interval", minutes=2)
    scheduler.start()
    return scheduler

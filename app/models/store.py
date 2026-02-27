from __future__ import annotations

from datetime import datetime, timezone
from threading import Lock
from typing import Optional
from uuid import uuid4

from app.models.schemas import RunRecord


class InMemoryRunStore:
    def __init__(self) -> None:
        self._lock = Lock()
        self._runs: dict[str, RunRecord] = {}

    def create(self, query: str) -> RunRecord:
        now = datetime.now(timezone.utc)
        run = RunRecord(
            id=str(uuid4()),
            query=query,
            status="queued",
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            self._runs[run.id] = run
        return run

    def get(self, run_id: str) -> Optional[RunRecord]:
        with self._lock:
            return self._runs.get(run_id)

    def list(self) -> list[RunRecord]:
        with self._lock:
            return sorted(self._runs.values(), key=lambda r: r.created_at, reverse=True)

    def update(self, run_id: str, **kwargs) -> RunRecord:
        with self._lock:
            run = self._runs[run_id]
            update_values = run.model_dump()
            update_values.update(kwargs)
            update_values["updated_at"] = datetime.now(timezone.utc)
            updated = RunRecord(**update_values)
            self._runs[run_id] = updated
            return updated


run_store = InMemoryRunStore()

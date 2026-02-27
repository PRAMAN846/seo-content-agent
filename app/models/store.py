from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Optional
from uuid import uuid4

from app.models.schemas import (
    QueuedRun,
    RunArtifacts,
    RunCreateRequest,
    RunRecord,
    UserPublic,
)


class SQLiteStore:
    def __init__(self) -> None:
        db_path = Path(os.getenv("APP_DB_PATH", "data/seo_agent.db"))
        db_path.parent.mkdir(parents=True, exist_ok=True)

        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = Lock()
        self._init_schema()

    @staticmethod
    def _utcnow() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _now_iso() -> str:
        return SQLiteStore._utcnow().isoformat()

    @staticmethod
    def _parse_dt(value: str) -> datetime:
        return datetime.fromisoformat(value)

    @staticmethod
    def _normalize_email(email: str) -> str:
        return email.strip().lower()

    @staticmethod
    def _hash_password(password: str, salt: Optional[bytes] = None) -> str:
        real_salt = salt or secrets.token_bytes(16)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), real_salt, 200_000)
        return f"{real_salt.hex()}${digest.hex()}"

    @staticmethod
    def _verify_password(password: str, password_hash: str) -> bool:
        try:
            salt_hex, digest_hex = password_hash.split("$", 1)
            computed = SQLiteStore._hash_password(password, bytes.fromhex(salt_hex)).split("$", 1)[1]
            return hmac.compare_digest(computed, digest_hex)
        except Exception:  # noqa: BLE001
            return False

    def _init_schema(self) -> None:
        with self._lock:
            cur = self._conn.cursor()
            cur.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    email TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS sessions (
                    token TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id)
                );

                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    query TEXT NOT NULL,
                    status TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    progress_percent INTEGER NOT NULL,
                    error TEXT,
                    artifacts_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id)
                );

                CREATE TABLE IF NOT EXISTS run_inputs (
                    run_id TEXT PRIMARY KEY,
                    seed_urls_json TEXT NOT NULL,
                    ai_citations_text TEXT NOT NULL,
                    ai_overview_text TEXT NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES runs(id)
                );

                CREATE INDEX IF NOT EXISTS idx_runs_user_created ON runs(user_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status);
                CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
                """
            )
            self._conn.commit()

    def create_user(self, email: str, password: str) -> UserPublic:
        normalized = self._normalize_email(email)
        now = self._now_iso()
        user_id = str(uuid4())
        password_hash = self._hash_password(password)

        with self._lock:
            cur = self._conn.cursor()
            try:
                cur.execute(
                    "INSERT INTO users (id, email, password_hash, created_at) VALUES (?, ?, ?, ?)",
                    (user_id, normalized, password_hash, now),
                )
                self._conn.commit()
            except sqlite3.IntegrityError as exc:
                raise ValueError("Email already registered") from exc

        return UserPublic(id=user_id, email=normalized, created_at=self._parse_dt(now))

    def authenticate_user(self, email: str, password: str) -> Optional[UserPublic]:
        normalized = self._normalize_email(email)
        with self._lock:
            row = self._conn.execute(
                "SELECT id, email, password_hash, created_at FROM users WHERE email = ?",
                (normalized,),
            ).fetchone()

        if not row or not self._verify_password(password, row["password_hash"]):
            return None

        return UserPublic(id=row["id"], email=row["email"], created_at=self._parse_dt(row["created_at"]))

    def create_session(self, user_id: str, ttl_days: int = 30) -> str:
        token = secrets.token_urlsafe(48)
        now = self._utcnow()
        expires = now + timedelta(days=ttl_days)

        with self._lock:
            self._conn.execute(
                "INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
                (token, user_id, now.isoformat(), expires.isoformat()),
            )
            self._conn.commit()

        return token

    def get_user_by_session(self, token: str) -> Optional[UserPublic]:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT u.id, u.email, u.created_at, s.expires_at
                FROM sessions s
                JOIN users u ON u.id = s.user_id
                WHERE s.token = ?
                """,
                (token,),
            ).fetchone()

        if not row:
            return None

        if self._parse_dt(row["expires_at"]) < self._utcnow():
            self.delete_session(token)
            return None

        return UserPublic(id=row["id"], email=row["email"], created_at=self._parse_dt(row["created_at"]))

    def delete_session(self, token: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
            self._conn.commit()

    def create_run(self, user_id: str, payload: RunCreateRequest) -> RunRecord:
        now = self._now_iso()
        run_id = str(uuid4())

        with self._lock:
            self._conn.execute(
                """
                INSERT INTO runs (
                    id, user_id, query, status, stage, progress_percent,
                    error, artifacts_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    user_id,
                    payload.query,
                    "queued",
                    "queued",
                    0,
                    None,
                    RunArtifacts().model_dump_json(),
                    now,
                    now,
                ),
            )
            self._conn.execute(
                """
                INSERT INTO run_inputs (run_id, seed_urls_json, ai_citations_text, ai_overview_text)
                VALUES (?, ?, ?, ?)
                """,
                (
                    run_id,
                    json.dumps(payload.seed_urls),
                    payload.ai_citations_text,
                    payload.ai_overview_text,
                ),
            )
            self._conn.commit()

        created = self.get_run(user_id=user_id, run_id=run_id)
        if not created:
            raise RuntimeError("Run creation failed unexpectedly")
        return created

    def get_run(self, user_id: str, run_id: str) -> Optional[RunRecord]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM runs WHERE id = ? AND user_id = ?",
                (run_id, user_id),
            ).fetchone()
        return self._row_to_run(row) if row else None

    def get_run_by_id(self, run_id: str) -> Optional[RunRecord]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        return self._row_to_run(row) if row else None

    def list_runs(self, user_id: str, limit: int = 100) -> list[RunRecord]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM runs WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
        return [self._row_to_run(row) for row in rows]

    def update_run(self, run_id: str, **kwargs: Any) -> Optional[RunRecord]:
        allowed = {"status", "stage", "progress_percent", "error", "artifacts"}
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return self.get_run_by_id(run_id)

        if "artifacts" in updates and isinstance(updates["artifacts"], RunArtifacts):
            updates["artifacts_json"] = updates.pop("artifacts").model_dump_json()

        columns = []
        values: list[Any] = []
        for key, value in updates.items():
            columns.append(f"{key} = ?")
            values.append(value)

        columns.append("updated_at = ?")
        values.append(self._now_iso())
        values.append(run_id)

        with self._lock:
            self._conn.execute(f"UPDATE runs SET {', '.join(columns)} WHERE id = ?", values)
            self._conn.commit()

        return self.get_run_by_id(run_id)

    def list_queued_runs(self, limit: int = 50) -> list[QueuedRun]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT r.id AS run_id, r.user_id, r.query,
                       i.seed_urls_json, i.ai_citations_text, i.ai_overview_text
                FROM runs r
                JOIN run_inputs i ON i.run_id = r.id
                WHERE r.status = 'queued'
                ORDER BY r.created_at ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

        queued: list[QueuedRun] = []
        for row in rows:
            queued.append(
                QueuedRun(
                    run_id=row["run_id"],
                    user_id=row["user_id"],
                    query=row["query"],
                    seed_urls=json.loads(row["seed_urls_json"]),
                    ai_citations_text=row["ai_citations_text"],
                    ai_overview_text=row["ai_overview_text"],
                )
            )
        return queued

    def _row_to_run(self, row: sqlite3.Row) -> RunRecord:
        artifacts = RunArtifacts(**json.loads(row["artifacts_json"]))
        return RunRecord(
            id=row["id"],
            user_id=row["user_id"],
            query=row["query"],
            status=row["status"],
            stage=row["stage"],
            progress_percent=int(row["progress_percent"]),
            created_at=self._parse_dt(row["created_at"]),
            updated_at=self._parse_dt(row["updated_at"]),
            error=row["error"],
            artifacts=artifacts,
        )


run_store = SQLiteStore()

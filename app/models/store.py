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
from typing import Any, List, Mapping, Optional
from uuid import uuid4

from app.models.schemas import (
    ArticleArtifacts,
    ArticleCreateRequest,
    ArticleRecord,
    BriefArtifacts,
    BriefCreateRequest,
    BriefRecord,
    RunArtifacts,
    RunCreateRequest,
    RunRecord,
    UserPublic,
    UserSettings,
)

try:
    import psycopg
    from psycopg.rows import dict_row
except Exception:  # noqa: BLE001
    psycopg = None
    dict_row = None


class StoreBase:
    @staticmethod
    def _utcnow() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _now_iso() -> str:
        return StoreBase._utcnow().isoformat()

    @staticmethod
    def _parse_dt(value: Any) -> datetime:
        if isinstance(value, datetime):
            if value.tzinfo:
                return value
            return value.replace(tzinfo=timezone.utc)
        return datetime.fromisoformat(str(value))

    @staticmethod
    def _normalize_email(email: str) -> str:
        return email.strip().lower()

    @staticmethod
    def _hash_password(password: str, salt: Optional[bytes] = None) -> str:
        real_salt = salt or secrets.token_bytes(16)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), real_salt, 200_000)
        return "{}${}".format(real_salt.hex(), digest.hex())

    @staticmethod
    def _verify_password(password: str, password_hash: str) -> bool:
        try:
            salt_hex, digest_hex = password_hash.split("$", 1)
            computed = StoreBase._hash_password(password, bytes.fromhex(salt_hex)).split("$", 1)[1]
            return hmac.compare_digest(computed, digest_hex)
        except Exception:  # noqa: BLE001
            return False

    @staticmethod
    def _row_to_run(row: Mapping[str, Any]) -> RunRecord:
        return RunRecord(
            id=str(row["id"]),
            user_id=str(row["user_id"]),
            query=str(row["query"]),
            status=str(row["status"]),
            stage=str(row["stage"]),
            progress_percent=int(row["progress_percent"]),
            created_at=StoreBase._parse_dt(row["created_at"]),
            updated_at=StoreBase._parse_dt(row["updated_at"]),
            error=row["error"],
            artifacts=RunArtifacts(**json.loads(str(row["artifacts_json"]))),
        )

    @staticmethod
    def _row_to_brief(row: Mapping[str, Any]) -> BriefRecord:
        return BriefRecord(
            id=str(row["id"]),
            user_id=str(row["user_id"]),
            query=str(row["query"]),
            status=str(row["status"]),
            stage=str(row["stage"]),
            progress_percent=int(row["progress_percent"]),
            created_at=StoreBase._parse_dt(row["created_at"]),
            updated_at=StoreBase._parse_dt(row["updated_at"]),
            error=row["error"],
            artifacts=BriefArtifacts(**json.loads(str(row["artifacts_json"]))),
        )

    @staticmethod
    def _row_to_article(row: Mapping[str, Any]) -> ArticleRecord:
        return ArticleRecord(
            id=str(row["id"]),
            user_id=str(row["user_id"]),
            mode=str(row["mode"]),
            query=str(row["query"]),
            status=str(row["status"]),
            stage=str(row["stage"]),
            progress_percent=int(row["progress_percent"]),
            created_at=StoreBase._parse_dt(row["created_at"]),
            updated_at=StoreBase._parse_dt(row["updated_at"]),
            error=row["error"],
            artifacts=ArticleArtifacts(**json.loads(str(row["artifacts_json"]))),
        )

    @staticmethod
    def _row_to_user(row: Mapping[str, Any]) -> UserPublic:
        return UserPublic(
            id=str(row["id"]),
            email=str(row["email"]),
            name=row.get("name") if hasattr(row, "get") else row["name"],
            brand_name=row.get("brand_name") if hasattr(row, "get") else row["brand_name"],
            brand_url=row.get("brand_url") if hasattr(row, "get") else row["brand_url"],
            created_at=StoreBase._parse_dt(row["created_at"]),
        )

    @staticmethod
    def _row_to_user_settings(row: Mapping[str, Any]) -> UserSettings:
        return UserSettings(
            id=str(row["id"]),
            email=str(row["email"]),
            name=row.get("name") if hasattr(row, "get") else row["name"],
            brand_name=row.get("brand_name") if hasattr(row, "get") else row["brand_name"],
            brand_url=row.get("brand_url") if hasattr(row, "get") else row["brand_url"],
            brief_prompt_override=(row.get("brief_prompt_override") if hasattr(row, "get") else row["brief_prompt_override"]) or "",
            writer_prompt_override=(row.get("writer_prompt_override") if hasattr(row, "get") else row["writer_prompt_override"]) or "",
            google_docs_connected=bool((row.get("google_docs_connected") if hasattr(row, "get") else row["google_docs_connected"]) or False),
            google_sheets_connected=bool((row.get("google_sheets_connected") if hasattr(row, "get") else row["google_sheets_connected"]) or False),
            created_at=StoreBase._parse_dt(row["created_at"]),
        )


class SQLiteStore(StoreBase):
    def __init__(self, db_path: str) -> None:
        db_file = Path(db_path)
        db_file.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_file), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = Lock()
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            cur = self._conn.cursor()
            cur.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    email TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    name TEXT,
                    brand_name TEXT,
                    brand_url TEXT,
                    brief_prompt_override TEXT DEFAULT '',
                    writer_prompt_override TEXT DEFAULT '',
                    google_docs_connected INTEGER DEFAULT 0,
                    google_sheets_connected INTEGER DEFAULT 0,
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

                CREATE TABLE IF NOT EXISTS briefs (
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

                CREATE TABLE IF NOT EXISTS articles (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    mode TEXT NOT NULL,
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

                CREATE INDEX IF NOT EXISTS idx_runs_user_created ON runs(user_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
                CREATE INDEX IF NOT EXISTS idx_briefs_user_created ON briefs(user_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_articles_user_created ON articles(user_id, created_at DESC);
                """
            )
            # Lightweight migrations for existing local databases.
            for statement in [
                "ALTER TABLE users ADD COLUMN name TEXT",
                "ALTER TABLE users ADD COLUMN brand_name TEXT",
                "ALTER TABLE users ADD COLUMN brand_url TEXT",
                "ALTER TABLE users ADD COLUMN brief_prompt_override TEXT DEFAULT ''",
                "ALTER TABLE users ADD COLUMN writer_prompt_override TEXT DEFAULT ''",
                "ALTER TABLE users ADD COLUMN google_docs_connected INTEGER DEFAULT 0",
                "ALTER TABLE users ADD COLUMN google_sheets_connected INTEGER DEFAULT 0",
            ]:
                try:
                    self._conn.execute(statement)
                except sqlite3.OperationalError:
                    pass
            self._conn.commit()

    def create_user(self, email: str, password: str) -> UserPublic:
        normalized = self._normalize_email(email)
        now = self._now_iso()
        user_id = str(uuid4())
        password_hash = self._hash_password(password)

        with self._lock:
            try:
                self._conn.execute(
                    """
                    INSERT INTO users (
                        id, email, password_hash, name, brand_name, brand_url,
                        brief_prompt_override, writer_prompt_override, google_docs_connected,
                        google_sheets_connected, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (user_id, normalized, password_hash, None, None, None, "", "", 0, 0, now),
                )
                self._conn.commit()
            except sqlite3.IntegrityError as exc:
                raise ValueError("Email already registered") from exc

        return UserPublic(id=user_id, email=normalized, created_at=self._parse_dt(now))

    def authenticate_user(self, email: str, password: str) -> Optional[UserPublic]:
        normalized = self._normalize_email(email)
        with self._lock:
            row = self._conn.execute(
                """
                SELECT id, email, password_hash, name, brand_name, brand_url,
                       brief_prompt_override, writer_prompt_override,
                       google_docs_connected, google_sheets_connected, created_at
                FROM users WHERE email = ?
                """,
                (normalized,),
            ).fetchone()
        if not row or not self._verify_password(password, str(row["password_hash"])):
            return None
        return self._row_to_user(row)

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
                     , u.name, u.brand_name, u.brand_url
                     , u.brief_prompt_override, u.writer_prompt_override
                     , u.google_docs_connected, u.google_sheets_connected
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
        return self._row_to_user(row)

    def delete_session(self, token: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
            self._conn.commit()

    def get_user_settings(self, user_id: str) -> Optional[UserSettings]:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT id, email, name, brand_name, brand_url,
                       brief_prompt_override, writer_prompt_override,
                       google_docs_connected, google_sheets_connected, created_at
                FROM users WHERE id = ?
                """,
                (user_id,),
            ).fetchone()
        return self._row_to_user_settings(row) if row else None

    def update_user_settings(self, user_id: str, **kwargs: Any) -> Optional[UserSettings]:
        allowed = {"name", "brand_name", "brand_url", "brief_prompt_override", "writer_prompt_override"}
        updates = dict((k, v) for (k, v) in kwargs.items() if k in allowed)
        if not updates:
            return self.get_user_settings(user_id)

        columns = []
        values: List[Any] = []
        for key, value in updates.items():
            columns.append("{} = ?".format(key))
            values.append(value)
        values.append(user_id)

        with self._lock:
            self._conn.execute("UPDATE users SET {} WHERE id = ?".format(", ".join(columns)), values)
            self._conn.commit()
        return self.get_user_settings(user_id)

    def create_run(self, user_id: str, payload: RunCreateRequest) -> RunRecord:
        record_id = str(uuid4())
        now = self._now_iso()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO runs (id, user_id, query, status, stage, progress_percent, error, artifacts_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (record_id, user_id, payload.query, "queued", "queued", 0, None, RunArtifacts().model_dump_json(), now, now),
            )
            self._conn.commit()
        created = self.get_run(user_id, record_id)
        if not created:
            raise RuntimeError("Run creation failed unexpectedly")
        return created

    def get_run(self, user_id: str, run_id: str) -> Optional[RunRecord]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM runs WHERE id = ? AND user_id = ?", (run_id, user_id)).fetchone()
        return self._row_to_run(row) if row else None

    def get_run_by_id(self, run_id: str) -> Optional[RunRecord]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        return self._row_to_run(row) if row else None

    def list_runs(self, user_id: str, limit: int = 100) -> List[RunRecord]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM runs WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
        return [self._row_to_run(row) for row in rows]

    def update_run(self, run_id: str, **kwargs: Any) -> Optional[RunRecord]:
        return self._update_entity("runs", run_id, self.get_run_by_id, kwargs)

    def create_brief(self, user_id: str, payload: BriefCreateRequest) -> BriefRecord:
        record_id = str(uuid4())
        now = self._now_iso()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO briefs (id, user_id, query, status, stage, progress_percent, error, artifacts_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (record_id, user_id, payload.query, "queued", "queued", 0, None, BriefArtifacts().model_dump_json(), now, now),
            )
            self._conn.commit()
        created = self.get_brief(user_id, record_id)
        if not created:
            raise RuntimeError("Brief creation failed unexpectedly")
        return created

    def get_brief(self, user_id: str, brief_id: str) -> Optional[BriefRecord]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM briefs WHERE id = ? AND user_id = ?", (brief_id, user_id)).fetchone()
        return self._row_to_brief(row) if row else None

    def get_brief_by_id(self, brief_id: str) -> Optional[BriefRecord]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM briefs WHERE id = ?", (brief_id,)).fetchone()
        return self._row_to_brief(row) if row else None

    def list_briefs(self, user_id: str, limit: int = 100) -> List[BriefRecord]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM briefs WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
        return [self._row_to_brief(row) for row in rows]

    def update_brief(self, brief_id: str, **kwargs: Any) -> Optional[BriefRecord]:
        return self._update_entity("briefs", brief_id, self.get_brief_by_id, kwargs)

    def create_article(
        self,
        user_id: str,
        payload: ArticleCreateRequest,
        artifacts: Optional[ArticleArtifacts] = None,
    ) -> ArticleRecord:
        record_id = str(uuid4())
        now = self._now_iso()
        initial_artifacts = artifacts or ArticleArtifacts()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO articles (id, user_id, mode, query, status, stage, progress_percent, error, artifacts_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record_id,
                    user_id,
                    payload.mode,
                    payload.query,
                    "queued",
                    "queued",
                    0,
                    None,
                    initial_artifacts.model_dump_json(),
                    now,
                    now,
                ),
            )
            self._conn.commit()
        created = self.get_article(user_id, record_id)
        if not created:
            raise RuntimeError("Article creation failed unexpectedly")
        return created

    def get_article(self, user_id: str, article_id: str) -> Optional[ArticleRecord]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM articles WHERE id = ? AND user_id = ?",
                (article_id, user_id),
            ).fetchone()
        return self._row_to_article(row) if row else None

    def get_article_by_id(self, article_id: str) -> Optional[ArticleRecord]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM articles WHERE id = ?", (article_id,)).fetchone()
        return self._row_to_article(row) if row else None

    def list_articles(self, user_id: str, limit: int = 100) -> List[ArticleRecord]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM articles WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
        return [self._row_to_article(row) for row in rows]

    def update_article(self, article_id: str, **kwargs: Any) -> Optional[ArticleRecord]:
        return self._update_entity("articles", article_id, self.get_article_by_id, kwargs)

    def _update_entity(self, table: str, record_id: str, getter, kwargs: Any):
        allowed = {"status", "stage", "progress_percent", "error", "artifacts"}
        updates = dict((k, v) for (k, v) in kwargs.items() if k in allowed)
        if not updates:
            return getter(record_id)
        if "artifacts" in updates:
            updates["artifacts_json"] = updates.pop("artifacts").model_dump_json()

        columns = []
        values: List[Any] = []
        for key, value in updates.items():
            columns.append("{} = ?".format(key))
            values.append(value)
        columns.append("updated_at = ?")
        values.append(self._now_iso())
        values.append(record_id)

        with self._lock:
            self._conn.execute("UPDATE {} SET {} WHERE id = ?".format(table, ", ".join(columns)), values)
            self._conn.commit()
        return getter(record_id)


class PostgresStore(StoreBase):
    def __init__(self, dsn: str) -> None:
        if psycopg is None:
            raise RuntimeError("psycopg is required for DATABASE_URL mode. Install requirements again.")
        self._dsn = dsn
        self._init_schema()

    def _connect(self):
        return psycopg.connect(self._dsn)

    def _init_schema(self) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS users (
                        id TEXT PRIMARY KEY,
                        email TEXT UNIQUE NOT NULL,
                        password_hash TEXT NOT NULL,
                        name TEXT,
                        brand_name TEXT,
                        brand_url TEXT,
                        brief_prompt_override TEXT DEFAULT '',
                        writer_prompt_override TEXT DEFAULT '',
                        google_docs_connected BOOLEAN DEFAULT FALSE,
                        google_sheets_connected BOOLEAN DEFAULT FALSE,
                        created_at TIMESTAMPTZ NOT NULL
                    )
                    """
                )
                for statement in [
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS name TEXT",
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS brand_name TEXT",
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS brand_url TEXT",
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS brief_prompt_override TEXT DEFAULT ''",
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS writer_prompt_override TEXT DEFAULT ''",
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS google_docs_connected BOOLEAN DEFAULT FALSE",
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS google_sheets_connected BOOLEAN DEFAULT FALSE",
                ]:
                    cur.execute(statement)
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS sessions (
                        token TEXT PRIMARY KEY,
                        user_id TEXT NOT NULL REFERENCES users(id),
                        created_at TIMESTAMPTZ NOT NULL,
                        expires_at TIMESTAMPTZ NOT NULL
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS runs (
                        id TEXT PRIMARY KEY,
                        user_id TEXT NOT NULL REFERENCES users(id),
                        query TEXT NOT NULL,
                        status TEXT NOT NULL,
                        stage TEXT NOT NULL,
                        progress_percent INTEGER NOT NULL,
                        error TEXT,
                        artifacts_json TEXT NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS briefs (
                        id TEXT PRIMARY KEY,
                        user_id TEXT NOT NULL REFERENCES users(id),
                        query TEXT NOT NULL,
                        status TEXT NOT NULL,
                        stage TEXT NOT NULL,
                        progress_percent INTEGER NOT NULL,
                        error TEXT,
                        artifacts_json TEXT NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS articles (
                        id TEXT PRIMARY KEY,
                        user_id TEXT NOT NULL REFERENCES users(id),
                        mode TEXT NOT NULL,
                        query TEXT NOT NULL,
                        status TEXT NOT NULL,
                        stage TEXT NOT NULL,
                        progress_percent INTEGER NOT NULL,
                        error TEXT,
                        artifacts_json TEXT NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL
                    )
                    """
                )
                cur.execute("CREATE INDEX IF NOT EXISTS idx_runs_user_created ON runs(user_id, created_at DESC)")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id)")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_briefs_user_created ON briefs(user_id, created_at DESC)")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_articles_user_created ON articles(user_id, created_at DESC)")
            conn.commit()

    def create_user(self, email: str, password: str) -> UserPublic:
        normalized = self._normalize_email(email)
        now = self._utcnow()
        user_id = str(uuid4())
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO users (
                            id, email, password_hash, name, brand_name, brand_url,
                            brief_prompt_override, writer_prompt_override,
                            google_docs_connected, google_sheets_connected, created_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (user_id, normalized, self._hash_password(password), None, None, None, "", "", False, False, now),
                    )
                conn.commit()
        except Exception as exc:  # noqa: BLE001
            message = str(exc).lower()
            if "duplicate" in message or "unique" in message:
                raise ValueError("Email already registered") from exc
            raise
        return UserPublic(id=user_id, email=normalized, created_at=now)

    def authenticate_user(self, email: str, password: str) -> Optional[UserPublic]:
        normalized = self._normalize_email(email)
        with self._connect() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """
                    SELECT id, email, password_hash, name, brand_name, brand_url,
                           brief_prompt_override, writer_prompt_override,
                           google_docs_connected, google_sheets_connected, created_at
                    FROM users WHERE email = %s
                    """,
                    (normalized,),
                )
                row = cur.fetchone()
        if not row or not self._verify_password(password, str(row["password_hash"])):
            return None
        return self._row_to_user(row)

    def create_session(self, user_id: str, ttl_days: int = 30) -> str:
        token = secrets.token_urlsafe(48)
        now = self._utcnow()
        expires = now + timedelta(days=ttl_days)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (%s, %s, %s, %s)",
                    (token, user_id, now, expires),
                )
            conn.commit()
        return token

    def get_user_by_session(self, token: str) -> Optional[UserPublic]:
        with self._connect() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """
                    SELECT u.id, u.email, u.created_at, s.expires_at
                         , u.name, u.brand_name, u.brand_url
                         , u.brief_prompt_override, u.writer_prompt_override
                         , u.google_docs_connected, u.google_sheets_connected
                    FROM sessions s
                    JOIN users u ON u.id = s.user_id
                    WHERE s.token = %s
                    """,
                    (token,),
                )
                row = cur.fetchone()
        if not row:
            return None
        if self._parse_dt(row["expires_at"]) < self._utcnow():
            self.delete_session(token)
            return None
        return self._row_to_user(row)

    def delete_session(self, token: str) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM sessions WHERE token = %s", (token,))
            conn.commit()

    def get_user_settings(self, user_id: str) -> Optional[UserSettings]:
        with self._connect() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """
                    SELECT id, email, name, brand_name, brand_url,
                           brief_prompt_override, writer_prompt_override,
                           google_docs_connected, google_sheets_connected, created_at
                    FROM users WHERE id = %s
                    """,
                    (user_id,),
                )
                row = cur.fetchone()
        return self._row_to_user_settings(row) if row else None

    def update_user_settings(self, user_id: str, **kwargs: Any) -> Optional[UserSettings]:
        allowed = {"name", "brand_name", "brand_url", "brief_prompt_override", "writer_prompt_override"}
        updates = dict((k, v) for (k, v) in kwargs.items() if k in allowed)
        if not updates:
            return self.get_user_settings(user_id)

        columns = []
        values: List[Any] = []
        for key, value in updates.items():
            columns.append("{} = %s".format(key))
            values.append(value)
        values.append(user_id)

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE users SET {} WHERE id = %s".format(", ".join(columns)), values)
            conn.commit()
        return self.get_user_settings(user_id)

    def create_run(self, user_id: str, payload: RunCreateRequest) -> RunRecord:
        record_id = str(uuid4())
        now = self._utcnow()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO runs (id, user_id, query, status, stage, progress_percent, error, artifacts_json, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (record_id, user_id, payload.query, "queued", "queued", 0, None, RunArtifacts().model_dump_json(), now, now),
                )
            conn.commit()
        created = self.get_run(user_id, record_id)
        if not created:
            raise RuntimeError("Run creation failed unexpectedly")
        return created

    def get_run(self, user_id: str, run_id: str) -> Optional[RunRecord]:
        with self._connect() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute("SELECT * FROM runs WHERE id = %s AND user_id = %s", (run_id, user_id))
                row = cur.fetchone()
        return self._row_to_run(row) if row else None

    def get_run_by_id(self, run_id: str) -> Optional[RunRecord]:
        with self._connect() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute("SELECT * FROM runs WHERE id = %s", (run_id,))
                row = cur.fetchone()
        return self._row_to_run(row) if row else None

    def list_runs(self, user_id: str, limit: int = 100) -> List[RunRecord]:
        with self._connect() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute("SELECT * FROM runs WHERE user_id = %s ORDER BY created_at DESC LIMIT %s", (user_id, limit))
                rows = cur.fetchall()
        return [self._row_to_run(row) for row in rows]

    def update_run(self, run_id: str, **kwargs: Any) -> Optional[RunRecord]:
        return self._update_entity("runs", run_id, self.get_run_by_id, kwargs)

    def create_brief(self, user_id: str, payload: BriefCreateRequest) -> BriefRecord:
        record_id = str(uuid4())
        now = self._utcnow()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO briefs (id, user_id, query, status, stage, progress_percent, error, artifacts_json, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (record_id, user_id, payload.query, "queued", "queued", 0, None, BriefArtifacts().model_dump_json(), now, now),
                )
            conn.commit()
        created = self.get_brief(user_id, record_id)
        if not created:
            raise RuntimeError("Brief creation failed unexpectedly")
        return created

    def get_brief(self, user_id: str, brief_id: str) -> Optional[BriefRecord]:
        with self._connect() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute("SELECT * FROM briefs WHERE id = %s AND user_id = %s", (brief_id, user_id))
                row = cur.fetchone()
        return self._row_to_brief(row) if row else None

    def get_brief_by_id(self, brief_id: str) -> Optional[BriefRecord]:
        with self._connect() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute("SELECT * FROM briefs WHERE id = %s", (brief_id,))
                row = cur.fetchone()
        return self._row_to_brief(row) if row else None

    def list_briefs(self, user_id: str, limit: int = 100) -> List[BriefRecord]:
        with self._connect() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute("SELECT * FROM briefs WHERE user_id = %s ORDER BY created_at DESC LIMIT %s", (user_id, limit))
                rows = cur.fetchall()
        return [self._row_to_brief(row) for row in rows]

    def update_brief(self, brief_id: str, **kwargs: Any) -> Optional[BriefRecord]:
        return self._update_entity("briefs", brief_id, self.get_brief_by_id, kwargs)

    def create_article(
        self,
        user_id: str,
        payload: ArticleCreateRequest,
        artifacts: Optional[ArticleArtifacts] = None,
    ) -> ArticleRecord:
        record_id = str(uuid4())
        now = self._utcnow()
        initial_artifacts = artifacts or ArticleArtifacts()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO articles (id, user_id, mode, query, status, stage, progress_percent, error, artifacts_json, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        record_id,
                        user_id,
                        payload.mode,
                        payload.query,
                        "queued",
                        "queued",
                        0,
                        None,
                        initial_artifacts.model_dump_json(),
                        now,
                        now,
                    ),
                )
            conn.commit()
        created = self.get_article(user_id, record_id)
        if not created:
            raise RuntimeError("Article creation failed unexpectedly")
        return created

    def get_article(self, user_id: str, article_id: str) -> Optional[ArticleRecord]:
        with self._connect() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute("SELECT * FROM articles WHERE id = %s AND user_id = %s", (article_id, user_id))
                row = cur.fetchone()
        return self._row_to_article(row) if row else None

    def get_article_by_id(self, article_id: str) -> Optional[ArticleRecord]:
        with self._connect() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute("SELECT * FROM articles WHERE id = %s", (article_id,))
                row = cur.fetchone()
        return self._row_to_article(row) if row else None

    def list_articles(self, user_id: str, limit: int = 100) -> List[ArticleRecord]:
        with self._connect() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute("SELECT * FROM articles WHERE user_id = %s ORDER BY created_at DESC LIMIT %s", (user_id, limit))
                rows = cur.fetchall()
        return [self._row_to_article(row) for row in rows]

    def update_article(self, article_id: str, **kwargs: Any) -> Optional[ArticleRecord]:
        return self._update_entity("articles", article_id, self.get_article_by_id, kwargs)

    def _update_entity(self, table: str, record_id: str, getter, kwargs: Any):
        allowed = {"status", "stage", "progress_percent", "error", "artifacts"}
        updates = dict((k, v) for (k, v) in kwargs.items() if k in allowed)
        if not updates:
            return getter(record_id)
        if "artifacts" in updates:
            updates["artifacts_json"] = updates.pop("artifacts").model_dump_json()
        columns = []
        values: List[Any] = []
        for key, value in updates.items():
            columns.append("{} = %s".format(key))
            values.append(value)
        columns.append("updated_at = %s")
        values.append(self._utcnow())
        values.append(record_id)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE {} SET {} WHERE id = %s".format(table, ", ".join(columns)), values)
            conn.commit()
        return getter(record_id)


def _build_store() -> Any:
    database_url = os.getenv("DATABASE_URL", "").strip()
    if database_url:
        return PostgresStore(database_url)
    return SQLiteStore(os.getenv("APP_DB_PATH", "data/seo_agent.db"))


run_store = _build_store()

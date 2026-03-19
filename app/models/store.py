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
    TopicDeleteResponse,
    UserPublic,
    UserSettings,
    VisibilityCompetitor,
    VisibilityJobRecord,
    VisibilityProjectRecord,
    VisibilityProjectSummary,
    VisibilityPromptListRecord,
    VisibilityPromptRecord,
    VisibilityPromptRunRecord,
    VisibilityScheduleFrequency,
    VisibilitySubtopicRecord,
    VisibilityTopicRecord,
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
            orchestrator_personality_id=(row.get("orchestrator_personality_id") if hasattr(row, "get") else row["orchestrator_personality_id"]) or "strategist",
            brief_personality_id=(row.get("brief_personality_id") if hasattr(row, "get") else row["brief_personality_id"]) or "seo_strategist",
            writer_personality_id=(row.get("writer_personality_id") if hasattr(row, "get") else row["writer_personality_id"]) or "seo_writer",
            custom_orchestrator_personality=(row.get("custom_orchestrator_personality") if hasattr(row, "get") else row["custom_orchestrator_personality"]) or "",
            custom_brief_personality=(row.get("custom_brief_personality") if hasattr(row, "get") else row["custom_brief_personality"]) or "",
            custom_writer_personality=(row.get("custom_writer_personality") if hasattr(row, "get") else row["custom_writer_personality"]) or "",
            google_docs_connected=bool((row.get("google_docs_connected") if hasattr(row, "get") else row["google_docs_connected"]) or False),
            google_sheets_connected=bool((row.get("google_sheets_connected") if hasattr(row, "get") else row["google_sheets_connected"]) or False),
            created_at=StoreBase._parse_dt(row["created_at"]),
        )

    @staticmethod
    def _row_to_visibility_competitor(row: Mapping[str, Any]) -> VisibilityCompetitor:
        return VisibilityCompetitor(
            id=str(row["id"]),
            user_id=str(row["user_id"]),
            project_id=str(row["project_id"]),
            name=str(row["name"]),
            domain=str(row["domain"] or ""),
            created_at=StoreBase._parse_dt(row["created_at"]),
            updated_at=StoreBase._parse_dt(row["updated_at"]),
        )

    @staticmethod
    def _row_to_visibility_project(
        row: Mapping[str, Any],
        competitors: Optional[List[VisibilityCompetitor]] = None,
    ) -> VisibilityProjectRecord:
        return VisibilityProjectRecord(
            id=str(row["id"]),
            user_id=str(row["user_id"]),
            name=str(row.get("name") if hasattr(row, "get") else row["name"] or ""),
            brand_name=str(row["brand_name"] or ""),
            brand_url=str(row["brand_url"] or ""),
            default_schedule_frequency=str(row["default_schedule_frequency"] or "disabled"),
            topic_count=int((row.get("topic_count") if hasattr(row, "get") else row["topic_count"]) or 0),
            prompt_list_count=int((row.get("prompt_list_count") if hasattr(row, "get") else row["prompt_list_count"]) or 0),
            prompt_count=int((row.get("prompt_count") if hasattr(row, "get") else row["prompt_count"]) or 0),
            run_count=int((row.get("run_count") if hasattr(row, "get") else row["run_count"]) or 0),
            last_run_at=StoreBase._parse_dt((row.get("last_run_at") if hasattr(row, "get") else row["last_run_at"])) if ((row.get("last_run_at") if hasattr(row, "get") else row["last_run_at"])) else None,
            created_at=StoreBase._parse_dt(row["created_at"]),
            updated_at=StoreBase._parse_dt(row["updated_at"]),
            competitors=competitors or [],
        )

    @staticmethod
    def _row_to_visibility_topic(row: Mapping[str, Any]) -> VisibilityTopicRecord:
        return VisibilityTopicRecord(
            id=str(row["id"]),
            user_id=str(row["user_id"]),
            project_id=str(row["project_id"]),
            name=str(row["name"]),
            created_at=StoreBase._parse_dt(row["created_at"]),
            updated_at=StoreBase._parse_dt(row["updated_at"]),
            subtopics=[],
        )

    @staticmethod
    def _row_to_visibility_subtopic(row: Mapping[str, Any]) -> VisibilitySubtopicRecord:
        return VisibilitySubtopicRecord(
            id=str(row["id"]),
            user_id=str(row["user_id"]),
            project_id=str(row["project_id"]),
            topic_id=str(row["topic_id"]),
            name=str(row["name"]),
            created_at=StoreBase._parse_dt(row["created_at"]),
            updated_at=StoreBase._parse_dt(row["updated_at"]),
            prompt_lists=[],
        )

    @staticmethod
    def _row_to_visibility_prompt_list(row: Mapping[str, Any]) -> VisibilityPromptListRecord:
        return VisibilityPromptListRecord(
            id=str(row["id"]),
            user_id=str(row["user_id"]),
            project_id=str(row["project_id"]),
            subtopic_id=str(row["subtopic_id"]),
            name=str(row["name"]),
            schedule_frequency=str(row["schedule_frequency"] or "disabled"),
            last_run_at=StoreBase._parse_dt(row["last_run_at"]) if row["last_run_at"] else None,
            next_run_at=StoreBase._parse_dt(row["next_run_at"]) if row["next_run_at"] else None,
            created_at=StoreBase._parse_dt(row["created_at"]),
            updated_at=StoreBase._parse_dt(row["updated_at"]),
            prompts=[],
        )

    @staticmethod
    def _row_to_visibility_prompt(row: Mapping[str, Any]) -> VisibilityPromptRecord:
        return VisibilityPromptRecord(
            id=str(row["id"]),
            user_id=str(row["user_id"]),
            project_id=str(row["project_id"]),
            prompt_list_id=str(row["prompt_list_id"]),
            prompt_text=str(row["prompt_text"]),
            position=int(row["position"] or 0),
            created_at=StoreBase._parse_dt(row["created_at"]),
            updated_at=StoreBase._parse_dt(row["updated_at"]),
        )

    @staticmethod
    def _row_to_visibility_prompt_run(row: Mapping[str, Any]) -> VisibilityPromptRunRecord:
        return VisibilityPromptRunRecord(
            id=str(row["id"]),
            user_id=str(row["user_id"]),
            project_id=str(row["project_id"]),
            job_id=str(row["job_id"]) if row["job_id"] else None,
            topic_id=str(row["topic_id"]),
            subtopic_id=str(row["subtopic_id"]),
            prompt_list_id=str(row["prompt_list_id"]),
            prompt_id=str(row["prompt_id"]),
            prompt_text=str(row["prompt_text"]),
            provider=str(row["provider"] or "openai"),
            model=str(row["model"] or "gpt-5-mini"),
            surface=str(row["surface"] or "api"),
            run_source=str(row["run_source"] or "manual"),
            status=str(row["status"]),
            response_text=str(row["response_text"] or ""),
            brands=json.loads(str(row["brands_json"] or "[]")),
            cited_domains=json.loads(str(row["cited_domains_json"] or "[]")),
            cited_urls=json.loads(str(row["cited_urls_json"] or "[]")),
            error=row["error"],
            created_at=StoreBase._parse_dt(row["created_at"]),
            updated_at=StoreBase._parse_dt(row["updated_at"]),
        )

    @staticmethod
    def _row_to_visibility_job(row: Mapping[str, Any]) -> VisibilityJobRecord:
        return VisibilityJobRecord(
            id=str(row["id"]),
            user_id=str(row["user_id"]),
            project_id=str(row["project_id"]),
            topic_id=str(row["topic_id"]),
            subtopic_id=str(row["subtopic_id"]),
            prompt_list_id=str(row["prompt_list_id"]),
            provider=str(row["provider"] or "openai"),
            model=str(row["model"] or "gpt-5-mini"),
            surface=str(row["surface"] or "api"),
            run_source=str(row["run_source"] or "manual"),
            status=str(row["status"]),
            stage=str(row["stage"]),
            progress_percent=int(row["progress_percent"] or 0),
            total_prompts=int(row["total_prompts"] or 0),
            completed_prompts=int(row["completed_prompts"] or 0),
            error=row["error"],
            created_at=StoreBase._parse_dt(row["created_at"]),
            updated_at=StoreBase._parse_dt(row["updated_at"]),
        )

    @staticmethod
    def _normalize_topics(topics: List[str]) -> List[str]:
        seen = set()
        normalized: List[str] = []
        for topic in topics:
            cleaned = topic.strip()
            if cleaned and cleaned not in seen:
                seen.add(cleaned)
                normalized.append(cleaned)
        return normalized

    @staticmethod
    def _normalize_schedule_frequency(value: str) -> VisibilityScheduleFrequency:
        cleaned = (value or "disabled").strip().lower().replace("-", "_")
        if cleaned in {"weekly", "twice_monthly", "monthly"}:
            return cleaned  # type: ignore[return-value]
        return "disabled"

    @staticmethod
    def _next_scheduled_run(
        frequency: VisibilityScheduleFrequency,
        base: Optional[datetime] = None,
    ) -> Optional[datetime]:
        if frequency == "disabled":
            return None
        anchor = base or StoreBase._utcnow()
        if frequency == "weekly":
            return anchor + timedelta(days=7)
        if frequency == "twice_monthly":
            return anchor + timedelta(days=15)
        return anchor + timedelta(days=30)


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
                    orchestrator_personality_id TEXT DEFAULT 'strategist',
                    brief_personality_id TEXT DEFAULT 'seo_strategist',
                    writer_personality_id TEXT DEFAULT 'seo_writer',
                    custom_orchestrator_personality TEXT DEFAULT '',
                    custom_brief_personality TEXT DEFAULT '',
                    custom_writer_personality TEXT DEFAULT '',
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

                CREATE TABLE IF NOT EXISTS visibility_profiles (
                    id TEXT PRIMARY KEY,
                    user_id TEXT UNIQUE NOT NULL,
                    brand_name TEXT DEFAULT '',
                    brand_url TEXT DEFAULT '',
                    default_schedule_frequency TEXT DEFAULT 'disabled',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id)
                );

                CREATE TABLE IF NOT EXISTS visibility_projects (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    brand_name TEXT DEFAULT '',
                    brand_url TEXT DEFAULT '',
                    default_schedule_frequency TEXT DEFAULT 'disabled',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id)
                );

                CREATE TABLE IF NOT EXISTS visibility_competitors (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    project_id TEXT,
                    name TEXT NOT NULL,
                    domain TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id),
                    FOREIGN KEY(project_id) REFERENCES visibility_projects(id)
                );

                CREATE TABLE IF NOT EXISTS visibility_topics (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    project_id TEXT,
                    name TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id),
                    FOREIGN KEY(project_id) REFERENCES visibility_projects(id)
                );

                CREATE TABLE IF NOT EXISTS visibility_subtopics (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    project_id TEXT,
                    topic_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id),
                    FOREIGN KEY(topic_id) REFERENCES visibility_topics(id),
                    FOREIGN KEY(project_id) REFERENCES visibility_projects(id)
                );

                CREATE TABLE IF NOT EXISTS visibility_prompt_lists (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    project_id TEXT,
                    subtopic_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    schedule_frequency TEXT DEFAULT 'disabled',
                    last_run_at TEXT,
                    next_run_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id),
                    FOREIGN KEY(subtopic_id) REFERENCES visibility_subtopics(id),
                    FOREIGN KEY(project_id) REFERENCES visibility_projects(id)
                );

                CREATE TABLE IF NOT EXISTS visibility_prompts (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    project_id TEXT,
                    prompt_list_id TEXT NOT NULL,
                    prompt_text TEXT NOT NULL,
                    position INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id),
                    FOREIGN KEY(prompt_list_id) REFERENCES visibility_prompt_lists(id),
                    FOREIGN KEY(project_id) REFERENCES visibility_projects(id)
                );

                CREATE TABLE IF NOT EXISTS visibility_jobs (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    project_id TEXT,
                    topic_id TEXT NOT NULL,
                    subtopic_id TEXT NOT NULL,
                    prompt_list_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    surface TEXT NOT NULL,
                    run_source TEXT NOT NULL,
                    status TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    progress_percent INTEGER NOT NULL DEFAULT 0,
                    total_prompts INTEGER NOT NULL DEFAULT 0,
                    completed_prompts INTEGER NOT NULL DEFAULT 0,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id),
                    FOREIGN KEY(project_id) REFERENCES visibility_projects(id),
                    FOREIGN KEY(topic_id) REFERENCES visibility_topics(id),
                    FOREIGN KEY(subtopic_id) REFERENCES visibility_subtopics(id),
                    FOREIGN KEY(prompt_list_id) REFERENCES visibility_prompt_lists(id)
                );

                CREATE TABLE IF NOT EXISTS visibility_prompt_runs (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    project_id TEXT,
                    job_id TEXT,
                    topic_id TEXT NOT NULL,
                    subtopic_id TEXT NOT NULL,
                    prompt_list_id TEXT NOT NULL,
                    prompt_id TEXT NOT NULL,
                    prompt_text TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    surface TEXT NOT NULL,
                    run_source TEXT NOT NULL,
                    status TEXT NOT NULL,
                    response_text TEXT DEFAULT '',
                    brands_json TEXT NOT NULL DEFAULT '[]',
                    cited_domains_json TEXT NOT NULL DEFAULT '[]',
                    cited_urls_json TEXT NOT NULL DEFAULT '[]',
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id),
                    FOREIGN KEY(project_id) REFERENCES visibility_projects(id),
                    FOREIGN KEY(job_id) REFERENCES visibility_jobs(id),
                    FOREIGN KEY(topic_id) REFERENCES visibility_topics(id),
                    FOREIGN KEY(subtopic_id) REFERENCES visibility_subtopics(id),
                    FOREIGN KEY(prompt_list_id) REFERENCES visibility_prompt_lists(id),
                    FOREIGN KEY(prompt_id) REFERENCES visibility_prompts(id)
                );

                CREATE INDEX IF NOT EXISTS idx_runs_user_created ON runs(user_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
                CREATE INDEX IF NOT EXISTS idx_briefs_user_created ON briefs(user_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_articles_user_created ON articles(user_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_visibility_projects_user ON visibility_projects(user_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_visibility_competitors_user ON visibility_competitors(user_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_visibility_topics_user ON visibility_topics(user_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_visibility_subtopics_topic ON visibility_subtopics(topic_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_visibility_lists_subtopic ON visibility_prompt_lists(subtopic_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_visibility_prompts_list ON visibility_prompts(prompt_list_id, position ASC);
                CREATE INDEX IF NOT EXISTS idx_visibility_jobs_user ON visibility_jobs(user_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_visibility_runs_user ON visibility_prompt_runs(user_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_visibility_lists_next_run ON visibility_prompt_lists(next_run_at);
                """
            )
            # Lightweight migrations for existing local databases.
            for statement in [
                "ALTER TABLE users ADD COLUMN name TEXT",
                "ALTER TABLE users ADD COLUMN brand_name TEXT",
                "ALTER TABLE users ADD COLUMN brand_url TEXT",
                "ALTER TABLE users ADD COLUMN brief_prompt_override TEXT DEFAULT ''",
                "ALTER TABLE users ADD COLUMN writer_prompt_override TEXT DEFAULT ''",
                "ALTER TABLE users ADD COLUMN orchestrator_personality_id TEXT DEFAULT 'strategist'",
                "ALTER TABLE users ADD COLUMN brief_personality_id TEXT DEFAULT 'seo_strategist'",
                "ALTER TABLE users ADD COLUMN writer_personality_id TEXT DEFAULT 'seo_writer'",
                "ALTER TABLE users ADD COLUMN custom_orchestrator_personality TEXT DEFAULT ''",
                "ALTER TABLE users ADD COLUMN custom_brief_personality TEXT DEFAULT ''",
                "ALTER TABLE users ADD COLUMN custom_writer_personality TEXT DEFAULT ''",
                "ALTER TABLE users ADD COLUMN google_docs_connected INTEGER DEFAULT 0",
                "ALTER TABLE users ADD COLUMN google_sheets_connected INTEGER DEFAULT 0",
                "ALTER TABLE visibility_competitors ADD COLUMN project_id TEXT",
                "ALTER TABLE visibility_topics ADD COLUMN project_id TEXT",
                "ALTER TABLE visibility_subtopics ADD COLUMN project_id TEXT",
                "ALTER TABLE visibility_prompt_lists ADD COLUMN project_id TEXT",
                "ALTER TABLE visibility_prompts ADD COLUMN project_id TEXT",
                "ALTER TABLE visibility_jobs ADD COLUMN project_id TEXT",
                "ALTER TABLE visibility_prompt_runs ADD COLUMN project_id TEXT",
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
                        brief_prompt_override, writer_prompt_override,
                        orchestrator_personality_id, brief_personality_id, writer_personality_id,
                        custom_orchestrator_personality, custom_brief_personality, custom_writer_personality,
                        google_docs_connected, google_sheets_connected, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (user_id, normalized, password_hash, None, None, None, "", "", "strategist", "seo_strategist", "seo_writer", "", "", "", 0, 0, now),
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
                       orchestrator_personality_id, brief_personality_id, writer_personality_id,
                       custom_orchestrator_personality, custom_brief_personality, custom_writer_personality,
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
                     , u.orchestrator_personality_id, u.brief_personality_id, u.writer_personality_id
                     , u.custom_orchestrator_personality, u.custom_brief_personality, u.custom_writer_personality
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
                       orchestrator_personality_id, brief_personality_id, writer_personality_id,
                       custom_orchestrator_personality, custom_brief_personality, custom_writer_personality,
                       google_docs_connected, google_sheets_connected, created_at
                FROM users WHERE id = ?
                """,
                (user_id,),
            ).fetchone()
        return self._row_to_user_settings(row) if row else None

    def update_user_settings(self, user_id: str, **kwargs: Any) -> Optional[UserSettings]:
        allowed = {
            "name",
            "brand_name",
            "brand_url",
            "brief_prompt_override",
            "writer_prompt_override",
            "orchestrator_personality_id",
            "brief_personality_id",
            "writer_personality_id",
            "custom_orchestrator_personality",
            "custom_brief_personality",
            "custom_writer_personality",
        }
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

    def _ensure_visibility_project_migration(self, user_id: str) -> None:
        with self._lock:
            project_rows = self._conn.execute(
                "SELECT id FROM visibility_projects WHERE user_id = ? ORDER BY created_at ASC",
                (user_id,),
            ).fetchall()
            if project_rows:
                default_project_id = str(project_rows[0]["id"])
            else:
                has_legacy = False
                legacy_profile = self._conn.execute(
                    "SELECT * FROM visibility_profiles WHERE user_id = ?",
                    (user_id,),
                ).fetchone()
                for table in [
                    "visibility_competitors",
                    "visibility_topics",
                    "visibility_subtopics",
                    "visibility_prompt_lists",
                    "visibility_prompts",
                    "visibility_jobs",
                    "visibility_prompt_runs",
                ]:
                    row = self._conn.execute(
                        f"SELECT id FROM {table} WHERE user_id = ? LIMIT 1",
                        (user_id,),
                    ).fetchone()
                    if row:
                        has_legacy = True
                        break
                if not legacy_profile and not has_legacy:
                    return
                user_row = self._conn.execute(
                    "SELECT brand_name, brand_url FROM users WHERE id = ?",
                    (user_id,),
                ).fetchone()
                now = self._now_iso()
                default_project_id = str(uuid4())
                project_name = ""
                if legacy_profile and str(legacy_profile["brand_name"] or "").strip():
                    project_name = str(legacy_profile["brand_name"]).strip()
                elif user_row and str(user_row["brand_name"] or "").strip():
                    project_name = str(user_row["brand_name"]).strip()
                else:
                    project_name = "Default Project"
                self._conn.execute(
                    """
                    INSERT INTO visibility_projects (
                        id, user_id, name, brand_name, brand_url, default_schedule_frequency, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        default_project_id,
                        user_id,
                        project_name,
                        str((legacy_profile["brand_name"] if legacy_profile else None) or (user_row["brand_name"] if user_row else "") or ""),
                        str((legacy_profile["brand_url"] if legacy_profile else None) or (user_row["brand_url"] if user_row else "") or ""),
                        str((legacy_profile["default_schedule_frequency"] if legacy_profile else "disabled") or "disabled"),
                        now,
                        now,
                    ),
                )
            for table in [
                "visibility_competitors",
                "visibility_topics",
                "visibility_subtopics",
                "visibility_prompt_lists",
                "visibility_prompts",
                "visibility_jobs",
                "visibility_prompt_runs",
            ]:
                self._conn.execute(
                    f"UPDATE {table} SET project_id = ? WHERE user_id = ? AND (project_id IS NULL OR project_id = '')",
                    (default_project_id, user_id),
                )
            self._conn.commit()

    def _visibility_project_row(self, user_id: str, project_id: str):
        return self._conn.execute(
            """
            SELECT p.*,
                   COUNT(DISTINCT t.id) AS topic_count,
                   COUNT(DISTINCT pl.id) AS prompt_list_count,
                   COUNT(DISTINCT pp.id) AS prompt_count,
                   COUNT(DISTINCT r.id) AS run_count,
                   MAX(r.created_at) AS last_run_at
            FROM visibility_projects p
            LEFT JOIN visibility_topics t ON t.project_id = p.id
            LEFT JOIN visibility_prompt_lists pl ON pl.project_id = p.id
            LEFT JOIN visibility_prompts pp ON pp.project_id = p.id
            LEFT JOIN visibility_prompt_runs r ON r.project_id = p.id
            WHERE p.user_id = ? AND p.id = ?
            GROUP BY p.id
            """,
            (user_id, project_id),
        ).fetchone()

    def list_visibility_projects(self, user_id: str) -> List[VisibilityProjectSummary]:
        self._ensure_visibility_project_migration(user_id)
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT p.*,
                       COUNT(DISTINCT t.id) AS topic_count,
                       COUNT(DISTINCT pl.id) AS prompt_list_count,
                       COUNT(DISTINCT pp.id) AS prompt_count,
                       COUNT(DISTINCT r.id) AS run_count,
                       MAX(r.created_at) AS last_run_at
                FROM visibility_projects p
                LEFT JOIN visibility_topics t ON t.project_id = p.id
                LEFT JOIN visibility_prompt_lists pl ON pl.project_id = p.id
                LEFT JOIN visibility_prompts pp ON pp.project_id = p.id
                LEFT JOIN visibility_prompt_runs r ON r.project_id = p.id
                WHERE p.user_id = ?
                GROUP BY p.id
                ORDER BY p.updated_at DESC, p.created_at DESC
                """,
                (user_id,),
            ).fetchall()
        summaries: List[VisibilityProjectSummary] = []
        for row in rows:
            competitors = self.list_visibility_competitors(user_id, str(row["id"]))
            summaries.append(self._row_to_visibility_project(row, competitors))
        return summaries

    def get_visibility_project(self, user_id: str, project_id: str) -> Optional[VisibilityProjectRecord]:
        self._ensure_visibility_project_migration(user_id)
        with self._lock:
            row = self._visibility_project_row(user_id, project_id)
        if not row:
            return None
        competitors = self.list_visibility_competitors(user_id, project_id)
        return self._row_to_visibility_project(row, competitors)

    def create_visibility_project(
        self,
        user_id: str,
        *,
        name: str,
        brand_name: str,
        brand_url: str,
        default_schedule_frequency: str,
    ) -> VisibilityProjectRecord:
        self._ensure_visibility_project_migration(user_id)
        project_id = str(uuid4())
        now = self._now_iso()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO visibility_projects (
                    id, user_id, name, brand_name, brand_url, default_schedule_frequency, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    project_id,
                    user_id,
                    name,
                    brand_name,
                    brand_url,
                    self._normalize_schedule_frequency(default_schedule_frequency),
                    now,
                    now,
                ),
            )
            self._conn.commit()
        created = self.get_visibility_project(user_id, project_id)
        if not created:
            raise RuntimeError("Visibility project creation failed unexpectedly")
        return created

    def update_visibility_project(
        self,
        user_id: str,
        project_id: str,
        *,
        name: str,
        brand_name: str,
        brand_url: str,
        default_schedule_frequency: str,
    ) -> Optional[VisibilityProjectRecord]:
        self._ensure_visibility_project_migration(user_id)
        with self._lock:
            self._conn.execute(
                """
                UPDATE visibility_projects
                SET name = ?, brand_name = ?, brand_url = ?, default_schedule_frequency = ?, updated_at = ?
                WHERE id = ? AND user_id = ?
                """,
                (
                    name,
                    brand_name,
                    brand_url,
                    self._normalize_schedule_frequency(default_schedule_frequency),
                    self._now_iso(),
                    project_id,
                    user_id,
                ),
            )
            self._conn.commit()
        return self.get_visibility_project(user_id, project_id)

    def delete_visibility_project(self, user_id: str, project_id: str) -> bool:
        self._ensure_visibility_project_migration(user_id)
        with self._lock:
            topic_rows = self._conn.execute(
                "SELECT id FROM visibility_topics WHERE project_id = ? AND user_id = ?",
                (project_id, user_id),
            ).fetchall()
        deleted_any = False
        for row in topic_rows:
            deleted_any = self.delete_visibility_topic(user_id, str(row["id"])) or deleted_any
        with self._lock:
            self._conn.execute("DELETE FROM visibility_prompt_runs WHERE project_id = ? AND user_id = ?", (project_id, user_id))
            self._conn.execute("DELETE FROM visibility_jobs WHERE project_id = ? AND user_id = ?", (project_id, user_id))
            self._conn.execute("DELETE FROM visibility_prompts WHERE project_id = ? AND user_id = ?", (project_id, user_id))
            self._conn.execute("DELETE FROM visibility_prompt_lists WHERE project_id = ? AND user_id = ?", (project_id, user_id))
            self._conn.execute("DELETE FROM visibility_subtopics WHERE project_id = ? AND user_id = ?", (project_id, user_id))
            self._conn.execute("DELETE FROM visibility_topics WHERE project_id = ? AND user_id = ?", (project_id, user_id))
            self._conn.execute("DELETE FROM visibility_competitors WHERE project_id = ? AND user_id = ?", (project_id, user_id))
            cur = self._conn.execute("DELETE FROM visibility_projects WHERE id = ? AND user_id = ?", (project_id, user_id))
            self._conn.commit()
        return cur.rowcount > 0 or deleted_any

    def create_visibility_competitor(self, user_id: str, *, project_id: str, name: str, domain: str) -> VisibilityCompetitor:
        self._ensure_visibility_project_migration(user_id)
        competitor_id = str(uuid4())
        now = self._now_iso()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO visibility_competitors (id, user_id, project_id, name, domain, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (competitor_id, user_id, project_id, name, domain, now, now),
            )
            self._conn.commit()
            row = self._conn.execute("SELECT * FROM visibility_competitors WHERE id = ?", (competitor_id,)).fetchone()
        if not row:
            raise RuntimeError("Visibility competitor creation failed unexpectedly")
        return self._row_to_visibility_competitor(row)

    def list_visibility_competitors(self, user_id: str, project_id: str) -> List[VisibilityCompetitor]:
        self._ensure_visibility_project_migration(user_id)
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM visibility_competitors
                WHERE user_id = ? AND project_id = ?
                ORDER BY updated_at DESC, created_at DESC
                """,
                (user_id, project_id),
            ).fetchall()
        return [self._row_to_visibility_competitor(row) for row in rows]

    def create_visibility_topic(self, user_id: str, *, project_id: str, name: str) -> VisibilityTopicRecord:
        self._ensure_visibility_project_migration(user_id)
        topic_id = str(uuid4())
        now = self._now_iso()
        with self._lock:
            self._conn.execute(
                "INSERT INTO visibility_topics (id, user_id, project_id, name, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (topic_id, user_id, project_id, name, now, now),
            )
            self._conn.commit()
            row = self._conn.execute("SELECT * FROM visibility_topics WHERE id = ?", (topic_id,)).fetchone()
        if not row:
            raise RuntimeError("Visibility topic creation failed unexpectedly")
        return self._row_to_visibility_topic(row)

    def create_visibility_subtopic(self, user_id: str, *, project_id: str, topic_id: str, name: str) -> VisibilitySubtopicRecord:
        self._ensure_visibility_project_migration(user_id)
        subtopic_id = str(uuid4())
        now = self._now_iso()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO visibility_subtopics (id, user_id, project_id, topic_id, name, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (subtopic_id, user_id, project_id, topic_id, name, now, now),
            )
            self._conn.commit()
            row = self._conn.execute("SELECT * FROM visibility_subtopics WHERE id = ?", (subtopic_id,)).fetchone()
        if not row:
            raise RuntimeError("Visibility subtopic creation failed unexpectedly")
        return self._row_to_visibility_subtopic(row)

    def create_visibility_prompt_list(
        self,
        user_id: str,
        *,
        project_id: str,
        subtopic_id: str,
        name: str,
        schedule_frequency: str,
    ) -> VisibilityPromptListRecord:
        self._ensure_visibility_project_migration(user_id)
        prompt_list_id = str(uuid4())
        now = self._utcnow()
        normalized_frequency = self._normalize_schedule_frequency(schedule_frequency)
        next_run_at = self._next_scheduled_run(normalized_frequency, now)
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO visibility_prompt_lists (
                    id, user_id, project_id, subtopic_id, name, schedule_frequency, last_run_at, next_run_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    prompt_list_id,
                    user_id,
                    project_id,
                    subtopic_id,
                    name,
                    normalized_frequency,
                    None,
                    next_run_at.isoformat() if next_run_at else None,
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
            self._conn.commit()
            row = self._conn.execute("SELECT * FROM visibility_prompt_lists WHERE id = ?", (prompt_list_id,)).fetchone()
        if not row:
            raise RuntimeError("Visibility prompt list creation failed unexpectedly")
        return self._row_to_visibility_prompt_list(row)

    def create_visibility_prompts(
        self,
        user_id: str,
        *,
        project_id: str,
        prompt_list_id: str,
        prompts: List[str],
    ) -> List[VisibilityPromptRecord]:
        self._ensure_visibility_project_migration(user_id)
        created_ids: List[str] = []
        with self._lock:
            position_row = self._conn.execute(
                "SELECT COALESCE(MAX(position), 0) AS max_position FROM visibility_prompts WHERE prompt_list_id = ? AND project_id = ?",
                (prompt_list_id, project_id),
            ).fetchone()
            next_position = int(position_row["max_position"] or 0) + 1 if position_row else 1
            for prompt_text in prompts:
                clean = prompt_text.strip()
                if not clean:
                    continue
                prompt_id = str(uuid4())
                now = self._now_iso()
                self._conn.execute(
                    """
                    INSERT INTO visibility_prompts (
                        id, user_id, project_id, prompt_list_id, prompt_text, position, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (prompt_id, user_id, project_id, prompt_list_id, clean, next_position, now, now),
                )
                created_ids.append(prompt_id)
                next_position += 1
            self._conn.commit()
            if not created_ids:
                return []
            placeholders = ", ".join("?" for _ in created_ids)
            rows = self._conn.execute(
                "SELECT * FROM visibility_prompts WHERE id IN ({}) ORDER BY position ASC".format(placeholders),
                created_ids,
            ).fetchall()
        return [self._row_to_visibility_prompt(row) for row in rows]

    def get_visibility_prompt_list(self, user_id: str, prompt_list_id: str, project_id: Optional[str] = None) -> Optional[VisibilityPromptListRecord]:
        self._ensure_visibility_project_migration(user_id)
        query = "SELECT * FROM visibility_prompt_lists WHERE id = ? AND user_id = ?"
        params: List[Any] = [prompt_list_id, user_id]
        if project_id:
            query += " AND project_id = ?"
            params.append(project_id)
        with self._lock:
            row = self._conn.execute(query, params).fetchone()
            if not row:
                return None
            prompt_rows = self._conn.execute(
                "SELECT * FROM visibility_prompts WHERE prompt_list_id = ? ORDER BY position ASC, created_at ASC",
                (prompt_list_id,),
            ).fetchall()
        prompt_list = self._row_to_visibility_prompt_list(row)
        prompt_list.prompts = [self._row_to_visibility_prompt(item) for item in prompt_rows]
        return prompt_list

    def get_visibility_prompt_list_context(self, prompt_list_id: str, project_id: Optional[str] = None) -> Optional[dict[str, Any]]:
        query = """
            SELECT pl.*, st.topic_id, st.name AS subtopic_name, t.name AS topic_name
            FROM visibility_prompt_lists pl
            JOIN visibility_subtopics st ON st.id = pl.subtopic_id
            JOIN visibility_topics t ON t.id = st.topic_id
            WHERE pl.id = ?
        """
        params: List[Any] = [prompt_list_id]
        if project_id:
            query += " AND pl.project_id = ?"
            params.append(project_id)
        with self._lock:
            row = self._conn.execute(query, params).fetchone()
            if not row:
                return None
        return {
            "id": str(row["id"]),
            "user_id": str(row["user_id"]),
            "project_id": str(row["project_id"]),
            "subtopic_id": str(row["subtopic_id"]),
            "topic_id": str(row["topic_id"]),
            "name": str(row["name"]),
            "subtopic_name": str(row["subtopic_name"]),
            "topic_name": str(row["topic_name"]),
            "schedule_frequency": str(row["schedule_frequency"] or "disabled"),
            "last_run_at": self._parse_dt(row["last_run_at"]) if row["last_run_at"] else None,
            "next_run_at": self._parse_dt(row["next_run_at"]) if row["next_run_at"] else None,
        }

    def list_visibility_topics(self, user_id: str, project_id: str) -> List[VisibilityTopicRecord]:
        self._ensure_visibility_project_migration(user_id)
        with self._lock:
            topic_rows = self._conn.execute(
                "SELECT * FROM visibility_topics WHERE user_id = ? AND project_id = ? ORDER BY updated_at DESC, created_at DESC",
                (user_id, project_id),
            ).fetchall()
            subtopic_rows = self._conn.execute(
                "SELECT * FROM visibility_subtopics WHERE user_id = ? AND project_id = ? ORDER BY updated_at DESC, created_at DESC",
                (user_id, project_id),
            ).fetchall()
            list_rows = self._conn.execute(
                "SELECT * FROM visibility_prompt_lists WHERE user_id = ? AND project_id = ? ORDER BY updated_at DESC, created_at DESC",
                (user_id, project_id),
            ).fetchall()
            prompt_rows = self._conn.execute(
                "SELECT * FROM visibility_prompts WHERE user_id = ? AND project_id = ? ORDER BY position ASC, created_at ASC",
                (user_id, project_id),
            ).fetchall()

        topics = {row["id"]: self._row_to_visibility_topic(row) for row in topic_rows}
        subtopics = {row["id"]: self._row_to_visibility_subtopic(row) for row in subtopic_rows}
        prompt_lists = {row["id"]: self._row_to_visibility_prompt_list(row) for row in list_rows}
        for prompt_row in prompt_rows:
            prompt = self._row_to_visibility_prompt(prompt_row)
            prompt_list = prompt_lists.get(prompt.prompt_list_id)
            if prompt_list:
                prompt_list.prompts.append(prompt)
        for prompt_list in prompt_lists.values():
            subtopic = subtopics.get(prompt_list.subtopic_id)
            if subtopic:
                subtopic.prompt_lists.append(prompt_list)
        for subtopic in subtopics.values():
            topic = topics.get(subtopic.topic_id)
            if topic:
                topic.subtopics.append(subtopic)
        return list(topics.values())

    def create_visibility_job(
        self,
        user_id: str,
        *,
        project_id: str,
        topic_id: str,
        subtopic_id: str,
        prompt_list_id: str,
        provider: str,
        model: str,
        surface: str,
        run_source: str,
        total_prompts: int,
    ) -> VisibilityJobRecord:
        job_id = str(uuid4())
        now = self._now_iso()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO visibility_jobs (
                    id, user_id, project_id, topic_id, subtopic_id, prompt_list_id, provider, model, surface, run_source,
                    status, stage, progress_percent, total_prompts, completed_prompts, error, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id, user_id, project_id, topic_id, subtopic_id, prompt_list_id,
                    provider, model, surface, run_source, "queued", "queued", 0,
                    total_prompts, 0, None, now, now,
                ),
            )
            self._conn.commit()
            row = self._conn.execute("SELECT * FROM visibility_jobs WHERE id = ?", (job_id,)).fetchone()
        if not row:
            raise RuntimeError("Visibility job creation failed unexpectedly")
        return self._row_to_visibility_job(row)

    def get_visibility_job(self, user_id: str, job_id: str) -> Optional[VisibilityJobRecord]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM visibility_jobs WHERE id = ? AND user_id = ?", (job_id, user_id)).fetchone()
        return self._row_to_visibility_job(row) if row else None

    def get_visibility_job_by_id(self, job_id: str) -> Optional[VisibilityJobRecord]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM visibility_jobs WHERE id = ?", (job_id,)).fetchone()
        return self._row_to_visibility_job(row) if row else None

    def list_visibility_jobs(
        self,
        user_id: str,
        project_id: str,
        *,
        limit: int = 20,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> List[VisibilityJobRecord]:
        query = "SELECT * FROM visibility_jobs WHERE user_id = ? AND project_id = ?"
        params: List[Any] = [user_id, project_id]
        if start_date:
            query += " AND created_at >= ?"
            params.append(start_date.isoformat())
        if end_date:
            query += " AND created_at <= ?"
            params.append(end_date.isoformat())
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return [self._row_to_visibility_job(row) for row in rows]

    def update_visibility_job(self, job_id: str, **kwargs: Any) -> Optional[VisibilityJobRecord]:
        allowed = {"status", "stage", "progress_percent", "completed_prompts", "total_prompts", "error"}
        updates = dict((k, v) for (k, v) in kwargs.items() if k in allowed)
        if not updates:
            return self.get_visibility_job_by_id(job_id)
        columns = []
        values: List[Any] = []
        for key, value in updates.items():
            columns.append(f"{key} = ?")
            values.append(value)
        columns.append("updated_at = ?")
        values.append(self._now_iso())
        values.append(job_id)
        with self._lock:
            self._conn.execute(f"UPDATE visibility_jobs SET {', '.join(columns)} WHERE id = ?", values)
            self._conn.commit()
        return self.get_visibility_job_by_id(job_id)

    def delete_visibility_prompt_runs_for_job(self, user_id: str, job_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM visibility_prompt_runs WHERE user_id = ? AND job_id = ?",
                (user_id, job_id),
            )
            self._conn.commit()

    def create_visibility_prompt_run(
        self,
        user_id: str,
        *,
        project_id: str,
        topic_id: str,
        subtopic_id: str,
        prompt_list_id: str,
        prompt_id: str,
        prompt_text: str,
        provider: str,
        model: str,
        surface: str,
        run_source: str,
        status: str,
        response_text: str = "",
        brands: Optional[List[str]] = None,
        cited_domains: Optional[List[str]] = None,
        cited_urls: Optional[List[str]] = None,
        error: Optional[str] = None,
        job_id: Optional[str] = None,
    ) -> VisibilityPromptRunRecord:
        run_id = str(uuid4())
        now = self._now_iso()
        with self._lock:
            if job_id:
                existing = self._conn.execute(
                    """
                    SELECT * FROM visibility_prompt_runs
                    WHERE user_id = ? AND job_id = ? AND prompt_id = ?
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (user_id, job_id, prompt_id),
                ).fetchone()
                if existing:
                    return self._row_to_visibility_prompt_run(existing)
            self._conn.execute(
                """
                INSERT INTO visibility_prompt_runs (
                    id, user_id, project_id, job_id, topic_id, subtopic_id, prompt_list_id, prompt_id, prompt_text,
                    provider, model, surface, run_source, status, response_text, brands_json,
                    cited_domains_json, cited_urls_json, error, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id, user_id, project_id, job_id, topic_id, subtopic_id, prompt_list_id, prompt_id, prompt_text,
                    provider, model, surface, run_source, status, response_text,
                    json.dumps(brands or []), json.dumps(cited_domains or []), json.dumps(cited_urls or []),
                    error, now, now,
                ),
            )
            self._conn.commit()
            row = self._conn.execute("SELECT * FROM visibility_prompt_runs WHERE id = ?", (run_id,)).fetchone()
        if not row:
            raise RuntimeError("Visibility prompt run creation failed unexpectedly")
        return self._row_to_visibility_prompt_run(row)

    def list_visibility_prompt_runs(
        self,
        user_id: str,
        *,
        project_id: str,
        limit: int = 200,
        topic_id: Optional[str] = None,
        subtopic_id: Optional[str] = None,
        prompt_list_id: Optional[str] = None,
        prompt_id: Optional[str] = None,
        job_id: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> List[VisibilityPromptRunRecord]:
        query = "SELECT * FROM visibility_prompt_runs WHERE user_id = ? AND project_id = ?"
        params: List[Any] = [user_id, project_id]
        if topic_id:
            query += " AND topic_id = ?"
            params.append(topic_id)
        if subtopic_id:
            query += " AND subtopic_id = ?"
            params.append(subtopic_id)
        if prompt_list_id:
            query += " AND prompt_list_id = ?"
            params.append(prompt_list_id)
        if prompt_id:
            query += " AND prompt_id = ?"
            params.append(prompt_id)
        if job_id:
            query += " AND job_id = ?"
            params.append(job_id)
        if start_date:
            query += " AND created_at >= ?"
            params.append(start_date.isoformat())
        if end_date:
            query += " AND created_at <= ?"
            params.append(end_date.isoformat())
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return [self._row_to_visibility_prompt_run(row) for row in rows]

    def list_due_visibility_prompt_lists(self, as_of: Optional[datetime] = None, limit: int = 25) -> List[dict[str, Any]]:
        due_at = (as_of or self._utcnow()).isoformat()
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT pl.*, st.topic_id
                FROM visibility_prompt_lists pl
                JOIN visibility_subtopics st ON st.id = pl.subtopic_id
                WHERE pl.schedule_frequency != 'disabled'
                  AND pl.next_run_at IS NOT NULL
                  AND pl.next_run_at <= ?
                ORDER BY pl.next_run_at ASC
                LIMIT ?
                """,
                (due_at, limit),
            ).fetchall()
        return [{"id": str(r["id"]), "user_id": str(r["user_id"]), "project_id": str(r["project_id"]), "subtopic_id": str(r["subtopic_id"]), "topic_id": str(r["topic_id"]), "schedule_frequency": str(r["schedule_frequency"] or "disabled")} for r in rows]

    def mark_visibility_prompt_list_run(
        self,
        prompt_list_id: str,
        *,
        frequency: str,
        run_at: Optional[datetime] = None,
    ) -> Optional[VisibilityPromptListRecord]:
        executed_at = run_at or self._utcnow()
        normalized_frequency = self._normalize_schedule_frequency(frequency)
        next_run_at = self._next_scheduled_run(normalized_frequency, executed_at)
        with self._lock:
            self._conn.execute(
                "UPDATE visibility_prompt_lists SET last_run_at = ?, next_run_at = ?, updated_at = ? WHERE id = ?",
                (executed_at.isoformat(), next_run_at.isoformat() if next_run_at else None, executed_at.isoformat(), prompt_list_id),
            )
            self._conn.commit()
            row = self._conn.execute("SELECT * FROM visibility_prompt_lists WHERE id = ?", (prompt_list_id,)).fetchone()
        return self._row_to_visibility_prompt_list(row) if row else None

    def delete_visibility_competitor(self, user_id: str, competitor_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM visibility_competitors WHERE id = ? AND user_id = ?", (competitor_id, user_id))
            self._conn.commit()
        return cur.rowcount > 0

    def delete_visibility_prompt(self, user_id: str, prompt_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM visibility_prompts WHERE id = ? AND user_id = ?", (prompt_id, user_id))
            self._conn.execute("DELETE FROM visibility_prompt_runs WHERE prompt_id = ? AND user_id = ?", (prompt_id, user_id))
            self._conn.commit()
        return cur.rowcount > 0

    def delete_visibility_prompt_list(self, user_id: str, prompt_list_id: str) -> bool:
        with self._lock:
            self._conn.execute("DELETE FROM visibility_prompt_runs WHERE prompt_list_id = ? AND user_id = ?", (prompt_list_id, user_id))
            self._conn.execute("DELETE FROM visibility_jobs WHERE prompt_list_id = ? AND user_id = ?", (prompt_list_id, user_id))
            self._conn.execute("DELETE FROM visibility_prompts WHERE prompt_list_id = ? AND user_id = ?", (prompt_list_id, user_id))
            cur = self._conn.execute("DELETE FROM visibility_prompt_lists WHERE id = ? AND user_id = ?", (prompt_list_id, user_id))
            self._conn.commit()
        return cur.rowcount > 0

    def delete_visibility_subtopic(self, user_id: str, subtopic_id: str) -> bool:
        with self._lock:
            list_rows = self._conn.execute("SELECT id FROM visibility_prompt_lists WHERE subtopic_id = ? AND user_id = ?", (subtopic_id, user_id)).fetchall()
        deleted_any = False
        for row in list_rows:
            deleted_any = self.delete_visibility_prompt_list(user_id, str(row["id"])) or deleted_any
        with self._lock:
            self._conn.execute("DELETE FROM visibility_prompt_runs WHERE subtopic_id = ? AND user_id = ?", (subtopic_id, user_id))
            self._conn.execute("DELETE FROM visibility_jobs WHERE subtopic_id = ? AND user_id = ?", (subtopic_id, user_id))
            cur = self._conn.execute("DELETE FROM visibility_subtopics WHERE id = ? AND user_id = ?", (subtopic_id, user_id))
            self._conn.commit()
        return cur.rowcount > 0 or deleted_any

    def delete_visibility_topic(self, user_id: str, topic_id: str) -> bool:
        with self._lock:
            subtopic_rows = self._conn.execute("SELECT id FROM visibility_subtopics WHERE topic_id = ? AND user_id = ?", (topic_id, user_id)).fetchall()
        deleted_any = False
        for row in subtopic_rows:
            deleted_any = self.delete_visibility_subtopic(user_id, str(row["id"])) or deleted_any
        with self._lock:
            self._conn.execute("DELETE FROM visibility_prompt_runs WHERE topic_id = ? AND user_id = ?", (topic_id, user_id))
            self._conn.execute("DELETE FROM visibility_jobs WHERE topic_id = ? AND user_id = ?", (topic_id, user_id))
            cur = self._conn.execute("DELETE FROM visibility_topics WHERE id = ? AND user_id = ?", (topic_id, user_id))
            self._conn.commit()
        return cur.rowcount > 0 or deleted_any

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
        initial_artifacts = BriefArtifacts(
            requested_target_location=payload.target_location,
            requested_seed_urls=payload.seed_urls,
            requested_ai_citations_text=payload.ai_citations_text,
            requested_ai_overview_text=payload.ai_overview_text,
        )
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO briefs (id, user_id, query, status, stage, progress_percent, error, artifacts_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (record_id, user_id, payload.query, "queued", "queued", 0, None, initial_artifacts.model_dump_json(), now, now),
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
        initial_artifacts = artifacts or ArticleArtifacts(
            requested_target_location=payload.target_location,
            requested_seed_urls=payload.seed_urls,
            requested_ai_citations_text=payload.ai_citations_text,
            requested_ai_overview_text=payload.ai_overview_text,
        )
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

    def delete_topics(self, user_id: str, topics: List[str]) -> TopicDeleteResponse:
        normalized = self._normalize_topics(topics)
        if not normalized:
            return TopicDeleteResponse()

        placeholders = ", ".join("?" for _ in normalized)
        with self._lock:
            brief_count_row = self._conn.execute(
                "SELECT COUNT(*) AS count FROM briefs WHERE user_id = ? AND query IN ({})".format(placeholders),
                [user_id] + normalized,
            ).fetchone()
            article_count_row = self._conn.execute(
                "SELECT COUNT(*) AS count FROM articles WHERE user_id = ? AND query IN ({})".format(placeholders),
                [user_id] + normalized,
            ).fetchone()
            self._conn.execute(
                "DELETE FROM briefs WHERE user_id = ? AND query IN ({})".format(placeholders),
                [user_id] + normalized,
            )
            self._conn.execute(
                "DELETE FROM articles WHERE user_id = ? AND query IN ({})".format(placeholders),
                [user_id] + normalized,
            )
            self._conn.commit()

        return TopicDeleteResponse(
            deleted_topics=normalized,
            deleted_briefs=int(brief_count_row["count"]) if brief_count_row else 0,
            deleted_articles=int(article_count_row["count"]) if article_count_row else 0,
        )

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
                        orchestrator_personality_id TEXT DEFAULT 'strategist',
                        brief_personality_id TEXT DEFAULT 'seo_strategist',
                        writer_personality_id TEXT DEFAULT 'seo_writer',
                        custom_orchestrator_personality TEXT DEFAULT '',
                        custom_brief_personality TEXT DEFAULT '',
                        custom_writer_personality TEXT DEFAULT '',
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
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS orchestrator_personality_id TEXT DEFAULT 'strategist'",
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS brief_personality_id TEXT DEFAULT 'seo_strategist'",
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS writer_personality_id TEXT DEFAULT 'seo_writer'",
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS custom_orchestrator_personality TEXT DEFAULT ''",
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS custom_brief_personality TEXT DEFAULT ''",
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS custom_writer_personality TEXT DEFAULT ''",
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS google_docs_connected BOOLEAN DEFAULT FALSE",
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS google_sheets_connected BOOLEAN DEFAULT FALSE",
                    "ALTER TABLE visibility_competitors ADD COLUMN IF NOT EXISTS project_id TEXT",
                    "ALTER TABLE visibility_topics ADD COLUMN IF NOT EXISTS project_id TEXT",
                    "ALTER TABLE visibility_subtopics ADD COLUMN IF NOT EXISTS project_id TEXT",
                    "ALTER TABLE visibility_prompt_lists ADD COLUMN IF NOT EXISTS project_id TEXT",
                    "ALTER TABLE visibility_prompts ADD COLUMN IF NOT EXISTS project_id TEXT",
                    "ALTER TABLE visibility_jobs ADD COLUMN IF NOT EXISTS project_id TEXT",
                    "ALTER TABLE visibility_prompt_runs ADD COLUMN IF NOT EXISTS project_id TEXT",
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
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS visibility_profiles (
                        id TEXT PRIMARY KEY,
                        user_id TEXT UNIQUE NOT NULL REFERENCES users(id),
                        brand_name TEXT DEFAULT '',
                        brand_url TEXT DEFAULT '',
                        default_schedule_frequency TEXT DEFAULT 'disabled',
                        created_at TIMESTAMPTZ NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS visibility_projects (
                        id TEXT PRIMARY KEY,
                        user_id TEXT NOT NULL REFERENCES users(id),
                        name TEXT NOT NULL,
                        brand_name TEXT DEFAULT '',
                        brand_url TEXT DEFAULT '',
                        default_schedule_frequency TEXT DEFAULT 'disabled',
                        created_at TIMESTAMPTZ NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS visibility_competitors (
                        id TEXT PRIMARY KEY,
                        user_id TEXT NOT NULL REFERENCES users(id),
                        project_id TEXT REFERENCES visibility_projects(id),
                        name TEXT NOT NULL,
                        domain TEXT DEFAULT '',
                        created_at TIMESTAMPTZ NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS visibility_topics (
                        id TEXT PRIMARY KEY,
                        user_id TEXT NOT NULL REFERENCES users(id),
                        project_id TEXT REFERENCES visibility_projects(id),
                        name TEXT NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS visibility_subtopics (
                        id TEXT PRIMARY KEY,
                        user_id TEXT NOT NULL REFERENCES users(id),
                        project_id TEXT REFERENCES visibility_projects(id),
                        topic_id TEXT NOT NULL REFERENCES visibility_topics(id),
                        name TEXT NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS visibility_prompt_lists (
                        id TEXT PRIMARY KEY,
                        user_id TEXT NOT NULL REFERENCES users(id),
                        project_id TEXT REFERENCES visibility_projects(id),
                        subtopic_id TEXT NOT NULL REFERENCES visibility_subtopics(id),
                        name TEXT NOT NULL,
                        schedule_frequency TEXT DEFAULT 'disabled',
                        last_run_at TIMESTAMPTZ,
                        next_run_at TIMESTAMPTZ,
                        created_at TIMESTAMPTZ NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS visibility_prompts (
                        id TEXT PRIMARY KEY,
                        user_id TEXT NOT NULL REFERENCES users(id),
                        project_id TEXT REFERENCES visibility_projects(id),
                        prompt_list_id TEXT NOT NULL REFERENCES visibility_prompt_lists(id),
                        prompt_text TEXT NOT NULL,
                        position INTEGER NOT NULL DEFAULT 0,
                        created_at TIMESTAMPTZ NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS visibility_jobs (
                        id TEXT PRIMARY KEY,
                        user_id TEXT NOT NULL REFERENCES users(id),
                        project_id TEXT REFERENCES visibility_projects(id),
                        topic_id TEXT NOT NULL REFERENCES visibility_topics(id),
                        subtopic_id TEXT NOT NULL REFERENCES visibility_subtopics(id),
                        prompt_list_id TEXT NOT NULL REFERENCES visibility_prompt_lists(id),
                        provider TEXT NOT NULL,
                        model TEXT NOT NULL,
                        surface TEXT NOT NULL,
                        run_source TEXT NOT NULL,
                        status TEXT NOT NULL,
                        stage TEXT NOT NULL,
                        progress_percent INTEGER NOT NULL DEFAULT 0,
                        total_prompts INTEGER NOT NULL DEFAULT 0,
                        completed_prompts INTEGER NOT NULL DEFAULT 0,
                        error TEXT,
                        created_at TIMESTAMPTZ NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS visibility_prompt_runs (
                        id TEXT PRIMARY KEY,
                        user_id TEXT NOT NULL REFERENCES users(id),
                        project_id TEXT REFERENCES visibility_projects(id),
                        job_id TEXT REFERENCES visibility_jobs(id),
                        topic_id TEXT NOT NULL REFERENCES visibility_topics(id),
                        subtopic_id TEXT NOT NULL REFERENCES visibility_subtopics(id),
                        prompt_list_id TEXT NOT NULL REFERENCES visibility_prompt_lists(id),
                        prompt_id TEXT NOT NULL REFERENCES visibility_prompts(id),
                        prompt_text TEXT NOT NULL,
                        provider TEXT NOT NULL,
                        model TEXT NOT NULL,
                        surface TEXT NOT NULL,
                        run_source TEXT NOT NULL,
                        status TEXT NOT NULL,
                        response_text TEXT DEFAULT '',
                        brands_json TEXT NOT NULL DEFAULT '[]',
                        cited_domains_json TEXT NOT NULL DEFAULT '[]',
                        cited_urls_json TEXT NOT NULL DEFAULT '[]',
                        error TEXT,
                        created_at TIMESTAMPTZ NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL
                    )
                    """
                )
                cur.execute("CREATE INDEX IF NOT EXISTS idx_runs_user_created ON runs(user_id, created_at DESC)")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id)")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_briefs_user_created ON briefs(user_id, created_at DESC)")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_articles_user_created ON articles(user_id, created_at DESC)")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_visibility_projects_user ON visibility_projects(user_id, created_at DESC)")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_visibility_competitors_user ON visibility_competitors(user_id, created_at DESC)")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_visibility_topics_user ON visibility_topics(user_id, created_at DESC)")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_visibility_subtopics_topic ON visibility_subtopics(topic_id, created_at DESC)")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_visibility_lists_subtopic ON visibility_prompt_lists(subtopic_id, created_at DESC)")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_visibility_prompts_list ON visibility_prompts(prompt_list_id, position ASC)")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_visibility_jobs_user ON visibility_jobs(user_id, created_at DESC)")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_visibility_runs_user ON visibility_prompt_runs(user_id, created_at DESC)")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_visibility_lists_next_run ON visibility_prompt_lists(next_run_at)")
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
                            orchestrator_personality_id, brief_personality_id, writer_personality_id,
                            custom_orchestrator_personality, custom_brief_personality, custom_writer_personality,
                            google_docs_connected, google_sheets_connected, created_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (user_id, normalized, self._hash_password(password), None, None, None, "", "", "strategist", "seo_strategist", "seo_writer", "", "", "", False, False, now),
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
                           orchestrator_personality_id, brief_personality_id, writer_personality_id,
                           custom_orchestrator_personality, custom_brief_personality, custom_writer_personality,
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
                         , u.orchestrator_personality_id, u.brief_personality_id, u.writer_personality_id
                         , u.custom_orchestrator_personality, u.custom_brief_personality, u.custom_writer_personality
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
                           orchestrator_personality_id, brief_personality_id, writer_personality_id,
                           custom_orchestrator_personality, custom_brief_personality, custom_writer_personality,
                           google_docs_connected, google_sheets_connected, created_at
                    FROM users WHERE id = %s
                    """,
                    (user_id,),
                )
                row = cur.fetchone()
        return self._row_to_user_settings(row) if row else None

    def update_user_settings(self, user_id: str, **kwargs: Any) -> Optional[UserSettings]:
        allowed = {
            "name",
            "brand_name",
            "brand_url",
            "brief_prompt_override",
            "writer_prompt_override",
            "orchestrator_personality_id",
            "brief_personality_id",
            "writer_personality_id",
            "custom_orchestrator_personality",
            "custom_brief_personality",
            "custom_writer_personality",
        }
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

    def _ensure_visibility_project_migration(self, user_id: str) -> None:
        with self._connect() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute("SELECT id FROM visibility_projects WHERE user_id = %s ORDER BY created_at ASC", (user_id,))
                project_rows = cur.fetchall()
                default_project_id = str(project_rows[0]["id"]) if project_rows else None
                if not default_project_id:
                    cur.execute("SELECT * FROM visibility_profiles WHERE user_id = %s", (user_id,))
                    legacy_profile = cur.fetchone()
                    has_legacy = False
                    for table in [
                        "visibility_competitors",
                        "visibility_topics",
                        "visibility_subtopics",
                        "visibility_prompt_lists",
                        "visibility_prompts",
                        "visibility_jobs",
                        "visibility_prompt_runs",
                    ]:
                        cur.execute(f"SELECT id FROM {table} WHERE user_id = %s LIMIT 1", (user_id,))
                        if cur.fetchone():
                            has_legacy = True
                            break
                    if not legacy_profile and not has_legacy:
                        return
                    cur.execute("SELECT brand_name, brand_url FROM users WHERE id = %s", (user_id,))
                    user_row = cur.fetchone()
                    default_project_id = str(uuid4())
                    project_name = (
                        str((legacy_profile["brand_name"] if legacy_profile else None) or (user_row["brand_name"] if user_row else "") or "").strip()
                        or "Default Project"
                    )
                    now = self._utcnow()
                    cur.execute(
                        """
                        INSERT INTO visibility_projects (
                            id, user_id, name, brand_name, brand_url, default_schedule_frequency, created_at, updated_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            default_project_id,
                            user_id,
                            project_name,
                            str((legacy_profile["brand_name"] if legacy_profile else None) or (user_row["brand_name"] if user_row else "") or ""),
                            str((legacy_profile["brand_url"] if legacy_profile else None) or (user_row["brand_url"] if user_row else "") or ""),
                            str((legacy_profile["default_schedule_frequency"] if legacy_profile else "disabled") or "disabled"),
                            now,
                            now,
                        ),
                    )
                for table in [
                    "visibility_competitors",
                    "visibility_topics",
                    "visibility_subtopics",
                    "visibility_prompt_lists",
                    "visibility_prompts",
                    "visibility_jobs",
                    "visibility_prompt_runs",
                ]:
                    cur.execute(
                        f"UPDATE {table} SET project_id = %s WHERE user_id = %s AND (project_id IS NULL OR project_id = '')",
                        (default_project_id, user_id),
                    )
            conn.commit()

    def list_visibility_projects(self, user_id: str) -> List[VisibilityProjectSummary]:
        self._ensure_visibility_project_migration(user_id)
        with self._connect() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """
                    SELECT p.*,
                           COUNT(DISTINCT t.id) AS topic_count,
                           COUNT(DISTINCT pl.id) AS prompt_list_count,
                           COUNT(DISTINCT pp.id) AS prompt_count,
                           COUNT(DISTINCT r.id) AS run_count,
                           MAX(r.created_at) AS last_run_at
                    FROM visibility_projects p
                    LEFT JOIN visibility_topics t ON t.project_id = p.id
                    LEFT JOIN visibility_prompt_lists pl ON pl.project_id = p.id
                    LEFT JOIN visibility_prompts pp ON pp.project_id = p.id
                    LEFT JOIN visibility_prompt_runs r ON r.project_id = p.id
                    WHERE p.user_id = %s
                    GROUP BY p.id
                    ORDER BY p.updated_at DESC, p.created_at DESC
                    """,
                    (user_id,),
                )
                rows = cur.fetchall()
        return [self.get_visibility_project(user_id, str(row["id"])) for row in rows if row]  # type: ignore[list-item]

    def get_visibility_project(self, user_id: str, project_id: str) -> Optional[VisibilityProjectRecord]:
        self._ensure_visibility_project_migration(user_id)
        with self._connect() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """
                    SELECT p.*,
                           COUNT(DISTINCT t.id) AS topic_count,
                           COUNT(DISTINCT pl.id) AS prompt_list_count,
                           COUNT(DISTINCT pp.id) AS prompt_count,
                           COUNT(DISTINCT r.id) AS run_count,
                           MAX(r.created_at) AS last_run_at
                    FROM visibility_projects p
                    LEFT JOIN visibility_topics t ON t.project_id = p.id
                    LEFT JOIN visibility_prompt_lists pl ON pl.project_id = p.id
                    LEFT JOIN visibility_prompts pp ON pp.project_id = p.id
                    LEFT JOIN visibility_prompt_runs r ON r.project_id = p.id
                    WHERE p.user_id = %s AND p.id = %s
                    GROUP BY p.id
                    """,
                    (user_id, project_id),
                )
                row = cur.fetchone()
        if not row:
            return None
        competitors = self.list_visibility_competitors(user_id, project_id)
        return self._row_to_visibility_project(row, competitors)

    def create_visibility_project(self, user_id: str, *, name: str, brand_name: str, brand_url: str, default_schedule_frequency: str) -> VisibilityProjectRecord:
        self._ensure_visibility_project_migration(user_id)
        project_id = str(uuid4())
        now = self._utcnow()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO visibility_projects (
                        id, user_id, name, brand_name, brand_url, default_schedule_frequency, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (project_id, user_id, name, brand_name, brand_url, self._normalize_schedule_frequency(default_schedule_frequency), now, now),
                )
            conn.commit()
        return self.get_visibility_project(user_id, project_id)  # type: ignore[return-value]

    def update_visibility_project(self, user_id: str, project_id: str, *, name: str, brand_name: str, brand_url: str, default_schedule_frequency: str) -> Optional[VisibilityProjectRecord]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE visibility_projects
                    SET name = %s, brand_name = %s, brand_url = %s, default_schedule_frequency = %s, updated_at = %s
                    WHERE id = %s AND user_id = %s
                    """,
                    (name, brand_name, brand_url, self._normalize_schedule_frequency(default_schedule_frequency), self._utcnow(), project_id, user_id),
                )
            conn.commit()
        return self.get_visibility_project(user_id, project_id)

    def delete_visibility_project(self, user_id: str, project_id: str) -> bool:
        with self._connect() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute("SELECT id FROM visibility_topics WHERE project_id = %s AND user_id = %s", (project_id, user_id))
                topic_rows = cur.fetchall()
        deleted_any = False
        for row in topic_rows:
            deleted_any = self.delete_visibility_topic(user_id, str(row["id"])) or deleted_any
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM visibility_prompt_runs WHERE project_id = %s AND user_id = %s", (project_id, user_id))
                cur.execute("DELETE FROM visibility_jobs WHERE project_id = %s AND user_id = %s", (project_id, user_id))
                cur.execute("DELETE FROM visibility_prompts WHERE project_id = %s AND user_id = %s", (project_id, user_id))
                cur.execute("DELETE FROM visibility_prompt_lists WHERE project_id = %s AND user_id = %s", (project_id, user_id))
                cur.execute("DELETE FROM visibility_subtopics WHERE project_id = %s AND user_id = %s", (project_id, user_id))
                cur.execute("DELETE FROM visibility_topics WHERE project_id = %s AND user_id = %s", (project_id, user_id))
                cur.execute("DELETE FROM visibility_competitors WHERE project_id = %s AND user_id = %s", (project_id, user_id))
                cur.execute("DELETE FROM visibility_projects WHERE id = %s AND user_id = %s", (project_id, user_id))
                deleted = cur.rowcount > 0
            conn.commit()
        return deleted or deleted_any

    def create_visibility_competitor(self, user_id: str, *, project_id: str, name: str, domain: str) -> VisibilityCompetitor:
        competitor_id = str(uuid4())
        now = self._utcnow()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO visibility_competitors (id, user_id, project_id, name, domain, created_at, updated_at) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    (competitor_id, user_id, project_id, name, domain, now, now),
                )
            conn.commit()
        with self._connect() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute("SELECT * FROM visibility_competitors WHERE id = %s", (competitor_id,))
                row = cur.fetchone()
        return self._row_to_visibility_competitor(row)

    def list_visibility_competitors(self, user_id: str, project_id: str) -> List[VisibilityCompetitor]:
        with self._connect() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute("SELECT * FROM visibility_competitors WHERE user_id = %s AND project_id = %s ORDER BY updated_at DESC, created_at DESC", (user_id, project_id))
                rows = cur.fetchall()
        return [self._row_to_visibility_competitor(row) for row in rows]

    def create_visibility_topic(self, user_id: str, *, project_id: str, name: str) -> VisibilityTopicRecord:
        topic_id = str(uuid4())
        now = self._utcnow()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO visibility_topics (id, user_id, project_id, name, created_at, updated_at) VALUES (%s, %s, %s, %s, %s, %s)", (topic_id, user_id, project_id, name, now, now))
            conn.commit()
        with self._connect() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute("SELECT * FROM visibility_topics WHERE id = %s", (topic_id,))
                row = cur.fetchone()
        return self._row_to_visibility_topic(row)

    def create_visibility_subtopic(self, user_id: str, *, project_id: str, topic_id: str, name: str) -> VisibilitySubtopicRecord:
        subtopic_id = str(uuid4())
        now = self._utcnow()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO visibility_subtopics (id, user_id, project_id, topic_id, name, created_at, updated_at) VALUES (%s, %s, %s, %s, %s, %s, %s)", (subtopic_id, user_id, project_id, topic_id, name, now, now))
            conn.commit()
        with self._connect() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute("SELECT * FROM visibility_subtopics WHERE id = %s", (subtopic_id,))
                row = cur.fetchone()
        return self._row_to_visibility_subtopic(row)

    def create_visibility_prompt_list(self, user_id: str, *, project_id: str, subtopic_id: str, name: str, schedule_frequency: str) -> VisibilityPromptListRecord:
        prompt_list_id = str(uuid4())
        now = self._utcnow()
        normalized_frequency = self._normalize_schedule_frequency(schedule_frequency)
        next_run_at = self._next_scheduled_run(normalized_frequency, now)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO visibility_prompt_lists (id, user_id, project_id, subtopic_id, name, schedule_frequency, last_run_at, next_run_at, created_at, updated_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (prompt_list_id, user_id, project_id, subtopic_id, name, normalized_frequency, None, next_run_at, now, now),
                )
            conn.commit()
        with self._connect() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute("SELECT * FROM visibility_prompt_lists WHERE id = %s", (prompt_list_id,))
                row = cur.fetchone()
        return self._row_to_visibility_prompt_list(row)

    def create_visibility_prompts(self, user_id: str, *, project_id: str, prompt_list_id: str, prompts: List[str]) -> List[VisibilityPromptRecord]:
        created_ids: List[str] = []
        with self._connect() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute("SELECT COALESCE(MAX(position), 0) AS max_position FROM visibility_prompts WHERE prompt_list_id = %s AND project_id = %s", (prompt_list_id, project_id))
                row = cur.fetchone()
                next_position = int(row["max_position"] or 0) + 1 if row else 1
                now = self._utcnow()
                for prompt_text in prompts:
                    clean = prompt_text.strip()
                    if not clean:
                        continue
                    prompt_id = str(uuid4())
                    cur.execute(
                        "INSERT INTO visibility_prompts (id, user_id, project_id, prompt_list_id, prompt_text, position, created_at, updated_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                        (prompt_id, user_id, project_id, prompt_list_id, clean, next_position, now, now),
                    )
                    created_ids.append(prompt_id)
                    next_position += 1
            conn.commit()
        if not created_ids:
            return []
        with self._connect() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute("SELECT * FROM visibility_prompts WHERE id = ANY(%s) ORDER BY position ASC, created_at ASC", (created_ids,))
                rows = cur.fetchall()
        return [self._row_to_visibility_prompt(row) for row in rows]

    def get_visibility_prompt_list(self, user_id: str, prompt_list_id: str, project_id: Optional[str] = None) -> Optional[VisibilityPromptListRecord]:
        query = "SELECT * FROM visibility_prompt_lists WHERE id = %s AND user_id = %s"
        params: List[Any] = [prompt_list_id, user_id]
        if project_id:
            query += " AND project_id = %s"
            params.append(project_id)
        with self._connect() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(query, params)
                row = cur.fetchone()
                if not row:
                    return None
                cur.execute("SELECT * FROM visibility_prompts WHERE prompt_list_id = %s ORDER BY position ASC, created_at ASC", (prompt_list_id,))
                prompt_rows = cur.fetchall()
        prompt_list = self._row_to_visibility_prompt_list(row)
        prompt_list.prompts = [self._row_to_visibility_prompt(item) for item in prompt_rows]
        return prompt_list

    def get_visibility_prompt_list_context(self, prompt_list_id: str, project_id: Optional[str] = None) -> Optional[dict[str, Any]]:
        query = """
            SELECT pl.*, st.topic_id, st.name AS subtopic_name, t.name AS topic_name
            FROM visibility_prompt_lists pl
            JOIN visibility_subtopics st ON st.id = pl.subtopic_id
            JOIN visibility_topics t ON t.id = st.topic_id
            WHERE pl.id = %s
        """
        params: List[Any] = [prompt_list_id]
        if project_id:
            query += " AND pl.project_id = %s"
            params.append(project_id)
        with self._connect() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(query, params)
                row = cur.fetchone()
        if not row:
            return None
        return {
            "id": str(row["id"]),
            "user_id": str(row["user_id"]),
            "project_id": str(row["project_id"]),
            "subtopic_id": str(row["subtopic_id"]),
            "topic_id": str(row["topic_id"]),
            "name": str(row["name"]),
            "subtopic_name": str(row["subtopic_name"]),
            "topic_name": str(row["topic_name"]),
            "schedule_frequency": str(row["schedule_frequency"] or "disabled"),
            "last_run_at": self._parse_dt(row["last_run_at"]) if row["last_run_at"] else None,
            "next_run_at": self._parse_dt(row["next_run_at"]) if row["next_run_at"] else None,
        }

    def list_visibility_topics(self, user_id: str, project_id: str) -> List[VisibilityTopicRecord]:
        with self._connect() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute("SELECT * FROM visibility_topics WHERE user_id = %s AND project_id = %s ORDER BY updated_at DESC, created_at DESC", (user_id, project_id))
                topic_rows = cur.fetchall()
                cur.execute("SELECT * FROM visibility_subtopics WHERE user_id = %s AND project_id = %s ORDER BY updated_at DESC, created_at DESC", (user_id, project_id))
                subtopic_rows = cur.fetchall()
                cur.execute("SELECT * FROM visibility_prompt_lists WHERE user_id = %s AND project_id = %s ORDER BY updated_at DESC, created_at DESC", (user_id, project_id))
                list_rows = cur.fetchall()
                cur.execute("SELECT * FROM visibility_prompts WHERE user_id = %s AND project_id = %s ORDER BY position ASC, created_at ASC", (user_id, project_id))
                prompt_rows = cur.fetchall()
        topics = {row["id"]: self._row_to_visibility_topic(row) for row in topic_rows}
        subtopics = {row["id"]: self._row_to_visibility_subtopic(row) for row in subtopic_rows}
        prompt_lists = {row["id"]: self._row_to_visibility_prompt_list(row) for row in list_rows}
        for prompt_row in prompt_rows:
            prompt = self._row_to_visibility_prompt(prompt_row)
            if prompt.prompt_list_id in prompt_lists:
                prompt_lists[prompt.prompt_list_id].prompts.append(prompt)
        for prompt_list in prompt_lists.values():
            if prompt_list.subtopic_id in subtopics:
                subtopics[prompt_list.subtopic_id].prompt_lists.append(prompt_list)
        for subtopic in subtopics.values():
            if subtopic.topic_id in topics:
                topics[subtopic.topic_id].subtopics.append(subtopic)
        return list(topics.values())

    def create_visibility_job(self, user_id: str, *, project_id: str, topic_id: str, subtopic_id: str, prompt_list_id: str, provider: str, model: str, surface: str, run_source: str, total_prompts: int) -> VisibilityJobRecord:
        job_id = str(uuid4())
        now = self._utcnow()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO visibility_jobs (id, user_id, project_id, topic_id, subtopic_id, prompt_list_id, provider, model, surface, run_source, status, stage, progress_percent, total_prompts, completed_prompts, error, created_at, updated_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (job_id, user_id, project_id, topic_id, subtopic_id, prompt_list_id, provider, model, surface, run_source, "queued", "queued", 0, total_prompts, 0, None, now, now),
                )
            conn.commit()
        return self.get_visibility_job(user_id, job_id)  # type: ignore[return-value]

    def get_visibility_job(self, user_id: str, job_id: str) -> Optional[VisibilityJobRecord]:
        with self._connect() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute("SELECT * FROM visibility_jobs WHERE id = %s AND user_id = %s", (job_id, user_id))
                row = cur.fetchone()
        return self._row_to_visibility_job(row) if row else None

    def get_visibility_job_by_id(self, job_id: str) -> Optional[VisibilityJobRecord]:
        with self._connect() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute("SELECT * FROM visibility_jobs WHERE id = %s", (job_id,))
                row = cur.fetchone()
        return self._row_to_visibility_job(row) if row else None

    def list_visibility_jobs(self, user_id: str, project_id: str, *, limit: int = 20, start_date: Optional[datetime] = None, end_date: Optional[datetime] = None) -> List[VisibilityJobRecord]:
        query = "SELECT * FROM visibility_jobs WHERE user_id = %s AND project_id = %s"
        params: List[Any] = [user_id, project_id]
        if start_date:
            query += " AND created_at >= %s"
            params.append(start_date)
        if end_date:
            query += " AND created_at <= %s"
            params.append(end_date)
        query += " ORDER BY created_at DESC LIMIT %s"
        params.append(limit)
        with self._connect() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(query, params)
                rows = cur.fetchall()
        return [self._row_to_visibility_job(row) for row in rows]

    def update_visibility_job(self, job_id: str, **kwargs: Any) -> Optional[VisibilityJobRecord]:
        allowed = {"status", "stage", "progress_percent", "completed_prompts", "total_prompts", "error"}
        updates = dict((k, v) for (k, v) in kwargs.items() if k in allowed)
        if not updates:
            return self.get_visibility_job_by_id(job_id)
        cols = []
        values: List[Any] = []
        for key, value in updates.items():
            cols.append(f"{key} = %s")
            values.append(value)
        cols.append("updated_at = %s")
        values.append(self._utcnow())
        values.append(job_id)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(f"UPDATE visibility_jobs SET {', '.join(cols)} WHERE id = %s", values)
            conn.commit()
        return self.get_visibility_job_by_id(job_id)

    def delete_visibility_prompt_runs_for_job(self, user_id: str, job_id: str) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM visibility_prompt_runs WHERE user_id = %s AND job_id = %s",
                    (user_id, job_id),
                )
            conn.commit()

    def create_visibility_prompt_run(self, user_id: str, *, project_id: str, topic_id: str, subtopic_id: str, prompt_list_id: str, prompt_id: str, prompt_text: str, provider: str, model: str, surface: str, run_source: str, status: str, response_text: str = "", brands: Optional[List[str]] = None, cited_domains: Optional[List[str]] = None, cited_urls: Optional[List[str]] = None, error: Optional[str] = None, job_id: Optional[str] = None) -> VisibilityPromptRunRecord:
        run_id = str(uuid4())
        now = self._utcnow()
        if job_id:
            with self._connect() as conn:
                with conn.cursor(row_factory=dict_row) as cur:
                    cur.execute("SELECT * FROM visibility_prompt_runs WHERE user_id = %s AND job_id = %s AND prompt_id = %s ORDER BY created_at DESC LIMIT 1", (user_id, job_id, prompt_id))
                    existing = cur.fetchone()
            if existing:
                return self._row_to_visibility_prompt_run(existing)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO visibility_prompt_runs (id, user_id, project_id, job_id, topic_id, subtopic_id, prompt_list_id, prompt_id, prompt_text, provider, model, surface, run_source, status, response_text, brands_json, cited_domains_json, cited_urls_json, error, created_at, updated_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (run_id, user_id, project_id, job_id, topic_id, subtopic_id, prompt_list_id, prompt_id, prompt_text, provider, model, surface, run_source, status, response_text, json.dumps(brands or []), json.dumps(cited_domains or []), json.dumps(cited_urls or []), error, now, now),
                )
            conn.commit()
        with self._connect() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute("SELECT * FROM visibility_prompt_runs WHERE id = %s", (run_id,))
                row = cur.fetchone()
        return self._row_to_visibility_prompt_run(row)

    def list_visibility_prompt_runs(self, user_id: str, *, project_id: str, limit: int = 200, topic_id: Optional[str] = None, subtopic_id: Optional[str] = None, prompt_list_id: Optional[str] = None, prompt_id: Optional[str] = None, job_id: Optional[str] = None, start_date: Optional[datetime] = None, end_date: Optional[datetime] = None) -> List[VisibilityPromptRunRecord]:
        query = "SELECT * FROM visibility_prompt_runs WHERE user_id = %s AND project_id = %s"
        params: List[Any] = [user_id, project_id]
        if topic_id:
            query += " AND topic_id = %s"
            params.append(topic_id)
        if subtopic_id:
            query += " AND subtopic_id = %s"
            params.append(subtopic_id)
        if prompt_list_id:
            query += " AND prompt_list_id = %s"
            params.append(prompt_list_id)
        if prompt_id:
            query += " AND prompt_id = %s"
            params.append(prompt_id)
        if job_id:
            query += " AND job_id = %s"
            params.append(job_id)
        if start_date:
            query += " AND created_at >= %s"
            params.append(start_date)
        if end_date:
            query += " AND created_at <= %s"
            params.append(end_date)
        query += " ORDER BY created_at DESC LIMIT %s"
        params.append(limit)
        with self._connect() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(query, params)
                rows = cur.fetchall()
        return [self._row_to_visibility_prompt_run(row) for row in rows]

    def list_due_visibility_prompt_lists(self, as_of: Optional[datetime] = None, limit: int = 25) -> List[dict[str, Any]]:
        with self._connect() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute("SELECT pl.*, st.topic_id FROM visibility_prompt_lists pl JOIN visibility_subtopics st ON st.id = pl.subtopic_id WHERE pl.schedule_frequency != 'disabled' AND pl.next_run_at IS NOT NULL AND pl.next_run_at <= %s ORDER BY pl.next_run_at ASC LIMIT %s", (as_of or self._utcnow(), limit))
                rows = cur.fetchall()
        return [{"id": str(row["id"]), "user_id": str(row["user_id"]), "project_id": str(row["project_id"]), "subtopic_id": str(row["subtopic_id"]), "topic_id": str(row["topic_id"]), "schedule_frequency": str(row["schedule_frequency"] or "disabled")} for row in rows]

    def mark_visibility_prompt_list_run(self, prompt_list_id: str, *, frequency: str, run_at: Optional[datetime] = None) -> Optional[VisibilityPromptListRecord]:
        executed_at = run_at or self._utcnow()
        normalized_frequency = self._normalize_schedule_frequency(frequency)
        next_run_at = self._next_scheduled_run(normalized_frequency, executed_at)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE visibility_prompt_lists SET last_run_at = %s, next_run_at = %s, updated_at = %s WHERE id = %s", (executed_at, next_run_at, executed_at, prompt_list_id))
            conn.commit()
        with self._connect() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute("SELECT * FROM visibility_prompt_lists WHERE id = %s", (prompt_list_id,))
                row = cur.fetchone()
        return self._row_to_visibility_prompt_list(row) if row else None

    def delete_visibility_competitor(self, user_id: str, competitor_id: str) -> bool:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM visibility_competitors WHERE id = %s AND user_id = %s", (competitor_id, user_id))
                deleted = cur.rowcount > 0
            conn.commit()
        return deleted

    def delete_visibility_prompt(self, user_id: str, prompt_id: str) -> bool:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM visibility_prompt_runs WHERE prompt_id = %s AND user_id = %s", (prompt_id, user_id))
                cur.execute("DELETE FROM visibility_prompts WHERE id = %s AND user_id = %s", (prompt_id, user_id))
                deleted = cur.rowcount > 0
            conn.commit()
        return deleted

    def delete_visibility_prompt_list(self, user_id: str, prompt_list_id: str) -> bool:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM visibility_prompt_runs WHERE prompt_list_id = %s AND user_id = %s", (prompt_list_id, user_id))
                cur.execute("DELETE FROM visibility_jobs WHERE prompt_list_id = %s AND user_id = %s", (prompt_list_id, user_id))
                cur.execute("DELETE FROM visibility_prompts WHERE prompt_list_id = %s AND user_id = %s", (prompt_list_id, user_id))
                cur.execute("DELETE FROM visibility_prompt_lists WHERE id = %s AND user_id = %s", (prompt_list_id, user_id))
                deleted = cur.rowcount > 0
            conn.commit()
        return deleted

    def delete_visibility_subtopic(self, user_id: str, subtopic_id: str) -> bool:
        with self._connect() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute("SELECT id FROM visibility_prompt_lists WHERE subtopic_id = %s AND user_id = %s", (subtopic_id, user_id))
                rows = cur.fetchall()
        deleted_any = False
        for row in rows:
            deleted_any = self.delete_visibility_prompt_list(user_id, str(row["id"])) or deleted_any
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM visibility_prompt_runs WHERE subtopic_id = %s AND user_id = %s", (subtopic_id, user_id))
                cur.execute("DELETE FROM visibility_jobs WHERE subtopic_id = %s AND user_id = %s", (subtopic_id, user_id))
                cur.execute("DELETE FROM visibility_subtopics WHERE id = %s AND user_id = %s", (subtopic_id, user_id))
                deleted = cur.rowcount > 0
            conn.commit()
        return deleted or deleted_any

    def delete_visibility_topic(self, user_id: str, topic_id: str) -> bool:
        with self._connect() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute("SELECT id FROM visibility_subtopics WHERE topic_id = %s AND user_id = %s", (topic_id, user_id))
                rows = cur.fetchall()
        deleted_any = False
        for row in rows:
            deleted_any = self.delete_visibility_subtopic(user_id, str(row["id"])) or deleted_any
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM visibility_prompt_runs WHERE topic_id = %s AND user_id = %s", (topic_id, user_id))
                cur.execute("DELETE FROM visibility_jobs WHERE topic_id = %s AND user_id = %s", (topic_id, user_id))
                cur.execute("DELETE FROM visibility_topics WHERE id = %s AND user_id = %s", (topic_id, user_id))
                deleted = cur.rowcount > 0
            conn.commit()
        return deleted or deleted_any

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
        initial_artifacts = BriefArtifacts(
            requested_target_location=payload.target_location,
            requested_seed_urls=payload.seed_urls,
            requested_ai_citations_text=payload.ai_citations_text,
            requested_ai_overview_text=payload.ai_overview_text,
        )
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO briefs (id, user_id, query, status, stage, progress_percent, error, artifacts_json, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (record_id, user_id, payload.query, "queued", "queued", 0, None, initial_artifacts.model_dump_json(), now, now),
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
        initial_artifacts = artifacts or ArticleArtifacts(
            requested_target_location=payload.target_location,
            requested_seed_urls=payload.seed_urls,
            requested_ai_citations_text=payload.ai_citations_text,
            requested_ai_overview_text=payload.ai_overview_text,
        )
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

    def delete_topics(self, user_id: str, topics: List[str]) -> TopicDeleteResponse:
        normalized = self._normalize_topics(topics)
        if not normalized:
            return TopicDeleteResponse()

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM briefs WHERE user_id = %s AND query = ANY(%s)",
                    (user_id, normalized),
                )
                brief_count = int(cur.fetchone()[0])
                cur.execute(
                    "SELECT COUNT(*) FROM articles WHERE user_id = %s AND query = ANY(%s)",
                    (user_id, normalized),
                )
                article_count = int(cur.fetchone()[0])
                cur.execute(
                    "DELETE FROM briefs WHERE user_id = %s AND query = ANY(%s)",
                    (user_id, normalized),
                )
                cur.execute(
                    "DELETE FROM articles WHERE user_id = %s AND query = ANY(%s)",
                    (user_id, normalized),
                )
            conn.commit()

        return TopicDeleteResponse(
            deleted_topics=normalized,
            deleted_briefs=brief_count,
            deleted_articles=article_count,
        )

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

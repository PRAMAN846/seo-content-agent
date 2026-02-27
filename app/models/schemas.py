from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


RunStatus = Literal["queued", "running", "completed", "failed"]


class RunCreateRequest(BaseModel):
    query: str = Field(min_length=3)
    seed_urls: list[str] = Field(default_factory=list)
    ai_citations_text: str = ""
    ai_overview_text: str = ""


class UrlContent(BaseModel):
    url: str
    title: str
    text: str


class ArticleSummary(BaseModel):
    url: str
    summary: str


class RunArtifacts(BaseModel):
    sources: list[str] = Field(default_factory=list)
    extracted_articles: list[UrlContent] = Field(default_factory=list)
    summaries: list[ArticleSummary] = Field(default_factory=list)
    seo_analysis: str = ""
    article_markdown: str = ""
    export_link: Optional[str] = None


class RunRecord(BaseModel):
    id: str
    user_id: str
    query: str
    status: RunStatus
    stage: str = "queued"
    progress_percent: int = Field(default=0, ge=0, le=100)
    created_at: datetime
    updated_at: datetime
    error: Optional[str] = None
    artifacts: RunArtifacts = Field(default_factory=RunArtifacts)


class UserPublic(BaseModel):
    id: str
    email: str
    created_at: datetime


class RegisterRequest(BaseModel):
    email: str = Field(min_length=5)
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    email: str = Field(min_length=5)
    password: str = Field(min_length=8, max_length=128)


class QueuedRun(BaseModel):
    run_id: str
    user_id: str
    query: str
    seed_urls: list[str] = Field(default_factory=list)
    ai_citations_text: str = ""
    ai_overview_text: str = ""

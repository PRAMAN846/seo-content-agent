from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


TaskStatus = Literal["queued", "running", "completed", "failed"]
ArticleMode = Literal["from_brief", "from_custom_brief", "quick_draft"]


class UrlContent(BaseModel):
    url: str
    title: str
    text: str


class ArticleSummary(BaseModel):
    url: str
    summary: str


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


class BriefCreateRequest(BaseModel):
    query: str = Field(min_length=3)
    seed_urls: list[str] = Field(default_factory=list)
    ai_citations_text: str = ""
    ai_overview_text: str = ""


class BriefUpdateRequest(BaseModel):
    brief_markdown: str = Field(min_length=20)


class BriefArtifacts(BaseModel):
    sources: list[str] = Field(default_factory=list)
    extracted_articles: list[UrlContent] = Field(default_factory=list)
    summaries: list[ArticleSummary] = Field(default_factory=list)
    seo_analysis: str = ""
    brief_markdown: str = ""


class BriefRecord(BaseModel):
    id: str
    user_id: str
    query: str
    status: TaskStatus
    stage: str = "queued"
    progress_percent: int = Field(default=0, ge=0, le=100)
    created_at: datetime
    updated_at: datetime
    error: Optional[str] = None
    artifacts: BriefArtifacts = Field(default_factory=BriefArtifacts)


class ArticleCreateRequest(BaseModel):
    mode: ArticleMode
    query: str = ""
    brief_id: Optional[str] = None
    custom_brief_markdown: str = ""
    seed_urls: list[str] = Field(default_factory=list)
    ai_citations_text: str = ""
    ai_overview_text: str = ""


class ArticleArtifacts(BaseModel):
    source_brief_id: Optional[str] = None
    source_brief_markdown: str = ""
    article_markdown: str = ""
    export_link: Optional[str] = None


class ArticleRecord(BaseModel):
    id: str
    user_id: str
    mode: ArticleMode
    query: str
    status: TaskStatus
    stage: str = "queued"
    progress_percent: int = Field(default=0, ge=0, le=100)
    created_at: datetime
    updated_at: datetime
    error: Optional[str] = None
    artifacts: ArticleArtifacts = Field(default_factory=ArticleArtifacts)


class RunCreateRequest(BaseModel):
    query: str = Field(min_length=3)
    seed_urls: list[str] = Field(default_factory=list)
    ai_citations_text: str = ""
    ai_overview_text: str = ""


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
    status: TaskStatus
    stage: str = "queued"
    progress_percent: int = Field(default=0, ge=0, le=100)
    created_at: datetime
    updated_at: datetime
    error: Optional[str] = None
    artifacts: RunArtifacts = Field(default_factory=RunArtifacts)


class QueuedRun(BaseModel):
    run_id: str
    user_id: str
    query: str
    seed_urls: list[str] = Field(default_factory=list)
    ai_citations_text: str = ""
    ai_overview_text: str = ""

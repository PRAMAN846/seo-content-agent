from __future__ import annotations

from datetime import datetime
from typing import Literal
from typing import Optional

from pydantic import BaseModel, Field


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
    query: str
    status: Literal["queued", "running", "completed", "failed"]
    created_at: datetime
    updated_at: datetime
    error: Optional[str] = None
    artifacts: RunArtifacts = Field(default_factory=RunArtifacts)

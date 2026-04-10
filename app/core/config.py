from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    openai_api_key: Optional[str] = os.getenv("OPENAI_API_KEY")
    small_model: str = os.getenv("SMALL_MODEL", "gpt-5-mini")
    analyst_model: str = os.getenv("ANALYST_MODEL", "gpt-5-mini")
    writer_model: str = os.getenv("WRITER_MODEL", "gpt-5.4")
    orchestrator_model: str = os.getenv("ORCHESTRATOR_MODEL", os.getenv("SMALL_MODEL", "gpt-5-mini"))
    orchestrator_reasoning_effort: str = os.getenv("ORCHESTRATOR_REASONING_EFFORT", "medium")
    writer_reasoning_effort: str = os.getenv("WRITER_REASONING_EFFORT", "medium")
    image_model: str = os.getenv("IMAGE_MODEL", "gpt-image-1.5")
    article_image_count: int = int(os.getenv("ARTICLE_IMAGE_COUNT", "3"))
    article_image_size: str = os.getenv("ARTICLE_IMAGE_SIZE", "1536x1024")
    article_image_quality: str = os.getenv("ARTICLE_IMAGE_QUALITY", "high")
    max_urls: int = int(os.getenv("MAX_URLS", "3"))
    app_host: str = os.getenv("APP_HOST", "0.0.0.0")
    app_port: int = int(os.getenv("APP_PORT", "8000"))
    app_brand_name: str = os.getenv("APP_BRAND_NAME", "Content Workspace")
    app_product_name: str = os.getenv("APP_PRODUCT_NAME", "Content Workspace")
    app_logo_path: str = os.getenv("APP_LOGO_PATH", "")
    app_wordmark_text: str = os.getenv("APP_WORDMARK_TEXT", "CONTENT")
    app_nav_eyebrow: str = os.getenv("APP_NAV_EYEBROW", "Content Operations")
    session_ttl_days: int = int(os.getenv("SESSION_TTL_DAYS", "30"))
    cookie_secure: bool = os.getenv("COOKIE_SECURE", "true").lower() == "true"
    enable_scheduler: bool = os.getenv("ENABLE_SCHEDULER", "true").lower() == "true"
    visibility_only: bool = os.getenv("VISIBILITY_ONLY", "false").lower() == "true"
    serper_api_key: Optional[str] = os.getenv("SERPER_API_KEY")
    content_agent_search_enabled: bool = os.getenv("CONTENT_AGENT_SEARCH_ENABLED", "true").lower() == "true"
    content_agent_search_result_count: int = int(os.getenv("CONTENT_AGENT_SEARCH_RESULT_COUNT", "3"))
    content_agent_browser_fallback_enabled: bool = os.getenv("CONTENT_AGENT_BROWSER_FALLBACK_ENABLED", "true").lower() == "true"
    content_agent_browser_timeout_ms: int = int(os.getenv("CONTENT_AGENT_BROWSER_TIMEOUT_MS", "18000"))


settings = Settings()

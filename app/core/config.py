from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    openai_api_key: Optional[str] = os.getenv("OPENAI_API_KEY")
    small_model: str = os.getenv("SMALL_MODEL", "gpt-4.1-mini")
    analyst_model: str = os.getenv("ANALYST_MODEL", "gpt-4.1-mini")
    writer_model: str = os.getenv("WRITER_MODEL", "gpt-4.1")
    orchestrator_model: str = os.getenv("ORCHESTRATOR_MODEL", os.getenv("SMALL_MODEL", "gpt-5-mini"))
    max_urls: int = int(os.getenv("MAX_URLS", "3"))
    app_host: str = os.getenv("APP_HOST", "0.0.0.0")
    app_port: int = int(os.getenv("APP_PORT", "8000"))
    app_brand_name: str = os.getenv("APP_BRAND_NAME", "Xpaan Digital")
    app_product_name: str = os.getenv("APP_PRODUCT_NAME", "Xpaan Content Agent")
    app_logo_path: str = os.getenv("APP_LOGO_PATH", "/frontend/assets/xpaan-logo.svg")
    app_wordmark_text: str = os.getenv("APP_WORDMARK_TEXT", "")
    app_nav_eyebrow: str = os.getenv("APP_NAV_EYEBROW", "Content Writing Agents")
    session_ttl_days: int = int(os.getenv("SESSION_TTL_DAYS", "30"))
    cookie_secure: bool = os.getenv("COOKIE_SECURE", "true").lower() == "true"
    enable_scheduler: bool = os.getenv("ENABLE_SCHEDULER", "true").lower() == "true"
    visibility_only: bool = os.getenv("VISIBILITY_ONLY", "false").lower() == "true"


settings = Settings()

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
    max_urls: int = int(os.getenv("MAX_URLS", "3"))
    app_host: str = os.getenv("APP_HOST", "0.0.0.0")
    app_port: int = int(os.getenv("APP_PORT", "8000"))
    session_ttl_days: int = int(os.getenv("SESSION_TTL_DAYS", "30"))
    cookie_secure: bool = os.getenv("COOKIE_SECURE", "true").lower() == "true"


settings = Settings()

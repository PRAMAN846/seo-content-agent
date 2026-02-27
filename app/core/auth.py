from __future__ import annotations

from typing import Optional

from fastapi import HTTPException, Request

from app.models.schemas import UserPublic
from app.models.store import run_store

SESSION_COOKIE = "session_token"


def get_current_user(request: Request) -> UserPublic:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    user = run_store.get_user_by_session(token)
    if not user:
        raise HTTPException(status_code=401, detail="Session expired or invalid")

    return user


def get_current_user_optional(request: Request) -> Optional[UserPublic]:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    return run_store.get_user_by_session(token)

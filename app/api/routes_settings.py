from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.core.auth import get_current_user
from app.models.schemas import UserPublic, UserSettings, UserSettingsUpdateRequest
from app.models.store import run_store

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("", response_model=UserSettings)
def get_settings(current_user: UserPublic = Depends(get_current_user)) -> UserSettings:
    settings = run_store.get_user_settings(current_user.id)
    if not settings:
        raise HTTPException(status_code=404, detail="Settings not found")
    return settings


@router.put("", response_model=UserSettings)
def update_settings(
    payload: UserSettingsUpdateRequest,
    current_user: UserPublic = Depends(get_current_user),
) -> UserSettings:
    updated = run_store.update_user_settings(
        current_user.id,
        name=payload.name.strip(),
        brand_name=payload.brand_name.strip(),
        brand_url=payload.brand_url.strip(),
        brief_prompt_override=payload.brief_prompt_override.strip(),
        writer_prompt_override=payload.writer_prompt_override.strip(),
    )
    if not updated:
        raise HTTPException(status_code=500, detail="Unable to update settings")
    return updated

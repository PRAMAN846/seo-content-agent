from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.auth import get_current_user
from app.models.schemas import PersonalityPreset, UserPublic
from app.services.personalities import list_personality_presets

router = APIRouter(prefix="/api/personalities", tags=["personalities"])


@router.get("", response_model=dict[str, list[PersonalityPreset]])
def list_personalities(current_user: UserPublic = Depends(get_current_user)) -> dict[str, list[PersonalityPreset]]:
    del current_user
    return {
        "workspace": list_personality_presets("workspace"),
        "brief": list_personality_presets("brief"),
        "writer": list_personality_presets("writer"),
        "reviewer": list_personality_presets("reviewer"),
    }

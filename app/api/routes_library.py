from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.core.auth import get_current_user
from app.models.schemas import TopicDeleteRequest, TopicDeleteResponse, UserPublic
from app.models.store import run_store

router = APIRouter(prefix="/api/library", tags=["library"])


@router.post("/topics/delete", response_model=TopicDeleteResponse)
def delete_topics(
    payload: TopicDeleteRequest,
    current_user: UserPublic = Depends(get_current_user),
) -> TopicDeleteResponse:
    topics = [topic.strip() for topic in payload.topics if topic.strip()]
    if not topics:
        raise HTTPException(status_code=400, detail="At least one topic is required")
    return run_store.delete_topics(user_id=current_user.id, topics=topics)

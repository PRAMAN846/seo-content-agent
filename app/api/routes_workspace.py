from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.auth import get_current_user
from app.models.schemas import UserPublic, WorkspaceMessageRequest, WorkspaceMessageResponse
from app.services.workspace_orchestrator import execute_workspace_action, plan_workspace_response

router = APIRouter(prefix="/api/workspace", tags=["workspace"])


@router.post("/message", response_model=WorkspaceMessageResponse)
def workspace_message(
    payload: WorkspaceMessageRequest,
    current_user: UserPublic = Depends(get_current_user),
) -> WorkspaceMessageResponse:
    response = plan_workspace_response(
        messages=payload.messages,
        selected_brief_id=payload.selected_brief_id,
        current_user=current_user,
    )
    if payload.auto_execute:
        return execute_workspace_action(response=response, current_user=current_user)
    return response

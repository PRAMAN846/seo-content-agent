from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.core.auth import get_current_user
from app.models.schemas import (
    ContentSkillOverrideCreateRequest,
    ContentSkillOverrideRecord,
    ContentSkillOverridesResponse,
    ContentStudioChatArchiveResponse,
    ContentStudioCatalogResponse,
    ContentStudioChatCreateRequest,
    ContentStudioChatRecord,
    ContentStudioChatSendRequest,
    ContentStudioChatSendResponse,
    ContentStudioChatsResponse,
    ContentStudioChatSummary,
    ContentStudioChatUpdateRequest,
    ContentStudioMessageRequest,
    ContentStudioMessageResponse,
    UserPublic,
)
from app.models.store import run_store
from app.services.billing import record_content_studio_billing, usage_scope
from app.services.content_studio import (
    build_content_studio_catalog,
    generate_content_studio_reply,
    send_content_studio_chat_message,
)

router = APIRouter(prefix="/api/content-studio", tags=["content-studio"])


@router.get("/catalog", response_model=ContentStudioCatalogResponse)
def content_studio_catalog(current_user: UserPublic = Depends(get_current_user)) -> ContentStudioCatalogResponse:
    _ = current_user
    return build_content_studio_catalog()


@router.get("/projects/{project_id}/chats", response_model=ContentStudioChatsResponse)
def list_content_studio_chats(
    project_id: str,
    current_user: UserPublic = Depends(get_current_user),
) -> ContentStudioChatsResponse:
    return ContentStudioChatsResponse(chats=run_store.list_content_studio_chats(current_user.id, project_id))


@router.get("/projects/{project_id}/skill-overrides", response_model=ContentSkillOverridesResponse)
def list_content_skill_overrides(
    project_id: str,
    current_user: UserPublic = Depends(get_current_user),
) -> ContentSkillOverridesResponse:
    return ContentSkillOverridesResponse(
        overrides=run_store.get_effective_content_skill_overrides(current_user.id, project_id=project_id)
    )


@router.get("/skill-overrides/workspace", response_model=ContentSkillOverridesResponse)
def list_workspace_content_skill_overrides(
    current_user: UserPublic = Depends(get_current_user),
) -> ContentSkillOverridesResponse:
    return ContentSkillOverridesResponse(
        overrides=run_store.list_content_skill_overrides(current_user.id, scope="workspace")
    )


@router.post("/projects/{project_id}/skill-overrides", response_model=ContentSkillOverrideRecord)
def create_content_skill_override(
    project_id: str,
    payload: ContentSkillOverrideCreateRequest,
    current_user: UserPublic = Depends(get_current_user),
) -> ContentSkillOverrideRecord:
    override = run_store.create_content_skill_override(
        current_user.id,
        project_id=project_id,
        skill_id=payload.skill_id.strip(),
        instruction=payload.instruction.strip(),
        scope=payload.scope,
    )
    return override


@router.post("/chats", response_model=ContentStudioChatRecord)
def create_content_studio_chat(
    payload: ContentStudioChatCreateRequest,
    current_user: UserPublic = Depends(get_current_user),
) -> ContentStudioChatRecord:
    return run_store.create_content_studio_chat(
        current_user.id,
        project_id=payload.project_id,
        title=payload.title,
    )


@router.get("/chats/{chat_id}", response_model=ContentStudioChatRecord)
def get_content_studio_chat(
    chat_id: str,
    current_user: UserPublic = Depends(get_current_user),
) -> ContentStudioChatRecord:
    chat = run_store.get_content_studio_chat(current_user.id, chat_id)
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")
    return chat


@router.patch("/chats/{chat_id}", response_model=ContentStudioChatSummary)
def update_content_studio_chat(
    chat_id: str,
    payload: ContentStudioChatUpdateRequest,
    current_user: UserPublic = Depends(get_current_user),
) -> ContentStudioChatSummary:
    chat = run_store.rename_content_studio_chat(
        current_user.id,
        chat_id,
        title=payload.title.strip(),
    )
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")
    return chat


@router.post("/chats/{chat_id}/archive", response_model=ContentStudioChatArchiveResponse)
def archive_content_studio_chat(
    chat_id: str,
    current_user: UserPublic = Depends(get_current_user),
) -> ContentStudioChatArchiveResponse:
    archived = run_store.archive_content_studio_chat(current_user.id, chat_id)
    if not archived:
        raise HTTPException(status_code=404, detail="Chat not found")
    return ContentStudioChatArchiveResponse(chat_id=chat_id, archived=True)


@router.post("/chats/{chat_id}/messages", response_model=ContentStudioChatSendResponse)
def send_content_studio_chat(
    chat_id: str,
    payload: ContentStudioChatSendRequest,
    current_user: UserPublic = Depends(get_current_user),
) -> ContentStudioChatSendResponse:
    chat = run_store.get_content_studio_chat(current_user.id, chat_id)
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")
    try:
        return send_content_studio_chat_message(
            current_user=current_user,
            chat=chat,
            content=payload.content.strip(),
            selected_skill_ids=payload.selected_skill_ids,
            workflow_id=payload.workflow_id,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/message", response_model=ContentStudioMessageResponse)
def content_studio_message(
    payload: ContentStudioMessageRequest,
    current_user: UserPublic = Depends(get_current_user),
) -> ContentStudioMessageResponse:
    with usage_scope(
        user_id=current_user.id,
        workspace_id=current_user.id,
        project_id=payload.project_id,
        feature="content_studio",
        reference_type="content_studio_message",
        metadata={"selected_skill_ids": payload.selected_skill_ids, "workflow_id": payload.workflow_id},
    ) as billing_context:
        response = generate_content_studio_reply(
            current_user=current_user,
            project_id=payload.project_id,
            messages=payload.messages,
            selected_skill_ids=payload.selected_skill_ids,
            workflow_id=payload.workflow_id,
        )
    if int((billing_context or {}).get("logged_usage_count") or 0) > 0:
        record_content_studio_billing(
            user_id=current_user.id,
            project_id=payload.project_id,
            chat_id=None,
            active_skill_ids=response.active_skill_ids,
            workflow_id=response.workflow_id,
            generated_images=0,
        )
    return response

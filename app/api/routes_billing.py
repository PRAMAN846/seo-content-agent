from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.auth import get_current_user
from app.models.schemas import UserPublic, WorkspaceBillingSummaryResponse
from app.services.billing import build_workspace_billing_summary


router = APIRouter(prefix="/api/billing", tags=["billing"])


@router.get("/summary", response_model=WorkspaceBillingSummaryResponse)
def get_workspace_billing_summary(
    current_user: UserPublic = Depends(get_current_user),
) -> WorkspaceBillingSummaryResponse:
    return build_workspace_billing_summary(user_id=current_user.id)

from __future__ import annotations

from collections import defaultdict
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator, Optional

from app.models.schemas import BillingBreakdownItem, WorkspaceBillingSummaryResponse
from app.models.store import run_store


PRICING_VERSION = "v1"
DEFAULT_INCLUDED_CREDITS = 0

VISIBILITY_PROVIDER_CHECK_CREDITS = 4

CONTENT_STUDIO_CREDITS = {
    "studio_brief_generation": 8,
    "studio_article_draft": 18,
    "studio_workflow_run": 30,
    "studio_rewrite": 4,
    "studio_ai_reply": 3,
    "studio_image_generation": 8,
}

CONTENT_AGENT_CREDITS = {
    "agent_run_base": 5,
    "agent_planning_stage": 2,
    "agent_brief_stage": 8,
    "agent_draft_stage": 16,
    "agent_edit_stage": 6,
    "agent_internal_linking_stage": 4,
    "agent_publish_qa_stage": 4,
    "agent_image_stage": 6,
    "agent_image_generation": 8,
}

_USAGE_SCOPE: ContextVar[Optional[dict[str, Any]]] = ContextVar("billing_usage_scope", default=None)

_FEATURE_LABELS = {
    "visibility": "AI Visibility Tracker",
    "content_studio": "Content Studio",
    "content_agent": "Content Agent",
}

_PROVIDER_LABELS = {
    "chatgpt": "ChatGPT",
    "claude": "Claude",
    "gemini": "Gemini",
    "perplexity": "Perplexity",
    "openai": "OpenAI",
}


def resolve_workspace_id(user_id: str) -> str:
    return user_id


def current_billing_period(now: Optional[datetime] = None) -> tuple[datetime, datetime]:
    anchor = now or datetime.now(timezone.utc)
    start = anchor.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    next_month = (start + timedelta(days=32)).replace(day=1)
    return start, next_month


@contextmanager
def usage_scope(
    *,
    user_id: str,
    feature: str,
    project_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
    provider_surface: str = "",
    reference_type: Optional[str] = None,
    reference_id: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> Iterator[dict[str, Any]]:
    scope = {
        "workspace_id": workspace_id or resolve_workspace_id(user_id),
        "user_id": user_id,
        "project_id": project_id,
        "feature": feature,
        "provider_surface": provider_surface,
        "reference_type": reference_type,
        "reference_id": reference_id,
        "metadata": dict(metadata or {}),
        "logged_usage_count": 0,
    }
    token = _USAGE_SCOPE.set(scope)
    try:
        yield scope
    finally:
        _USAGE_SCOPE.reset(token)


def get_usage_scope() -> Optional[dict[str, Any]]:
    return _USAGE_SCOPE.get()


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _feature_label(key: str) -> str:
    return _FEATURE_LABELS.get(key, key.replace("_", " ").title())


def _provider_label(key: str) -> str:
    return _PROVIDER_LABELS.get(key, key.replace("_", " ").title())


def _input_cached_tokens(usage: Any) -> int:
    details = getattr(usage, "input_tokens_details", None)
    return _safe_int(getattr(details, "cached_tokens", 0))


def _output_reasoning_tokens(usage: Any) -> int:
    details = getattr(usage, "output_tokens_details", None)
    return _safe_int(getattr(details, "reasoning_tokens", 0))


def log_openai_response_usage(
    *,
    response: Any,
    model: str,
    operation: str,
    status: str = "completed",
    metadata: Optional[dict[str, Any]] = None,
) -> None:
    scope = get_usage_scope()
    if not scope:
        return
    usage = getattr(response, "usage", None)
    input_tokens = _safe_int(getattr(usage, "input_tokens", 0))
    output_tokens = _safe_int(getattr(usage, "output_tokens", 0))
    cached_tokens = _input_cached_tokens(usage)
    reasoning_tokens = _output_reasoning_tokens(usage)
    total_tokens = _safe_int(getattr(usage, "total_tokens", 0)) or (input_tokens + output_tokens)
    combined_metadata = dict(scope.get("metadata") or {})
    combined_metadata.update(metadata or {})
    combined_metadata["operation"] = operation
    run_store.create_provider_usage_event(
        user_id=scope["user_id"],
        workspace_id=scope["workspace_id"],
        project_id=scope.get("project_id"),
        feature=scope["feature"],
        provider="openai",
        provider_surface=scope.get("provider_surface") or "",
        provider_model=model,
        provider_request_id=getattr(response, "id", None),
        status=status,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_tokens=cached_tokens,
        reasoning_tokens=reasoning_tokens,
        total_tokens=total_tokens,
        provider_cost_usd=0.0,
        metadata_json=combined_metadata,
    )
    scope["logged_usage_count"] = int(scope.get("logged_usage_count") or 0) + 1


def create_customer_billing_event(
    *,
    user_id: str,
    feature: str,
    billing_unit_type: str,
    quantity: int,
    credits_charged: int,
    project_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
    reference_type: Optional[str] = None,
    reference_id: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> None:
    if quantity <= 0 or credits_charged <= 0:
        return
    run_store.create_customer_billing_event(
        user_id=user_id,
        workspace_id=workspace_id or resolve_workspace_id(user_id),
        project_id=project_id,
        feature=feature,
        billing_unit_type=billing_unit_type,
        quantity=quantity,
        credits_charged=credits_charged,
        pricing_version=PRICING_VERSION,
        reference_type=reference_type,
        reference_id=reference_id,
        metadata_json=metadata or {},
    )


def record_visibility_provider_check(
    *,
    user_id: str,
    project_id: str,
    prompt_id: str,
    prompt_list_id: str,
    provider_surface: str,
    quantity: int = 1,
) -> None:
    create_customer_billing_event(
        user_id=user_id,
        project_id=project_id,
        feature="visibility",
        billing_unit_type="visibility_provider_check",
        quantity=quantity,
        credits_charged=VISIBILITY_PROVIDER_CHECK_CREDITS * quantity,
        reference_type="visibility_prompt",
        reference_id=prompt_id,
        metadata={
            "prompt_list_id": prompt_list_id,
            "provider_surface": provider_surface,
        },
    )


def classify_content_studio_billing_unit(
    *,
    active_skill_ids: list[str],
    workflow_id: Optional[str],
) -> tuple[str, int]:
    skills = set(active_skill_ids or [])
    if workflow_id:
        return "studio_workflow_run", CONTENT_STUDIO_CREDITS["studio_workflow_run"]
    if "content_brief" in skills and "content_writer" not in skills:
        return "studio_brief_generation", CONTENT_STUDIO_CREDITS["studio_brief_generation"]
    if "content_writer" in skills:
        return "studio_article_draft", CONTENT_STUDIO_CREDITS["studio_article_draft"]
    if skills.intersection({"content_editor", "internal_linking", "publish_qa"}):
        return "studio_rewrite", CONTENT_STUDIO_CREDITS["studio_rewrite"]
    return "studio_ai_reply", CONTENT_STUDIO_CREDITS["studio_ai_reply"]


def record_content_studio_billing(
    *,
    user_id: str,
    project_id: str,
    chat_id: Optional[str],
    active_skill_ids: list[str],
    workflow_id: Optional[str],
    generated_images: int = 0,
) -> None:
    billing_unit, credits = classify_content_studio_billing_unit(
        active_skill_ids=active_skill_ids,
        workflow_id=workflow_id,
    )
    create_customer_billing_event(
        user_id=user_id,
        project_id=project_id,
        feature="content_studio",
        billing_unit_type=billing_unit,
        quantity=1,
        credits_charged=credits,
        reference_type="content_studio_chat",
        reference_id=chat_id,
        metadata={
            "workflow_id": workflow_id,
            "active_skill_ids": list(active_skill_ids or []),
        },
    )
    if generated_images > 0:
        create_customer_billing_event(
            user_id=user_id,
            project_id=project_id,
            feature="content_studio",
            billing_unit_type="studio_image_generation",
            quantity=generated_images,
            credits_charged=CONTENT_STUDIO_CREDITS["studio_image_generation"] * generated_images,
            reference_type="content_studio_chat",
            reference_id=chat_id,
            metadata={"generated_images": generated_images},
        )


def record_content_agent_run_started(
    *,
    user_id: str,
    project_id: str,
    run_id: str,
    workflow_id: Optional[str],
    planned_steps: list[str],
) -> None:
    create_customer_billing_event(
        user_id=user_id,
        project_id=project_id,
        feature="content_agent",
        billing_unit_type="agent_run_base",
        quantity=1,
        credits_charged=CONTENT_AGENT_CREDITS["agent_run_base"],
        reference_type="content_agent_run",
        reference_id=run_id,
        metadata={"workflow_id": workflow_id, "planned_steps": planned_steps},
    )
    create_customer_billing_event(
        user_id=user_id,
        project_id=project_id,
        feature="content_agent",
        billing_unit_type="agent_planning_stage",
        quantity=1,
        credits_charged=CONTENT_AGENT_CREDITS["agent_planning_stage"],
        reference_type="content_agent_run",
        reference_id=run_id,
        metadata={"workflow_id": workflow_id, "planned_steps": planned_steps},
    )


def record_content_agent_follow_up_plan(
    *,
    user_id: str,
    project_id: str,
    run_id: str,
    workflow_id: Optional[str],
    planned_steps: list[str],
) -> None:
    create_customer_billing_event(
        user_id=user_id,
        project_id=project_id,
        feature="content_agent",
        billing_unit_type="agent_planning_stage",
        quantity=1,
        credits_charged=CONTENT_AGENT_CREDITS["agent_planning_stage"],
        reference_type="content_agent_run",
        reference_id=run_id,
        metadata={"follow_up": True, "workflow_id": workflow_id, "planned_steps": planned_steps},
    )


def record_content_agent_step_completed(
    *,
    user_id: str,
    project_id: str,
    run_id: str,
    step_id: str,
    generated_images: int = 0,
) -> None:
    unit_map = {
        "content_brief": ("agent_brief_stage", CONTENT_AGENT_CREDITS["agent_brief_stage"]),
        "content_writer": ("agent_draft_stage", CONTENT_AGENT_CREDITS["agent_draft_stage"]),
        "content_editor": ("agent_edit_stage", CONTENT_AGENT_CREDITS["agent_edit_stage"]),
        "internal_linking": ("agent_internal_linking_stage", CONTENT_AGENT_CREDITS["agent_internal_linking_stage"]),
        "publish_qa": ("agent_publish_qa_stage", CONTENT_AGENT_CREDITS["agent_publish_qa_stage"]),
        "image_generation": ("agent_image_stage", CONTENT_AGENT_CREDITS["agent_image_stage"]),
    }
    unit = unit_map.get(step_id)
    if unit:
        create_customer_billing_event(
            user_id=user_id,
            project_id=project_id,
            feature="content_agent",
            billing_unit_type=unit[0],
            quantity=1,
            credits_charged=unit[1],
            reference_type="content_agent_run",
            reference_id=run_id,
            metadata={"step_id": step_id},
        )
    if generated_images > 0:
        create_customer_billing_event(
            user_id=user_id,
            project_id=project_id,
            feature="content_agent",
            billing_unit_type="agent_image_generation",
            quantity=generated_images,
            credits_charged=CONTENT_AGENT_CREDITS["agent_image_generation"] * generated_images,
            reference_type="content_agent_run",
            reference_id=run_id,
            metadata={"step_id": step_id, "generated_images": generated_images},
        )


def build_workspace_billing_summary(
    *,
    user_id: str,
    workspace_id: Optional[str] = None,
) -> WorkspaceBillingSummaryResponse:
    resolved_workspace_id = workspace_id or resolve_workspace_id(user_id)
    period_start, period_end = current_billing_period()
    customer_events = run_store.list_customer_billing_events(
        user_id=user_id,
        workspace_id=resolved_workspace_id,
        start_date=period_start,
        end_date=period_end,
        limit=500,
    )
    provider_events = run_store.list_provider_usage_events(
        user_id=user_id,
        workspace_id=resolved_workspace_id,
        start_date=period_start,
        end_date=period_end,
        limit=1000,
    )

    feature_rollup: dict[str, dict[str, int]] = defaultdict(lambda: {"quantity": 0, "credits": 0})
    provider_rollup: dict[str, dict[str, int]] = defaultdict(lambda: {"quantity": 0, "credits": 0})
    total_credits = 0
    total_actions = 0
    for event in customer_events:
        total_credits += event.credits_charged
        total_actions += event.quantity
        feature_row = feature_rollup[event.feature]
        feature_row["quantity"] += event.quantity
        feature_row["credits"] += event.credits_charged
        provider_surface = str((event.metadata_json or {}).get("provider_surface") or "").strip().lower()
        if provider_surface:
            provider_row = provider_rollup[provider_surface]
            provider_row["quantity"] += event.quantity
            provider_row["credits"] += event.credits_charged

    included_credits = DEFAULT_INCLUDED_CREDITS
    remaining_credits = max(included_credits - total_credits, 0)
    overage_credits = max(total_credits - included_credits, 0)

    return WorkspaceBillingSummaryResponse(
        workspace_id=resolved_workspace_id,
        current_period_start=period_start,
        current_period_end=min(period_end, datetime.now(timezone.utc)),
        included_credits=included_credits,
        total_credits_used=total_credits,
        remaining_credits=remaining_credits,
        overage_credits=overage_credits,
        total_billable_actions=total_actions,
        total_provider_requests=len(provider_events),
        total_input_tokens=sum(event.input_tokens for event in provider_events),
        total_output_tokens=sum(event.output_tokens for event in provider_events),
        total_cached_tokens=sum(event.cached_tokens for event in provider_events),
        total_reasoning_tokens=sum(event.reasoning_tokens for event in provider_events),
        feature_breakdown=[
            BillingBreakdownItem(
                key=key,
                label=_feature_label(key),
                quantity=value["quantity"],
                credits=value["credits"],
            )
            for key, value in sorted(feature_rollup.items(), key=lambda item: item[1]["credits"], reverse=True)
        ],
        provider_breakdown=[
            BillingBreakdownItem(
                key=key,
                label=_provider_label(key),
                quantity=value["quantity"],
                credits=value["credits"],
            )
            for key, value in sorted(provider_rollup.items(), key=lambda item: item[1]["credits"], reverse=True)
        ],
        recent_events=customer_events[:12],
    )

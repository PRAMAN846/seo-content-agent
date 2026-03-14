from __future__ import annotations

import asyncio
from typing import List, Optional

from app.core.config import settings
from app.models.schemas import (
    ArticleArtifacts,
    ArticleCreateRequest,
    BriefCreateRequest,
    BriefRecord,
    UserPublic,
    WorkspaceAction,
    WorkspaceArtifact,
    WorkspaceIntent,
    WorkspaceMessage,
    WorkspaceMessageResponse,
)
from app.models.store import run_store
from app.services.llm_client import llm_client
from app.workflows.article_pipeline import process_article_from_brief, process_quick_draft
from app.workflows.brief_pipeline import process_brief


ORCHESTRATOR_INSTRUCTION = """You are an AI workspace orchestrator for an SEO content system.
Your job is to detect the user's intent, ask only necessary clarifying questions, and choose one of these actions:
- create_brief
- create_article_from_brief
- create_quick_draft
- none

Rules:
- Prefer brief_only when the user explicitly asks for a brief, outline, SEO analysis, content brief, SERP analysis, or strategy.
- Prefer write_from_query when the user asks for a full article, blog post, draft, or content from a query.
- Prefer write_from_existing_brief when the user explicitly asks to use an existing/saved brief and a selected brief is available.
- If the user wants content but hasn't made clear whether they want a brief first or a direct draft, ask a short clarification question.
- If selected_brief_id is provided and the user asks to write from an existing brief, use it.
- Keep questions short and specific.
- Return valid JSON only.

JSON schema:
{
  "intent": "brief_only" | "write_from_query" | "write_from_existing_brief" | "clarify",
  "needs_clarification": true | false,
  "reply": "assistant reply to show the user",
  "suggested_next_step": "short text",
  "action": {
    "type": "create_brief" | "create_article_from_brief" | "create_quick_draft" | "none",
    "query": "string",
    "brief_id": "string or null",
    "seed_urls": ["url"],
    "ai_citations_text": "string",
    "ai_overview_text": "string"
  }
}
"""


def _conversation_text(messages: List[WorkspaceMessage]) -> str:
    return "\n".join("{}: {}".format(msg.role.upper(), msg.content.strip()) for msg in messages)


def _available_briefs_text(briefs: List[BriefRecord]) -> str:
    if not briefs:
        return "No saved briefs available."
    lines = []
    for brief in briefs[:25]:
        lines.append("- {} | {} | status={}".format(brief.id, brief.query, brief.status))
    return "\n".join(lines)


def _heuristic_response(messages: List[WorkspaceMessage], selected_brief_id: Optional[str]) -> WorkspaceMessageResponse:
    latest = messages[-1].content.lower()
    if any(term in latest for term in ["brief", "outline", "serp", "seo analysis"]):
        return WorkspaceMessageResponse(
            reply="I can create a content brief for this. I’m ready to run the Content Brief Agent now.",
            intent="brief_only",
            suggested_next_step="Create a content brief",
            action=WorkspaceAction(type="create_brief", query=messages[-1].content.strip()),
        )
    if any(term in latest for term in ["article", "blog", "draft", "write"]):
        if "brief" in latest and selected_brief_id:
            return WorkspaceMessageResponse(
                reply="I can generate detailed content from the selected saved brief.",
                intent="write_from_existing_brief",
                suggested_next_step="Create article from saved brief",
                action=WorkspaceAction(type="create_article_from_brief", brief_id=selected_brief_id),
            )
        return WorkspaceMessageResponse(
            reply="Do you want me to create a content brief first, or generate a direct detailed draft now?",
            intent="clarify",
            needs_clarification=True,
            suggested_next_step="Choose brief-first or direct draft",
        )
    return WorkspaceMessageResponse(
        reply="Tell me whether you want a content brief, a full article, or an article from an existing brief.",
        intent="clarify",
        needs_clarification=True,
        suggested_next_step="Clarify the requested output",
    )


def plan_workspace_response(
    *,
    messages: List[WorkspaceMessage],
    selected_brief_id: Optional[str],
    current_user: UserPublic,
) -> WorkspaceMessageResponse:
    available_briefs = run_store.list_briefs(current_user.id, limit=25)

    if not llm_client.enabled:
        return _heuristic_response(messages, selected_brief_id)

    input_text = "\n\n".join(
        [
            "Conversation:\n{}".format(_conversation_text(messages)),
            "Selected brief id: {}".format(selected_brief_id or "none"),
            "Available briefs:\n{}".format(_available_briefs_text(available_briefs)),
        ]
    )

    try:
        data = llm_client.complete_json(
            model=settings.orchestrator_model,
            instruction=ORCHESTRATOR_INSTRUCTION,
            input_text=input_text,
        )
        action = WorkspaceAction(**data.get("action", {}))
        return WorkspaceMessageResponse(
            reply=str(data.get("reply", "")).strip() or "I reviewed your request.",
            intent=data.get("intent", "clarify"),
            needs_clarification=bool(data.get("needs_clarification", False)),
            suggested_next_step=str(data.get("suggested_next_step", "")).strip(),
            action=action,
        )
    except Exception:
        return _heuristic_response(messages, selected_brief_id)


def execute_workspace_action(
    *,
    response: WorkspaceMessageResponse,
    current_user: UserPublic,
) -> WorkspaceMessageResponse:
    action = response.action

    if response.needs_clarification or action.type == "none":
        return response

    if action.type == "create_brief":
        query = action.query.strip()
        if not query:
            return response.model_copy(
                update={
                    "needs_clarification": True,
                    "intent": "clarify",
                    "reply": "I need the primary query before I can create a content brief.",
                    "suggested_next_step": "Provide the primary query",
                    "action": WorkspaceAction(),
                }
            )
        payload = BriefCreateRequest(
            query=query,
            seed_urls=action.seed_urls,
            ai_citations_text=action.ai_citations_text,
            ai_overview_text=action.ai_overview_text,
        )
        brief = run_store.create_brief(user_id=current_user.id, payload=payload)
        asyncio.create_task(
            process_brief(
                brief_id=brief.id,
                query=payload.query,
                seed_urls=payload.seed_urls,
                ai_citations_text=payload.ai_citations_text,
                ai_overview_text=payload.ai_overview_text,
            )
        )
        return response.model_copy(
            update={
                "reply": "I started the Content Brief Agent for `{}`. You can open the brief as it progresses.".format(query),
                "artifact": WorkspaceArtifact(kind="brief", id=brief.id, query=brief.query, status=brief.status),
            }
        )

    if action.type == "create_article_from_brief":
        brief_id = action.brief_id or ""
        brief = run_store.get_brief(user_id=current_user.id, brief_id=brief_id)
        if not brief or not brief.artifacts.brief_markdown.strip():
            return response.model_copy(
                update={
                    "needs_clarification": True,
                    "intent": "clarify",
                    "reply": "I need a valid saved brief before I can generate detailed content from it.",
                    "suggested_next_step": "Select a saved brief or ask me to create one",
                    "action": WorkspaceAction(),
                }
            )
        initial_artifacts = ArticleArtifacts(
            source_brief_id=brief.id,
            source_brief_markdown=brief.artifacts.brief_markdown.strip(),
        )
        payload = ArticleCreateRequest(mode="from_brief", brief_id=brief.id, query=brief.query)
        article = run_store.create_article(user_id=current_user.id, payload=payload, artifacts=initial_artifacts)
        asyncio.create_task(
            process_article_from_brief(
                article_id=article.id,
                query=brief.query,
                source_brief_id=brief.id,
                brief_markdown=brief.artifacts.brief_markdown.strip(),
            )
        )
        return response.model_copy(
            update={
                "reply": "I started the Content Writing Agent from the saved brief for `{}`.".format(brief.query),
                "artifact": WorkspaceArtifact(kind="article", id=article.id, query=article.query, status=article.status),
            }
        )

    if action.type == "create_quick_draft":
        query = action.query.strip()
        if not query:
            return response.model_copy(
                update={
                    "needs_clarification": True,
                    "intent": "clarify",
                    "reply": "I need the topic or query before I can create a direct draft.",
                    "suggested_next_step": "Provide the primary query",
                    "action": WorkspaceAction(),
                }
            )
        payload = ArticleCreateRequest(
            mode="quick_draft",
            query=query,
            seed_urls=action.seed_urls,
            ai_citations_text=action.ai_citations_text,
            ai_overview_text=action.ai_overview_text,
        )
        article = run_store.create_article(user_id=current_user.id, payload=payload)
        asyncio.create_task(
            process_quick_draft(
                article_id=article.id,
                query=payload.query,
                seed_urls=payload.seed_urls,
                ai_citations_text=payload.ai_citations_text,
                ai_overview_text=payload.ai_overview_text,
            )
        )
        return response.model_copy(
            update={
                "reply": "I started a direct detailed draft for `{}` using the Content Writing Agent.".format(query),
                "artifact": WorkspaceArtifact(kind="article", id=article.id, query=article.query, status=article.status),
            }
        )

    return response

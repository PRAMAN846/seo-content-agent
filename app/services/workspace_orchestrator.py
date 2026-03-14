from __future__ import annotations

import asyncio
import re
from typing import Dict, List, Optional

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
from app.services.personalities import build_personality_prompt
from app.workflows.article_pipeline import process_article_from_brief, process_quick_draft
from app.workflows.brief_pipeline import process_brief


ORCHESTRATOR_BASE_INSTRUCTION = """You are an AI workspace orchestrator for an SEO content system.
Your job is to detect the user's intent, ask only necessary clarifying questions, and choose one of these actions:
- create_brief
- create_article_from_brief
- create_quick_draft
- none

Rules:
- Prefer brief_only when the user explicitly asks for a brief, outline, SEO analysis, content brief, SERP analysis, or strategy.
- Prefer write_from_query when the user asks for a full article, blog post, draft, or content from a query.
- Prefer write_from_existing_brief when the user explicitly asks to use an existing/saved brief and a selected brief is available.
- If the user asks to create a new brief, ignore selected_brief_id and existing saved briefs unless the user explicitly says to reuse one.
- If the user wants content but hasn't made clear whether they want a brief first or a direct draft, ask a short clarification question.
- If selected_brief_id is provided and the user asks to write from an existing brief, use it.
- Keep questions short and specific.
- When creating a brief or draft from a natural-language request, extract the actual topic/query instead of copying command phrasing like "create a content brief on".
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
    "target_location": "string",
    "brief_id": "string or null",
    "seed_urls": ["url"],
    "ai_citations_text": "string",
    "ai_overview_text": "string"
  }
}
"""

URL_PATTERN = re.compile(r"https?://[^\s<>\"]+")
LABEL_PATTERNS: Dict[str, List[str]] = {
    "query": ["primary query", "query", "topic"],
    "target_location": ["target location", "location", "market"],
    "seed_urls": ["top ranking urls", "top urls", "seed urls", "urls"],
    "ai_overview_text": ["ai overview text", "ai overview", "overview text", "overview"],
    "ai_citations_text": ["ai citations text", "ai citations", "citations text", "citations"],
}
SKIP_MARKERS = {"skip", "none", "n/a", "na", "no", "not available"}


def _conversation_text(messages: List[WorkspaceMessage]) -> str:
    return "\n".join("{}: {}".format(msg.role.upper(), msg.content.strip()) for msg in messages)


def _available_briefs_text(briefs: List[BriefRecord]) -> str:
    if not briefs:
        return "No saved briefs available."
    lines = []
    for brief in briefs[:25]:
        lines.append("- {} | {} | status={}".format(brief.id, brief.query, brief.status))
    return "\n".join(lines)


def _extract_query(text: str) -> str:
    cleaned = text.strip()
    prefixes = [
        "create a content brief on",
        "create a content brief for",
        "create content brief on",
        "create content brief for",
        "make a content brief on",
        "make a content brief for",
        "generate a content brief on",
        "generate a content brief for",
        "content brief on",
        "content brief for",
        "write an article on",
        "write an article for",
        "write a blog on",
        "write a blog for",
        "create a draft on",
        "create a draft for",
        "generate a draft on",
        "generate a draft for",
        "write content on",
        "write content for",
    ]
    lower = cleaned.lower()
    for prefix in prefixes:
        if lower.startswith(prefix):
            return cleaned[len(prefix):].strip(" .:-")
    return cleaned


def _extract_labeled_value(text: str, labels: List[str]) -> str:
    lines = text.splitlines()
    lower_lines = [line.lower() for line in lines]
    for index, lower_line in enumerate(lower_lines):
        for label in labels:
            marker = "{}:".format(label)
            if lower_line.strip().startswith(marker):
                same_line = lines[index].split(":", 1)[1].strip()
                if same_line:
                    return same_line
                collected: List[str] = []
                for follow_index in range(index + 1, len(lines)):
                    candidate = lines[follow_index]
                    candidate_lower = candidate.lower().strip()
                    if not candidate.strip():
                        if collected:
                            break
                        continue
                    if any(candidate_lower.startswith("{}:".format(other_label)) for other_labels in LABEL_PATTERNS.values() for other_label in other_labels):
                        break
                    collected.append(candidate.rstrip())
                return "\n".join(collected).strip()
    return ""


def _extract_workspace_context(messages: List[WorkspaceMessage]) -> Dict[str, object]:
    user_messages = [message.content.strip() for message in messages if message.role == "user" and message.content.strip()]
    combined = "\n".join(user_messages)
    lowered = combined.lower()

    request_text = ""
    intent = "clarify"
    for content in reversed(user_messages):
        lowered_content = content.lower()
        if (
            any(term in lowered_content for term in ["article", "blog", "draft", "write", "content"])
            and any(term in lowered_content for term in ["saved brief", "existing brief", "selected brief", "from brief"])
        ):
            request_text = content
            intent = "write_from_existing_brief"
            break
        if any(term in lowered_content for term in ["brief", "outline", "serp", "seo analysis"]):
            request_text = content
            intent = "brief_only"
            break
        if any(term in lowered_content for term in ["article", "blog", "draft", "write", "content"]):
            request_text = content
            intent = "write_from_query"
            break

    query = _extract_labeled_value(combined, LABEL_PATTERNS["query"])
    if not query and request_text:
        query = _extract_query(request_text)

    target_location = _extract_labeled_value(combined, LABEL_PATTERNS["target_location"])
    ai_overview_text = _extract_labeled_value(combined, LABEL_PATTERNS["ai_overview_text"])
    ai_citations_text = _extract_labeled_value(combined, LABEL_PATTERNS["ai_citations_text"])

    seed_urls_block = _extract_labeled_value(combined, LABEL_PATTERNS["seed_urls"])
    seed_urls = URL_PATTERN.findall(seed_urls_block) if seed_urls_block else []
    if not seed_urls:
        seed_urls = URL_PATTERN.findall(combined)

    skip_all_optional = any(phrase in lowered for phrase in ["skip all optional", "skip optional", "use no optional inputs"])

    def is_skipped(field_key: str) -> bool:
        if skip_all_optional and field_key in {"seed_urls", "ai_overview_text", "ai_citations_text"}:
            return True
        value = _extract_labeled_value(combined, LABEL_PATTERNS[field_key]).strip().lower()
        return value in SKIP_MARKERS

    return {
        "intent": intent,
        "query": query,
        "target_location": target_location.strip(),
        "seed_urls": seed_urls,
        "ai_overview_text": "" if is_skipped("ai_overview_text") else ai_overview_text.strip(),
        "ai_citations_text": "" if is_skipped("ai_citations_text") else ai_citations_text.strip(),
        "skip_seed_urls": is_skipped("seed_urls"),
        "skip_ai_overview_text": is_skipped("ai_overview_text"),
        "skip_ai_citations_text": is_skipped("ai_citations_text"),
        "has_request": bool(request_text),
    }


def _build_intake_clarification(intent: str, query: str, target_location: str, context: Dict[str, object]) -> WorkspaceMessageResponse:
    task_name = "content brief" if intent == "brief_only" else "detailed draft"
    missing = []
    if not query:
        missing.append("Primary Query")
    if not target_location:
        missing.append("Target Location")
    if not context["seed_urls"] and not context["skip_seed_urls"]:
        missing.append("Top Ranking URLs")
    if not context["ai_overview_text"] and not context["skip_ai_overview_text"]:
        missing.append("AI Overview Text")
    if not context["ai_citations_text"] and not context["skip_ai_citations_text"]:
        missing.append("AI Citations Text")

    return WorkspaceMessageResponse(
        reply=(
            "Before I start the {}, send the missing inputs in this format:\n\n"
            "Primary Query: {}\n"
            "Target Location: {}\n"
            "Top Ranking URLs: paste URLs one per line, or write skip\n"
            "AI Overview Text: paste it, or write skip\n"
            "AI Citations Text: paste it, or write skip"
        ).format(
            task_name,
            query or "<your topic>",
            target_location or "<country, city, or target market>",
        ),
        intent="clarify",
        needs_clarification=True,
        suggested_next_step="Send the required intake fields for the {}".format(task_name),
        action=WorkspaceAction(),
    )


def _intake_ready_response(messages: List[WorkspaceMessage], selected_brief_id: Optional[str]) -> Optional[WorkspaceMessageResponse]:
    context = _extract_workspace_context(messages)
    intent = str(context["intent"])
    query = str(context["query"]).strip()
    target_location = str(context["target_location"]).strip()

    if intent == "write_from_existing_brief" and selected_brief_id:
        return WorkspaceMessageResponse(
            reply="I can generate detailed content from the selected saved brief.",
            intent="write_from_existing_brief",
            suggested_next_step="Create article from saved brief",
            action=WorkspaceAction(type="create_article_from_brief", brief_id=selected_brief_id),
        )

    if intent == "brief_only" and context["has_request"]:
        if not query or not target_location or (
            not context["seed_urls"] and not context["skip_seed_urls"]
        ) or (
            not context["ai_overview_text"] and not context["skip_ai_overview_text"]
        ) or (
            not context["ai_citations_text"] and not context["skip_ai_citations_text"]
        ):
            return _build_intake_clarification(intent, query, target_location, context)
        return WorkspaceMessageResponse(
            reply="I have the required brief inputs and can start the Content Brief Agent now.",
            intent="brief_only",
            suggested_next_step="Create the content brief",
            action=WorkspaceAction(
                type="create_brief",
                query=query,
                target_location=target_location,
                seed_urls=list(context["seed_urls"]),
                ai_overview_text=str(context["ai_overview_text"]),
                ai_citations_text=str(context["ai_citations_text"]),
            ),
        )

    latest = messages[-1].content.lower()
    if intent == "write_from_query" and context["has_request"] and "brief" not in latest:
        if not query or not target_location or (
            not context["seed_urls"] and not context["skip_seed_urls"]
        ) or (
            not context["ai_overview_text"] and not context["skip_ai_overview_text"]
        ) or (
            not context["ai_citations_text"] and not context["skip_ai_citations_text"]
        ):
            return _build_intake_clarification(intent, query, target_location, context)
        return WorkspaceMessageResponse(
            reply="I have the required draft inputs and can start the Content Writing Agent now.",
            intent="write_from_query",
            suggested_next_step="Create the detailed draft",
            action=WorkspaceAction(
                type="create_quick_draft",
                query=query,
                target_location=target_location,
                seed_urls=list(context["seed_urls"]),
                ai_overview_text=str(context["ai_overview_text"]),
                ai_citations_text=str(context["ai_citations_text"]),
            ),
        )

    if "brief" in latest and selected_brief_id and intent == "write_from_query":
        return WorkspaceMessageResponse(
            reply="I can generate detailed content from the selected saved brief.",
            intent="write_from_existing_brief",
            suggested_next_step="Create article from saved brief",
            action=WorkspaceAction(type="create_article_from_brief", brief_id=selected_brief_id),
        )

    return None


def _heuristic_response(messages: List[WorkspaceMessage], selected_brief_id: Optional[str]) -> WorkspaceMessageResponse:
    intake_response = _intake_ready_response(messages, selected_brief_id)
    if intake_response:
        return intake_response

    latest_raw = messages[-1].content.strip()
    latest = latest_raw.lower()
    extracted_query = _extract_query(latest_raw)

    if any(term in latest for term in ["brief", "outline", "serp", "seo analysis"]):
        return WorkspaceMessageResponse(
            reply="I can create a content brief for this. I’m ready to run the Content Brief Agent now.",
            intent="brief_only",
            suggested_next_step="Create a content brief",
            action=WorkspaceAction(type="create_brief", query=extracted_query),
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
    user_settings = run_store.get_user_settings(current_user.id)

    intake_response = _intake_ready_response(messages, selected_brief_id)
    if intake_response:
        return intake_response

    if not llm_client.enabled:
        return _heuristic_response(messages, selected_brief_id)

    personality_prompt = build_personality_prompt(
        "workspace",
        user_settings.orchestrator_personality_id if user_settings else "strategist",
        user_settings.custom_orchestrator_personality if user_settings else "",
    )
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
            instruction="{}\n\nWorkspace personality:\n{}".format(
                ORCHESTRATOR_BASE_INSTRUCTION,
                personality_prompt or "Use the default balanced strategist style.",
            ),
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
            target_location=action.target_location.strip(),
            seed_urls=action.seed_urls,
            ai_citations_text=action.ai_citations_text,
            ai_overview_text=action.ai_overview_text,
        )
        brief = run_store.create_brief(user_id=current_user.id, payload=payload)
        asyncio.create_task(
            process_brief(
                brief_id=brief.id,
                query=payload.query,
                target_location=payload.target_location,
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
            requested_target_location=brief.artifacts.requested_target_location,
            requested_seed_urls=brief.artifacts.requested_seed_urls,
            requested_ai_citations_text=brief.artifacts.requested_ai_citations_text,
            requested_ai_overview_text=brief.artifacts.requested_ai_overview_text,
            source_brief_id=brief.id,
            source_brief_markdown=brief.artifacts.brief_markdown.strip(),
        )
        payload = ArticleCreateRequest(
            mode="from_brief",
            brief_id=brief.id,
            query=brief.query,
            target_location=brief.artifacts.requested_target_location,
        )
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
            target_location=action.target_location.strip(),
            seed_urls=action.seed_urls,
            ai_citations_text=action.ai_citations_text,
            ai_overview_text=action.ai_overview_text,
        )
        article = run_store.create_article(user_id=current_user.id, payload=payload)
        asyncio.create_task(
            process_quick_draft(
                article_id=article.id,
                query=payload.query,
                target_location=payload.target_location,
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

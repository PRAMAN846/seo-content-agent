from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Optional

from app.core.config import settings
from app.models.schemas import ContentAgentRunRecord
from app.services.llm_client import llm_client

QUESTION_PREFIXES = (
    "what",
    "why",
    "how",
    "which",
    "can",
    "could",
    "do",
    "does",
    "did",
    "is",
    "are",
    "where",
    "when",
    "who",
    "tell me",
    "explain",
    "show me",
)

RUN_QUESTION_TERMS = (
    "this run",
    "current run",
    "selected run",
    "why did",
    "why didn't",
    "why did not",
    "what happened",
    "what went wrong",
    "what is missing",
    "what's missing",
    "what do you still need",
    "what do you need from me",
    "which stage",
    "what stage",
    "status of",
    "images were not generated",
    "images were not created",
    "didn't generate",
    "did not generate",
    "not generated",
    "not created",
    "where is the final",
    "where are the images",
    "what did you create",
    "what did you use",
    "what outputs",
    "what artifacts",
)

CAPABILITY_TERMS = (
    "what can you do",
    "can you fetch",
    "can you browse",
    "can you access",
    "do you support",
    "does the agent use",
    "how do skill updates work",
    "how does skill update work",
    "can you update the skill",
    "can i upload skills",
    "can you export",
    "what formats",
    "which model",
    "which llm",
    "what model",
    "how does this work",
    "how do project settings work",
    "what inputs do you need",
)

EXECUTION_ACTION_TERMS = (
    "create",
    "generate",
    "write",
    "run",
    "build",
    "draft",
    "review",
    "edit",
    "produce",
    "make",
    "start",
    "continue",
    "do the workflow",
)

EXECUTION_OBJECT_TERMS = (
    "workflow",
    "content",
    "article",
    "brief",
    "draft",
    "editorial review",
    "internal linking",
    "images",
    "visuals",
    "publish qa",
)

MIXED_SEQUENCE_TERMS = (
    "and then",
    "then run",
    "then create",
    "then generate",
    "then write",
    "after that",
    "before you",
    "first and then",
)

META_ROUTER_INSTRUCTION = """You are routing a message inside a content creation assistant.
Return valid JSON only in this shape:
{
  "route": "answer" | "clarify" | "execute",
  "kind": "run_question" | "capability_question" | "mixed_request" | "none",
  "clarification_question": ""
}

Rules:
- Route to "answer" when the user is asking a question about the current run, capabilities, settings, inputs, exports, skill behavior, or what happened.
- Route to "execute" only when the user is clearly asking the assistant to perform content work now.
- Route to "clarify" when the user mixes a question with an execution request and it would be risky to assume which they want first.
- If the message is mostly a question, prefer "answer" over "execute".
- Never route export requests or explicit skill-update requests here; those are handled elsewhere.
"""

META_ANSWER_INSTRUCTION = """You are answering a conversational question inside a content creation tool.
Answer only from the supplied context. Be direct, calm, and practical.

Rules:
- If the context does not confirm something, say clearly that it is not available or cannot be confirmed.
- Do not invent run details, outputs, URLs, or capabilities.
- If the user asked about the current run, explain what happened, what exists already, what is missing, and the next sensible move.
- If the user asked about capabilities, answer what the tool can do today and what still requires another layer or setup.
- Keep the answer concise but complete enough to be useful.
"""


@dataclass
class ContentMetaRouteResult:
    status: str = "none"
    kind: str = ""
    reply: str = ""
    notes: list[str] = field(default_factory=list)


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().lower())


def _looks_like_question(message_text: str) -> bool:
    lowered = _normalize_text(message_text)
    return "?" in (message_text or "") or lowered.startswith(QUESTION_PREFIXES)


def _looks_like_execution_request(message_text: str) -> bool:
    lowered = _normalize_text(message_text)
    return any(term in lowered for term in EXECUTION_ACTION_TERMS) and any(term in lowered for term in EXECUTION_OBJECT_TERMS)


def _looks_like_run_question(message_text: str, *, has_run: bool) -> bool:
    if not has_run:
        return False
    lowered = _normalize_text(message_text)
    if any(term in lowered for term in RUN_QUESTION_TERMS):
        return True
    return _looks_like_question(message_text) and any(term in lowered for term in ("run", "stage", "artifact", "output", "image", "draft", "brief"))


def _looks_like_capability_question(message_text: str) -> bool:
    lowered = _normalize_text(message_text)
    if any(term in lowered for term in CAPABILITY_TERMS):
        return True
    return _looks_like_question(message_text) and any(
        term in lowered
        for term in (
            "skill",
            "project settings",
            "workspace settings",
            "browser",
            "fetch",
            "search",
            "export",
            "model",
            "llm",
            "upload",
            "inputs",
        )
    )


def _heuristic_route(message_text: str, *, has_run: bool) -> tuple[str, str]:
    if not (message_text or "").strip():
        return "none", "none"
    lowered = _normalize_text(message_text)
    looks_question = _looks_like_question(message_text)
    looks_execution = _looks_like_execution_request(message_text)
    if looks_question and looks_execution and any(term in lowered for term in MIXED_SEQUENCE_TERMS):
        return "clarify", "mixed_request"
    if _looks_like_run_question(message_text, has_run=has_run):
        return "answer", "run_question"
    if _looks_like_capability_question(message_text):
        return "answer", "capability_question"
    if looks_question and looks_execution:
        return "clarify", "mixed_request"
    if looks_question and not looks_execution:
        return "answer", "capability_question"
    return "none", "none"


def _route_with_llm(message_text: str, *, has_run: bool) -> tuple[str, str, str]:
    if not llm_client.enabled:
        return "none", "none", ""
    try:
        payload = llm_client.complete_json(
            model=settings.orchestrator_model,
            instruction=META_ROUTER_INSTRUCTION,
            input_text=f"Has current run context: {'yes' if has_run else 'no'}\nMessage: {message_text}",
            reasoning_effort=settings.orchestrator_reasoning_effort,
        )
    except Exception:
        return "none", "none", ""
    route = str(payload.get("route") or "none").strip()
    kind = str(payload.get("kind") or "none").strip()
    clarification = str(payload.get("clarification_question") or "").strip()
    if route not in {"answer", "clarify", "execute"}:
        route = "none"
    if kind not in {"run_question", "capability_question", "mixed_request", "none"}:
        kind = "none"
    return route, kind, clarification


def _build_capability_context(*, surface: str, has_run: bool) -> str:
    lines = [
        f"Surface: {surface}",
        f"Current run selected: {'yes' if has_run else 'no'}",
        f"Planner/orchestrator model: {settings.orchestrator_model}",
        f"Execution/writer model: {settings.writer_model}",
        f"Image model: {settings.image_model}",
        f"Search enabled: {'yes' if settings.content_agent_search_enabled else 'no'}",
        f"Serper configured: {'yes' if bool(settings.serper_api_key) else 'no'}",
        f"Browser fallback enabled: {'yes' if settings.content_agent_browser_fallback_enabled else 'no'}",
        "",
        "Current capabilities:",
        "- Can run staged informational workflows across content brief, content writer, editorial review, internal linking, image generation, and final publish QA.",
        "- Can save skill updates from chat at project scope today, and at workspace scope where supported by settings.",
        "- Can use project settings as shared context so brand and sitemap information does not have to be entered twice.",
        "- Can search the public web, fetch public URLs, and use browser fallback for JS-heavy pages when available.",
        "- Can export final article outputs as Markdown or Word (.docx), and generated images as a zip when those artifacts exist.",
        "",
        "Current limitations:",
        "- AI Overview text and cited URLs are not guaranteed from the current search stack; if unavailable, the assistant should say so.",
        "- Login-gated or private pages are not reliably accessible unless they are available through supported public fetching in this environment.",
        "- Custom skill upload/import is not the same as a full free-form Cursor prompt file yet; that still needs a structured importer layer.",
    ]
    return "\n".join(lines)


def _build_run_context(run: ContentAgentRunRecord) -> str:
    lines = [
        f"Run title: {run.title}",
        f"Run goal: {run.goal}",
        f"Status: {run.status}",
        f"Stage: {run.stage}",
        f"Progress: {run.progress_percent}%",
        f"Current step title: {run.current_step_title}",
        f"Workflow: {run.selected_workflow_id or 'custom'}",
    ]
    if run.error:
        lines.append(f"Error: {run.error}")
    lines.append("")
    lines.append("Steps:")
    for step in run.steps:
        detail = f"- {step.title or step.step_type}: {step.status}"
        if step.output_json.get("planner_model"):
            detail += f" | planner={step.output_json.get('planner_model')}"
        if step.output_json.get("execution_model"):
            detail += f" | executor={step.output_json.get('execution_model')}"
        lines.append(detail)
    lines.append("")
    lines.append("Artifacts:")
    if run.artifacts:
        for artifact in sorted(run.artifacts, key=lambda item: item.created_at):
            metadata = artifact.metadata_json or {}
            summary_bits = [artifact.artifact_type]
            if metadata.get("step"):
                summary_bits.append(f"step={metadata['step']}")
            if metadata.get("image_count"):
                summary_bits.append(f"image_count={metadata['image_count']}")
            if metadata.get("public_url"):
                summary_bits.append("download_ready=yes")
            lines.append(f"- {artifact.title}: {' | '.join(summary_bits)}")
    else:
        lines.append("- No artifacts yet")
    return "\n".join(lines)


def _fallback_meta_answer(*, kind: str, surface: str, run: Optional[ContentAgentRunRecord]) -> str:
    if kind == "run_question":
        if not run:
            return "I can explain a run, but there is no selected run context available yet."
        base = (
            f"The current run is {run.status.replace('_', ' ')} at {run.progress_percent}% and the latest stage is "
            f"{run.current_step_title or run.stage or 'not set'}."
        )
        if run.status == "awaiting_approval":
            base += " It is paused for approval before continuing."
        if run.error:
            base += f" The latest recorded error is: {run.error}"
        return base
    return (
        f"This {surface} can answer capability questions, use shared project settings, run content stages, "
        "and explain what happened in a selected run. If something is not available from the current stack, it should say so explicitly."
    )


def _generate_meta_answer(
    *,
    message_text: str,
    kind: str,
    surface: str,
    run: Optional[ContentAgentRunRecord],
) -> str:
    context_parts = [_build_capability_context(surface=surface, has_run=run is not None)]
    if run:
        context_parts.extend(["", "Current run context:", _build_run_context(run)])
    context = "\n".join(context_parts)
    if not llm_client.enabled:
        return _fallback_meta_answer(kind=kind, surface=surface, run=run)
    try:
        return llm_client.complete(
            model=settings.orchestrator_model,
            instruction=META_ANSWER_INSTRUCTION,
            input_text=f"User question:\n{message_text}\n\nContext:\n{context}",
            reasoning_effort=settings.orchestrator_reasoning_effort,
        )
    except Exception:
        return _fallback_meta_answer(kind=kind, surface=surface, run=run)


def answer_content_meta_request(
    *,
    message_text: str,
    surface: str,
    run: Optional[ContentAgentRunRecord] = None,
) -> ContentMetaRouteResult:
    route, kind = _heuristic_route(message_text, has_run=run is not None)
    clarification = ""
    if route == "none" and (_looks_like_question(message_text) or _looks_like_execution_request(message_text)):
        route, kind, clarification = _route_with_llm(message_text, has_run=run is not None)
    if route == "clarify":
        reply = clarification or (
            "I can answer your question first or run the requested content work next, but I do not want to guess which you want. "
            "Tell me whether you want an explanation first or execution first."
        )
        return ContentMetaRouteResult(
            status="clarify",
            kind=kind or "mixed_request",
            reply=reply,
            notes=["Clarification was requested before starting a new run step."],
        )
    if route == "answer":
        reply = _generate_meta_answer(
            message_text=message_text,
            kind=kind or "capability_question",
            surface=surface,
            run=run,
        )
        note = "Answered a question about the current run." if kind == "run_question" else "Answered a capability or settings question."
        return ContentMetaRouteResult(
            status="answered",
            kind=kind or "capability_question",
            reply=reply,
            notes=[note],
        )
    return ContentMetaRouteResult()

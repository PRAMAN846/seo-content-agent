from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from app.core.config import settings
from app.models.schemas import ContentAgentRunRecord, UserPublic
from app.models.store import run_store
from app.services.content_agent_research import build_research_packet
from app.services.llm_client import llm_client

ALLOWED_SKILL_IDS = {
    "content_brief",
    "content_writer",
    "content_editor",
    "internal_linking",
    "image_generation",
    "publish_qa",
}

SKILL_LABELS = {
    "content_brief": "Content Brief",
    "content_writer": "Content Writer",
    "content_editor": "Editorial Review",
    "internal_linking": "Internal Linking",
    "image_generation": "Image Generate & Review",
    "publish_qa": "Final Publish QA",
}

STEP_ALIASES = {
    "content_brief": ["content brief", "brief", "serp", "outline", "strategy"],
    "content_writer": ["content writer", "writer", "write", "draft", "article", "blog"],
    "content_editor": ["content editor", "editorial review", "content review", "editor", "edit", "polish", "rewrite", "improve"],
    "internal_linking": ["internal linking", "internal link", "anchor text", "sitemap"],
    "image_generation": ["image generate", "image review", "image", "visual", "illustration", "diagram", "alt text"],
    "publish_qa": ["publish qa", "qa", "publish", "metadata", "final review", "final qa"],
}

CONFIRMATION_TERMS = (
    "go ahead",
    "proceed",
    "do it",
    "use that approach",
    "use this approach",
    "go with that approach",
    "go with this approach",
    "looks good",
    "approved",
    "continue with that",
)

DISCUSS_FIRST_TERMS = (
    "first tell me",
    "tell me first",
    "first show me",
    "show me first",
    "before rewriting",
    "when i confirm",
    "wait for my confirmation",
    "dont do it yet",
    "don't do it yet",
    "guide me first",
)

SAVE_APPROACH_TERMS = (
    "save this approach",
    "save this in",
    "remember this approach",
    "remember this for",
    "save this for",
    "save the approach",
)

RESEARCH_TERMS = (
    "similar topic in other niches",
    "other niches",
    "similar articles",
    "look at similar topic",
    "look at similar articles",
    "how those article have written",
    "how those articles have written",
    "how those articles are written",
    "guide me by looking",
    "compare with similar articles",
)

FEEDBACK_HINT_TERMS = (
    "feedback",
    "readability",
    "readable",
    "skimmable",
    "bullet points",
    "paragraph breaks",
    "stats",
    "intro",
    "sources",
    "source urls",
    "jump links",
    "citation",
    "citations",
    "boxes are coming",
    "review it",
    "review this",
    "improve it",
    "rewrite this",
)

FEEDBACK_ANALYSIS_INSTRUCTION = """You analyze post-draft content feedback and convert it into a safe next action.
Return valid JSON only in this shape:
{
  "is_feedback_request": true | false,
  "feedback_summary": "short paragraph",
  "feedback_rules": ["short reusable rule"],
  "discuss_before_execution": true | false,
  "requests_comparative_research": true | false,
  "research_query": "string",
  "requests_skill_memory": true | false,
  "skill_targets": ["content_writer" | "content_editor" | "internal_linking" | "image_generation" | "publish_qa"],
  "requests_rerun": true | false,
  "rerun_steps": ["content_writer" | "content_editor" | "internal_linking" | "image_generation" | "publish_qa"]
}

Rules:
- Use only the allowed skill ids.
- Treat article revision feedback, critique, rewrite requests, polish requests, or instructions about how the content should improve as feedback.
- If the user asks to explain, compare, or guide first, or says they will confirm before execution, set discuss_before_execution to true.
- If the user wants the lesson remembered for future similar articles, set requests_skill_memory to true and choose the most relevant skills.
- If the user wants the article reworked, rerun, or downstream stages rerun, set requests_rerun to true and include the minimal ordered steps needed.
- feedback_rules must be reusable and concrete, not just a copy of the user wording.
"""

FEEDBACK_RESEARCH_SYNTHESIS = """You are helping a content agent explain how similar informational articles are written in other niches.
Use only the supplied research packet.
Return concise markdown with exactly these sections:
## Patterns worth borrowing
- concise bullet

## Recommended approach for this article
- concise bullet

Rules:
- Focus on readability, intro structure, how evidence/statistics are woven into prose, and citation/source presentation.
- If source coverage is thin, say so briefly in a bullet instead of guessing.
- Do not claim patterns that are not supported by the research packet.
"""


@dataclass
class FeedbackOrchestrationResult:
    status: str = ""
    reply: str = ""
    notes: list[str] = field(default_factory=list)
    directive: str = ""
    feedback_rules: list[str] = field(default_factory=list)
    rerun_steps: list[str] = field(default_factory=list)
    skill_ids: list[str] = field(default_factory=list)
    save_scope: str = "project"
    save_requested: bool = False
    save_instructions: dict[str, str] = field(default_factory=dict)
    pending_confirmation: bool = False
    research_markdown: str = ""
    research_metadata: dict = field(default_factory=dict)
    summary: str = ""


def _normalize_text(value: str) -> str:
    return (value or "").strip().lower()


def _message_has_any(message_text: str, terms: tuple[str, ...]) -> bool:
    lowered = _normalize_text(message_text)
    return any(term in lowered for term in terms)


def _message_is_confirmation(message_text: str) -> bool:
    lowered = _normalize_text(message_text)
    if lowered in {"yes", "yep", "yeah", "sure", "ok", "okay"}:
        return True
    return any(term in lowered for term in CONFIRMATION_TERMS)


def _parse_scope(message_text: str) -> str:
    lowered = _normalize_text(message_text)
    if "workspace level" in lowered or "workspace-wide" in lowered or "workspace specific" in lowered:
        return "workspace"
    return "project"


def _extract_skill_ids(message_text: str) -> list[str]:
    lowered = _normalize_text(message_text)
    found: list[str] = []
    for skill_id, aliases in STEP_ALIASES.items():
        if any(alias in lowered for alias in aliases) and skill_id not in found:
            found.append(skill_id)
    return found


def _save_target_segment(message_text: str) -> str:
    lowered = _normalize_text(message_text)
    if not any(term in lowered for term in SAVE_APPROACH_TERMS):
        return lowered
    segment = lowered
    for marker in (" and then ", " then use ", " then rerun ", " then ", " & then "):
        if marker in segment:
            segment = segment.split(marker, 1)[0]
            break
    return segment


def _extract_save_target_skill_ids(message_text: str) -> list[str]:
    segment = _save_target_segment(message_text)
    return _extract_skill_ids(segment)


def _extract_rerun_step_ids(message_text: str) -> list[str]:
    lowered = _normalize_text(message_text)
    candidates = []
    for marker in (" then use ", " then rerun ", " rerun ", " use the relevant skills again", " start working on that"):
        idx = lowered.find(marker)
        if idx >= 0:
            candidates.append((idx, lowered[idx + len(marker) :]))
    segment = min(candidates, key=lambda item: item[0])[1] if candidates else lowered
    return _ordered_steps_from_prompt(segment)


def _ordered_steps_from_prompt(prompt: str) -> list[str]:
    lowered = _normalize_text(prompt)
    indexed_steps: list[tuple[int, str]] = []
    for step_id, aliases in STEP_ALIASES.items():
        positions = [lowered.find(alias) for alias in aliases if lowered.find(alias) >= 0]
        if positions:
            indexed_steps.append((min(positions), step_id))
    indexed_steps.sort(key=lambda item: item[0])
    ordered: list[str] = []
    seen: set[str] = set()
    for _, step_id in indexed_steps:
        if step_id not in seen:
            seen.add(step_id)
            ordered.append(step_id)
    return ordered


def _normalize_steps(step_ids: list[str]) -> list[str]:
    cleaned: list[str] = []
    for step_id in step_ids:
        if step_id in ALLOWED_SKILL_IDS and step_id not in cleaned:
            cleaned.append(step_id)
    return cleaned


def _default_rerun_steps(skill_ids: list[str], explicit_steps: list[str]) -> list[str]:
    if explicit_steps:
        return _normalize_steps(explicit_steps)
    if not skill_ids:
        return ["content_writer", "internal_linking", "image_generation", "content_editor", "publish_qa"]
    if "content_writer" in skill_ids or "content_editor" in skill_ids:
        return ["content_writer", "internal_linking", "image_generation", "content_editor", "publish_qa"]
    if "internal_linking" in skill_ids:
        return ["internal_linking", "content_editor", "publish_qa"]
    if "image_generation" in skill_ids:
        return ["image_generation", "content_editor", "publish_qa"]
    if "publish_qa" in skill_ids:
        return ["publish_qa"]
    return ["content_writer", "internal_linking", "image_generation", "content_editor", "publish_qa"]


def _default_skill_targets(message_text: str, explicit_skill_ids: list[str]) -> list[str]:
    if explicit_skill_ids:
        return _normalize_steps(explicit_skill_ids)
    lowered = _normalize_text(message_text)
    targets = ["content_writer", "content_editor"]
    if "internal link" in lowered or "sitemap" in lowered:
        targets.append("internal_linking")
    if "image" in lowered or "visual" in lowered:
        targets.append("image_generation")
    if "qa" in lowered or "publish" in lowered or "metadata" in lowered:
        targets.append("publish_qa")
    return _normalize_steps(targets)


def _heuristic_feedback_analysis(message_text: str) -> dict:
    explicit_skill_ids = _extract_skill_ids(message_text)
    explicit_steps = _extract_rerun_step_ids(message_text) or _ordered_steps_from_prompt(message_text)
    discuss_first = _message_has_any(message_text, DISCUSS_FIRST_TERMS)
    save_requested = _message_has_any(message_text, SAVE_APPROACH_TERMS) or "relevant skills" in _normalize_text(message_text)
    research_requested = _message_has_any(message_text, RESEARCH_TERMS) or "other niches" in _normalize_text(message_text)
    skill_targets = _default_skill_targets(message_text, explicit_skill_ids)
    rerun_steps = _default_rerun_steps(skill_targets, explicit_steps)
    rules: list[str] = []
    lowered = _normalize_text(message_text)
    if any(term in lowered for term in ["readability", "bullet points", "paragraph", "skimmable"]):
        rules.append("Make the article more skimmable with shorter paragraphs, purposeful bullet points, and clearer subhead-led structure.")
    if any(term in lowered for term in ["stats", "report said", "report", "understanding part", "intro"]):
        rules.append("Integrate evidence into the narrative instead of stacking report-by-report statements, and use intros that set context before listing proof points.")
    if any(term in lowered for term in ["sources", "jump links", "urls", "citation", "boxes are coming"]):
        rules.append("Use cleaner superscript-style source markers that jump to a source list with the full cited URLs.")
    if not rules:
        rules.append("Apply the user feedback directly, keep the article clearer and more practical, and preserve informational search intent.")
    return {
        "is_feedback_request": _message_has_any(message_text, FEEDBACK_HINT_TERMS) or "below are some feedbacks" in lowered,
        "feedback_summary": "The user is asking for a feedback-led revision path, with better readability, more natural evidence integration, and cleaner source handling.",
        "feedback_rules": rules,
        "discuss_before_execution": discuss_first,
        "requests_comparative_research": research_requested,
        "research_query": "how strong informational articles in other niches handle readability, intros, and statistics",
        "requests_skill_memory": save_requested,
        "skill_targets": skill_targets,
        "requests_rerun": any(term in lowered for term in ["write this again", "rerun", "use content writer", "use the relevant skills again", "improve it"]),
        "rerun_steps": rerun_steps,
    }


def _analyze_feedback_request(message_text: str) -> dict:
    heuristic = _heuristic_feedback_analysis(message_text)
    if not llm_client.enabled:
        return heuristic
    try:
        payload = llm_client.complete_json(
            model=settings.orchestrator_model,
            instruction=FEEDBACK_ANALYSIS_INSTRUCTION,
            input_text=message_text,
            reasoning_effort=settings.orchestrator_reasoning_effort,
        )
    except Exception:
        return heuristic

    skill_targets = _normalize_steps([str(item or "").strip() for item in (payload.get("skill_targets") or [])])
    if not skill_targets:
        skill_targets = heuristic["skill_targets"]
    explicit_steps = _extract_rerun_step_ids(message_text) or _ordered_steps_from_prompt(message_text)
    rerun_steps = _normalize_steps([str(item or "").strip() for item in (payload.get("rerun_steps") or [])])
    rerun_steps = rerun_steps or _default_rerun_steps(skill_targets, explicit_steps)
    rules = [str(rule).strip() for rule in (payload.get("feedback_rules") or []) if str(rule).strip()]
    if not rules:
        rules = list(heuristic["feedback_rules"])
    return {
        "is_feedback_request": bool(payload.get("is_feedback_request")),
        "feedback_summary": str(payload.get("feedback_summary") or heuristic["feedback_summary"]).strip(),
        "feedback_rules": rules,
        "discuss_before_execution": bool(payload.get("discuss_before_execution")),
        "requests_comparative_research": bool(payload.get("requests_comparative_research")),
        "research_query": str(payload.get("research_query") or heuristic["research_query"]).strip(),
        "requests_skill_memory": bool(payload.get("requests_skill_memory")),
        "skill_targets": skill_targets,
        "requests_rerun": bool(payload.get("requests_rerun")),
        "rerun_steps": rerun_steps,
    }


def _build_feedback_directive(summary: str, rules: list[str], research_summary: str = "") -> str:
    lines = [
        "Apply this approved feedback approach while revising the article.",
        "",
        "Revision summary:",
        summary.strip() or "Apply the approved editorial improvements.",
        "",
        "Must-hold rules:",
    ]
    lines.extend(f"- {rule}" for rule in rules if rule.strip())
    if research_summary.strip():
        lines.extend(["", "Comparable-article patterns to follow:", research_summary.strip()])
    return "\n".join(lines).strip()


def _skill_specific_feedback_instruction(skill_id: str, rules: list[str]) -> str:
    joined = " ".join(rule.strip() for rule in rules if rule.strip())
    if skill_id == "content_writer":
        return f"For similar informational articles, write with this standard: {joined}"
    if skill_id == "content_editor":
        return f"For similar informational articles, review and revise against this standard: {joined}"
    if skill_id == "internal_linking":
        return f"For similar informational articles, keep internal linking work aligned with this standard: {joined}"
    if skill_id == "image_generation":
        return f"For similar informational articles, plan visuals and captions in a way that supports this standard: {joined}"
    if skill_id == "publish_qa":
        return f"For similar informational articles, include this in final QA checks: {joined}"
    return joined


def _summarize_research_patterns(*, query: str, focus_rules: list[str], default_target_country: Optional[str]) -> tuple[str, str, dict]:
    research_markdown, research_metadata = build_research_packet(
        prompt=query,
        default_target_country=default_target_country,
    )
    if not research_markdown.strip():
        return "", "", research_metadata
    if not llm_client.enabled:
        return "", research_markdown, research_metadata
    focus_text = "\n".join(f"- {rule}" for rule in focus_rules if rule.strip()) or "- readability\n- intro structure\n- evidence integration"
    try:
        summary = llm_client.complete(
            model=settings.small_model,
            instruction=FEEDBACK_RESEARCH_SYNTHESIS,
            input_text=(
                f"Request:\n{query}\n\n"
                f"Focus areas:\n{focus_text}\n\n"
                f"Research packet:\n{research_markdown}"
            ),
            reasoning_effort="low",
        ).strip()
    except Exception:
        summary = ""
    return summary, research_markdown, research_metadata


def _build_feedback_reply(
    *,
    summary: str,
    rules: list[str],
    research_summary: str,
    rerun_steps: list[str],
    skill_ids: list[str],
    explain_only: bool,
    save_requested: bool,
    has_article_context: bool,
) -> str:
    lines = [
        "Here’s how I’d handle that feedback before rewriting anything:",
        "",
        "What I would change:",
    ]
    lines.extend(f"- {rule}" for rule in rules if rule.strip())
    if research_summary.strip():
        lines.extend(["", research_summary.strip()])
    elif explain_only:
        lines.extend(
            [
                "",
                "I could not pull a strong comparable-article research pattern set right now, so I would use the article feedback itself as the working approach unless you want me to retry.",
            ]
        )
    if not has_article_context:
        lines.extend(
            [
                "",
                "I can discuss and save the approach now, but to rewrite the article I still need either a selected run with the draft artifacts or the draft pasted into the thread.",
            ]
        )
        return "\n".join(lines).strip()
    step_labels = " -> ".join(SKILL_LABELS.get(step_id, step_id) for step_id in rerun_steps)
    lines.extend(
        [
            "",
            f"If this approach looks right, I can next rerun: {step_labels}.",
        ]
    )
    if save_requested or skill_ids:
        skill_labels = ", ".join(SKILL_LABELS.get(skill_id, skill_id) for skill_id in skill_ids) or "the relevant skills"
        lines.append(f"If you want it remembered for future similar articles, I can also save it to: {skill_labels}.")
    lines.append('Reply with "go ahead" when you want me to execute it, or tell me what you want changed first.')
    return "\n".join(lines).strip()


def orchestrate_feedback_request(
    *,
    current_user: UserPublic,
    project_id: str,
    message_text: str,
    run: Optional[ContentAgentRunRecord],
    latest_article: str,
    pending_context: Optional[dict] = None,
) -> FeedbackOrchestrationResult:
    prompt = (message_text or "").strip()
    if not prompt:
        return FeedbackOrchestrationResult()

    default_target_country = None
    project = run_store.get_visibility_project(current_user.id, project_id)
    if project:
        default_target_country = project.default_target_country
    has_article_context = bool(latest_article.strip()) or len(prompt.split()) >= 180

    if pending_context and _message_is_confirmation(prompt):
        rules = [str(rule).strip() for rule in (pending_context.get("feedback_rules") or []) if str(rule).strip()]
        directive = str(pending_context.get("directive") or "").strip() or _build_feedback_directive(
            str(pending_context.get("feedback_summary") or "").strip(),
            rules,
            str(pending_context.get("research_summary") or "").strip(),
        )
        requested_skill_ids = _extract_save_target_skill_ids(prompt)
        skill_ids = _normalize_steps(requested_skill_ids or list(pending_context.get("skill_ids") or []))
        save_requested = _message_has_any(prompt, SAVE_APPROACH_TERMS) or "relevant skills" in _normalize_text(prompt)
        save_instructions = {}
        if save_requested and skill_ids:
            save_instructions = {skill_id: _skill_specific_feedback_instruction(skill_id, rules) for skill_id in skill_ids}
        explicit_steps = _extract_rerun_step_ids(prompt) or _ordered_steps_from_prompt(prompt)
        rerun_steps = _default_rerun_steps(skill_ids, explicit_steps or list(pending_context.get("rerun_steps") or []))
        if not has_article_context:
            reply = (
                "I can save the approved approach now, but I still do not have article artifacts in this thread to rerun. "
                "Select the run with the article or paste the article here."
            )
            return FeedbackOrchestrationResult(
                status="clarify",
                reply=reply,
                notes=["Awaiting an article/draft before applying the approved feedback approach."],
            )
        reply_lines = ["Approved. I’ll use the feedback approach for the rerun."]
        if save_instructions:
            reply_lines.append(
                "I’ll also save it to: "
                + ", ".join(SKILL_LABELS.get(skill_id, skill_id) for skill_id in save_instructions)
                + "."
            )
        reply_lines.append(
            "Rerun path: " + " -> ".join(SKILL_LABELS.get(step_id, step_id) for step_id in rerun_steps) + "."
        )
        return FeedbackOrchestrationResult(
            status="execute",
            reply="\n".join(reply_lines),
            notes=["Approved feedback approach is being applied to the article workflow."],
            directive=directive,
            feedback_rules=rules,
            rerun_steps=rerun_steps,
            skill_ids=skill_ids,
            save_scope=_parse_scope(prompt),
            save_requested=save_requested,
            save_instructions=save_instructions,
            summary=str(pending_context.get("feedback_summary") or "").strip(),
        )

    if not pending_context and not (
        _message_has_any(prompt, FEEDBACK_HINT_TERMS)
        or "below are some feedbacks" in _normalize_text(prompt)
        or "for this type of articles" in _normalize_text(prompt)
    ):
        return FeedbackOrchestrationResult()

    analysis = _analyze_feedback_request(prompt)
    if not analysis.get("is_feedback_request"):
        return FeedbackOrchestrationResult()

    feedback_summary = str(analysis.get("feedback_summary") or "").strip()
    feedback_rules = [str(rule).strip() for rule in (analysis.get("feedback_rules") or []) if str(rule).strip()]
    save_requested = bool(analysis.get("requests_skill_memory"))
    explicit_skill_ids = _extract_save_target_skill_ids(prompt) if save_requested else _extract_skill_ids(prompt)
    skill_ids = _normalize_steps(explicit_skill_ids or list(analysis.get("skill_targets") or []))
    rerun_steps = _default_rerun_steps(skill_ids, _extract_rerun_step_ids(prompt) or list(analysis.get("rerun_steps") or []))
    research_summary = ""
    research_markdown = ""
    research_metadata: dict = {}
    if analysis.get("requests_comparative_research"):
        research_summary, research_markdown, research_metadata = _summarize_research_patterns(
            query=str(analysis.get("research_query") or prompt).strip(),
            focus_rules=feedback_rules,
            default_target_country=default_target_country,
        )
    directive = _build_feedback_directive(feedback_summary, feedback_rules, research_summary)
    discuss_first = bool(analysis.get("discuss_before_execution"))
    wants_rerun = bool(analysis.get("requests_rerun"))

    if discuss_first or not wants_rerun:
        reply = _build_feedback_reply(
            summary=feedback_summary,
            rules=feedback_rules,
            research_summary=research_summary,
            rerun_steps=rerun_steps,
            skill_ids=skill_ids,
            explain_only=True,
            save_requested=save_requested,
            has_article_context=has_article_context,
        )
        notes = ["Prepared a feedback-led revision approach and paused for user confirmation."]
        if research_markdown.strip():
            notes.append("Comparable-article research was gathered to support the recommendation.")
        return FeedbackOrchestrationResult(
            status="answered",
            reply=reply,
            notes=notes,
            directive=directive,
            feedback_rules=feedback_rules,
            rerun_steps=rerun_steps,
            skill_ids=skill_ids,
            save_scope=_parse_scope(prompt),
            save_requested=save_requested,
            pending_confirmation=True,
            research_markdown=research_markdown,
            research_metadata=research_metadata,
            summary=feedback_summary,
        )

    if wants_rerun and not has_article_context:
        reply = (
            "I can help with that feedback and I can save the approach for future similar articles, but I do not have enough article context to rerun anything yet. "
            "Select the run that contains the draft/final article or paste the article into this thread first."
        )
        return FeedbackOrchestrationResult(
            status="clarify",
            reply=reply,
            notes=["Awaiting the current article before starting a feedback-driven rerun."],
            directive=directive,
            feedback_rules=feedback_rules,
            rerun_steps=rerun_steps,
            skill_ids=skill_ids,
            save_scope=_parse_scope(prompt),
            save_requested=save_requested,
            pending_confirmation=save_requested or bool(research_summary.strip()),
            research_markdown=research_markdown,
            research_metadata=research_metadata,
            summary=feedback_summary,
        )

    save_instructions = {}
    if save_requested and skill_ids:
        save_instructions = {skill_id: _skill_specific_feedback_instruction(skill_id, feedback_rules) for skill_id in skill_ids}
    reply_lines = ["I’ll apply that feedback to the article now."]
    if save_instructions:
        reply_lines.append(
            "I’ll also save the approach to: "
            + ", ".join(SKILL_LABELS.get(skill_id, skill_id) for skill_id in save_instructions)
            + "."
        )
    reply_lines.append("Rerun path: " + " -> ".join(SKILL_LABELS.get(step_id, step_id) for step_id in rerun_steps) + ".")
    return FeedbackOrchestrationResult(
        status="execute",
        reply="\n".join(reply_lines),
        notes=["Started a feedback-driven rerun for the relevant downstream stages."],
        directive=directive,
        feedback_rules=feedback_rules,
        rerun_steps=rerun_steps,
        skill_ids=skill_ids,
        save_scope=_parse_scope(prompt),
        save_requested=save_requested,
        save_instructions=save_instructions,
        research_markdown=research_markdown,
        research_metadata=research_metadata,
        summary=feedback_summary,
    )

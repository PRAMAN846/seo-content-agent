from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from threading import Thread
import time
from typing import Optional

from app.core.config import settings
from app.models.schemas import ContentAgentRunRecord, UserPublic, WorkspaceMessage
from app.models.store import run_store
from app.services.article_visuals import generate_article_images, inject_article_images
from app.services.content_agent_export import export_docx, export_images_zip, export_markdown
from app.services.content_feedback_orchestrator import (
    FeedbackOrchestrationResult,
    orchestrate_feedback_request,
)
from app.services.billing import (
    record_content_agent_follow_up_plan,
    record_content_agent_run_started,
    record_content_agent_step_completed,
    usage_scope,
)
from app.services.content_meta_router import ContentMetaRouteResult, answer_content_meta_request
from app.services.content_agent_research import build_research_packet
from app.services.content_studio import (
    DEFAULT_WORKFLOWS,
    answer_skill_meta_request_from_chat,
    generate_content_studio_reply,
    save_skill_update_from_chat,
    _normalize_skill_ids,
)
from app.services.llm_client import GenerationCancelled, llm_client

FULL_WORKFLOW_STEPS = [
    "content_brief",
    "content_writer",
    "content_editor",
    "internal_linking",
    "image_generation",
    "publish_qa",
]

STEP_LABELS = {
    "research": "Research",
    "content_brief": "Content Brief",
    "content_writer": "Content Writer",
    "content_editor": "Editorial Review",
    "internal_linking": "Internal Linking",
    "image_generation": "Image Generate & Review",
    "publish_qa": "Final Publish QA",
    "export": "Export",
}

STEP_ALIASES = {
    "content_brief": ["content brief", "brief", "serp", "outline", "strategy"],
    "content_writer": ["content writer", "writer", "write", "draft", "article", "blog"],
    "content_editor": ["content editor", "editorial review", "content review", "editor", "edit", "polish", "rewrite", "improve"],
    "internal_linking": ["internal linking", "internal link", "anchor text", "sitemap"],
    "image_generation": ["image generate", "image review", "image", "visual", "illustration", "diagram", "alt text"],
    "publish_qa": ["publish qa", "qa", "publish", "metadata", "final review", "final qa"],
}

STEP_ARTIFACT_TYPES = {
    "research": "research_packet",
    "content_brief": "brief",
    "content_writer": "draft",
    "content_editor": "edited_draft",
    "internal_linking": "linked_draft",
    "image_generation": "image_plan",
    "publish_qa": "publish_qa",
}

STEP_TITLES = {
    "research": "Collect research packet",
    "content_brief": "Generate informational brief",
    "content_writer": "Write first article draft",
    "content_editor": "Run editorial review",
    "internal_linking": "Add or review internal links",
    "image_generation": "Plan and generate article visuals",
    "publish_qa": "Run final publish QA",
    "export": "Export final output",
}

EXPORT_REQUEST_TERMS = ("export", "download", ".docx", ".md", "markdown", "word file", "docx", "zip")

LATER_STAGE_TERMS = ["full workflow", "full editorial workflow", "end to end", "end-to-end"]

PLANNER_INSTRUCTION = """You are the Content Agent planner for an informational-content workflow.
Return valid JSON only in this shape:
{
  "workflow_id": "full_editorial_workflow" | "draft_to_publish" | "brief_to_draft" | "custom_sequence" | null,
  "steps": ["content_brief" | "content_writer" | "content_editor" | "internal_linking" | "image_generation" | "publish_qa"],
  "stop_after_step": "content_brief" | "content_writer" | "content_editor" | "internal_linking" | "image_generation" | "publish_qa" | null,
  "requested_approval": true | false,
  "planning_notes": ["short note"]
}

Rules:
- Plan only informational-content work.
- Prefer these workflows when they match:
  - full_editorial_workflow = content_brief, content_writer, content_editor, internal_linking, image_generation, publish_qa
  - draft_to_publish = content_editor, internal_linking, image_generation, publish_qa
  - brief_to_draft = content_brief, content_writer
- If the prompt clearly asks for specific stages in a custom order, return custom_sequence.
- If the prompt asks to stop after a stage, set stop_after_step to that step.
- If the prompt asks for approval between stages, set requested_approval to true.
- Never return steps outside the allowed list.
- Keep planning_notes concise and factual.
"""


@dataclass
class AgentPlan:
    workflow_id: Optional[str]
    steps: list[str]
    stop_after_step: Optional[str]
    requested_approval: bool
    planning_notes: list[str]


def _normalize_text(value: str) -> str:
    return (value or "").strip().lower()


def _looks_like_missing_input_reply(text: str) -> bool:
    lowered = _normalize_text(text)
    return "still needed to start:" in lowered or (
        "required inputs:" in lowered and "helpful optional inputs:" in lowered
    )


def _artifact_title(prefix: str, goal: str) -> str:
    cleaned = (goal or "").strip()
    if not cleaned:
        return prefix
    return f"{prefix}: {cleaned[:80]}"


def _latest_artifact_content(artifacts: dict[str, str], *step_ids: str) -> str:
    for step_id in step_ids:
        content = (artifacts.get(step_id) or "").strip()
        if content:
            return content
    return ""


def _project_research_inputs(current_user: UserPublic, project_id: str) -> dict[str, Optional[str]]:
    project = run_store.get_visibility_project(current_user.id, project_id)
    if not project:
        return {
            "brand_name": None,
            "default_target_country": None,
            "product_page_urls": None,
            "approved_internal_urls": None,
            "sitemap_url": None,
        }
    return {
        "brand_name": project.brand_name,
        "default_target_country": project.default_target_country,
        "product_page_urls": project.product_page_urls,
        "approved_internal_urls": project.approved_internal_urls,
        "sitemap_url": project.sitemap_url,
    }


def _parse_stop_after_step(prompt: str) -> Optional[str]:
    lowered = _normalize_text(prompt)
    if "stop after" not in lowered:
        return None
    for step_id, aliases in STEP_ALIASES.items():
        if any(f"stop after {alias}" in lowered for alias in aliases):
            return step_id
    return None


def _requested_approval(prompt: str) -> bool:
    lowered = _normalize_text(prompt)
    return "approval between stages" in lowered or "approve between stages" in lowered or "approval" in lowered


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


def _infer_steps(prompt: str) -> list[str]:
    lowered = _normalize_text(prompt)
    ordered = _ordered_steps_from_prompt(prompt)
    if len(ordered) > 1:
        return ordered
    if any(term in lowered for term in LATER_STAGE_TERMS):
        return list(FULL_WORKFLOW_STEPS)
    if "draft to publish" in lowered:
        return ["content_editor", "internal_linking", "image_generation", "publish_qa"]
    if "brief to draft" in lowered:
        return ["content_brief", "content_writer"]
    if ordered:
        return ordered
    if any(term in lowered for term in ["edit", "polish", "rewrite", "tighten"]):
        return ["content_editor"]
    if any(term in lowered for term in ["internal link", "internal linking", "anchor text"]):
        return ["internal_linking"]
    if any(term in lowered for term in ["image", "visual", "illustration", "diagram", "alt text"]):
        return ["image_generation"]
    if any(term in lowered for term in ["qa", "publish", "metadata", "final review"]):
        return ["publish_qa"]
    if any(term in lowered for term in ["write", "draft", "article", "blog", "content"]):
        return ["content_brief", "content_writer"]
    return ["content_brief", "content_writer"]


def _workflow_id_for_steps(steps: list[str]) -> Optional[str]:
    for workflow in DEFAULT_WORKFLOWS:
        if workflow.skill_ids == steps:
            return workflow.id
    return "custom_sequence" if steps else None


def _heuristic_plan(prompt: str) -> AgentPlan:
    steps = _infer_steps(prompt)
    stop_after_step = _parse_stop_after_step(prompt)
    requested_approval = _requested_approval(prompt)
    planning_notes: list[str] = []
    if stop_after_step and stop_after_step in steps:
        planning_notes.append(f"Run should stop after {STEP_LABELS.get(stop_after_step, stop_after_step)}.")
    if requested_approval:
        planning_notes.append("User requested approval checkpoints between stages.")
    workflow_id = _workflow_id_for_steps(steps)
    return AgentPlan(
        workflow_id=workflow_id,
        steps=steps,
        stop_after_step=stop_after_step,
        requested_approval=requested_approval,
        planning_notes=planning_notes,
    )


def _sanitize_planner_steps(raw_steps: object, prompt: str) -> list[str]:
    cleaned: list[str] = []
    for item in raw_steps or []:
        step_id = str(item or "").strip()
        if step_id in FULL_WORKFLOW_STEPS and step_id not in cleaned:
            cleaned.append(step_id)
    return cleaned or _infer_steps(prompt)


def _sanitize_stop_after_step(raw_step: object, steps: list[str]) -> Optional[str]:
    step_id = str(raw_step or "").strip()
    return step_id if step_id in steps else None


def _plan_with_orchestrator(prompt: str) -> AgentPlan:
    heuristic = _heuristic_plan(prompt)
    if not llm_client.enabled:
        return heuristic
    try:
        payload = llm_client.complete_json(
            model=settings.orchestrator_model,
            instruction=PLANNER_INSTRUCTION,
            input_text=prompt,
            reasoning_effort=settings.orchestrator_reasoning_effort,
        )
    except Exception:
        return heuristic

    steps = _sanitize_planner_steps(payload.get("steps"), prompt)
    workflow_id = payload.get("workflow_id")
    if workflow_id not in {"full_editorial_workflow", "draft_to_publish", "brief_to_draft", "custom_sequence", None}:
        workflow_id = None
    workflow_id = workflow_id or _workflow_id_for_steps(steps)
    stop_after_step = _sanitize_stop_after_step(payload.get("stop_after_step"), steps)
    requested_approval = bool(payload.get("requested_approval"))
    planning_notes = [str(note).strip() for note in (payload.get("planning_notes") or []) if str(note).strip()]
    if not planning_notes:
        planning_notes = list(heuristic.planning_notes)
    planning_notes.append(f"Planner model: {settings.orchestrator_model}")
    return AgentPlan(
        workflow_id=workflow_id,
        steps=steps,
        stop_after_step=stop_after_step,
        requested_approval=requested_approval,
        planning_notes=planning_notes,
    )


def _plan_from_run(run: ContentAgentRunRecord) -> AgentPlan:
    plan_step = next((step for step in reversed(run.steps) if step.step_type == "plan"), None)
    if not plan_step:
        return AgentPlan(
            workflow_id=run.selected_workflow_id,
            steps=[],
            stop_after_step=None,
            requested_approval=False,
            planning_notes=[],
        )
    output = plan_step.output_json or {}
    return AgentPlan(
        workflow_id=output.get("workflow_id") or run.selected_workflow_id,
        steps=list(output.get("planned_steps") or []),
        stop_after_step=output.get("stop_after_step"),
        requested_approval=bool(output.get("requested_approval")),
        planning_notes=list(output.get("planning_notes") or []),
    )


def _completed_primary_steps(run: ContentAgentRunRecord) -> list[str]:
    return [
        step.step_type
        for step in run.steps
        if step.step_type in FULL_WORKFLOW_STEPS and step.status == "completed"
    ]


def _artifacts_by_step(run: ContentAgentRunRecord) -> dict[str, str]:
    artifacts: dict[str, str] = {}
    for artifact in run.artifacts:
        step_id = (artifact.metadata_json or {}).get("step")
        if artifact.artifact_type == "note":
            continue
        target_key = step_id
        if artifact.artifact_type == "illustrated_draft":
            target_key = "illustrated_draft"
        elif artifact.artifact_type == "final_article":
            target_key = "final_article"
        if target_key and target_key not in artifacts:
            artifacts[target_key] = artifact.content_markdown or ""
    return artifacts


def _latest_run_images(run: ContentAgentRunRecord) -> list[dict[str, str]]:
    images: list[dict[str, str]] = []
    for artifact in run.artifacts:
        if artifact.artifact_type != "image":
            continue
        metadata = artifact.metadata_json or {}
        images.append(
            {
                "title": artifact.title,
                "alt_text": str(metadata.get("alt_text") or ""),
                "prompt": str(metadata.get("prompt") or ""),
                "public_url": str(metadata.get("public_url") or ""),
                "local_path": str(metadata.get("local_path") or ""),
            }
        )
    return images


def _recent_skill_ids_from_run(run: ContentAgentRunRecord) -> list[str]:
    for artifact in run.artifacts:
        metadata = artifact.metadata_json or {}
        skill_ids = _normalize_skill_ids(metadata.get("skill_ids") or [])
        if skill_ids:
            return skill_ids
    for step in reversed(run.steps):
        if step.skill_id:
            return _normalize_skill_ids([step.skill_id])
        if step.step_type in FULL_WORKFLOW_STEPS:
            return _normalize_skill_ids([step.step_type])
    return []


def _pending_skill_update_context_from_run(run: ContentAgentRunRecord) -> tuple[bool, list[str], Optional[str]]:
    for artifact in run.artifacts:
        if artifact.artifact_type != "note":
            continue
        metadata = artifact.metadata_json or {}
        if metadata.get("skill_update_saved"):
            return False, [], None
        if metadata.get("skill_update_pending"):
            return True, _normalize_skill_ids(metadata.get("skill_ids") or []), str(metadata.get("scope") or "project")
    return False, [], None


def _pending_feedback_context_from_run(run: ContentAgentRunRecord) -> Optional[dict]:
    for artifact in run.artifacts:
        if artifact.artifact_type != "note":
            continue
        metadata = artifact.metadata_json or {}
        if metadata.get("feedback_pending_resolved"):
            return None
        if metadata.get("feedback_pending"):
            return metadata
    return None


def _latest_exportable_article(run: ContentAgentRunRecord, artifacts: dict[str, str]) -> str:
    return _latest_artifact_content(
        artifacts,
        "final_article",
        "illustrated_draft",
        "internal_linking",
        "content_editor",
        "content_writer",
    )


def _is_export_request(prompt: str) -> bool:
    lowered = _normalize_text(prompt)
    return any(term in lowered for term in EXPORT_REQUEST_TERMS)


def _requested_export_format(prompt: str) -> str:
    lowered = _normalize_text(prompt)
    if "zip" in lowered or "images" in lowered:
        return "images_zip"
    if ".docx" in lowered or "docx" in lowered or "word" in lowered:
        return "docx"
    return "markdown"


def _next_step_index(plan: AgentPlan, run: ContentAgentRunRecord) -> int:
    completed = _completed_primary_steps(run)
    for index, step_id in enumerate(plan.steps):
        if step_id not in completed:
            return index
    return len(plan.steps)


def _latest_approval_next_step(run: ContentAgentRunRecord) -> Optional[str]:
    for artifact in run.artifacts:
        metadata = artifact.metadata_json or {}
        if artifact.artifact_type == "note" and metadata.get("approval_required") and metadata.get("next_step"):
            return str(metadata.get("next_step"))
    return None


def _step_prompt(step_id: str, goal: str, artifacts: dict[str, str], directive: str = "") -> str:
    research_packet = _latest_artifact_content(artifacts, "research")
    brief = _latest_artifact_content(artifacts, "content_brief")
    draft = _latest_artifact_content(artifacts, "illustrated_draft", "internal_linking", "content_editor", "content_writer")
    edited_draft = _latest_artifact_content(artifacts, "illustrated_draft", "internal_linking", "content_editor")
    image_plan = _latest_artifact_content(artifacts, "image_generation")
    directive_block = f"\n\nAdditional user directive for this pass:\n{directive.strip()}" if directive.strip() else ""

    if step_id == "content_brief":
        base = "Create only the informational content brief from the request below. Do not coordinate later workflow stages in the answer.\n\n"
        body = f"{goal}\n\nUse this research packet where useful and cite it carefully:\n\n{research_packet}" if research_packet else goal
        return f"{base}{body}{directive_block}"
    if step_id == "content_writer":
        if brief:
            base = (
                "Using the approved informational brief below, write the full publish-ready informational article draft.\n\n"
                f"{brief}"
            )
            return (f"{base}\n\nUse this research packet where useful:\n\n{research_packet}" if research_packet else base) + directive_block
        fallback = f"{goal}\n\nUse this research packet where useful:\n\n{research_packet}" if research_packet else goal
        return f"Write the article draft from the available request details below.{directive_block}\n\n{fallback}"
    if step_id == "content_editor":
        source_draft = draft or goal
        return (
            "Edit the informational article draft below. Improve clarity, structure, flow, and naturalness without changing the core article promise.\n\n"
            f"{source_draft}"
        ) + directive_block
    if step_id == "internal_linking":
        source_draft = edited_draft or draft or goal
        return (
            "Review the article below for internal linking. Use only approved project URLs or the configured sitemap, never invent links, and return the article with safe internal linking improvements plus short notes.\n\n"
            f"{source_draft}"
        ) + directive_block
    if step_id == "image_generation":
        source_draft = edited_draft or draft or goal
        return (
            "Generate and review the highest-value editorial visuals for the informational article below. Recommend only visuals that materially improve clarity and provide concrete image prompts.\n\n"
            f"{source_draft}"
        ) + directive_block
    if step_id == "publish_qa":
        source_draft = _latest_artifact_content(artifacts, "illustrated_draft", "internal_linking", "content_editor", "content_writer") or goal
        extra = f"\n\nSuggested visuals context:\n{image_plan}" if image_plan else ""
        return (
            "Run a final publish QA pass on the article package below. If metadata inputs are missing, create clearly labelled provisional metadata so the QA can still be actionable.\n\n"
            f"{source_draft}{extra}"
        ) + directive_block
    return goal + directive_block


def _render_agent_image_markdown(title: str, alt_text: str, public_url: str) -> str:
    caption = title.strip() or alt_text.strip() or "Generated image"
    return f"![{alt_text}]({public_url})\n*{caption}*"


def _image_plan_markdown(images: list[dict[str, str]]) -> str:
    if not images:
        return "No article visuals were generated for this pass."
    lines = ["# Image plan", ""]
    for index, image in enumerate(images, start=1):
        lines.extend(
            [
                f"## Visual {index}: {image['title']}",
                f"- Placement: {image.get('placement') or 'inline'}",
                f"- Section heading: {image.get('section_heading') or 'not specified'}",
                f"- Alt text: {image['alt_text']}",
                f"- Download: [Open image]({image['public_url']})",
                "",
                "Prompt:",
                image["prompt"],
                "",
            ]
        )
    return "\n".join(lines).strip()


def _collect_agent_image_payloads(
    *,
    current_user: UserPublic,
    run: ContentAgentRunRecord,
    source_draft: str,
    brief: str,
) -> list[dict[str, str]]:
    project = run_store.get_visibility_project(current_user.id, run.project_id)
    assets = generate_article_images(
        query=run.goal,
        brief_markdown=brief or run.goal,
        article_markdown=source_draft,
        brand_name=project.brand_name if project else "",
    )
    return [
        {
            "title": asset.title,
            "alt_text": asset.alt_text,
            "prompt": asset.prompt,
            "revised_prompt": asset.revised_prompt,
            "public_url": asset.public_url,
            "local_path": asset.local_path,
            "placement": asset.placement,
            "section_heading": asset.section_heading,
            "markdown": _render_agent_image_markdown(asset.title, asset.alt_text, asset.public_url),
        }
        for asset in assets
    ]


def _progress_for_step(index: int, total_steps: int) -> int:
    if total_steps <= 0:
        return 100
    return min(95, int(((index + 1) / total_steps) * 90))


def _refresh_run(user_id: str, run_id: str) -> Optional[ContentAgentRunRecord]:
    return run_store.get_content_agent_run(user_id, run_id)


def _run_cancel_requested(user_id: str, run_id: str) -> bool:
    run = _refresh_run(user_id, run_id)
    return bool(run and run.status == "cancel_requested")


def _cancel_run_now(
    *,
    current_user: UserPublic,
    run: ContentAgentRunRecord,
    step_id: Optional[str] = None,
    reason: str = "Run cancelled by user.",
    partial_output: str = "",
) -> ContentAgentRunRecord:
    if partial_output.strip():
        run_store.create_content_agent_artifact(
            current_user.id,
            run_id=run.id,
            project_id=run.project_id,
            artifact_type="note",
            title=_artifact_title("Partial output before stop", run.goal),
            content_markdown=partial_output,
            metadata_json={"step": step_id or "unknown", "partial": True},
        )
    run_store.create_content_agent_artifact(
        current_user.id,
        run_id=run.id,
        project_id=run.project_id,
        artifact_type="note",
        title=_artifact_title("Run stopped", run.goal),
        content_markdown=reason,
        metadata_json={"step": step_id or "unknown", "cancelled": True},
    )
    updated = run_store.update_content_agent_run(
        current_user.id,
        run.id,
        status="cancelled",
        stage="cancelled",
        current_step_title="Run stopped",
        error=None,
    )
    if not updated:
        raise RuntimeError("Content Agent run could not be cancelled")
    return updated


def _fail_run(user_id: str, run_id: str, error: Exception) -> Optional[ContentAgentRunRecord]:
    return run_store.update_content_agent_run(
        user_id,
        run_id,
        status="failed",
        stage="failed",
        current_step_title="Run failed",
        error=str(error),
    )


def _create_final_article_artifact(
    *,
    current_user: UserPublic,
    run: ContentAgentRunRecord,
    artifacts: dict[str, str],
) -> None:
    final_article = _latest_exportable_article(run, artifacts)
    if not final_article.strip():
        return
    run_store.create_content_agent_artifact(
        current_user.id,
        run_id=run.id,
        project_id=run.project_id,
        artifact_type="final_article",
        title=_artifact_title("Final article", run.goal),
        content_markdown=final_article,
        metadata_json={"step": "finalize", "export_formats": ["markdown", "docx", "images_zip"]},
    )


def _export_run_output(
    *,
    current_user: UserPublic,
    run: ContentAgentRunRecord,
    export_format: str,
) -> ContentAgentRunRecord:
    artifacts = _artifacts_by_step(run)
    final_article = _latest_exportable_article(run, artifacts)
    if not final_article.strip():
        raise ValueError("This run does not have a final article to export yet")

    image_payloads = _latest_run_images(run)
    if export_format == "docx":
        export = export_docx(run.goal, final_article)
        label = "Word (.docx)"
    elif export_format == "images_zip":
        export = export_images_zip(run.goal, image_payloads)
        if not export:
            raise ValueError("This run does not have generated images to download yet")
        label = "Images (.zip)"
    else:
        export = export_markdown(run.goal, final_article)
        label = "Markdown (.md)"

    run_store.create_content_agent_step(
        current_user.id,
        run_id=run.id,
        project_id=run.project_id,
        step_type="export",
        title=f"Export {label}",
        status="completed",
        input_json={"format": export_format},
        output_json={"public_url": export["public_url"], "filename": export["filename"]},
        started_at=datetime.utcnow(),
        completed_at=datetime.utcnow(),
    )
    run_store.create_content_agent_artifact(
        current_user.id,
        run_id=run.id,
        project_id=run.project_id,
        artifact_type="export",
        title=_artifact_title(f"{label} export", run.goal),
        content_markdown=f"[Download {label}]({export['public_url']})",
        metadata_json={
            "step": "export",
            "format": export_format,
            "public_url": export["public_url"],
            "filename": export["filename"],
        },
    )
    updated = run_store.update_content_agent_run(
        current_user.id,
        run.id,
        current_step_title=f"Export ready: {label}",
        error=None,
    )
    return updated or (run_store.get_content_agent_run(current_user.id, run.id) or run)


def _finalize_run(
    *,
    current_user: UserPublic,
    run_id: str,
    project_id: str,
    workflow_id: Optional[str],
    steps: list[str],
    run: Optional[ContentAgentRunRecord] = None,
    artifacts: Optional[dict[str, str]] = None,
) -> ContentAgentRunRecord:
    if run is not None and artifacts is not None:
        _create_final_article_artifact(current_user=current_user, run=run, artifacts=artifacts)
    run_store.create_content_agent_step(
        current_user.id,
        run_id=run_id,
        project_id=project_id,
        step_type="finalize",
        title="Finalize Content Agent run",
        status="completed",
        input_json={},
        output_json={"result": "completed", "workflow_id": workflow_id, "steps": steps},
    )
    updated = run_store.update_content_agent_run(
        current_user.id,
        run_id,
        status="completed",
        stage="completed",
        progress_percent=100,
        current_step_title="Run completed",
        error=None,
    )
    if not updated:
        raise RuntimeError("Content Agent run could not be finalized")
    return updated


def _record_skill_update_interaction(
    *,
    current_user: UserPublic,
    run: ContentAgentRunRecord,
    prompt: str,
    result,
    follow_up: bool = False,
) -> ContentAgentRunRecord:
    primary_skill_id = result.skill_ids[0] if result.skill_ids else None
    skill_name = STEP_LABELS.get(primary_skill_id or "", None)
    if primary_skill_id == "content_brief":
        skill_name = "Content Brief"
    elif primary_skill_id == "content_writer":
        skill_name = "Content Writer"
    elif primary_skill_id == "content_editor":
        skill_name = "Editorial Review"
    elif primary_skill_id == "internal_linking":
        skill_name = "Internal Linking"
    elif primary_skill_id == "image_generation":
        skill_name = "Image Generate & Review"
    elif primary_skill_id == "publish_qa":
        skill_name = "Final Publish QA"
    skill_suffix = f" / {skill_name}" if skill_name else ""
    is_clarification = result.status == "clarify"
    is_answer = result.status == "answered"
    if is_answer:
        step_title = f"Answer skill question{skill_suffix}"
        current_step_title = "Shared current skill instructions"
        artifact_title = f"Skill instructions{skill_suffix}"
    elif is_clarification:
        step_title = f"Clarify skill update request{skill_suffix}"
        current_step_title = "Waiting for skill update details"
        artifact_title = f"Skill update clarification{skill_suffix}"
    else:
        step_title = f"Capture skill update{skill_suffix}"
        current_step_title = "Skill update saved"
        artifact_title = f"Skill update saved{skill_suffix}"
    run_store.create_content_agent_step(
        current_user.id,
        run_id=run.id,
        project_id=run.project_id,
        step_type="plan",
        title=step_title,
        status="completed",
        input_json={"prompt": prompt, "follow_up": follow_up, "skill_update": True},
        output_json={
            "skill_update": True,
            "status": result.status,
            "skill_ids": result.skill_ids,
            "notes": result.notes,
            "scope": result.scope,
        },
    )
    run_store.create_content_agent_artifact(
        current_user.id,
        run_id=run.id,
        project_id=run.project_id,
        artifact_type="note",
        title=artifact_title,
        content_markdown=result.reply or "",
        metadata_json={
            "step": "plan",
            "skill_update_pending": is_clarification,
            "skill_update_saved": result.status == "saved",
            "skill_ids": result.skill_ids,
            "scope": result.scope,
        },
    )
    updated = run_store.update_content_agent_run(
        current_user.id,
        run.id,
        status="completed",
        stage="completed",
        progress_percent=100,
        current_step_title=current_step_title,
        error=None,
    )
    if not updated:
        raise RuntimeError("Content Agent run could not be updated with the skill update result")
    return updated


def _record_meta_reply_interaction(
    *,
    current_user: UserPublic,
    run: ContentAgentRunRecord,
    prompt: str,
    result: ContentMetaRouteResult,
    follow_up: bool = False,
) -> ContentAgentRunRecord:
    if result.kind == "run_question":
        step_title = "Answer question about this run"
        current_step_title = "Answered run question"
        artifact_title = "Run explanation"
    elif result.kind == "mixed_request":
        step_title = "Clarify mixed request"
        current_step_title = "Waiting for clarification"
        artifact_title = "Clarification needed"
    else:
        step_title = "Answer capability or settings question"
        current_step_title = "Answered question"
        artifact_title = "Agent reply"
    run_store.create_content_agent_step(
        current_user.id,
        run_id=run.id,
        project_id=run.project_id,
        step_type="plan",
        title=step_title,
        status="completed",
        input_json={"prompt": prompt, "follow_up": follow_up, "meta_reply": True},
        output_json={
            "meta_reply": True,
            "kind": result.kind,
            "status": result.status,
            "notes": result.notes,
        },
    )
    run_store.create_content_agent_artifact(
        current_user.id,
        run_id=run.id,
        project_id=run.project_id,
        artifact_type="note",
        title=artifact_title,
        content_markdown=result.reply or "",
        metadata_json={
            "step": "plan",
            "meta_reply": True,
            "meta_kind": result.kind,
            "clarification_required": result.status == "clarify",
        },
    )
    updated = run_store.update_content_agent_run(
        current_user.id,
        run.id,
        status="completed",
        stage="completed",
        progress_percent=100,
        current_step_title=current_step_title,
        error=None,
    )
    if not updated:
        raise RuntimeError("Content Agent run could not be updated with the meta reply")
    return updated


def _record_feedback_reply_interaction(
    *,
    current_user: UserPublic,
    run: ContentAgentRunRecord,
    prompt: str,
    result: FeedbackOrchestrationResult,
    follow_up: bool = False,
) -> ContentAgentRunRecord:
    is_clarification = result.status == "clarify"
    pending_confirmation = bool(result.pending_confirmation)
    if is_clarification:
        step_title = "Clarify feedback request"
        current_step_title = "Waiting for feedback clarification"
        artifact_title = "Feedback clarification"
    elif pending_confirmation:
        step_title = "Analyze revision feedback"
        current_step_title = "Waiting for feedback confirmation"
        artifact_title = "Suggested revision approach"
    else:
        step_title = "Answer feedback question"
        current_step_title = "Answered feedback question"
        artifact_title = "Feedback answer"

    run_store.create_content_agent_step(
        current_user.id,
        run_id=run.id,
        project_id=run.project_id,
        step_type="plan",
        title=step_title,
        status="completed",
        input_json={"prompt": prompt, "follow_up": follow_up, "feedback_reply": True},
        output_json={
            "feedback_reply": True,
            "status": result.status,
            "pending_confirmation": pending_confirmation,
            "notes": result.notes,
            "rerun_steps": result.rerun_steps,
            "skill_ids": result.skill_ids,
            "save_requested": result.save_requested,
        },
    )
    run_store.create_content_agent_artifact(
        current_user.id,
        run_id=run.id,
        project_id=run.project_id,
        artifact_type="note",
        title=artifact_title,
        content_markdown=result.reply or "",
        metadata_json={
            "step": "plan",
            "feedback_reply": True,
            "feedback_pending": pending_confirmation,
            "feedback_summary": result.summary,
            "feedback_rules": result.feedback_rules,
            "directive": result.directive,
            "rerun_steps": result.rerun_steps,
            "skill_ids": result.skill_ids,
            "scope": result.save_scope,
            "save_requested": result.save_requested,
        },
    )
    if result.research_markdown.strip():
        run_store.create_content_agent_artifact(
            current_user.id,
            run_id=run.id,
            project_id=run.project_id,
            artifact_type="research_packet",
            title=_artifact_title("Comparable article patterns", prompt),
            content_markdown=result.research_markdown,
            metadata_json={"step": "plan", "feedback_research": True, **result.research_metadata},
        )
    updated = run_store.update_content_agent_run(
        current_user.id,
        run.id,
        status="completed",
        stage="completed",
        progress_percent=100,
        current_step_title=current_step_title,
        error=None,
    )
    if not updated:
        raise RuntimeError("Content Agent run could not be updated with the feedback reply")
    return updated


def _execute_feedback_follow_up(
    *,
    current_user: UserPublic,
    run: ContentAgentRunRecord,
    prompt: str,
    result: FeedbackOrchestrationResult,
) -> ContentAgentRunRecord:
    saved_skill_ids: list[str] = []
    for skill_id, instruction in (result.save_instructions or {}).items():
        run_store.create_content_skill_override(
            current_user.id,
            project_id=run.project_id,
            skill_id=skill_id,
            instruction=instruction,
            scope=result.save_scope or "project",
        )
        saved_skill_ids.append(skill_id)

    if result.research_markdown.strip():
        run_store.create_content_agent_artifact(
            current_user.id,
            run_id=run.id,
            project_id=run.project_id,
            artifact_type="research_packet",
            title=_artifact_title("Comparable article patterns", prompt),
            content_markdown=result.research_markdown,
            metadata_json={"step": "plan", "feedback_research": True, **result.research_metadata},
        )

    run_store.create_content_agent_artifact(
        current_user.id,
        run_id=run.id,
        project_id=run.project_id,
        artifact_type="note",
        title=_artifact_title("Approved feedback approach", prompt),
        content_markdown=result.reply or "",
        metadata_json={
            "step": "plan",
            "feedback_execution": True,
            "feedback_pending_resolved": True,
            "skill_ids": result.skill_ids,
            "saved_skill_ids": saved_skill_ids,
            "rerun_steps": result.rerun_steps,
            "directive": result.directive,
        },
    )

    if not result.rerun_steps:
        updated = run_store.update_content_agent_run(
            current_user.id,
            run.id,
            status="completed",
            stage="completed",
            progress_percent=100,
            current_step_title="Saved the approved feedback approach",
            error=None,
        )
        if not updated:
            raise RuntimeError("Content Agent run could not be updated after saving feedback")
        return updated

    workflow_id = _workflow_id_for_steps(result.rerun_steps)
    planning_notes = ["Feedback-driven rerun approved from this thread."]
    if saved_skill_ids:
        labels = ", ".join(STEP_LABELS.get(skill_id, skill_id) for skill_id in saved_skill_ids)
        planning_notes.append(f"Saved reusable guidance to: {labels}.")
    run_store.update_content_agent_run(
        current_user.id,
        run.id,
        status="running",
        stage="planning",
        progress_percent=8,
        current_step_title="Planning feedback revision",
        error=None,
    )
    run_store.create_content_agent_step(
        current_user.id,
        run_id=run.id,
        project_id=run.project_id,
        step_type="plan",
        title="Plan feedback-driven revision",
        status="completed",
        input_json={"prompt": prompt, "follow_up": True, "feedback_execution": True},
        output_json={
            "feedback_execution": True,
            "workflow_id": workflow_id,
            "planned_steps": result.rerun_steps,
            "planning_notes": planning_notes,
            "requested_approval": False,
            "stop_after_step": None,
            "planner_model": "feedback_orchestrator",
            "execution_model": settings.writer_model,
        },
    )
    plan = AgentPlan(
        workflow_id=workflow_id,
        steps=result.rerun_steps,
        stop_after_step=None,
        requested_approval=False,
        planning_notes=planning_notes,
    )
    refreshed = run_store.get_content_agent_run(current_user.id, run.id) or run
    _launch_content_agent_worker(
        current_user=current_user,
        run_id=refreshed.id,
        plan=plan,
        start_index=0,
        directive=result.directive,
    )
    return refreshed


def _launch_content_agent_worker(
    *,
    current_user: UserPublic,
    run_id: str,
    plan: AgentPlan,
    start_index: int,
    approval_note: str = "",
    directive: str = "",
) -> None:
    def runner() -> None:
        run = run_store.get_content_agent_run(current_user.id, run_id)
        if not run:
            return
        try:
            _execute_run_from_index(
                current_user=current_user,
                run=run,
                plan=plan,
                start_index=start_index,
                approval_note=approval_note,
                directive=directive,
            )
        except Exception as exc:  # noqa: BLE001
            _fail_run(current_user.id, run_id, exc)

    Thread(target=runner, daemon=True).start()


def _execute_run_from_index(
    *,
    current_user: UserPublic,
    run: ContentAgentRunRecord,
    plan: AgentPlan,
    start_index: int,
    approval_note: str = "",
    directive: str = "",
) -> ContentAgentRunRecord:
    artifacts = _artifacts_by_step(run)
    total_steps = len(plan.steps)

    if start_index == 0 and "research" not in artifacts:
        research_inputs = _project_research_inputs(current_user, run.project_id)
        research_markdown, research_metadata = build_research_packet(
            prompt=run.goal,
            brand_name=research_inputs["brand_name"],
            default_target_country=research_inputs["default_target_country"],
            product_page_urls=research_inputs["product_page_urls"],
            approved_internal_urls=research_inputs["approved_internal_urls"],
            sitemap_url=research_inputs["sitemap_url"],
        )
        if research_metadata.get("urls"):
            run_store.update_content_agent_run(
                current_user.id,
                run.id,
                status="running",
                stage="research",
                progress_percent=12,
                current_step_title=STEP_TITLES["research"],
                error=None,
            )
            run_store.create_content_agent_step(
                current_user.id,
                run_id=run.id,
                project_id=run.project_id,
                step_type="research",
                title=STEP_TITLES["research"],
                status="completed",
                input_json={"goal": run.goal, "urls": research_metadata.get("urls", [])},
                output_json=research_metadata,
            )
            run_store.create_content_agent_artifact(
                current_user.id,
                run_id=run.id,
                project_id=run.project_id,
                artifact_type="research_packet",
                title=_artifact_title("Research packet", run.goal),
                content_markdown=research_markdown,
                metadata_json={"step": "research", **research_metadata},
            )
            artifacts["research"] = research_markdown

    if approval_note:
        run_store.create_content_agent_step(
            current_user.id,
            run_id=run.id,
            project_id=run.project_id,
            step_type="approval",
            title="Approval granted to continue",
            status="completed",
            input_json={"note": approval_note},
            output_json={"approved": True},
        )

    for index in range(start_index, total_steps):
        if _run_cancel_requested(current_user.id, run.id):
            return _cancel_run_now(
                current_user=current_user,
                run=run,
                step_id=plan.steps[index] if index < len(plan.steps) else None,
                reason="Run stopped before the next stage started.",
            )
        step_id = plan.steps[index]
        run_store.update_content_agent_run(
            current_user.id,
            run.id,
            status="running",
            stage=step_id,
            progress_percent=_progress_for_step(index, total_steps),
            current_step_title=STEP_TITLES.get(step_id, step_id),
            error=None,
        )
        step_prompt = _step_prompt(step_id, run.goal, artifacts, directive)
        step_record = run_store.create_content_agent_step(
            current_user.id,
            run_id=run.id,
            project_id=run.project_id,
            step_type=step_id,
            skill_id=step_id,
            title=STEP_TITLES.get(step_id, step_id),
            status="running",
            input_json={"prompt": step_prompt},
            output_json={"stream_text": "", "model": settings.writer_model},
            started_at=datetime.utcnow(),
        )

        last_stream_text = ""
        last_stream_flush = 0.0

        def should_stop_generation() -> bool:
            return _run_cancel_requested(current_user.id, run.id)

        def handle_stream_text(text: str) -> None:
            nonlocal last_stream_text, last_stream_flush
            snapshot = str(text or "")
            if not snapshot:
                return
            now = time.monotonic()
            if snapshot == last_stream_text:
                return
            if (now - last_stream_flush) < 0.2 and (len(snapshot) - len(last_stream_text)) < 80:
                return
            last_stream_text = snapshot
            last_stream_flush = now
            run_store.update_content_agent_step(
                current_user.id,
                step_record.id,
                output_json={"stream_text": snapshot, "model": settings.writer_model},
            )

        try:
            with usage_scope(
                user_id=current_user.id,
                workspace_id=current_user.id,
                project_id=run.project_id,
                feature="content_agent",
                reference_type="content_agent_run",
                reference_id=run.id,
                metadata={"step_id": step_id},
            ):
                payload = generate_content_studio_reply(
                    current_user=current_user,
                    project_id=run.project_id,
                    messages=[WorkspaceMessage(role="user", content=step_prompt)],
                    selected_skill_ids=[step_id],
                    workflow_id=None,
                    stream_callback=handle_stream_text,
                    should_stop_callback=should_stop_generation,
                    direct_skill_mode=False,
                )
        except GenerationCancelled:
            run_store.update_content_agent_step(
                current_user.id,
                step_record.id,
                status="cancelled",
                output_json={"stream_text": last_stream_text, "model": settings.writer_model},
                completed_at=datetime.utcnow(),
            )
            return _cancel_run_now(
                current_user=current_user,
                run=run,
                step_id=step_id,
                reason=f"Run stopped while {STEP_LABELS.get(step_id, step_id)} was generating.",
                partial_output=last_stream_text,
            )
        run_store.update_content_agent_step(
            current_user.id,
            step_record.id,
            status="completed",
            output_json={
                "stream_text": payload.reply,
                "reply": payload.reply,
                "notes": payload.notes,
                "model": settings.writer_model,
            },
            completed_at=datetime.utcnow(),
        )

        if _run_cancel_requested(current_user.id, run.id):
            return _cancel_run_now(
                current_user=current_user,
                run=run,
                step_id=step_id,
                reason=f"Run stopped after {STEP_LABELS.get(step_id, step_id)} completed.",
            )

        if _looks_like_missing_input_reply(payload.reply):
            run_store.create_content_agent_artifact(
                current_user.id,
                run_id=run.id,
                project_id=run.project_id,
                artifact_type="note",
                title=_artifact_title(f"Missing inputs for {STEP_LABELS.get(step_id, step_id)}", run.goal),
                content_markdown=payload.reply,
                metadata_json={"step": step_id, "notes": payload.notes},
            )
            run_store.create_content_agent_step(
                current_user.id,
                run_id=run.id,
                project_id=run.project_id,
                step_type="finalize",
                title="Finish after collecting missing inputs",
                status="completed",
                input_json={},
                output_json={"result": "awaiting_inputs", "step": step_id},
            )
            updated = run_store.update_content_agent_run(
                current_user.id,
                run.id,
                status="completed",
                stage="awaiting_inputs",
                progress_percent=100,
                current_step_title=f"Waiting for missing inputs before {STEP_LABELS.get(step_id, step_id)}",
                error=None,
            )
            if not updated:
                raise RuntimeError("Content Agent run could not be finalized")
            return updated

        artifacts[step_id] = payload.reply
        run_store.create_content_agent_artifact(
            current_user.id,
            run_id=run.id,
            project_id=run.project_id,
            artifact_type=STEP_ARTIFACT_TYPES[step_id],
            title=_artifact_title(STEP_LABELS.get(step_id, step_id), run.goal),
            content_markdown=payload.reply,
            metadata_json={"step": step_id, "notes": payload.notes, "model": settings.writer_model},
        )
        generated_image_count = 0
        if step_id == "image_generation":
            source_draft = _latest_artifact_content(artifacts, "internal_linking", "content_editor", "content_writer")
            source_brief = _latest_artifact_content(artifacts, "content_brief")
            try:
                with usage_scope(
                    user_id=current_user.id,
                    workspace_id=current_user.id,
                    project_id=run.project_id,
                    feature="content_agent",
                    reference_type="content_agent_run",
                    reference_id=run.id,
                    metadata={"step_id": step_id, "asset_type": "images"},
                ):
                    generated_images = _collect_agent_image_payloads(
                        current_user=current_user,
                        run=run,
                        source_draft=source_draft or payload.reply,
                        brief=source_brief,
                    )
                if generated_images:
                    generated_image_count = len(generated_images)
                    from app.models.schemas import ArticleImageAsset

                    illustrated_draft = inject_article_images(
                        source_draft or payload.reply,
                        [
                            ArticleImageAsset(
                                id=f"agent-image-{image['title']}-{idx}",
                                title=image["title"],
                                alt_text=image["alt_text"],
                                prompt=image["prompt"],
                                revised_prompt=image["revised_prompt"],
                                section_heading=image.get("section_heading", ""),
                                placement=image.get("placement", "inline"),
                                local_path=image["local_path"],
                                public_url=image["public_url"],
                            )
                            for idx, image in enumerate(generated_images, start=1)
                        ],
                    )
                    run_store.create_content_agent_artifact(
                        current_user.id,
                        run_id=run.id,
                        project_id=run.project_id,
                        artifact_type="illustrated_draft",
                        title=_artifact_title("Illustrated draft", run.goal),
                        content_markdown=illustrated_draft,
                        metadata_json={"step": step_id, "image_count": len(generated_images)},
                    )
                    artifacts["illustrated_draft"] = illustrated_draft
                    artifacts["image_generation"] = _image_plan_markdown(generated_images)
                for image in generated_images:
                    run_store.create_content_agent_artifact(
                        current_user.id,
                        run_id=run.id,
                        project_id=run.project_id,
                        artifact_type="image",
                        title=image["title"],
                        content_markdown=image["markdown"],
                        metadata_json={
                            "step": step_id,
                            "prompt": image["prompt"],
                            "revised_prompt": image["revised_prompt"],
                            "alt_text": image["alt_text"],
                            "public_url": image["public_url"],
                            "local_path": image["local_path"],
                            "placement": image.get("placement", "inline"),
                            "section_heading": image.get("section_heading", ""),
                        },
                    )
            except Exception as exc:  # noqa: BLE001
                run_store.create_content_agent_artifact(
                    current_user.id,
                    run_id=run.id,
                    project_id=run.project_id,
                    artifact_type="note",
                    title=_artifact_title("Image generation issue", run.goal),
                    content_markdown=f"Image generation could not complete: {exc}",
                    metadata_json={"step": step_id},
                )

        record_content_agent_step_completed(
            user_id=current_user.id,
            project_id=run.project_id,
            run_id=run.id,
            step_id=step_id,
            generated_images=generated_image_count,
        )

        if plan.stop_after_step == step_id:
            run_store.create_content_agent_artifact(
                current_user.id,
                run_id=run.id,
                project_id=run.project_id,
                artifact_type="note",
                title=_artifact_title("Run stopped after requested stage", run.goal),
                content_markdown=f"Run stopped after {STEP_LABELS.get(step_id, step_id)} because the prompt asked to stop there.",
                metadata_json={"step": step_id},
            )
            run_store.create_content_agent_step(
                current_user.id,
                run_id=run.id,
                project_id=run.project_id,
                step_type="finalize",
                title="Stop after requested stage",
                status="completed",
                input_json={},
                output_json={"result": "stopped_after_requested_stage", "step": step_id},
            )
            updated = run_store.update_content_agent_run(
                current_user.id,
                run.id,
                status="completed",
                stage=f"stopped_after_{step_id}",
                progress_percent=100,
                current_step_title=f"Stopped after {STEP_LABELS.get(step_id, step_id)}",
                error=None,
            )
            if not updated:
                raise RuntimeError("Content Agent run could not be finalized")
            return updated

        has_more_steps = index < total_steps - 1
        if plan.requested_approval and has_more_steps:
            next_step_id = plan.steps[index + 1]
            run_store.create_content_agent_artifact(
                current_user.id,
                run_id=run.id,
                project_id=run.project_id,
                artifact_type="note",
                title=_artifact_title("Approval required", run.goal),
                content_markdown=(
                    f"{STEP_LABELS.get(step_id, step_id)} is complete. "
                    f"Approve this run to continue to {STEP_LABELS.get(next_step_id, next_step_id)}."
                ),
                metadata_json={"step": step_id, "next_step": next_step_id, "approval_required": True},
            )
            updated = run_store.update_content_agent_run(
                current_user.id,
                run.id,
                status="awaiting_approval",
                stage="awaiting_approval",
                progress_percent=_progress_for_step(index, total_steps),
                current_step_title=f"Awaiting approval to continue to {STEP_LABELS.get(next_step_id, next_step_id)}",
                error=None,
            )
            if not updated:
                raise RuntimeError("Content Agent run could not be paused for approval")
            return updated

    return _finalize_run(
        current_user=current_user,
        run_id=run.id,
        project_id=run.project_id,
        workflow_id=plan.workflow_id,
        steps=plan.steps,
        run=run_store.get_content_agent_run(current_user.id, run.id) or run,
        artifacts=artifacts,
    )


def list_content_agent_runs(user_id: str, project_id: str):
    return run_store.list_content_agent_runs(user_id, project_id)


def get_content_agent_run(user_id: str, run_id: str) -> Optional[ContentAgentRunRecord]:
    return run_store.get_content_agent_run(user_id, run_id)


def export_content_agent_run(
    *,
    current_user: UserPublic,
    run_id: str,
    export_format: str,
) -> ContentAgentRunRecord:
    run = run_store.get_content_agent_run(current_user.id, run_id)
    if not run:
        raise ValueError("Content Agent run not found")
    return _export_run_output(
        current_user=current_user,
        run=run,
        export_format=export_format,
    )


def cancel_content_agent_run(
    *,
    current_user: UserPublic,
    run_id: str,
) -> ContentAgentRunRecord:
    run = run_store.get_content_agent_run(current_user.id, run_id)
    if not run:
        raise ValueError("Content Agent run not found")
    if run.status in {"completed", "failed", "cancelled"}:
        raise ValueError("This run has already finished")
    if run.status == "cancel_requested":
        return run
    if run.status == "awaiting_approval":
        return _cancel_run_now(
            current_user=current_user,
            run=run,
            step_id=run.stage,
            reason="Run stopped while it was waiting for approval.",
        )
    updated = run_store.update_content_agent_run(
        current_user.id,
        run.id,
        status="cancel_requested",
        stage="cancel_requested",
        current_step_title="Stopping run...",
        error=None,
    )
    if not updated:
        raise RuntimeError("Content Agent run could not be stopped")
    return updated


def start_content_agent_run(*, current_user: UserPublic, project_id: str, prompt: str) -> ContentAgentRunRecord:
    goal = prompt.strip()
    feedback_result = orchestrate_feedback_request(
        current_user=current_user,
        project_id=project_id,
        message_text=goal,
        run=None,
        latest_article="",
        pending_context=None,
    )
    if feedback_result.status in {"clarify", "answered"} and feedback_result.reply:
        run = run_store.create_content_agent_run(
            current_user.id,
            project_id=project_id,
            goal=goal,
            selected_workflow_id=None,
        )
        return _record_feedback_reply_interaction(
            current_user=current_user,
            run=run,
            prompt=goal,
            result=feedback_result,
        )
    if feedback_result.status == "execute":
        run = run_store.create_content_agent_run(
            current_user.id,
            project_id=project_id,
            goal=goal,
            selected_workflow_id=None,
        )
        return _execute_feedback_follow_up(
            current_user=current_user,
            run=run,
            prompt=goal,
            result=feedback_result,
        )

    skill_update = save_skill_update_from_chat(
        current_user=current_user,
        project_id=project_id,
        message_text=goal,
        selected_skill_ids=[],
        workflow_id=None,
        recent_skill_ids=[],
    )
    if skill_update.status in {"clarify", "saved"} and skill_update.reply:
        run = run_store.create_content_agent_run(
            current_user.id,
            project_id=project_id,
            goal=goal,
            selected_workflow_id=None,
        )
        return _record_skill_update_interaction(
            current_user=current_user,
            run=run,
            prompt=goal,
            result=skill_update,
        )

    skill_meta = answer_skill_meta_request_from_chat(
        current_user=current_user,
        project_id=project_id,
        message_text=goal,
        selected_skill_ids=[],
        workflow_id=None,
        recent_skill_ids=[],
    )
    if skill_meta.status in {"clarify", "answered"} and skill_meta.reply:
        run = run_store.create_content_agent_run(
            current_user.id,
            project_id=project_id,
            goal=goal,
            selected_workflow_id=None,
        )
        return _record_skill_update_interaction(
            current_user=current_user,
            run=run,
            prompt=goal,
            result=skill_meta,
        )

    meta_result = answer_content_meta_request(
        message_text=goal,
        surface="content_agent",
        run=None,
    )
    if meta_result.status in {"clarify", "answered"} and meta_result.reply:
        run = run_store.create_content_agent_run(
            current_user.id,
            project_id=project_id,
            goal=goal,
            selected_workflow_id=None,
        )
        return _record_meta_reply_interaction(
            current_user=current_user,
            run=run,
            prompt=goal,
            result=meta_result,
        )

    with usage_scope(
        user_id=current_user.id,
        workspace_id=current_user.id,
        project_id=project_id,
        feature="content_agent",
        reference_type="content_agent_plan",
        metadata={"mode": "initial_run"},
    ):
        plan = _plan_with_orchestrator(goal)
    run = run_store.create_content_agent_run(
        current_user.id,
        project_id=project_id,
        goal=goal,
        selected_workflow_id=plan.workflow_id,
    )
    run_store.update_content_agent_run(
        current_user.id,
        run.id,
        status="running",
        stage="planning",
        progress_percent=8,
        current_step_title="Planning agent workflow",
        error=None,
    )
    run_store.create_content_agent_step(
        current_user.id,
        run_id=run.id,
        project_id=project_id,
        step_type="plan",
        title="Plan agent workflow",
        status="completed",
        input_json={"goal": goal},
        output_json={
            "workflow_id": plan.workflow_id,
            "planned_steps": plan.steps,
            "planning_notes": plan.planning_notes,
            "requested_approval": plan.requested_approval,
            "stop_after_step": plan.stop_after_step,
            "planner_model": settings.orchestrator_model if llm_client.enabled else "heuristic_fallback",
            "execution_model": settings.writer_model,
        },
    )
    record_content_agent_run_started(
        user_id=current_user.id,
        project_id=project_id,
        run_id=run.id,
        workflow_id=plan.workflow_id,
        planned_steps=plan.steps,
    )
    run = run_store.get_content_agent_run(current_user.id, run.id) or run
    _launch_content_agent_worker(
        current_user=current_user,
        run_id=run.id,
        plan=plan,
        start_index=0,
    )
    return run


def continue_content_agent_run(
    *,
    current_user: UserPublic,
    run_id: str,
    prompt: str,
) -> ContentAgentRunRecord:
    run = run_store.get_content_agent_run(current_user.id, run_id)
    if not run:
        raise ValueError("Content Agent run not found")
    if run.status in {"queued", "running", "cancel_requested"}:
        raise ValueError("This run is already in progress. Stop it first or wait for it to finish.")
    follow_up = prompt.strip()
    if not follow_up:
        raise ValueError("Follow-up prompt is required")
    if _is_export_request(follow_up):
        return _export_run_output(
            current_user=current_user,
            run=run,
            export_format=_requested_export_format(follow_up),
        )

    artifacts = _artifacts_by_step(run)
    latest_article = _latest_exportable_article(run, artifacts)
    pending_feedback = _pending_feedback_context_from_run(run)
    feedback_result = orchestrate_feedback_request(
        current_user=current_user,
        project_id=run.project_id,
        message_text=follow_up,
        run=run,
        latest_article=latest_article,
        pending_context=pending_feedback,
    )
    if feedback_result.status in {"clarify", "answered"} and feedback_result.reply:
        return _record_feedback_reply_interaction(
            current_user=current_user,
            run=run,
            prompt=follow_up,
            result=feedback_result,
            follow_up=True,
        )
    if feedback_result.status == "execute":
        return _execute_feedback_follow_up(
            current_user=current_user,
            run=run,
            prompt=follow_up,
            result=feedback_result,
        )

    pending_skill_update, pending_skill_ids, pending_scope = _pending_skill_update_context_from_run(run)
    skill_update = save_skill_update_from_chat(
        current_user=current_user,
        project_id=run.project_id,
        message_text=follow_up,
        selected_skill_ids=[],
        workflow_id=run.selected_workflow_id,
        recent_skill_ids=_recent_skill_ids_from_run(run),
        pending=pending_skill_update,
        pending_skill_ids=pending_skill_ids,
        pending_scope=pending_scope,
    )
    if skill_update.status in {"clarify", "saved"} and skill_update.reply:
        return _record_skill_update_interaction(
            current_user=current_user,
            run=run,
            prompt=follow_up,
            result=skill_update,
            follow_up=True,
        )

    skill_meta = answer_skill_meta_request_from_chat(
        current_user=current_user,
        project_id=run.project_id,
        message_text=follow_up,
        selected_skill_ids=[],
        workflow_id=run.selected_workflow_id,
        recent_skill_ids=_recent_skill_ids_from_run(run),
    )
    if skill_meta.status in {"clarify", "answered"} and skill_meta.reply:
        return _record_skill_update_interaction(
            current_user=current_user,
            run=run,
            prompt=follow_up,
            result=skill_meta,
            follow_up=True,
        )

    meta_result = answer_content_meta_request(
        message_text=follow_up,
        surface="content_agent",
        run=run,
    )
    if meta_result.status in {"clarify", "answered"} and meta_result.reply:
        return _record_meta_reply_interaction(
            current_user=current_user,
            run=run,
            prompt=follow_up,
            result=meta_result,
            follow_up=True,
        )

    with usage_scope(
        user_id=current_user.id,
        workspace_id=current_user.id,
        project_id=run.project_id,
        feature="content_agent",
        reference_type="content_agent_plan",
        reference_id=run.id,
        metadata={"mode": "follow_up"},
    ):
        plan = _plan_with_orchestrator(follow_up)
    run_store.update_content_agent_run(
        current_user.id,
        run.id,
        status="running",
        stage="planning",
        progress_percent=8,
        current_step_title="Planning follow-up",
        error=None,
    )
    run_store.create_content_agent_step(
        current_user.id,
        run_id=run.id,
        project_id=run.project_id,
        step_type="plan",
        title="Plan follow-up request",
        status="completed",
        input_json={"prompt": follow_up},
        output_json={
            "follow_up": True,
            "workflow_id": plan.workflow_id,
            "planned_steps": plan.steps,
            "planning_notes": plan.planning_notes,
            "requested_approval": plan.requested_approval,
            "stop_after_step": plan.stop_after_step,
            "planner_model": settings.orchestrator_model if llm_client.enabled else "heuristic_fallback",
            "execution_model": settings.writer_model,
        },
    )
    record_content_agent_follow_up_plan(
        user_id=current_user.id,
        project_id=run.project_id,
        run_id=run.id,
        workflow_id=plan.workflow_id,
        planned_steps=plan.steps,
    )
    refreshed = run_store.get_content_agent_run(current_user.id, run.id) or run
    _launch_content_agent_worker(
        current_user=current_user,
        run_id=run.id,
        plan=plan,
        start_index=0,
        directive=follow_up,
    )
    return refreshed


def approve_content_agent_run(
    *,
    current_user: UserPublic,
    run_id: str,
    note: str = "",
) -> ContentAgentRunRecord:
    run = run_store.get_content_agent_run(current_user.id, run_id)
    if not run:
        raise ValueError("Content Agent run not found")
    if run.status != "awaiting_approval":
        raise ValueError("This run is not waiting for approval")
    plan = _plan_from_run(run)
    next_step_id = _latest_approval_next_step(run)
    if next_step_id and next_step_id in plan.steps:
        next_index = plan.steps.index(next_step_id)
    else:
        next_index = _next_step_index(plan, run)
    if next_index >= len(plan.steps):
        raise ValueError("No further steps remain for this run")
    updated = run_store.update_content_agent_run(
        current_user.id,
        run.id,
        status="running",
        stage=plan.steps[next_index],
        current_step_title=f"Continuing to {STEP_LABELS.get(plan.steps[next_index], plan.steps[next_index])}",
        error=None,
    )
    _launch_content_agent_worker(
        current_user=current_user,
        run_id=run.id,
        plan=plan,
        start_index=next_index,
        approval_note=note.strip(),
    )
    return updated or run

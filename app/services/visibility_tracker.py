from __future__ import annotations

import asyncio
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse

from app.core.config import settings
from app.models.schemas import (
    VisibilityBrandMetric,
    VisibilityCitationDrilldown,
    VisibilityCitationMetric,
    VisibilityDailyMetric,
    VisibilityProjectRecord,
    VisibilityProjectWorkspaceResponse,
    VisibilityProjectsResponse,
    VisibilityPromptReference,
    VisibilityReport,
)
from app.models.store import run_store
from app.services.llm_client import llm_client


URL_PATTERN = re.compile(r"https?://[^\s)>\]}\"']+")


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_url(url: str) -> str:
    return url.rstrip(".,);]").strip()


def _extract_urls(text: str) -> list[str]:
    urls: list[str] = []
    seen = set()
    for match in URL_PATTERN.findall(text or ""):
        cleaned = _normalize_url(match)
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            urls.append(cleaned)
    return urls


def _extract_domains(urls: list[str]) -> list[str]:
    domains: list[str] = []
    seen = set()
    for url in urls:
        host = urlparse(url).netloc.lower().strip()
        host = host[4:] if host.startswith("www.") else host
        if host and host not in seen:
            seen.add(host)
            domains.append(host)
    return domains


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    seen = set()
    for value in values:
        clean = value.strip()
        key = clean.lower()
        if clean and key not in seen:
            seen.add(key)
            result.append(clean)
    return result


def _extract_brand_mentions(response_text: str, project: VisibilityProjectRecord) -> list[str]:
    known_brands = [project.brand_name] + [competitor.name for competitor in project.competitors]
    found: list[str] = []
    response_lower = (response_text or "").lower()
    for brand in known_brands:
        cleaned = brand.strip()
        if cleaned and cleaned.lower() in response_lower:
            found.append(cleaned)
    return _dedupe(found)


def _extract_entities(response_text: str, project: VisibilityProjectRecord) -> tuple[list[str], list[str], list[str]]:
    urls = _extract_urls(response_text)
    domains = _extract_domains(urls)
    brands = _extract_brand_mentions(response_text, project)

    if not llm_client.enabled or not response_text.strip():
        return brands, domains, urls

    known_brands = [project.brand_name] + [competitor.name for competitor in project.competitors]
    instruction = (
        "Extract structured data from the assistant answer. "
        "Return strict JSON with keys brands and cited_urls. "
        "Only include brands explicitly mentioned in the answer. "
        "If a URL is not explicitly visible in the answer, do not invent it. "
        "Known brands to watch: {}"
    ).format(", ".join([item for item in known_brands if item.strip()]) or "none")
    try:
        parsed = llm_client.complete_json(
            model=settings.small_model,
            instruction=instruction,
            input_text=response_text,
        )
        brands = _dedupe([str(item) for item in parsed.get("brands", [])] + brands)
        urls = _dedupe([_normalize_url(str(item)) for item in parsed.get("cited_urls", [])] + urls)
        domains = _extract_domains(urls)
    except Exception:
        pass

    return brands, domains, urls


def _build_visibility_instruction(project: VisibilityProjectRecord) -> str:
    brand_context = ""
    if project.brand_name.strip():
        brand_context = "Tracked brand context: {} ({})".format(
            project.brand_name.strip(),
            project.brand_url.strip() or "URL not set",
        )
    return (
        "You are generating a user-facing AI answer for a visibility tracking system. "
        "Answer the user's prompt directly and naturally. "
        "If you reference sources, include a short 'Citations' section with full direct URLs. "
        "Do not fabricate citations. If you do not have visible URLs, say 'No direct citations provided.'\n\n"
        "{}".format(brand_context)
    )


async def run_visibility_prompt_list_job(job_id: str, *, force: bool = False) -> None:
    job = run_store.get_visibility_job_by_id(job_id)
    if not job:
        return
    if job.status == "completed":
        return
    if job.status == "running" and not force:
        return

    prompt_list = run_store.get_visibility_prompt_list(job.user_id, job.prompt_list_id, job.project_id)
    project = run_store.get_visibility_project(job.user_id, job.project_id)
    context = run_store.get_visibility_prompt_list_context(job.prompt_list_id, job.project_id)
    if not prompt_list or not context or not project:
        run_store.update_visibility_job(job_id, status="failed", stage="failed", error="Prompt list not found")
        return
    if not prompt_list.prompts:
        run_store.update_visibility_job(job_id, status="failed", stage="failed", error="Prompt list has no prompts")
        return

    run_store.update_visibility_job(job_id, status="running", stage="running", progress_percent=0, completed_prompts=0)

    completed = 0
    for prompt in prompt_list.prompts:
        try:
            response_text = await asyncio.to_thread(
                llm_client.complete,
                model=job.model,
                instruction=_build_visibility_instruction(project),
                input_text=prompt.prompt_text,
            )
            brands, domains, urls = await asyncio.to_thread(_extract_entities, response_text, project)
            run_store.create_visibility_prompt_run(
                job.user_id,
                project_id=job.project_id,
                job_id=job.id,
                topic_id=job.topic_id,
                subtopic_id=job.subtopic_id,
                prompt_list_id=job.prompt_list_id,
                prompt_id=prompt.id,
                prompt_text=prompt.prompt_text,
                provider=job.provider,
                model=job.model,
                surface=job.surface,
                run_source=job.run_source,
                status="completed",
                response_text=response_text,
                brands=brands,
                cited_domains=domains,
                cited_urls=urls,
            )
        except Exception as exc:  # noqa: BLE001
            run_store.create_visibility_prompt_run(
                job.user_id,
                project_id=job.project_id,
                job_id=job.id,
                topic_id=job.topic_id,
                subtopic_id=job.subtopic_id,
                prompt_list_id=job.prompt_list_id,
                prompt_id=prompt.id,
                prompt_text=prompt.prompt_text,
                provider=job.provider,
                model=job.model,
                surface=job.surface,
                run_source=job.run_source,
                status="failed",
                error=str(exc),
            )
        completed += 1
        progress_percent = int((completed / max(len(prompt_list.prompts), 1)) * 100)
        run_store.update_visibility_job(
            job_id,
            stage="running",
            completed_prompts=completed,
            progress_percent=progress_percent,
            status="running" if completed < len(prompt_list.prompts) else "completed",
        )

    run_store.mark_visibility_prompt_list_run(
        job.prompt_list_id,
        frequency=context["schedule_frequency"],
        run_at=_now_utc(),
    )
    run_store.update_visibility_job(job_id, status="completed", stage="completed", progress_percent=100)


def build_visibility_report(
    user_id: str,
    *,
    project_id: str,
    level: str,
    entity_id: str,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
) -> VisibilityReport:
    project = run_store.get_visibility_project(user_id, project_id)
    if not project:
        return VisibilityReport(project_id=project_id, level=level, entity_id=entity_id, entity_name="Unknown project")
    topics = run_store.list_visibility_topics(user_id, project_id)
    entity_name = "All tracked prompts"
    run_filters: dict[str, str] = {}

    if level == "topic":
        topic = next((item for item in topics if item.id == entity_id), None)
        if not topic:
            return VisibilityReport(project_id=project_id, level=level, entity_id=entity_id, entity_name="Unknown topic")
        entity_name = topic.name
        run_filters["topic_id"] = entity_id
    elif level == "subtopic":
        subtopic = next(
            (sub for topic in topics for sub in topic.subtopics if sub.id == entity_id),
            None,
        )
        if not subtopic:
            return VisibilityReport(project_id=project_id, level=level, entity_id=entity_id, entity_name="Unknown subtopic")
        entity_name = subtopic.name
        run_filters["subtopic_id"] = entity_id
    elif level == "prompt_list":
        prompt_list = next(
            (lst for topic in topics for sub in topic.subtopics for lst in sub.prompt_lists if lst.id == entity_id),
            None,
        )
        if not prompt_list:
            return VisibilityReport(project_id=project_id, level=level, entity_id=entity_id, entity_name="Unknown prompt list")
        entity_name = prompt_list.name
        run_filters["prompt_list_id"] = entity_id
    else:
        level = "all"
        entity_id = "all"

    runs = run_store.list_visibility_prompt_runs(user_id, project_id=project_id, limit=1000, start_date=start_date, end_date=end_date, **run_filters)
    tracked_brands = _dedupe([project.brand_name] + [item.name for item in project.competitors])
    presence_counter = Counter()
    domain_counter = Counter()
    url_counter = Counter()
    domain_prompts: dict[str, list[VisibilityPromptReference]] = defaultdict(list)
    url_prompts: dict[str, list[VisibilityPromptReference]] = defaultdict(list)
    daily_runs: dict[str, list] = defaultdict(list)

    for run in runs:
        daily_runs[run.created_at.date().isoformat()].append(run)
        brand_set = {item.lower(): item for item in run.brands}
        for tracked_brand in tracked_brands:
            if tracked_brand.lower() in brand_set or tracked_brand.lower() in run.response_text.lower():
                presence_counter[tracked_brand] += 1
        for domain in run.cited_domains:
            domain_counter[domain] += 1
            domain_prompts[domain].append(
                VisibilityPromptReference(
                    run_id=run.id,
                    prompt_id=run.prompt_id,
                    prompt_text=run.prompt_text,
                    created_at=run.created_at,
                )
            )
        for url in run.cited_urls:
            url_counter[url] += 1
            url_prompts[url].append(
                VisibilityPromptReference(
                    run_id=run.id,
                    prompt_id=run.prompt_id,
                    prompt_text=run.prompt_text,
                    created_at=run.created_at,
                )
            )

    total_mentions = sum(presence_counter.values()) or 1
    brand_presence = [
        VisibilityBrandMetric(
            brand=brand,
            prompt_mentions=presence_counter.get(brand, 0),
            share_of_voice=round(presence_counter.get(brand, 0) / total_mentions, 4),
        )
        for brand in tracked_brands
    ]
    brand_presence.sort(key=lambda item: item.prompt_mentions, reverse=True)

    top_domains = [
        VisibilityCitationMetric(value=value, count=count)
        for value, count in domain_counter.most_common(10)
    ]
    top_urls = [
        VisibilityCitationMetric(value=value, count=count)
        for value, count in url_counter.most_common(10)
    ]
    domain_drilldown = [
        VisibilityCitationDrilldown(value=value, count=count, prompts=domain_prompts[value][:25])
        for value, count in domain_counter.most_common(10)
    ]
    url_drilldown = [
        VisibilityCitationDrilldown(value=value, count=count, prompts=url_prompts[value][:25])
        for value, count in url_counter.most_common(10)
    ]

    daily_metrics: list[VisibilityDailyMetric] = []
    for date_key in sorted(daily_runs.keys()):
        run_items = daily_runs[date_key]
        day_presence = Counter()
        for run in run_items:
            for tracked_brand in tracked_brands:
                if tracked_brand and tracked_brand.lower() in run.response_text.lower():
                    day_presence[tracked_brand] += 1
        total_day_mentions = sum(day_presence.values()) or 1
        daily_metrics.append(
            VisibilityDailyMetric(
                date=date_key,
                run_count=len(run_items),
                brand_mentions=[
                    VisibilityBrandMetric(
                        brand=brand,
                        prompt_mentions=day_presence.get(brand, 0),
                        share_of_voice=round(day_presence.get(brand, 0) / total_day_mentions, 4),
                    )
                    for brand in tracked_brands
                ],
            )
        )

    return VisibilityReport(
        project_id=project_id,
        level=level,
        entity_id=entity_id,
        entity_name=entity_name,
        total_runs=len(runs),
        brand_presence=brand_presence,
        top_domains=top_domains,
        top_urls=top_urls,
        domain_drilldown=domain_drilldown,
        url_drilldown=url_drilldown,
        competitor_matrix=brand_presence,
        daily_metrics=daily_metrics,
    )


def build_visibility_projects(user_id: str) -> VisibilityProjectsResponse:
    return VisibilityProjectsResponse(projects=run_store.list_visibility_projects(user_id))


def build_visibility_workspace(
    user_id: str,
    *,
    project_id: str,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
) -> VisibilityProjectWorkspaceResponse:
    project = run_store.get_visibility_project(user_id, project_id)
    if not project:
        raise ValueError("Project not found")
    topics = run_store.list_visibility_topics(user_id, project_id)
    recent_jobs = run_store.list_visibility_jobs(user_id, project_id, limit=12, start_date=start_date, end_date=end_date)
    recent_runs = run_store.list_visibility_prompt_runs(user_id, project_id=project_id, limit=24, start_date=start_date, end_date=end_date)
    reports = {"all": build_visibility_report(user_id, project_id=project_id, level="all", entity_id="all", start_date=start_date, end_date=end_date)}
    return VisibilityProjectWorkspaceResponse(
        project=project,
        topics=topics,
        recent_jobs=recent_jobs,
        recent_runs=recent_runs,
        reports=reports,
    )


async def run_due_visibility_schedules() -> None:
    due_lists = run_store.list_due_visibility_prompt_lists(limit=20)
    for item in due_lists:
        existing_jobs = run_store.list_visibility_jobs(item["user_id"], item["project_id"], limit=30)
        if any(
            job.prompt_list_id == item["id"] and job.status in {"queued", "running"}
            for job in existing_jobs
        ):
            continue
        prompt_list = run_store.get_visibility_prompt_list(item["user_id"], item["id"], item["project_id"])
        if not prompt_list or not prompt_list.prompts:
            continue
        job = run_store.create_visibility_job(
            item["user_id"],
            project_id=item["project_id"],
            topic_id=item["topic_id"],
            subtopic_id=item["subtopic_id"],
            prompt_list_id=item["id"],
            provider="openai",
            model=settings.small_model,
            surface="api",
            run_source="scheduled",
            total_prompts=len(prompt_list.prompts),
        )
        asyncio.create_task(run_visibility_prompt_list_job(job.id))

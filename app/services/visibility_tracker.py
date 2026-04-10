from __future__ import annotations

import asyncio
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from itertools import cycle
from typing import Optional
from urllib.parse import urlparse

from app.core.config import settings
from app.models.schemas import (
    VisibilityBrandMetric,
    VisibilityCitationDrilldown,
    VisibilityCitationMetric,
    VisibilityDailyMetric,
    VisibilityGeneratedPrompt,
    VisibilityProjectRecord,
    VisibilityProjectWorkspaceResponse,
    VisibilityProjectsResponse,
    VisibilityPromptGeneratorIntentGroup,
    VisibilityPromptGeneratorRequest,
    VisibilityPromptGeneratorResponse,
    VisibilityPromptGeneratorTypeSummary,
    VisibilityPromptReference,
    VisibilityReport,
)
from app.models.store import run_store
from app.services.billing import record_visibility_provider_check, usage_scope
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


def _humanize_token(value: str) -> str:
    return value.replace("_", " ").replace("-", " ").strip()


def _clean_list(values: list[str]) -> list[str]:
    return _dedupe([value.strip() for value in values if value and value.strip()])


def _safe_float(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _append_candidate(
    candidates: list[dict[str, object]],
    seen: set[str],
    *,
    prompt_text: str,
    intent_stage: str,
    prompt_type: str,
    ai_format_likely: str,
    priority_score: int,
) -> None:
    text = " ".join((prompt_text or "").split())
    if len(text) < 12:
        return
    key = text.lower()
    if key in seen:
        return
    seen.add(key)
    candidates.append(
        {
            "prompt_text": text,
            "intent_stage": intent_stage,
            "prompt_type": prompt_type,
            "ai_format_likely": ai_format_likely,
            "priority_score": max(0, min(int(priority_score), 100)),
        }
    )


def _gsc_focus_queries(rows: list) -> list[str]:
    prioritized: list[str] = []
    secondary: list[str] = []
    for row in rows:
        query = getattr(row, "query", "").strip()
        if not query:
            continue
        impressions = _safe_float(getattr(row, "impressions", 0.0))
        ctr = _safe_float(getattr(row, "ctr", 0.0))
        position = _safe_float(getattr(row, "position", 0.0))
        if impressions >= 50 and ctr <= 3.5:
            prioritized.append(query)
        elif 5 <= position <= 15:
            prioritized.append(query)
        else:
            secondary.append(query)
    return _dedupe(prioritized + secondary)[:8]


def _pad_candidates(
    candidates: list[dict[str, object]],
    seen: set[str],
    *,
    target_count: int,
    project_type: str,
    fallback_subject: str,
) -> None:
    if len(candidates) >= target_count:
        return
    modifiers = (
        ["in india", "for global buyers", "for 2026", "for smaller teams", "for growing companies", "for budget-conscious buyers"]
        if project_type == "b2b_saas"
        else ["for 2026", "for first-time buyers", "for daily use", "for comparison research", "before buying online", "for gifting"]
    )
    secondary_modifiers = [
        "with pricing in mind",
        "for shortlist research",
        "with strong reviews",
        "before switching",
        "for buyer validation",
        "with comparison intent",
    ]
    base_candidates = list(candidates) or [
        {
            "prompt_text": f"best {fallback_subject} options",
            "intent_stage": "awareness" if project_type == "b2b_saas" else "discovery",
            "prompt_type": "use-case",
            "ai_format_likely": "list",
            "priority_score": 55,
        }
    ]
    modifier_cycle = cycle(modifiers)
    index = 1
    while len(candidates) < target_count:
        template = base_candidates[(index - 1) % len(base_candidates)]
        modifier = next(modifier_cycle)
        secondary = secondary_modifiers[((index - 1) // max(len(modifiers), 1)) % len(secondary_modifiers)]
        suffix = modifier if index <= len(base_candidates) * len(modifiers) else f"{modifier} {secondary}"
        _append_candidate(
            candidates,
            seen,
            prompt_text=f"{template['prompt_text']} {suffix}",
            intent_stage=str(template["intent_stage"]),
            prompt_type=str(template["prompt_type"]),
            ai_format_likely=str(template["ai_format_likely"]),
            priority_score=int(template["priority_score"]) - 5,
        )
        index += 1


def _rebalance_prompt_candidates(
    candidates: list[dict[str, object]],
    *,
    desired_count: int,
    project_type: str,
) -> list[dict[str, object]]:
    if not candidates:
        return []
    ranked = sorted(candidates, key=lambda item: (int(item["priority_score"]), str(item["prompt_text"])), reverse=True)
    preferred_non_comparison_stages = {"awareness", "consideration"} if project_type in {"b2b_saas", "services"} else {"discovery", "purchase", "validation"}
    comparison_target = max(1, round(desired_count * 0.4))
    non_comparison_target = max(1, desired_count - comparison_target)

    comparison_pool = [item for item in ranked if str(item["prompt_type"]) == "comparison"]
    primary_pool = [
        item for item in ranked
        if str(item["prompt_type"]) != "comparison" and str(item["intent_stage"]) in preferred_non_comparison_stages
    ]
    secondary_pool = [
        item for item in ranked
        if str(item["prompt_type"]) != "comparison" and str(item["intent_stage"]) not in preferred_non_comparison_stages
    ]

    selected: list[dict[str, object]] = []
    seen = set()

    def take_from(pool: list[dict[str, object]], limit: int) -> None:
        for item in pool:
            if len(selected) >= desired_count:
                return
            key = str(item["prompt_text"]).lower()
            if key in seen:
                continue
            if limit <= 0:
                return
            seen.add(key)
            selected.append(item)
            limit -= 1

    take_from(primary_pool, non_comparison_target)
    take_from(comparison_pool, comparison_target)
    take_from(primary_pool, desired_count - len(selected))
    take_from(secondary_pool, desired_count - len(selected))
    take_from(comparison_pool, desired_count - len(selected))
    take_from(ranked, desired_count - len(selected))
    return selected[:desired_count]


def _polish_generated_prompt_texts(
    prompts: list[dict[str, object]],
    *,
    project_type: str,
    project_name: str,
) -> list[dict[str, object]]:
    if not prompts or not llm_client.enabled:
        return prompts
    prompt_payload = [
        {
            "id": f"draft-{index + 1}",
            "prompt_text": str(item["prompt_text"]),
            "intent_stage": str(item["intent_stage"]),
            "prompt_type": str(item["prompt_type"]),
        }
        for index, item in enumerate(prompts)
    ]
    instruction = (
        "You improve prompt phrasing for an AI visibility tracker.\n"
        "Rewrite prompts to sound natural, high-intent, and non-repetitive.\n"
        "Keep each prompt focused and realistic for a user to ask in ChatGPT.\n"
        "Do not change intent_stage or prompt_type. Do not add or remove prompts.\n"
        "Return strict JSON with shape {\"prompts\":[{\"id\":\"...\",\"prompt_text\":\"...\"}]} only."
    )
    input_text = json.dumps(
        {
            "project_type": project_type,
            "project_name": project_name,
            "prompts": prompt_payload,
        },
        ensure_ascii=True,
    )
    try:
        parsed = llm_client.complete_json(
            model="gpt-5-mini",
            instruction=instruction,
            input_text=input_text,
        )
    except Exception:
        return prompts

    rewritten = {
        str(item.get("id")): str(item.get("prompt_text", "")).strip()
        for item in parsed.get("prompts", [])
        if str(item.get("id", "")).strip() and str(item.get("prompt_text", "")).strip()
    }
    polished: list[dict[str, object]] = []
    for index, item in enumerate(prompts):
        revised = rewritten.get(f"draft-{index + 1}")
        if revised:
            polished.append({**item, "prompt_text": revised})
        else:
            polished.append(item)
    return polished


def _generate_b2b_prompt_candidates(project: VisibilityProjectRecord, payload: VisibilityPromptGeneratorRequest) -> list[dict[str, object]]:
    product_name = payload.product_name.strip() or project.brand_name.strip() or "this software"
    category = payload.category.strip() or "software"
    pricing_tier = _humanize_token(payload.pricing_tier.strip() or "mid")
    target_market = payload.target_market_custom.strip() if payload.target_market == "custom" else _humanize_token(payload.target_market.strip() or "global")
    role = payload.quick_audience.strip() or payload.role.strip() or "team leads"
    company_size = payload.company_size.strip() or "11-50"
    industry = payload.quick_context.strip() or payload.industry.strip() or "modern businesses"
    awareness = _humanize_token(payload.awareness_level.strip() or "solution_aware")
    pain_points = _clean_list(payload.pain_points) or _clean_list([payload.quick_use_case]) or [f"evaluating the right {category}", f"improving {category} workflows", f"reducing manual work in {category}"]
    desired_outcomes = _clean_list(payload.desired_outcomes) or _clean_list([payload.quick_use_case]) or ["faster team execution", "better reporting visibility", "stronger ROI from tooling"]
    fears = _clean_list(payload.fears_objections) or ["slow adoption", "switching effort", "unclear ROI"]
    triggers = _clean_list(payload.buying_triggers) or ["team growth", "process complexity", "cost pressure"]
    competitors = _clean_list(payload.competitors) or _clean_list([item.name for item in project.competitors])
    gsc_queries = _gsc_focus_queries(payload.gsc_rows)

    candidates: list[dict[str, object]] = []
    seen: set[str] = set()

    for pain in pain_points[:5]:
        _append_candidate(candidates, seen, prompt_text=f"how to solve {pain} for {role}s in {industry}", intent_stage="awareness", prompt_type="problem-solving", ai_format_likely="explanation", priority_score=92)
        _append_candidate(candidates, seen, prompt_text=f"why does {pain} happen for {role}s at {company_size} companies", intent_stage="awareness", prompt_type="problem-solving", ai_format_likely="explanation", priority_score=90)
        _append_candidate(candidates, seen, prompt_text=f"signs your team needs better {category} for {pain}", intent_stage="awareness", prompt_type="problem-solving", ai_format_likely="list", priority_score=84)
        _append_candidate(candidates, seen, prompt_text=f"best ways to solve {pain} for {role}s in {target_market}", intent_stage="consideration", prompt_type="use-case", ai_format_likely="list", priority_score=91)
        _append_candidate(candidates, seen, prompt_text=f"tools for {pain} for {role}s at {company_size} {industry} companies", intent_stage="consideration", prompt_type="use-case", ai_format_likely="list", priority_score=95)
        _append_candidate(candidates, seen, prompt_text=f"how to choose {category} for {pain} without overbuying", intent_stage="consideration", prompt_type="decision-validation", ai_format_likely="explanation", priority_score=88)

    for outcome in desired_outcomes[:5]:
        _append_candidate(candidates, seen, prompt_text=f"best {category} for {role}s who want {outcome}", intent_stage="consideration", prompt_type="use-case", ai_format_likely="list", priority_score=93)
        _append_candidate(candidates, seen, prompt_text=f"how to evaluate {category} for {outcome} at {company_size} companies", intent_stage="consideration", prompt_type="decision-validation", ai_format_likely="explanation", priority_score=86)
        _append_candidate(candidates, seen, prompt_text=f"is {category} worth it for {role}s focused on {outcome}", intent_stage="consideration", prompt_type="decision-validation", ai_format_likely="explanation", priority_score=84)
        _append_candidate(candidates, seen, prompt_text=f"how teams get ROI from {category} after buying for {outcome}", intent_stage="post-decision", prompt_type="use-case", ai_format_likely="explanation", priority_score=74)

    for fear in fears[:5]:
        _append_candidate(candidates, seen, prompt_text=f"why do {role}s hesitate to buy {category} because of {fear}", intent_stage="consideration", prompt_type="objection", ai_format_likely="explanation", priority_score=89)
        _append_candidate(candidates, seen, prompt_text=f"common objections to {category} tools for {industry} teams", intent_stage="consideration", prompt_type="objection", ai_format_likely="list", priority_score=83)
        _append_candidate(candidates, seen, prompt_text=f"how to reduce {fear} when switching to a new {category}", intent_stage="post-decision", prompt_type="objection", ai_format_likely="explanation", priority_score=76)

    for trigger in triggers[:5]:
        _append_candidate(candidates, seen, prompt_text=f"best {category} when {trigger} starts affecting {industry} teams", intent_stage="consideration", prompt_type="trigger-based", ai_format_likely="list", priority_score=92)
        _append_candidate(candidates, seen, prompt_text=f"what should a {role} evaluate in {category} when {trigger} happens", intent_stage="consideration", prompt_type="trigger-based", ai_format_likely="list", priority_score=87)
        _append_candidate(candidates, seen, prompt_text=f"how to roll out {category} after {trigger}", intent_stage="post-decision", prompt_type="trigger-based", ai_format_likely="explanation", priority_score=72)

    for competitor in competitors[:8]:
        _append_candidate(candidates, seen, prompt_text=f"{product_name} vs {competitor} for {role}s in {industry}", intent_stage="decision", prompt_type="comparison", ai_format_likely="comparison", priority_score=98)
        _append_candidate(candidates, seen, prompt_text=f"alternatives to {competitor} for {role}s managing {category}", intent_stage="decision", prompt_type="comparison", ai_format_likely="list", priority_score=97)
        _append_candidate(candidates, seen, prompt_text=f"is {product_name} better than {competitor} for {company_size} companies", intent_stage="decision", prompt_type="comparison", ai_format_likely="comparison", priority_score=95)

    for query in gsc_queries:
        _append_candidate(candidates, seen, prompt_text=f"{query} alternatives for {role}s", intent_stage="consideration", prompt_type="comparison", ai_format_likely="list", priority_score=90)
        _append_candidate(candidates, seen, prompt_text=f"{query} vs {product_name}", intent_stage="decision", prompt_type="comparison", ai_format_likely="comparison", priority_score=94)
        _append_candidate(candidates, seen, prompt_text=f"is {query} worth it for {company_size} companies", intent_stage="consideration", prompt_type="decision-validation", ai_format_likely="explanation", priority_score=85)
        _append_candidate(candidates, seen, prompt_text=f"common objections buyers have before choosing {query}", intent_stage="consideration", prompt_type="objection", ai_format_likely="list", priority_score=82)

    _append_candidate(candidates, seen, prompt_text=f"best {pricing_tier} {category} for {role}s in {target_market}", intent_stage="consideration", prompt_type="price-based", ai_format_likely="list", priority_score=81)
    _append_candidate(candidates, seen, prompt_text=f"how to shortlist {category} vendors for {role}s who are {awareness}", intent_stage="consideration", prompt_type="decision-validation", ai_format_likely="list", priority_score=79)
    _append_candidate(candidates, seen, prompt_text=f"what should {role}s ask in a {category} demo before buying", intent_stage="decision", prompt_type="decision-validation", ai_format_likely="list", priority_score=83)
    _append_candidate(candidates, seen, prompt_text=f"how to onboard teams to a new {category} after purchase", intent_stage="post-decision", prompt_type="use-case", ai_format_likely="explanation", priority_score=71)

    _pad_candidates(candidates, seen, target_count=payload.desired_prompt_count, project_type="b2b_saas", fallback_subject=category)
    return candidates


def _generate_ecommerce_prompt_candidates(project: VisibilityProjectRecord, payload: VisibilityPromptGeneratorRequest) -> list[dict[str, object]]:
    product_name = payload.product_name.strip() or project.brand_name.strip() or "this product"
    category = payload.category.strip() or "product"
    price_range = _humanize_token(payload.price_range.strip() or "mid_range")
    brand_positioning = _humanize_token(payload.brand_positioning.strip() or "premium")
    audience = payload.quick_audience.strip() or (payload.target_audience_custom.strip() if payload.target_audience == "custom" else _humanize_token(payload.target_audience.strip() or "unisex"))
    age_group = payload.age_group.strip()
    use_case = payload.quick_use_case.strip() or (payload.use_case_custom.strip() if payload.use_case == "custom" else _humanize_token(payload.use_case.strip() or "daily_use"))
    awareness = _humanize_token(payload.awareness_level.strip() or "exploring")
    triggers = _clean_list(payload.intent_triggers) or _clean_list([payload.quick_context]) or ["problem solving", "upgrade", "replacement"]
    factors = _clean_list(payload.decision_factors) or _clean_list([payload.quick_context]) or ["quality", "brand trust", "features"]
    objections = _clean_list(payload.objections) or ["is it worth it", "does it work", "will it suit me"]
    competitors = _clean_list(payload.competitors) or _clean_list([item.name for item in project.competitors])
    gsc_queries = _gsc_focus_queries(payload.gsc_rows)

    audience_phrase = f"{audience} {age_group}".strip()
    candidates: list[dict[str, object]] = []
    seen: set[str] = set()

    for factor in factors[:5]:
        _append_candidate(candidates, seen, prompt_text=f"best {category} for {use_case} with strong {factor}", intent_stage="discovery", prompt_type="use-case", ai_format_likely="list", priority_score=92)
        _append_candidate(candidates, seen, prompt_text=f"top {price_range} {category} options for {audience_phrase or audience} shoppers", intent_stage="discovery", prompt_type="price-based", ai_format_likely="list", priority_score=88)
        _append_candidate(candidates, seen, prompt_text=f"how to choose {category} for {use_case} based on {factor}", intent_stage="purchase", prompt_type="decision-validation", ai_format_likely="explanation", priority_score=84)

    for trigger in triggers[:5]:
        _append_candidate(candidates, seen, prompt_text=f"best {category} for {trigger} buyers", intent_stage="discovery", prompt_type="use-case", ai_format_likely="list", priority_score=86)
        _append_candidate(candidates, seen, prompt_text=f"which {category} is better for {trigger}: {brand_positioning} or budget options", intent_stage="comparison", prompt_type="comparison", ai_format_likely="comparison", priority_score=83)

    for objection in objections[:5]:
        _append_candidate(candidates, seen, prompt_text=f"is {product_name} worth it for {use_case}", intent_stage="validation", prompt_type="review-style", ai_format_likely="explanation", priority_score=91)
        _append_candidate(candidates, seen, prompt_text=f"{product_name} review for buyers asking {objection}", intent_stage="validation", prompt_type="objection", ai_format_likely="explanation", priority_score=89)
        _append_candidate(candidates, seen, prompt_text=f"does {product_name} actually work for {audience_phrase or audience}", intent_stage="validation", prompt_type="objection", ai_format_likely="explanation", priority_score=86)

    for competitor in competitors[:8]:
        _append_candidate(candidates, seen, prompt_text=f"{product_name} vs {competitor} for {use_case}", intent_stage="comparison", prompt_type="comparison", ai_format_likely="comparison", priority_score=98)
        _append_candidate(candidates, seen, prompt_text=f"which is better {product_name} or {competitor} for {audience_phrase or audience}", intent_stage="comparison", prompt_type="comparison", ai_format_likely="comparison", priority_score=95)
        _append_candidate(candidates, seen, prompt_text=f"alternatives to {competitor} in the {category} category", intent_stage="comparison", prompt_type="comparison", ai_format_likely="list", priority_score=92)

    for query in gsc_queries:
        _append_candidate(candidates, seen, prompt_text=f"{query} alternatives", intent_stage="comparison", prompt_type="comparison", ai_format_likely="list", priority_score=89)
        _append_candidate(candidates, seen, prompt_text=f"{query} pros and cons", intent_stage="validation", prompt_type="review-style", ai_format_likely="list", priority_score=85)
        _append_candidate(candidates, seen, prompt_text=f"{query} for {use_case}", intent_stage="discovery", prompt_type="use-case", ai_format_likely="list", priority_score=82)
        _append_candidate(candidates, seen, prompt_text=f"is {query} worth buying", intent_stage="purchase", prompt_type="decision-validation", ai_format_likely="explanation", priority_score=84)

    price_examples = {
        "budget": ["under ₹500", "under ₹1000", "under ₹2000"],
        "mid range": ["under ₹3000", "under ₹5000", "between ₹3000 and ₹7000"],
        "premium": ["premium options", "high-end options", "luxury options"],
    }.get(price_range, ["under ₹1000", "under ₹3000", "premium options"])
    for price_hint in price_examples:
        _append_candidate(candidates, seen, prompt_text=f"best {category} {price_hint} for {use_case}", intent_stage="purchase", prompt_type="price-based", ai_format_likely="list", priority_score=90)
        _append_candidate(candidates, seen, prompt_text=f"budget vs premium {category} for {audience_phrase or audience}", intent_stage="comparison", prompt_type="price-based", ai_format_likely="comparison", priority_score=82)

    _append_candidate(candidates, seen, prompt_text=f"best {brand_positioning} {category} brands for {audience_phrase or audience}", intent_stage="discovery", prompt_type="use-case", ai_format_likely="list", priority_score=80)
    _append_candidate(candidates, seen, prompt_text=f"what to check before buying {category} online for {use_case}", intent_stage="purchase", prompt_type="decision-validation", ai_format_likely="list", priority_score=81)
    _append_candidate(candidates, seen, prompt_text=f"is {product_name} legit for buyers who are {awareness}", intent_stage="validation", prompt_type="objection", ai_format_likely="explanation", priority_score=83)

    _pad_candidates(candidates, seen, target_count=payload.desired_prompt_count, project_type="ecommerce", fallback_subject=category)
    return candidates


def _generate_services_prompt_candidates(project: VisibilityProjectRecord, payload: VisibilityPromptGeneratorRequest) -> list[dict[str, object]]:
    product_name = payload.product_name.strip() or project.brand_name.strip() or "this service"
    category = payload.category.strip() or "service provider"
    audience = payload.quick_audience.strip() or "buyers"
    context = payload.quick_context.strip() or "growing businesses"
    use_case = payload.quick_use_case.strip() or f"choosing the right {category}"
    competitors = _clean_list(payload.competitors) or _clean_list([item.name for item in project.competitors])
    gsc_queries = _gsc_focus_queries(payload.gsc_rows)

    candidates: list[dict[str, object]] = []
    seen: set[str] = set()

    _append_candidate(candidates, seen, prompt_text=f"best {category} for {audience} who need {use_case}", intent_stage="awareness", prompt_type="use-case", ai_format_likely="list", priority_score=95)
    _append_candidate(candidates, seen, prompt_text=f"how to choose a {category} for {use_case}", intent_stage="consideration", prompt_type="decision-validation", ai_format_likely="list", priority_score=91)
    _append_candidate(candidates, seen, prompt_text=f"what should {audience} ask before hiring a {category}", intent_stage="decision", prompt_type="decision-validation", ai_format_likely="list", priority_score=88)
    _append_candidate(candidates, seen, prompt_text=f"signs you need a better {category} partner for {use_case}", intent_stage="awareness", prompt_type="problem-solving", ai_format_likely="list", priority_score=86)
    _append_candidate(candidates, seen, prompt_text=f"common mistakes when hiring a {category} for {context}", intent_stage="decision", prompt_type="objection", ai_format_likely="explanation", priority_score=82)
    _append_candidate(candidates, seen, prompt_text=f"what outcomes should you expect after hiring a {category} for {use_case}", intent_stage="post-decision", prompt_type="use-case", ai_format_likely="explanation", priority_score=78)

    for competitor in competitors[:8]:
        _append_candidate(candidates, seen, prompt_text=f"{product_name} vs {competitor} for {use_case}", intent_stage="decision", prompt_type="comparison", ai_format_likely="comparison", priority_score=98)
        _append_candidate(candidates, seen, prompt_text=f"alternatives to {competitor} for {audience}", intent_stage="consideration", prompt_type="comparison", ai_format_likely="list", priority_score=94)
        _append_candidate(candidates, seen, prompt_text=f"is {product_name} better than {competitor} for {context}", intent_stage="decision", prompt_type="comparison", ai_format_likely="comparison", priority_score=92)

    for query in gsc_queries:
        _append_candidate(candidates, seen, prompt_text=f"{query} alternatives", intent_stage="consideration", prompt_type="comparison", ai_format_likely="list", priority_score=90)
        _append_candidate(candidates, seen, prompt_text=f"{query} for {audience}", intent_stage="awareness", prompt_type="use-case", ai_format_likely="list", priority_score=86)

    _pad_candidates(candidates, seen, target_count=payload.desired_prompt_count, project_type="b2b_saas", fallback_subject=category)
    return candidates


def _generate_local_business_prompt_candidates(project: VisibilityProjectRecord, payload: VisibilityPromptGeneratorRequest) -> list[dict[str, object]]:
    product_name = payload.product_name.strip() or project.brand_name.strip() or "this business"
    category = payload.category.strip() or "local business"
    audience = payload.quick_audience.strip() or "local customers"
    location = payload.quick_context.strip() or "your area"
    use_case = payload.quick_use_case.strip() or f"finding the right {category}"
    competitors = _clean_list(payload.competitors) or _clean_list([item.name for item in project.competitors])
    gsc_queries = _gsc_focus_queries(payload.gsc_rows)

    candidates: list[dict[str, object]] = []
    seen: set[str] = set()

    _append_candidate(candidates, seen, prompt_text=f"best {category} in {location} for {use_case}", intent_stage="discovery", prompt_type="use-case", ai_format_likely="list", priority_score=97)
    _append_candidate(candidates, seen, prompt_text=f"top-rated {category} in {location}", intent_stage="discovery", prompt_type="use-case", ai_format_likely="list", priority_score=92)
    _append_candidate(candidates, seen, prompt_text=f"how to choose a {category} in {location} for {audience}", intent_stage="decision", prompt_type="decision-validation", ai_format_likely="list", priority_score=89)
    _append_candidate(candidates, seen, prompt_text=f"affordable {category} in {location} for {use_case}", intent_stage="comparison", prompt_type="price-based", ai_format_likely="list", priority_score=87)
    _append_candidate(candidates, seen, prompt_text=f"is {product_name} good for {use_case} in {location}", intent_stage="validation", prompt_type="objection", ai_format_likely="explanation", priority_score=86)
    _append_candidate(candidates, seen, prompt_text=f"what should you ask before booking a {category} in {location}", intent_stage="decision", prompt_type="decision-validation", ai_format_likely="list", priority_score=84)

    for competitor in competitors[:8]:
        _append_candidate(candidates, seen, prompt_text=f"{product_name} vs {competitor} in {location}", intent_stage="comparison", prompt_type="comparison", ai_format_likely="comparison", priority_score=96)
        _append_candidate(candidates, seen, prompt_text=f"alternatives to {competitor} in {location}", intent_stage="comparison", prompt_type="comparison", ai_format_likely="list", priority_score=90)

    for query in gsc_queries:
        _append_candidate(candidates, seen, prompt_text=f"{query} near {location}", intent_stage="discovery", prompt_type="use-case", ai_format_likely="list", priority_score=88)
        _append_candidate(candidates, seen, prompt_text=f"is {query} worth it in {location}", intent_stage="validation", prompt_type="decision-validation", ai_format_likely="explanation", priority_score=82)

    _pad_candidates(candidates, seen, target_count=payload.desired_prompt_count, project_type="ecommerce", fallback_subject=category)
    return candidates


def generate_visibility_prompt_suggestions(
    user_id: str,
    *,
    project_id: str,
    payload: VisibilityPromptGeneratorRequest,
) -> VisibilityPromptGeneratorResponse:
    project = run_store.get_visibility_project(user_id, project_id)
    if not project:
        raise ValueError("Project not found")

    if payload.project_type == "b2b_saas":
        candidates = _generate_b2b_prompt_candidates(project, payload)
        stage_order = ["awareness", "consideration", "decision", "post-decision"]
    elif payload.project_type == "ecommerce":
        candidates = _generate_ecommerce_prompt_candidates(project, payload)
        stage_order = ["discovery", "comparison", "purchase", "validation"]
    elif payload.project_type == "services":
        candidates = _generate_services_prompt_candidates(project, payload)
        stage_order = ["awareness", "consideration", "decision", "post-decision"]
    else:
        candidates = _generate_local_business_prompt_candidates(project, payload)
        stage_order = ["discovery", "comparison", "decision", "validation"]

    trimmed = _rebalance_prompt_candidates(
        candidates,
        desired_count=payload.desired_prompt_count,
        project_type=payload.project_type,
    )
    trimmed = _polish_generated_prompt_texts(
        trimmed,
        project_type=payload.project_type,
        project_name=project.brand_name or project.name,
    )
    prompts = [
        VisibilityGeneratedPrompt(
            id=f"generated-{index + 1}",
            prompt_text=str(item["prompt_text"]),
            intent_stage=str(item["intent_stage"]),
            prompt_type=str(item["prompt_type"]),
            ai_format_likely=str(item["ai_format_likely"]),
            priority_score=int(item["priority_score"]),
        )
        for index, item in enumerate(trimmed)
    ]

    intent_groups: list[VisibilityPromptGeneratorIntentGroup] = []
    for stage in stage_order:
        stage_prompts = [prompt for prompt in prompts if prompt.intent_stage == stage]
        if stage_prompts:
            intent_groups.append(
                VisibilityPromptGeneratorIntentGroup(
                    intent_stage=stage,
                    prompt_count=len(stage_prompts),
                    prompts=stage_prompts,
                )
            )

    type_counter = Counter(prompt.prompt_type for prompt in prompts)
    type_summary = [
        VisibilityPromptGeneratorTypeSummary(prompt_type=prompt_type, prompt_count=count)
        for prompt_type, count in type_counter.most_common()
    ]

    return VisibilityPromptGeneratorResponse(
        project_id=project_id,
        project_type=payload.project_type,
        requested_prompt_count=payload.desired_prompt_count,
        generated_prompt_count=len(prompts),
        prompts=prompts,
        intent_groups=intent_groups,
        type_summary=type_summary,
    )


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


def cancel_visibility_job(user_id: str, job_id: str):
    job = run_store.get_visibility_job(user_id, job_id)
    if not job:
        raise ValueError("Visibility job not found")
    if job.status in {"completed", "failed", "cancelled"}:
        raise RuntimeError("This run can no longer be stopped.")
    if job.status == "queued":
        return run_store.update_visibility_job(
            job_id,
            status="cancelled",
            stage="cancelled",
            progress_percent=0,
            error="Cancelled by user",
        )
    return run_store.update_visibility_job(
        job_id,
        status="cancel_requested",
        stage="cancelling",
        error="Cancellation requested by user",
    )


async def run_visibility_prompt_list_job(job_id: str, *, force: bool = False) -> None:
    job = run_store.get_visibility_job_by_id(job_id)
    if not job:
        return
    if job.status in {"completed", "cancelled"}:
        return
    if job.status == "cancel_requested":
        run_store.delete_visibility_prompt_runs_for_job(job.user_id, job_id)
        run_store.update_visibility_job(job_id, status="cancelled", stage="cancelled", error="Cancelled by user")
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
        latest_job = run_store.get_visibility_job_by_id(job_id)
        if latest_job and latest_job.status == "cancel_requested":
            run_store.delete_visibility_prompt_runs_for_job(job.user_id, job_id)
            run_store.update_visibility_job(
                job_id,
                status="cancelled",
                stage="cancelled",
                progress_percent=int((completed / max(len(prompt_list.prompts), 1)) * 100),
                completed_prompts=completed,
                error="Cancelled by user",
            )
            return
        try:
            with usage_scope(
                user_id=job.user_id,
                workspace_id=job.user_id,
                project_id=job.project_id,
                feature="visibility",
                provider_surface=job.provider,
                reference_type="visibility_job",
                reference_id=job.id,
                metadata={"prompt_id": prompt.id, "prompt_list_id": job.prompt_list_id, "surface": job.surface},
            ):
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
            record_visibility_provider_check(
                user_id=job.user_id,
                project_id=job.project_id,
                prompt_id=prompt.id,
                prompt_list_id=job.prompt_list_id,
                provider_surface=job.provider,
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
        latest_job = run_store.get_visibility_job_by_id(job_id)
        if latest_job and latest_job.status == "cancel_requested":
            run_store.delete_visibility_prompt_runs_for_job(job.user_id, job_id)
            run_store.update_visibility_job(
                job_id,
                status="cancelled",
                stage="cancelled",
                progress_percent=int((completed / max(len(prompt_list.prompts), 1)) * 100),
                completed_prompts=completed,
                error="Cancelled by user",
            )
            return
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
                    status=run.status,
                    response_text=run.response_text,
                    brands=run.brands,
                    cited_domains=run.cited_domains,
                    cited_urls=run.cited_urls,
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
                    status=run.status,
                    response_text=run.response_text,
                    brands=run.brands,
                    cited_domains=run.cited_domains,
                    cited_urls=run.cited_urls,
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
    prompt_runs = run_store.list_visibility_prompt_runs(user_id, project_id=project_id, limit=1000, start_date=start_date, end_date=end_date)
    prompt_run_map: dict[str, list] = defaultdict(list)
    for run in prompt_runs:
        prompt_run_map[run.prompt_id].append(run)
    for runs in prompt_run_map.values():
        runs.sort(key=lambda item: item.created_at, reverse=True)
    for topic in topics:
        for subtopic in topic.subtopics:
            for prompt_list in subtopic.prompt_lists:
                for prompt in prompt_list.prompts:
                    runs = prompt_run_map.get(prompt.id, [])
                    latest = runs[0] if runs else None
                    prompt.run_count = len(runs)
                    prompt.latest_run_at = latest.created_at if latest else None
                    prompt.latest_status = latest.status if latest else None
                    prompt.latest_response_text = latest.response_text if latest else ""
                    prompt.latest_brands = latest.brands if latest else []
                    prompt.latest_cited_domains = latest.cited_domains if latest else []
                    prompt.latest_cited_urls = latest.cited_urls if latest else []
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

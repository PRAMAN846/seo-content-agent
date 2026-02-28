from __future__ import annotations

from app.core.config import settings
from app.models.schemas import ArticleSummary
from app.services.llm_client import llm_client

BRIEF_INSTRUCTION = (
    "You are an SEO brief strategist. Create an editable markdown content brief using the competitor analysis and source summaries. "
    "Include these sections with markdown headings: Primary Query, Search Intent, Target Audience, Recommended Title, Meta Description, "
    "Core Keywords, Questions To Answer, Competitor Gaps To Win, Recommended Outline, Tone And Brand Notes, CTA Notes. "
    "Keep the brief practical so a human editor can modify it before writing."
)

FALLBACK_BRIEF_INSTRUCTION = (
    "You are an SEO strategist creating a provisional content brief from only a search query. "
    "State reasonable assumptions clearly. Return editable markdown with headings: Primary Query, Search Intent, "
    "Target Audience, Recommended Title, Meta Description, Core Keywords, Questions To Answer, "
    "Recommended Outline, Tone And Brand Notes, CTA Notes."
)


def build_brief(query: str, summaries: list[ArticleSummary], seo_analysis: str) -> str:
    return build_brief_with_customization(query, summaries, seo_analysis, "", "", "")


def build_brief_with_customization(
    query: str,
    summaries: list[ArticleSummary],
    seo_analysis: str,
    brand_name: str,
    brand_url: str,
    prompt_override: str,
) -> str:
    joined = "\n\n".join("Source: {}\n{}".format(summary.url, summary.summary) for summary in summaries)
    extra = []
    if brand_name:
        extra.append("Brand name: {}".format(brand_name))
    if brand_url:
        extra.append("Brand URL: {}".format(brand_url))
    if prompt_override:
        extra.append("Custom brief instructions:\n{}".format(prompt_override))
    return llm_client.complete(
        model=settings.analyst_model,
        instruction=BRIEF_INSTRUCTION,
        input_text="Primary query: {}\n\n{}\n\nCompetitor summaries:\n{}\n\nSEO analysis:\n{}".format(
            query,
            "\n".join(extra),
            joined,
            seo_analysis,
        ),
    )


def build_brief_from_query(query: str) -> str:
    return build_brief_from_query_with_customization(query, "", "", "")


def build_brief_from_query_with_customization(
    query: str,
    brand_name: str,
    brand_url: str,
    prompt_override: str,
) -> str:
    extra = []
    if brand_name:
        extra.append("Brand name: {}".format(brand_name))
    if brand_url:
        extra.append("Brand URL: {}".format(brand_url))
    if prompt_override:
        extra.append("Custom brief instructions:\n{}".format(prompt_override))
    return llm_client.complete(
        model=settings.analyst_model,
        instruction=FALLBACK_BRIEF_INSTRUCTION,
        input_text="Primary query: {}\n\n{}".format(query, "\n".join(extra)),
    )

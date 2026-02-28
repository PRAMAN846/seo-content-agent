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
    joined = "\n\n".join("Source: {}\n{}".format(summary.url, summary.summary) for summary in summaries)
    return llm_client.complete(
        model=settings.analyst_model,
        instruction=BRIEF_INSTRUCTION,
        input_text="Primary query: {}\n\nCompetitor summaries:\n{}\n\nSEO analysis:\n{}".format(query, joined, seo_analysis),
    )


def build_brief_from_query(query: str) -> str:
    return llm_client.complete(
        model=settings.analyst_model,
        instruction=FALLBACK_BRIEF_INSTRUCTION,
        input_text="Primary query: {}".format(query),
    )

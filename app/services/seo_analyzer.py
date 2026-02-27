from __future__ import annotations

from app.core.config import settings
from app.models.schemas import ArticleSummary
from app.services.llm_client import llm_client

ANALYSIS_INSTRUCTION = (
    "You are a senior SEO strategist. Given article summaries, produce: "
    "1) common coverage, 2) common gaps, 3) tone/style pattern, 4) structural pattern, "
    "5) recommended outranking outline, 6) key entities/phrases to include."
)


def analyze_summaries(query: str, summaries: list[ArticleSummary]) -> str:
    joined = "\n\n".join(f"Source: {s.url}\n{s.summary}" for s in summaries)
    return llm_client.complete(
        model=settings.analyst_model,
        instruction=ANALYSIS_INSTRUCTION,
        input_text=f"Query: {query}\n\n{joined}",
    )

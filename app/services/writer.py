from __future__ import annotations

from app.core.config import settings
from app.services.llm_client import llm_client

WRITER_INSTRUCTION = (
    "You are an expert SEO writer. Write a new, original article that is factual and grounded in the source analysis. "
    "Constraints: 1500-2000 words, clear H2/H3 structure, intro, actionable steps, FAQ, conclusion, "
    "meta title and meta description at top. Return markdown only."
)

BRIEF_WRITER_INSTRUCTION = (
    "You are an expert SEO writer. Write a detailed, original article from the provided content brief. "
    "Use the outline, keyword guidance, search intent, and editorial notes in the brief. "
    "Constraints: 1500-2000 words, clear H2/H3 structure, intro, actionable steps, FAQ, conclusion, "
    "meta title and meta description at top. Return markdown only."
)


def write_article(query: str, seo_analysis: str) -> str:
    return llm_client.complete(
        model=settings.writer_model,
        instruction=WRITER_INSTRUCTION,
        input_text=f"Primary query: {query}\n\nSEO analysis:\n{seo_analysis}",
    )


def write_article_from_brief(query: str, brief_markdown: str) -> str:
    return llm_client.complete(
        model=settings.writer_model,
        instruction=BRIEF_WRITER_INSTRUCTION,
        input_text="Primary query: {}\n\nContent brief:\n{}".format(query, brief_markdown),
    )

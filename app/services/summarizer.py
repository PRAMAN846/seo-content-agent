from __future__ import annotations

from app.core.config import settings
from app.models.schemas import ArticleSummary, UrlContent
from app.services.llm_client import llm_client

SUMMARY_INSTRUCTION = (
    "You are an SEO analyst. Summarize article with sections: intent, key topics, strengths, "
    "missing points, tone, structure, estimated word count, likely target keywords. "
    "Return concise markdown."
)


def summarize_article(article: UrlContent) -> ArticleSummary:
    if len(article.text.split()) < 80:
        summary_text = "Content too short for reliable SEO summary."
    else:
        summary_text = llm_client.complete(
            model=settings.small_model,
            instruction=SUMMARY_INSTRUCTION,
            input_text=f"URL: {article.url}\nTitle: {article.title}\n\n{article.text}",
        )
    return ArticleSummary(url=article.url, summary=summary_text)

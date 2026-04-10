from __future__ import annotations

import re
from pathlib import Path
from uuid import uuid4

from app.core.config import settings
from app.models.schemas import ArticleImageAsset
from app.services.llm_client import llm_client

IMAGE_PLANNER_INSTRUCTION = """You are planning production-ready images for a long-form blog article.
Return valid JSON only in this shape:
{"images":[{"title":"","section_heading":"","placement":"hero","alt_text":"","prompt":""}]}

Rules:
- Suggest at most the requested number of images.
- The first image must be a hero image near the top of the article.
- The remaining images must align to H2 sections already present in the article.
- Prompts must be visually specific and ready for image generation.
- Avoid text overlays, logos, watermarks, UI chrome, or infographic labels unless the article explicitly requires them.
- Use landscape composition suitable for blog layouts.
- Alt text must clearly describe the final visual for readers and accessibility.
- Prefer tasteful editorial visuals that elevate the article instead of generic stock-photo scenes.
"""


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "article"


def _normalize_heading(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _extract_h2_headings(article_markdown: str) -> list[str]:
    headings: list[str] = []
    for line in article_markdown.splitlines():
        if line.startswith("## "):
            heading = line[3:].strip()
            if heading:
                headings.append(heading)
    return headings


def plan_article_images(
    *,
    query: str,
    brief_markdown: str,
    article_markdown: str,
    brand_name: str = "",
) -> list[dict[str, str]]:
    if not llm_client.enabled or settings.article_image_count <= 0:
        return []

    headings = _extract_h2_headings(article_markdown)
    if not article_markdown.strip():
        return []

    planner_input = "\n".join(
        [
            f"Primary query: {query}",
            f"Brand name: {brand_name or 'None provided'}",
            f"Maximum images: {settings.article_image_count}",
            "Available H2 headings:",
            *(f"- {heading}" for heading in headings[:12]),
            "",
            "Source brief:",
            brief_markdown,
            "",
            "Article markdown:",
            article_markdown,
        ]
    )
    payload = llm_client.complete_json(
        model=settings.writer_model,
        instruction=IMAGE_PLANNER_INSTRUCTION,
        input_text=planner_input,
        reasoning_effort=settings.writer_reasoning_effort,
    )
    raw_images = payload.get("images") if isinstance(payload, dict) else None
    if not isinstance(raw_images, list):
        return []

    planned: list[dict[str, str]] = []
    for index, item in enumerate(raw_images[: settings.article_image_count]):
        if not isinstance(item, dict):
            continue
        prompt = str(item.get("prompt") or "").strip()
        alt_text = str(item.get("alt_text") or "").strip()
        if not prompt or not alt_text:
            continue
        placement = "hero" if index == 0 else str(item.get("placement") or "inline").strip().lower()
        planned.append(
            {
                "title": str(item.get("title") or f"Article visual {index + 1}").strip(),
                "section_heading": str(item.get("section_heading") or "").strip(),
                "placement": "hero" if placement == "hero" else "inline",
                "alt_text": alt_text,
                "prompt": prompt,
            }
        )
    return planned


def generate_article_images(
    *,
    query: str,
    brief_markdown: str,
    article_markdown: str,
    brand_name: str = "",
) -> list[ArticleImageAsset]:
    planned = plan_article_images(
        query=query,
        brief_markdown=brief_markdown,
        article_markdown=article_markdown,
        brand_name=brand_name,
    )
    if not planned:
        return []

    assets: list[ArticleImageAsset] = []
    exports_dir = Path("exports") / "images"
    query_slug = _slugify(query)

    for index, image_plan in enumerate(planned, start=1):
        image_id = uuid4().hex
        filename = f"{query_slug}-{index}-{image_id[:8]}.png"
        output_path = exports_dir / filename
        generated = llm_client.generate_image(
            prompt=image_plan["prompt"],
            output_path=output_path,
            model=settings.image_model,
            size=settings.article_image_size,
            quality=settings.article_image_quality,
        )
        assets.append(
            ArticleImageAsset(
                id=image_id,
                title=image_plan["title"],
                alt_text=image_plan["alt_text"],
                prompt=image_plan["prompt"],
                revised_prompt=generated.get("revised_prompt", ""),
                section_heading=image_plan["section_heading"],
                placement=image_plan["placement"],
                local_path=generated["path"],
                public_url=f"/exports/images/{filename}",
            )
        )

    return assets


def inject_article_images(article_markdown: str, image_assets: list[ArticleImageAsset]) -> str:
    if not article_markdown.strip() or not image_assets:
        return article_markdown

    lines = article_markdown.splitlines()
    result: list[str] = []
    hero_asset = image_assets[0]
    inline_assets = image_assets[1:]
    hero_inserted = False
    inline_map: dict[str, list[ArticleImageAsset]] = {}
    leftovers: list[ArticleImageAsset] = []

    for asset in inline_assets:
        key = _normalize_heading(asset.section_heading)
        if key:
            inline_map.setdefault(key, []).append(asset)
        else:
            leftovers.append(asset)

    h1_seen = False
    for line in lines:
        result.append(line)
        if line.startswith("# ") and not h1_seen:
            h1_seen = True
            if not hero_inserted:
                result.extend(["", _render_image_markdown(hero_asset), ""])
                hero_inserted = True
            continue

        if line.startswith("## "):
            key = _normalize_heading(line[3:].strip())
            matches = inline_map.pop(key, [])
            for asset in matches:
                result.extend(["", _render_image_markdown(asset), ""])

    if not hero_inserted:
        result = [article_markdown, "", _render_image_markdown(hero_asset)]
        hero_inserted = True

    for remaining in inline_map.values():
        leftovers.extend(remaining)

    if leftovers:
        result.append("")
        for asset in leftovers:
            result.extend([_render_image_markdown(asset), ""])

    return "\n".join(result).strip()


def _render_image_markdown(asset: ArticleImageAsset) -> str:
    caption = asset.title.strip() or asset.alt_text.strip()
    return "\n".join(
        [
            f"![{asset.alt_text}]({asset.public_url})",
            f"*{caption}*",
        ]
    )

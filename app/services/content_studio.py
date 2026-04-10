from __future__ import annotations

from dataclasses import dataclass, field
import re
from pathlib import Path
from typing import Callable, Iterable, Optional
from uuid import uuid4

from app.core.config import settings
from app.models.schemas import (
    ContentStudioArtifactRecord,
    ContentStudioCatalogResponse,
    ContentStudioChatRecord,
    ContentStudioChatMessageRecord,
    ContentStudioChatSendResponse,
    ContentStudioMessageResponse,
    ContentStudioSettingHint,
    ContentStudioSkillDefinition,
    ContentStudioWorkflowDefinition,
    UserPublic,
    WorkspaceMessage,
)
from app.models.store import run_store
from app.services.billing import record_content_studio_billing, usage_scope
from app.services.llm_client import llm_client
from app.services.content_meta_router import answer_content_meta_request


def _hint(key: str, label: str, description: str, *, required: bool = False) -> ContentStudioSettingHint:
    return ContentStudioSettingHint(key=key, label=label, description=description, required=required)


DEFAULT_SKILLS: list[ContentStudioSkillDefinition] = [
    ContentStudioSkillDefinition(
        id="content_brief",
        name="Content Brief",
        category="strategy",
        description="Create research-backed informational briefs with clear angle, outline, metadata, and evidence guidance.",
        when_to_use="Use when the user needs strategy, research framing, SERP gap analysis, or a production-ready brief before drafting.",
        required_inputs=[
            _hint("topic", "Topic", "The topic or working title for the informational brief.", required=True),
            _hint("primary_keyword", "Primary keyword", "The main keyword the brief should target.", required=True),
            _hint("secondary_keywords", "Secondary keywords", "Supporting keywords or closely related queries.", required=True),
            _hint("target_country", "Target country", "Country used for SERP framing and language/market context.", required=True),
        ],
    ),
    ContentStudioSkillDefinition(
        id="content_writer",
        name="Content Writer",
        category="drafting",
        description="Turn an approved informational brief into a natural, publish-ready article draft.",
        when_to_use="Use when the user already has a strong brief and wants a full article draft aligned to it.",
        required_inputs=[
            _hint("content_brief", "Approved informational content brief", "A full brief is the source of truth for scope, outline, intent, and evidence.", required=True),
            _hint("target_country", "Target country", "Sets the writing variant and country-specific English rules.", required=True),
            _hint("source_list", "Source list", "Evidence base from the approved brief used to keep claims grounded."),
        ],
    ),
    ContentStudioSkillDefinition(
        id="content_editor",
        name="Editorial Review",
        category="editing",
        description="Review and improve an existing draft for clarity, naturalness, structure, usefulness, and publish-readiness without rewriting it into something else.",
        when_to_use="Use when the user already has a draft and wants line-by-line editorial review, cleaner language, tighter structure, or a more natural final version.",
        required_inputs=[
            _hint("draft_article", "Full draft article", "The editor improves an existing article, not a keyword-only idea.", required=True),
        ],
    ),
    ContentStudioSkillDefinition(
        id="internal_linking",
        name="Internal Linking",
        category="optimization",
        description="Identify or insert natural internal links using approved URLs and the right page types.",
        when_to_use="Use when the article draft exists and the user wants internal links added or reviewed safely.",
        required_inputs=[
            _hint("draft_article", "Full article draft or relevant sections", "The linking pass needs article context, not only keywords.", required=True),
        ],
    ),
    ContentStudioSkillDefinition(
        id="image_generation",
        name="Image Generate & Review",
        category="visuals",
        description="Inspect the article, recommend the highest-value editorial visuals, generate them when asked, and attach usable image guidance.",
        when_to_use="Use when the user wants the article reviewed for image opportunities, image prompts generated, visuals created, or existing visuals reviewed.",
        required_inputs=[
            _hint("draft_article", "Full article draft or relevant section", "The image pass should inspect the article before recommending or generating visuals.", required=True),
        ],
    ),
    ContentStudioSkillDefinition(
        id="publish_qa",
        name="Final Publish QA",
        category="qa",
        description="Run the final publication review across copy, metadata, internal links, visuals, and country-language consistency.",
        when_to_use="Use when the article package is nearly ready and the user wants a fast verdict plus prioritized fixes.",
        required_inputs=[
            _hint("draft_article", "Full article draft", "QA reviews the actual article package, not just notes.", required=True),
        ],
    ),
]

DEFAULT_WORKFLOWS: list[ContentStudioWorkflowDefinition] = [
    ContentStudioWorkflowDefinition(
        id="full_editorial_workflow",
        name="Full Editorial Workflow",
        description="Run the default brief -> draft -> edit -> internal links -> visuals -> publish QA flow.",
        skill_ids=[
            "content_brief",
            "content_writer",
            "content_editor",
            "internal_linking",
            "image_generation",
            "publish_qa",
        ],
    ),
    ContentStudioWorkflowDefinition(
        id="draft_to_publish",
        name="Draft To Publish",
        description="Start from an existing draft, tighten it, add links and visuals where helpful, then run final QA.",
        skill_ids=[
            "content_editor",
            "internal_linking",
            "image_generation",
            "publish_qa",
        ],
    ),
    ContentStudioWorkflowDefinition(
        id="brief_to_draft",
        name="Brief To Draft",
        description="Go from strategic brief to first complete article draft.",
        skill_ids=["content_brief", "content_writer"],
    ),
]

RECOMMENDED_SETTINGS: list[ContentStudioSettingHint] = [
    _hint("brand_name", "Brand name", "Client or brand name used in content context.", required=True),
    _hint("brand_url", "Brand URL", "Main site or product URL for brand grounding.", required=True),
    _hint("default_target_country", "Default target country", "Country or market most content is written for.", required=True),
    _hint("brand_positioning", "Brand positioning notes", "When and how the brand should be mentioned."),
    _hint("editorial_voice", "Editorial voice", "Tone, style, and what strong writing sounds like for this account."),
    _hint("editorial_quality_bar", "Editorial quality bar", "Non-negotiables for usefulness, evidence, and clarity."),
    _hint("sitemap_url", "Sitemap URL", "Approved sitemap for internal link discovery."),
    _hint("approved_domains", "Approved domains", "Domains that can be linked or cited safely."),
    _hint("visual_style_notes", "Visual style notes", "Preferred illustration direction, layout rules, and image constraints."),
]

EXPORT_FORMATS = [
    "Markdown (.md)",
    "Word (.docx)",
    "HTML (.html)",
    "Plain text (.txt)",
]

INPUT_REQUEST_TERMS = [
    "what inputs",
    "what input",
    "what do you need",
    "what do you need from me",
    "what details",
    "what information",
    "required inputs",
    "which inputs",
    "what should i provide",
    "what should i send",
    "what all do you need",
    "what fields",
]

SKILL_UPDATE_TRIGGER_TERMS = [
    "update the relevant skill",
    "update relevant skill",
    "update the skill",
    "save this to the skill",
    "save this in the skill",
    "remember this in the skill",
    "skill update",
]

MODEL_STRATEGY = (
    "Keep model routing backend-managed by default so quality and cost stay predictable. "
    "Expose a model picker later as an advanced option once usage data shows where agency users truly need overrides."
)

STUDIO_IMAGE_PLAN_INSTRUCTION = """You are preparing content visuals for a Content Studio chat request.
Return valid JSON only in this shape:
{"images":[{"title":"","alt_text":"","prompt":""}]}

Rules:
- Suggest only the number of images requested, defaulting to one if the user did not specify a count.
- Prefer blog-ready editorial visuals, not decorative filler.
- Prompts must be detailed enough for direct image generation.
- Avoid logos, watermarks, fake UI, unreadable text overlays, and cluttered infographic layouts unless the user explicitly asks for them.
- Keep compositions suitable for article embeds and content marketing use.
- Alt text must clearly describe the final image.
"""

IMAGE_GENERATION_VERBS = ("generate", "create", "make", "produce", "render")
IMAGE_GENERATION_NOUNS = ("image", "images", "visual", "visuals", "illustration", "illustrations", "graphic", "graphics", "hero image", "diagram")
IMAGE_GENERATION_NEGATIONS = (
    "prompt only",
    "just the prompt",
    "only the prompt",
    "alt text only",
    "review this image",
    "review these images",
    "do not generate",
    "don't generate",
    "without generating",
)
IMAGE_COUNT_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
}

FIELD_LABEL_ALIASES = {
    "topic": ["topic", "title", "working title", "topic or title", "topic/title"],
    "primary_keyword": ["primary keyword", "target keyword", "main keyword", "focus keyword"],
    "secondary_keywords": ["secondary keywords", "supporting keywords", "related keywords"],
    "target_country": ["target country", "country", "market", "country/english", "country / english"],
    "target_audience_notes": ["target audience notes", "audience", "audience notes"],
    "top_ranking_urls": ["top ranking urls", "ranking urls", "top urls", "serp urls"],
    "ai_overview_answer_text": ["ai overview answer text", "ai overview text", "ai answer text"],
    "ai_overview_cited_urls": ["ai overview cited urls", "ai overview citations", "ai cited urls"],
    "people_also_ask_questions": ["people also ask questions", "people also ask", "paa"],
    "must_include_sources": ["must-include sources", "must include sources", "sources"],
    "approved_internal_urls_or_sitemap_constraints": [
        "approved internal urls or sitemap constraints",
        "approved internal urls",
        "internal urls",
        "sitemap constraints",
    ],
    "approved_product_page_references": [
        "approved product page references",
        "approved product/commercial page references",
        "product page references",
        "product pages",
    ],
    "workflow_scope": ["workflow scope", "full workflow or stop after a stage", "workflow"],
    "approval_between_stages": ["approval between stages", "approve between stages", "approval"],
    "article_slug": ["article slug", "slug"],
    "content_brief": ["content brief", "brief", "input brief", "approved brief", "full brief"],
    "editor_notes": ["editor notes", "special focus notes", "focus notes"],
    "existing_draft_fragments": ["existing draft fragments", "draft fragments"],
    "product_mention_preference": ["product mention preference", "product mentions", "brand mention preference"],
    "draft_article": ["draft article", "full draft article", "article draft", "written content", "article"],
    "original_content_brief": ["original content brief"],
    "meta_title": ["meta title", "title tag"],
    "meta_description": ["meta description", "meta desc"],
    "slug": ["slug", "url slug"],
    "suggested_visuals_section": ["suggested visuals section", "visuals section"],
    "internal_link_notes": ["internal link notes", "linked draft", "internal links"],
    "brand_mention_preference": ["brand mention preference"],
    "editor_focus_notes": ["editor focus notes", "review focus", "editorial focus"],
    "approved_internal_urls_or_sitemap": ["approved internal urls or sitemap", "approved internal urls", "sitemap"],
    "approved_url_list": ["approved url list", "approved urls"],
    "article_section_context": ["article section context", "section context", "article title or section title"],
    "image_purpose": ["image purpose", "visual purpose"],
    "what_to_include": ["what to include", "include"],
    "image_type": ["image type", "visual type"],
    "what_to_avoid": ["what to avoid", "avoid"],
    "surrounding_text_or_section_summary": ["surrounding text", "section summary", "surrounding text or section summary"],
    "brand_product_context_required": ["brand/product context required", "brand context required"],
    "visual_guidelines": ["visual guidelines", "image guidelines"],
    "source_list": ["source list", "sources list", "references"],
}

SKILL_DIRECTIVE_ALIASES = {
    "content_brief": ["content brief", "brief skill"],
    "content_writer": ["content writer", "writer skill"],
    "content_editor": ["editorial review", "content editor", "editorial skill"],
    "internal_linking": ["internal linking", "internal linking skill", "internal link skill"],
    "image_generation": ["image generate", "image generation", "image skill", "generate images", "create images"],
    "publish_qa": ["final publish qa", "publish qa", "qa skill", "quality check"],
}

FIELD_LINE_PATTERN = re.compile(r"^\s*([A-Za-z][A-Za-z0-9 /&()'_-]{1,80})\s*:\s*(.*)$")
DIRECT_EXECUTION_TERMS = ("use ", "using ", "with ", "run ", "review ", "edit ", "write ", "create ", "generate ")
FREEFORM_LEAD_PATTERNS = (
    r"^\s*here(?:'s| is)\b.*?\n\s*\n",
    r"^\s*below(?:'s| is)\b.*?\n\s*\n",
)

SKILL_UPDATE_REGEX = re.compile(
    r"\b(update|change|modify|improve|revise|tweak|adjust|save|remember)\b.*?\bskill\b",
    flags=re.IGNORECASE | re.DOTALL,
)

SKILL_UPDATE_PENDING_PHRASES = (
    "what update would you like me to save to",
    "which skill should i update",
)

SKILL_INSTRUCTION_REQUEST_TERMS = (
    "share complete skill instructions",
    "share the complete skill instructions",
    "share complete skills instructions",
    "share the full skill instructions",
    "show the full skill instructions",
    "show complete skill instructions",
    "show the skill instructions",
    "show me the skill instructions",
    "what are the current instructions",
    "what are the full instructions",
    "what instructions are currently available",
    "what instructions are available",
    "which instructions are currently available",
    "what instructions does it have",
    "what does the skill currently have",
    "show current instructions",
    "share current instructions",
)


@dataclass
class SkillUpdateHandlingResult:
    status: str = "none"
    reply: Optional[str] = None
    skill_ids: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    scope: str = "project"


SKILL_INSTRUCTIONS = {
    "content_brief": (
        "Use the informational content brief skill contract faithfully. Create only the brief, not the article. "
        "This skill handles informational briefs only, not listicles, alternatives pages, competitor reviews, vs pages, "
        "template or policy content, landing pages, or article drafting. "
        "The required user inputs for this skill are exactly: Topic, Primary keyword, Secondary keywords, and Target country. "
        "Helpful optional inputs are: Target audience notes, Top ranking URLs, AI overview answer text, AI overview cited URLs, "
        "People also ask questions, Must-include sources, Approved internal URLs or sitemap constraints, Approved product/commercial page references, "
        "whether to stop after a stage or run the full workflow, whether approval is needed between stages, and article slug. "
        "If the user asks what inputs are needed, ask only for those fields in a clear Required inputs and Helpful optional inputs structure. "
        "Do not replace this intake with generic fields like KPI, deadline, competitors to beat, tone, length target, or broad marketing questionnaire items. "
        "If optional SERP or AI overview inputs are missing, continue in fallback mode and say they were not provided instead of blocking. "
        "Focus on informational search intent, evidence-backed claims, differentiation, metadata, outline guidance, FAQs, and source discipline. "
        "If evidence is weak, say so instead of padding. Do not invent statistics, quotes, links, product claims, or internal URLs."
    ),
    "content_writer": (
        "Write a publish-ready informational article from the brief. Satisfy search intent early, keep the draft practical, "
        "natural, and non-generic, and use country-appropriate English. Only recommend visuals that materially improve clarity."
    ),
    "content_editor": (
        "Edit an existing article for usefulness, clarity, flow, repetition, and naturalness. Preserve what already works, "
        "cut fluff, reduce AI-sounding phrasing, and avoid unsupported claims or forced brand mentions."
    ),
    "internal_linking": (
        "Improve internal linking safely. Never invent URLs. Prefer a small number of highly relevant links with natural anchors "
        "and the right page type for the reader's stage."
    ),
    "image_generation": (
        "Plan or review only useful editorial visuals. Avoid decorative filler, invented UI, overloaded infographics, and unreadable text. "
        "Provide detailed prompts, avoid guidance, placement suggestions, filenames, and alt text when helpful."
    ),
    "publish_qa": (
        "Run a final publication QA review. Focus on verdict, blockers, minor fixes, metadata quality, internal linking quality, "
        "visual usefulness, and country-language consistency. Do not turn QA into a full rewrite."
    ),
}

CONTENT_BRIEF_RUNTIME_RULES = """
When the active skill includes content_brief, apply these runtime rules:
- Treat the content brief intake contract as strict.
- If the user asks what inputs are needed, respond with these exact sections:
  Required inputs:
  - Topic
  - Primary keyword
  - Secondary keywords
  - Target country
  Helpful optional inputs:
  - Target audience notes
  - Top ranking URLs
  - AI overview answer text
  - AI overview cited URLs
  - People also ask questions
  - Must-include sources
  - Approved internal URLs or sitemap constraints
  - Approved product/commercial page references
  - Do you want the full workflow or to stop after a stage?
  - Do you want approval between stages? (yes/no)
  - Article slug
- You may offer a copy-paste template using those exact fields.
- Do not ask for KPI, deadline, target length, tone of voice, competitor list, or format as default required intake fields for this skill.
- If the user provides only the four required inputs, that is enough to begin the brief.
"""

CONTENT_WRITER_RUNTIME_RULES = """
When the active skill includes content_writer, apply these runtime rules:
- This skill writes publish-ready informational articles from an approved informational brief only.
- If the user asks what inputs are needed, ask for:
  Required inputs:
  - Full informational content brief
  Helpful optional inputs:
  - Target country if it is not already clear from the brief or project settings
  - Editor notes or special focus notes
  - Existing draft fragments to preserve
  - Product mention preference
- The brief should include, where possible: content brief title, primary keyword, secondary keywords, target country, suggested meta title, suggested meta description, suggested slug, target reader and search intent, semantic entities to include, content angle and differentiation, suggested length, key messaging to maintain, full outline with detailed writing guidance, FAQs with guidance, and source list.
- If the brief is incomplete, use what is available and prioritise outline, search intent, key messaging, and source list. Do not invent missing strategy.
- This skill is informational-only. Do not treat it as appropriate for listicles, competitor reviews, alternatives pages, vs pages, template or policy pages, landing pages, article briefs, metadata-only tasks, social copy, or standalone image generation.
- Set language mode from target country and apply it consistently. Never use em dashes.
- Add a suggested visuals section only when visuals materially improve clarity. Do not add decorative filler visuals under every heading.
- Keep brand/product mentions light, credible, and useful. Use only approved project/product references and never invent UI, features, or claims.
"""

CONTENT_EDITOR_RUNTIME_RULES = """
When the active skill includes content_editor, apply these runtime rules:
- This skill edits an existing informational article draft. It is not a first-draft writer and not a brief generator.
- If the user asks what inputs are needed, ask for:
  Required inputs:
  - Full draft article
  Helpful optional inputs:
  - Target country
  - Primary keyword
  - Original content brief
  - Secondary keywords
  - Meta title, meta description, and slug
  - Suggested visuals section
  - Internal link opportunities or existing inserted links
  - Brand mention preference: keep, soften, or remove unless essential
  - Editor focus notes
- If the user provides only a keyword or topic and no article draft, ask for the draft rather than behaving like a writer.
- If the user provides the draft and project settings already cover the target country, proceed without re-asking for it.
- Treat requests phrased as editorial review or content review as this skill.
- Default output order must be:
  1. Edited article
  2. Short change notes
- Preserve what works, improve usefulness first, cut repetition and AI-sounding phrasing, and avoid turning the article into a different piece.
- Keep country-appropriate English, preserve search intent, and never invent claims, examples, reviews, quotes, or case studies.
"""

INTERNAL_LINKING_RUNTIME_RULES = """
When the active skill includes internal_linking, apply these runtime rules:
- This skill is for internal linking only. It does not write the article from scratch, perform keyword research, or invent URLs.
- If the user asks what inputs are needed, ask for:
  Required inputs:
  - Full article draft or relevant article sections
  Helpful optional inputs:
  - Target country
  - Primary keyword
  - Original content brief
  - Approved internal URL list
- If an approved internal URL list is not provided, use the project sitemap URL and approved internal URLs as the source of truth for discoverable pages.
- If the article draft is supplied and project settings already provide internal URL sources or target country, proceed without re-asking for them.
- Never fabricate URLs.
- Prefer one strong link over several weak links, use natural anchors, avoid exact-match anchor stuffing, and choose the right page type for the reader stage.
- Product/commercial links should be sparse and genuinely useful. Help/support docs should only be used when they help with a real how-to step, not as positioning copy.
- If the user wants recommendations only, return section/paragraph, recommended target URL, suggested anchor text, page type, and why it fits.
- Otherwise return:
  1. Edited article with internal links added or clearly marked
  2. Short change notes
"""

IMAGE_GENERATION_RUNTIME_RULES = """
When the active skill includes image_generation, apply these runtime rules:
- This skill supports informational article visuals only.
- Supported modes are:
  1. Plan only
  2. Generate and review
  3. Review and refine
- If the user asks what inputs are needed, ask for:
  Required inputs:
  - Full article draft or relevant section
  Helpful optional inputs:
  - Target country
  - Image purpose
  - What to include
  - Preferred image count
  - Image type if already chosen
  - What to avoid
  - Relevant surrounding text or section summary
  - Whether brand/product context is required
- Default behavior is to inspect the article first, identify where visuals would materially improve clarity, and only then recommend or generate images.
- Do not force visuals where none are needed. Say no visual is recommended when appropriate.
- Avoid decorative filler, generic stock-photo style, fake data, invented product UI, tiny unreadable labels, and crowded infographic posters.
- Default planning output order must be:
  1. Image title
  2. Recommended filename
  3. Image type
  4. Placement suggestion
  5. Image purpose
  6. Detailed prompt
  7. Negative prompt or avoid guidance
  8. Aspect ratio or size recommendation
  9. Caption suggestion
  10. Alt text
  11. Short rationale
- For review mode, return:
  1. Pass or revise verdict
  2. What works
  3. Issues found
  4. Severity by issue
  5. Revised prompt
  6. Revised avoid guidance if needed
  7. Revised alt text if needed
  8. Short regeneration note
"""

PUBLISH_QA_RUNTIME_RULES = """
When the active skill includes publish_qa, apply these runtime rules:
- This is final pre-publish QA for informational articles only.
- If the user asks what inputs are needed, ask for:
  Required inputs:
  - Full article draft
  Helpful optional inputs:
  - Target country
  - Primary keyword
  - Meta title
  - Meta description
  - Slug
  - Original content brief
  - Suggested visuals section
  - Internal link notes or linked draft
  - Source list
  - Editor notes from earlier steps
- Proceed even if some supporting inputs are missing, but state what was not reviewed.
- Default output structure must be:
  # Publish QA verdict
  - Verdict
  - Confidence
  - Reviewed inputs
  - Not reviewed or missing
  ## Blocking issues
  ## Minor fixes
  ## What is already strong
  ## Recommended quick fixes
  ## Metadata check
  ## Internal linking check
  ## Visuals check
  ## Country-language check
  ## Brief alignment check
  ## Optional corrected lines
  ## Final note
- Focus on verdict, prioritised issues, and compact fixes. Do not turn QA into a full rewrite.
- Keep brand/product mention checks light and credibility-focused, not promotional.
"""

SKILL_RUNTIME_RULES = {
    "content_brief": CONTENT_BRIEF_RUNTIME_RULES,
    "content_writer": CONTENT_WRITER_RUNTIME_RULES,
    "content_editor": CONTENT_EDITOR_RUNTIME_RULES,
    "internal_linking": INTERNAL_LINKING_RUNTIME_RULES,
    "image_generation": IMAGE_GENERATION_RUNTIME_RULES,
    "publish_qa": PUBLISH_QA_RUNTIME_RULES,
}

SKILL_INPUT_SPECS = {
    "content_brief": {
        "required": ["topic", "primary_keyword", "secondary_keywords", "target_country"],
        "optional": [
            "target_audience_notes",
            "top_ranking_urls",
            "ai_overview_answer_text",
            "ai_overview_cited_urls",
            "people_also_ask_questions",
            "must_include_sources",
            "approved_internal_urls_or_sitemap_constraints",
            "approved_product_page_references",
            "workflow_scope",
            "approval_between_stages",
            "article_slug",
        ],
    },
    "content_writer": {
        "required": ["content_brief"],
        "optional": ["editor_notes", "existing_draft_fragments", "product_mention_preference"],
    },
    "content_editor": {
        "required": ["draft_article"],
        "optional": [
            "target_country",
            "primary_keyword",
            "original_content_brief",
            "secondary_keywords",
            "meta_title",
            "meta_description",
            "slug",
            "suggested_visuals_section",
            "internal_link_notes",
            "brand_mention_preference",
            "editor_focus_notes",
        ],
    },
    "internal_linking": {
        "required": ["draft_article"],
        "optional": ["target_country", "primary_keyword", "approved_internal_urls_or_sitemap", "original_content_brief", "approved_url_list"],
    },
    "image_generation": {
        "required": ["draft_article"],
        "optional": [
            "target_country",
            "article_section_context",
            "image_purpose",
            "what_to_include",
            "image_type",
            "what_to_avoid",
            "surrounding_text_or_section_summary",
            "brand_product_context_required",
            "visual_guidelines",
        ],
    },
    "publish_qa": {
        "required": ["draft_article"],
        "optional": [
            "target_country",
            "primary_keyword",
            "meta_title",
            "meta_description",
            "slug",
            "original_content_brief",
            "suggested_visuals_section",
            "internal_link_notes",
            "source_list",
            "editor_notes",
        ],
    },
}

INPUT_FIELD_LABELS = {
    "topic": "Topic",
    "primary_keyword": "Primary keyword",
    "secondary_keywords": "Secondary keywords",
    "target_country": "Target country",
    "target_audience_notes": "Target audience notes",
    "top_ranking_urls": "Top ranking URLs",
    "ai_overview_answer_text": "AI overview answer text",
    "ai_overview_cited_urls": "AI overview cited URLs",
    "people_also_ask_questions": "People also ask questions",
    "must_include_sources": "Must-include sources",
    "approved_internal_urls_or_sitemap_constraints": "Approved internal URLs or sitemap constraints",
    "approved_product_page_references": "Approved product/commercial page references",
    "workflow_scope": "Do you want the full workflow or to stop after a stage?",
    "approval_between_stages": "Do you want approval between stages? (yes/no)",
    "article_slug": "Article slug",
    "content_brief": "Full informational content brief",
    "editor_notes": "Editor notes or special focus notes",
    "existing_draft_fragments": "Existing draft fragments to preserve",
    "product_mention_preference": "Product mention preference",
    "draft_article": "Full draft article",
    "original_content_brief": "Original content brief",
    "meta_title": "Meta title",
    "meta_description": "Meta description",
    "slug": "Slug",
    "suggested_visuals_section": "Suggested visuals section",
    "internal_link_notes": "Internal link notes or existing inserted links",
    "brand_mention_preference": "Brand mention preference",
    "editor_focus_notes": "Editor focus notes",
    "approved_internal_urls_or_sitemap": "Approved internal URLs or sitemap",
    "approved_url_list": "Approved internal URL list",
    "article_section_context": "Article title or section title",
    "image_purpose": "Image purpose",
    "what_to_include": "What to include",
    "image_type": "Image type if already chosen",
    "what_to_avoid": "What to avoid",
    "surrounding_text_or_section_summary": "Relevant surrounding text or section summary",
    "brand_product_context_required": "Whether brand/product context is required",
    "visual_guidelines": "Visual guidelines",
    "source_list": "Source list",
}


def build_content_studio_catalog() -> ContentStudioCatalogResponse:
    return ContentStudioCatalogResponse(
        skills=DEFAULT_SKILLS,
        workflows=DEFAULT_WORKFLOWS,
        export_formats=EXPORT_FORMATS,
        recommended_settings=RECOMMENDED_SETTINGS,
        model_strategy=MODEL_STRATEGY,
    )


def _skill_definition(skill_id: str) -> Optional[ContentStudioSkillDefinition]:
    return next((skill for skill in DEFAULT_SKILLS if skill.id == skill_id), None)


def _normalize_skill_ids(skill_ids: Iterable[str]) -> list[str]:
    known_ids = {skill.id for skill in DEFAULT_SKILLS}
    seen: set[str] = set()
    normalized: list[str] = []
    for skill_id in skill_ids:
        cleaned = (skill_id or "").strip()
        if cleaned and cleaned in known_ids and cleaned not in seen:
            seen.add(cleaned)
            normalized.append(cleaned)
    return normalized


def _find_workflow(workflow_id: Optional[str]) -> Optional[ContentStudioWorkflowDefinition]:
    if not workflow_id:
        return None
    for workflow in DEFAULT_WORKFLOWS:
        if workflow.id == workflow_id:
            return workflow
    return None


def _infer_skill_ids(message_text: str) -> list[str]:
    lowered = message_text.lower()
    inferred: list[str] = []
    if any(term in lowered for term in ["brief", "outline", "serp", "research", "strategy"]):
        inferred.append("content_brief")
    if any(term in lowered for term in ["draft", "write", "article", "blog", "content"]):
        inferred.append("content_writer")
    if any(term in lowered for term in ["edit", "polish", "rewrite", "improve", "tighten", "editorial review", "content review"]):
        inferred.append("content_editor")
    if any(term in lowered for term in ["internal link", "internal linking", "anchor text", "sitemap"]):
        inferred.append("internal_linking")
    if any(term in lowered for term in ["image", "visual", "illustration", "diagram", "alt text"]):
        inferred.append("image_generation")
    if any(term in lowered for term in ["qa", "quality check", "publish", "final review", "metadata"]):
        inferred.append("publish_qa")
    return _normalize_skill_ids(inferred)


def _infer_explicit_skill_targets(message_text: str) -> list[str]:
    lowered = (message_text or "").strip().lower()
    inferred: list[str] = []
    for skill in DEFAULT_SKILLS:
        aliases = set(SKILL_DIRECTIVE_ALIASES.get(skill.id, []))
        aliases.add(skill.name.lower())
        aliases.add(skill.id.replace("_", " "))
        if any(alias in lowered for alias in aliases):
            inferred.append(skill.id)
    return _normalize_skill_ids(inferred)


def _active_skill_ids(*, selected_skill_ids: list[str], workflow_id: Optional[str], messages: list[WorkspaceMessage]) -> list[str]:
    normalized = _normalize_skill_ids(selected_skill_ids)
    workflow = _find_workflow(workflow_id)
    if workflow:
        normalized = _normalize_skill_ids([*workflow.skill_ids, *normalized])
    if normalized:
        return normalized
    latest_message = messages[-1].content if messages else ""
    inferred = _infer_skill_ids(latest_message)
    return inferred or ["content_writer"]


def _looks_like_skill_update_request(message_text: str) -> bool:
    lowered = (message_text or "").strip().lower()
    return any(term in lowered for term in SKILL_UPDATE_TRIGGER_TERMS) or bool(SKILL_UPDATE_REGEX.search(message_text or ""))


def _recent_skill_ids_from_chat(chat: ContentStudioChatRecord) -> list[str]:
    for message in reversed(chat.messages):
        if message.active_skill_ids:
            return _normalize_skill_ids(message.active_skill_ids)
    return []


def _parse_skill_update_scope(message_text: str) -> str:
    lowered = (message_text or "").strip().lower()
    workspace_terms = ["workspace level", "workspace-level", "workspace wide", "workspace-wide", "all projects", "every project"]
    if any(term in lowered for term in workspace_terms):
        return "workspace"
    return "project"


def _rewrite_skill_update_instruction(message_text: str) -> str:
    text = (message_text or "").strip()
    patterns = [
        r"^(?:(?:please|kindly)\s+)?(?:(?:i|we)\s+(?:want|need|would like)\s+to\s+)?(?:update|change|modify|improve|revise|tweak|adjust)\b.*?\bskill\b\s*",
        r"^(please\s+)?update (the )?(relevant )?skill\s*(for this project|for this workspace|at project level|at workspace level|project level|workspace level)?\s*(to|so that)?\s*[:,-]?\s*",
        r"^(?:(?:please|kindly)\s+)?(?:(?:i|we)\s+(?:want|need|would like)\s+to\s+)?(?:save|remember)\b.*?\bskill\b\s*",
        r"^(please\s+)?save this (to|in) (the )?skill\s*[:,-]?\s*",
        r"^(please\s+)?remember this in (the )?skill\s*[:,-]?\s*",
        r"^skill update\s*[:,-]?\s*",
    ]
    for pattern in patterns:
        cleaned = re.sub(pattern, "", text, flags=re.IGNORECASE | re.DOTALL).strip(" \t:-,")
        if cleaned and cleaned.lower() != text.lower():
            text = cleaned
            break
    text = re.sub(
        r"^(for this project|for this workspace|at project level|at workspace level|project level|workspace level|workspace-wide|project-wide)\b",
        "",
        text,
        flags=re.IGNORECASE,
    ).strip(" \t:-,")
    text = re.sub(
        r"^(to|so that|with this rule|with this change|based on this feedback|based on this)\b",
        "",
        text,
        flags=re.IGNORECASE,
    ).strip(" \t:-,")
    text = re.sub(r"^that\b", "", text, flags=re.IGNORECASE).strip(" \t:-,")
    follow_up_markers = [
        r"\bafter updating the skill\b",
        r"\bafter updating\b",
        r"\bafter you update\b",
        r"\bcan you share\b",
        r"\bcan you show\b",
        r"\bshow me\b",
        r"\bshare the full\b",
        r"\bshare complete\b",
    ]
    for marker in follow_up_markers:
        text = re.split(marker, text, maxsplit=1, flags=re.IGNORECASE)[0].strip(" \t:-,.?&")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _is_missing_skill_update_instruction(message_text: str, rewritten_instruction: str) -> bool:
    if not rewritten_instruction.strip():
        return True
    normalized_original = re.sub(r"\s+", " ", (message_text or "").strip().lower())
    normalized_rewritten = re.sub(r"\s+", " ", rewritten_instruction.strip().lower())
    if normalized_original == normalized_rewritten and _looks_like_skill_update_request(message_text):
        return True
    return False


def _pending_skill_update_context_from_messages(messages: list[ContentStudioChatMessageRecord]) -> tuple[bool, list[str], Optional[str]]:
    for message in reversed(messages):
        if message.role != "assistant":
            continue
        lowered = (message.content or "").strip().lower()
        if not any(phrase in lowered for phrase in SKILL_UPDATE_PENDING_PHRASES):
            continue
        scope = "workspace" if "workspace" in lowered else "project"
        return True, _normalize_skill_ids(message.active_skill_ids), scope
    return False, [], None


def _clarify_skill_update_reply(
    *,
    skill_id: Optional[str],
    scope: str,
    needs_target: bool,
) -> tuple[str, list[str], list[str]]:
    scope_label = "workspace" if scope == "workspace" else "project"
    if needs_target:
        reply = (
            "I can save that as a persistent skill update, but I need the target skill to be explicit. "
            "Tell me which skill to update, and if you want, also say whether it should be saved at project or workspace level."
        )
        return reply, [], ["Awaiting skill name for skill update."]

    skill_name = _skill_definition(skill_id).name if skill_id and _skill_definition(skill_id) else (skill_id or "that skill")
    reply = (
        f"What update would you like me to save to {skill_name} for this {scope_label}? "
        "Send the rule itself or a short bullet list, and I'll save it for future runs."
    )
    return reply, [skill_id] if skill_id else [], [f"Awaiting skill update details for {skill_name}."]


def _resolve_skill_update_target(
    *,
    message_text: str,
    selected_skill_ids: list[str],
    workflow_id: Optional[str],
    recent_skill_ids: list[str],
) -> list[str]:
    explicit = _infer_explicit_skill_targets(message_text)
    if explicit:
        return explicit
    selected = _normalize_skill_ids(selected_skill_ids)
    if len(selected) == 1:
        return selected
    workflow = _find_workflow(workflow_id)
    if workflow and len(workflow.skill_ids) == 1:
        return list(workflow.skill_ids)
    recent = _normalize_skill_ids(recent_skill_ids)
    if len(recent) == 1:
        return recent
    return []


def effective_skill_instruction(*, current_user: UserPublic, project_id: str, skill_id: str) -> str:
    base = SKILL_INSTRUCTIONS.get(skill_id, "")
    overrides = run_store.get_effective_content_skill_overrides(current_user.id, project_id=project_id, skill_id=skill_id)
    if not overrides:
        return base
    lines = [base, "", "Persistent skill updates:"]
    for override in overrides:
        scope_label = "Workspace" if override.scope == "workspace" else "Project"
        lines.append(f"- {scope_label}: {override.instruction}")
    return "\n".join(part for part in lines if part is not None).strip()


PROJECT_BACKED_FIELD_HINTS = {
    "target_country": "Can auto-fill from project settings when a default target country is configured.",
    "target_audience_notes": "Can auto-fill from project settings when target audience notes are configured.",
    "approved_internal_urls_or_sitemap_constraints": "Can auto-fill from project settings when approved internal URLs or a sitemap URL are configured.",
    "approved_product_page_references": "Can auto-fill from project settings when approved product/commercial page URLs are configured.",
    "product_mention_preference": "Can auto-fill from project settings when brand positioning notes are configured.",
    "brand_mention_preference": "Can auto-fill from project settings when brand positioning notes are configured.",
    "approved_internal_urls_or_sitemap": "Can auto-fill from project settings when approved internal URLs or a sitemap URL are configured.",
    "visual_guidelines": "Can auto-fill from project settings when visual guidelines are configured.",
    "brand_product_context_required": "Can auto-fill from project settings when brand positioning or approved product page references are configured.",
}


def full_effective_skill_instruction(
    *,
    current_user: UserPublic,
    project_id: str,
    skill_id: str,
) -> str:
    skill = _skill_definition(skill_id)
    skill_name = skill.name if skill else skill_id
    project = _project_record(current_user, project_id)
    required_fields = SKILL_INPUT_SPECS.get(skill_id, {}).get("required", [])
    optional_fields = SKILL_INPUT_SPECS.get(skill_id, {}).get("optional", [])
    runtime_rules = (SKILL_RUNTIME_RULES.get(skill_id) or "").strip()
    overrides = run_store.get_effective_content_skill_overrides(current_user.id, project_id=project_id, skill_id=skill_id)

    autofill_fields: list[str] = []
    configured_autofill_fields: list[str] = []
    for field_key in [*required_fields, *optional_fields]:
        hint = PROJECT_BACKED_FIELD_HINTS.get(field_key)
        if hint and field_key not in autofill_fields:
            autofill_fields.append(field_key)
        summary = _project_backed_field_summary(project, field_key)
        if summary and field_key not in configured_autofill_fields:
            configured_autofill_fields.append(field_key)

    lines = [
        f"# {skill_name}",
        "",
        "## Core instruction",
        SKILL_INSTRUCTIONS.get(skill_id, "") or "No base instruction is defined for this skill.",
    ]

    if runtime_rules:
        lines.extend(
            [
                "",
                "## Runtime rules",
                runtime_rules,
            ]
        )

    lines.extend(["", "## Required inputs"])
    if required_fields:
        lines.extend(f"- {INPUT_FIELD_LABELS.get(field_key, field_key)}" for field_key in required_fields)
    else:
        lines.append("- No required inputs are defined for this skill.")

    lines.extend(["", "## Optional inputs"])
    if optional_fields:
        lines.extend(f"- {INPUT_FIELD_LABELS.get(field_key, field_key)}" for field_key in optional_fields)
    else:
        lines.append("- No optional inputs are defined for this skill.")

    if autofill_fields:
        lines.extend(["", "## Project-setting autofill"])
        for field_key in autofill_fields:
            label = INPUT_FIELD_LABELS.get(field_key, field_key)
            configured = _project_backed_field_summary(project, field_key)
            if configured:
                lines.append(f"- {label}: configured in this project now. {configured}.")
            else:
                lines.append(f"- {label}: {PROJECT_BACKED_FIELD_HINTS.get(field_key)}")

    lines.extend(["", "## Persistent skill updates"])
    if overrides:
        for override in overrides:
            scope_label = "Workspace" if override.scope == "workspace" else "Project"
            lines.append(f"- {scope_label}: {override.instruction}")
    else:
        lines.append("- No saved workspace/project overrides are active for this skill.")

    return "\n".join(lines).strip()


def _wants_full_skill_instruction_reply(message_text: str) -> bool:
    lowered = (message_text or "").strip().lower()
    if any(term in lowered for term in SKILL_INSTRUCTION_REQUEST_TERMS):
        return True
    has_instruction_language = any(term in lowered for term in ["instruction", "instructions", "rule", "rules"])
    has_skill_language = "skill" in lowered
    has_availability_language = any(term in lowered for term in ["current", "currently", "available", "full", "show", "share", "what"])
    return has_instruction_language and has_skill_language and has_availability_language


def answer_skill_meta_request_from_chat(
    *,
    current_user: UserPublic,
    project_id: str,
    message_text: str,
    selected_skill_ids: list[str],
    workflow_id: Optional[str],
    recent_skill_ids: list[str],
) -> SkillUpdateHandlingResult:
    if not _wants_full_skill_instruction_reply(message_text):
        return SkillUpdateHandlingResult()

    target_skill_ids = _resolve_skill_update_target(
        message_text=message_text,
        selected_skill_ids=selected_skill_ids,
        workflow_id=workflow_id,
        recent_skill_ids=recent_skill_ids,
    )
    if len(target_skill_ids) != 1:
        return SkillUpdateHandlingResult(
            status="clarify",
            reply="I can share the full current skill instructions, but I need the skill name to be explicit. Tell me which skill you want to inspect.",
            notes=["Awaiting skill name for instruction request."],
        )

    skill_id = target_skill_ids[0]
    skill_name = _skill_definition(skill_id).name if _skill_definition(skill_id) else skill_id
    instruction = full_effective_skill_instruction(current_user=current_user, project_id=project_id, skill_id=skill_id)
    reply = f"Here is the full current skill package for {skill_name}:\n\n{instruction}"
    return SkillUpdateHandlingResult(
        status="answered",
        reply=reply,
        skill_ids=[skill_id],
        notes=[f"Shared the current effective instructions for {skill_name}."],
    )


def save_skill_update_from_chat(
    *,
    current_user: UserPublic,
    project_id: str,
    message_text: str,
    selected_skill_ids: list[str],
    workflow_id: Optional[str],
    recent_skill_ids: list[str],
    pending: bool = False,
    pending_skill_ids: Optional[list[str]] = None,
    pending_scope: Optional[str] = None,
) -> SkillUpdateHandlingResult:
    explicit_request = _looks_like_skill_update_request(message_text)
    pending_skill_ids = _normalize_skill_ids(pending_skill_ids or [])
    is_pending_follow_up = bool(pending) and not explicit_request
    if not explicit_request and not is_pending_follow_up:
        return SkillUpdateHandlingResult()

    target_skill_ids = _resolve_skill_update_target(
        message_text=message_text,
        selected_skill_ids=selected_skill_ids,
        workflow_id=workflow_id,
        recent_skill_ids=pending_skill_ids or recent_skill_ids,
    )

    scope = pending_scope or _parse_skill_update_scope(message_text)

    if len(target_skill_ids) != 1:
        reply, reply_skill_ids, notes = _clarify_skill_update_reply(
            skill_id=None,
            scope=scope,
            needs_target=True,
        )
        return SkillUpdateHandlingResult(
            status="clarify",
            reply=reply,
            skill_ids=reply_skill_ids,
            notes=notes,
            scope=scope,
        )

    skill_id = target_skill_ids[0]
    instruction = message_text.strip() if is_pending_follow_up else _rewrite_skill_update_instruction(message_text)
    if _is_missing_skill_update_instruction(message_text, instruction):
        reply, reply_skill_ids, notes = _clarify_skill_update_reply(
            skill_id=skill_id,
            scope=scope,
            needs_target=False,
        )
        return SkillUpdateHandlingResult(
            status="clarify",
            reply=reply,
            skill_ids=reply_skill_ids,
            notes=notes,
            scope=scope,
        )

    override = run_store.create_content_skill_override(
        current_user.id,
        project_id=project_id,
        skill_id=skill_id,
        instruction=instruction,
        scope=scope,
    )
    skill_name = _skill_definition(skill_id).name if _skill_definition(skill_id) else skill_id
    scope_label = "workspace" if override.scope == "workspace" else "project"
    reply = (
        f"Saved this as a {scope_label}-level update for {skill_name}. "
        f"It will now apply to future Content Studio chats and Content Agent runs"
        f"{' across this workspace' if override.scope == 'workspace' else ' in this project'}.\n\n"
        f"Saved rule:\n- {override.instruction}"
    )
    if _wants_full_skill_instruction_reply(message_text):
        reply += (
            f"\n\nCurrent full skill package for {skill_name}:\n\n"
            f"{full_effective_skill_instruction(current_user=current_user, project_id=project_id, skill_id=skill_id)}"
        )
    notes = [f"{scope_label.title()}-level skill update saved for {skill_name}."]
    return SkillUpdateHandlingResult(
        status="saved",
        reply=reply,
        skill_ids=[skill_id],
        notes=notes,
        scope=override.scope,
    )


def _user_context(current_user: UserPublic) -> str:
    user_settings = run_store.get_user_settings(current_user.id)
    context_lines = [
        "Account context:",
        f"- User name: {(user_settings.name if user_settings else current_user.name) or 'not set'}",
        f"- Brand name: {(user_settings.brand_name if user_settings else current_user.brand_name) or 'not set'}",
        f"- Brand URL: {(user_settings.brand_url if user_settings else current_user.brand_url) or 'not set'}",
    ]
    if user_settings and user_settings.brief_prompt_override:
        context_lines.append(f"- Core content instructions: {user_settings.brief_prompt_override.strip()}")
    if user_settings and user_settings.writer_prompt_override:
        context_lines.append(f"- Editorial polish instructions: {user_settings.writer_prompt_override.strip()}")
    return "\n".join(context_lines)


def _project_context(current_user: UserPublic, project_id: str) -> str:
    project = run_store.get_visibility_project(current_user.id, project_id)
    if not project:
        return "Project context:\n- Project not found."
    context_lines = [
        "Project context:",
        f"- Project name: {project.name or 'not set'}",
        f"- Brand name: {project.brand_name or 'not set'}",
        f"- Brand URL: {project.brand_url or 'not set'}",
        f"- Default target country: {project.default_target_country or 'not set'}",
        f"- Target audience notes: {project.target_audience_notes or 'not set'}",
        f"- Brand positioning: {project.brand_positioning or 'not set'}",
        f"- Editorial voice: {project.editorial_voice or 'not set'}",
        f"- Editorial quality bar: {project.editorial_quality_bar or 'not set'}",
        f"- Sitemap URL: {project.sitemap_url or 'not set'}",
        f"- Approved domains: {project.approved_domains or 'not set'}",
        f"- Approved internal URLs: {project.approved_internal_urls or 'not set'}",
        f"- Product page URLs: {project.product_page_urls or 'not set'}",
        f"- Visual guidelines: {project.visual_guidelines or 'not set'}",
        f"- Allow standard user project skill updates: {'yes' if project.allow_standard_skill_updates else 'no'}",
    ]
    return "\n".join(context_lines)


def _project_record(current_user: UserPublic, project_id: str):
    return run_store.get_visibility_project(current_user.id, project_id)


def _clean_project_value(value: object) -> Optional[str]:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _project_backed_field_value(project, field_key: str) -> Optional[str]:
    if not project:
        return None
    if field_key == "target_country":
        return _clean_project_value(getattr(project, "default_target_country", None))
    if field_key == "target_audience_notes":
        return _clean_project_value(getattr(project, "target_audience_notes", None))
    if field_key in {"approved_internal_urls_or_sitemap_constraints", "approved_internal_urls_or_sitemap"}:
        approved = _clean_project_value(getattr(project, "approved_internal_urls", None))
        sitemap = _clean_project_value(getattr(project, "sitemap_url", None))
        if approved and sitemap:
            return f"Approved internal URLs:\n{approved}\n\nSitemap URL:\n{sitemap}"
        return approved or sitemap
    if field_key == "approved_product_page_references":
        return _clean_project_value(getattr(project, "product_page_urls", None))
    if field_key in {"product_mention_preference", "brand_mention_preference"}:
        return _clean_project_value(getattr(project, "brand_positioning", None))
    if field_key == "visual_guidelines":
        return _clean_project_value(getattr(project, "visual_guidelines", None))
    if field_key == "brand_product_context_required":
        positioning = _clean_project_value(getattr(project, "brand_positioning", None))
        product_pages = _clean_project_value(getattr(project, "product_page_urls", None))
        if product_pages and positioning:
            return "Yes. Use the configured product/commercial references and brand positioning."
        if product_pages or positioning:
            return "Yes. Brand/product context is configured in project settings."
        return None
    return None


def _project_backed_field_summary(project, field_key: str) -> Optional[str]:
    if not project:
        return None
    if field_key == "target_country":
        value = _clean_project_value(getattr(project, "default_target_country", None))
        return f"Target country: {value}" if value else None
    if field_key == "target_audience_notes":
        value = _clean_project_value(getattr(project, "target_audience_notes", None))
        return "Target audience notes are configured" if value else None
    if field_key == "approved_internal_urls_or_sitemap_constraints":
        approved = _clean_project_value(getattr(project, "approved_internal_urls", None))
        sitemap = _clean_project_value(getattr(project, "sitemap_url", None))
        if approved and sitemap:
            return "Approved internal URLs and sitemap constraints are configured"
        if approved:
            return "Approved internal URLs are configured"
        if sitemap:
            return f"Sitemap URL: {sitemap}"
        return None
    if field_key == "approved_product_page_references":
        value = _clean_project_value(getattr(project, "product_page_urls", None))
        return "Approved product/commercial page references are configured" if value else None
    if field_key == "product_mention_preference":
        value = _clean_project_value(getattr(project, "brand_positioning", None))
        return "Brand positioning notes are configured" if value else None
    if field_key == "brand_mention_preference":
        value = _clean_project_value(getattr(project, "brand_positioning", None))
        return "Brand positioning notes are configured" if value else None
    if field_key == "approved_internal_urls_or_sitemap":
        approved = _clean_project_value(getattr(project, "approved_internal_urls", None))
        sitemap = _clean_project_value(getattr(project, "sitemap_url", None))
        if approved and sitemap:
            return "Approved internal URLs and sitemap are configured"
        if approved:
            return "Approved internal URLs are configured"
        if sitemap:
            return f"Sitemap URL: {sitemap}"
        return None
    if field_key == "visual_guidelines":
        value = _clean_project_value(getattr(project, "visual_guidelines", None))
        return "Visual guidelines are configured" if value else None
    if field_key == "brand_product_context_required":
        positioning = _clean_project_value(getattr(project, "brand_positioning", None))
        product_pages = _clean_project_value(getattr(project, "product_page_urls", None))
        if positioning and product_pages:
            return "Brand positioning and approved product page references are configured"
        if positioning:
            return "Brand positioning notes are configured"
        if product_pages:
            return "Approved product/commercial page references are configured"
        return None
    return None


def _normalize_field_label(label: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", (label or "").strip().lower())).strip()


FIELD_LABEL_TO_KEY = {
    _normalize_field_label(alias): field_key
    for field_key, aliases in FIELD_LABEL_ALIASES.items()
    for alias in aliases
}


def _parse_labeled_skill_inputs(message_text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    current_field: Optional[str] = None
    buffer: list[str] = []

    def flush() -> None:
        nonlocal current_field, buffer
        if current_field is None:
            buffer = []
            return
        value = "\n".join(buffer).strip()
        if value:
            values[current_field] = value
        current_field = None
        buffer = []

    for raw_line in (message_text or "").splitlines():
        match = FIELD_LINE_PATTERN.match(raw_line)
        if match:
            candidate = FIELD_LABEL_TO_KEY.get(_normalize_field_label(match.group(1)))
            if candidate:
                flush()
                current_field = candidate
                first_value = match.group(2).strip()
                buffer = [first_value] if first_value else []
                continue
        if current_field is not None:
            buffer.append(raw_line)
    flush()
    return values


def _extract_freeform_skill_payload(message_text: str) -> str:
    text = (message_text or "").strip()
    if not text:
        return ""
    fenced = re.findall(r"```(?:[a-zA-Z0-9_-]+)?\n([\s\S]*?)```", text)
    if fenced:
        candidate = max((part.strip() for part in fenced), key=len, default="")
        if len(candidate) >= 120:
            return candidate
    for pattern in FREEFORM_LEAD_PATTERNS:
        stripped = re.sub(pattern, "", text, count=1, flags=re.IGNORECASE | re.DOTALL).strip()
        if len(stripped) >= 120 and stripped != text:
            return stripped
    parts = re.split(r"\n\s*\n", text, maxsplit=1)
    if len(parts) == 2 and len(parts[1].strip()) >= 120:
        return parts[1].strip()
    return ""


def _skill_is_explicitly_requested(message_text: str, skill_id: str) -> bool:
    lowered = (message_text or "").strip().lower()
    aliases = SKILL_DIRECTIVE_ALIASES.get(skill_id, [])
    return any(alias in lowered for alias in aliases) and any(term in lowered for term in DIRECT_EXECUTION_TERMS)


def _resolve_direct_skill_id(
    *,
    message_text: str,
    selected_skill_ids: list[str],
    active_skill_ids: list[str],
) -> Optional[str]:
    selected = _normalize_skill_ids(selected_skill_ids)
    if len(selected) == 1:
        return selected[0]
    explicit = [skill_id for skill_id in active_skill_ids if _skill_is_explicitly_requested(message_text, skill_id)]
    if len(explicit) == 1:
        return explicit[0]
    return None


def _apply_freeform_fallback_inputs(skill_id: str, message_text: str, inputs: dict[str, str]) -> dict[str, str]:
    resolved = dict(inputs)
    payload = _extract_freeform_skill_payload(message_text)
    if not payload:
        return resolved
    if skill_id == "content_writer" and "content_brief" not in resolved:
        resolved["content_brief"] = payload
    elif skill_id in {"content_editor", "internal_linking", "publish_qa", "image_generation"} and "draft_article" not in resolved:
        resolved["draft_article"] = payload
    elif skill_id == "content_brief" and "topic" not in resolved and len(payload) < 220:
        resolved["topic"] = payload
    return resolved


def _resolve_skill_inputs(
    *,
    current_user: UserPublic,
    project_id: str,
    skill_id: str,
    message_text: str,
) -> tuple[dict[str, str], list[str], list[str]]:
    project = _project_record(current_user, project_id)
    inputs = _parse_labeled_skill_inputs(message_text)
    inputs = _apply_freeform_fallback_inputs(skill_id, message_text, inputs)

    spec = SKILL_INPUT_SPECS.get(skill_id, {"required": [], "optional": []})
    project_loaded: list[str] = []
    for field_key in [*spec.get("required", []), *spec.get("optional", [])]:
        if inputs.get(field_key):
            continue
        project_value = _project_backed_field_value(project, field_key)
        if project_value:
            inputs[field_key] = project_value
            project_loaded.append(INPUT_FIELD_LABELS.get(field_key, field_key))

    missing_required = [field_key for field_key in spec.get("required", []) if not (inputs.get(field_key) or "").strip()]
    return inputs, missing_required, project_loaded


def _format_skill_execution_input(skill_id: str, resolved_inputs: dict[str, str], user_message: str) -> str:
    ordered_keys = [
        *SKILL_INPUT_SPECS.get(skill_id, {}).get("required", []),
        *SKILL_INPUT_SPECS.get(skill_id, {}).get("optional", []),
    ]
    seen: set[str] = set()
    lines = [
        f"Run only the {(_skill_definition(skill_id).name if _skill_definition(skill_id) else skill_id)} skill on the supplied inputs.",
        "Treat the resolved inputs below as the source of truth for this pass.",
        "",
        "Resolved inputs:",
    ]
    for field_key in ordered_keys:
        value = (resolved_inputs.get(field_key) or "").strip()
        if not value or field_key in seen:
            continue
        seen.add(field_key)
        lines.append(f"{INPUT_FIELD_LABELS.get(field_key, field_key)}:")
        lines.append(value)
        lines.append("")
    lines.append("Latest user message:")
    lines.append(user_message.strip())
    return "\n".join(lines).strip()


def _looks_like_input_request(message_text: str) -> bool:
    lowered = (message_text or "").strip().lower()
    return any(term in lowered for term in INPUT_REQUEST_TERMS)


def _prefer_brief_for_generic_input_request(
    message_text: str,
    *,
    selected_skill_ids: list[str],
    workflow_id: Optional[str],
) -> bool:
    if selected_skill_ids or workflow_id or not _looks_like_input_request(message_text):
        return False
    lowered = (message_text or "").strip().lower()
    later_stage_terms = [
        "edit",
        "editor",
        "internal link",
        "linking",
        "anchor",
        "image",
        "visual",
        "alt text",
        "qa",
        "publish",
        "metadata",
        "meta title",
        "meta description",
        "slug",
        "draft article",
        "existing draft",
    ]
    return not any(term in lowered for term in later_stage_terms)


def _intake_skill_ids(active_skill_ids: list[str], workflow_id: Optional[str]) -> list[str]:
    workflow = _find_workflow(workflow_id)
    if workflow and "content_brief" in workflow.skill_ids:
        return ["content_brief"]
    if workflow and "content_writer" in workflow.skill_ids:
        return ["content_writer"]
    return active_skill_ids


def _build_intake_response(
    *,
    current_user: UserPublic,
    project_id: str,
    active_skill_ids: list[str],
    workflow_id: Optional[str],
) -> tuple[str, list[str]]:
    project = _project_record(current_user, project_id)
    intake_skill_ids = _intake_skill_ids(active_skill_ids, workflow_id)
    loaded_from_project: list[str] = []
    required_fields: list[str] = []
    optional_fields: list[str] = []
    seen_required: set[str] = set()
    seen_optional: set[str] = set()

    for skill_id in intake_skill_ids:
        spec = SKILL_INPUT_SPECS.get(skill_id, {})
        for field_key in spec.get("required", []):
            project_summary = _project_backed_field_summary(project, field_key)
            if project_summary:
                if project_summary not in loaded_from_project:
                    loaded_from_project.append(project_summary)
                continue
            if field_key not in seen_required:
                seen_required.add(field_key)
                required_fields.append(field_key)
        for field_key in spec.get("optional", []):
            project_summary = _project_backed_field_summary(project, field_key)
            if project_summary:
                if project_summary not in loaded_from_project:
                    loaded_from_project.append(project_summary)
                continue
            if field_key not in seen_optional:
                seen_optional.add(field_key)
                optional_fields.append(field_key)

    skill_names = [
        next((skill.name for skill in DEFAULT_SKILLS if skill.id == skill_id), skill_id)
        for skill_id in intake_skill_ids
    ]
    intro = (
        f"I'll use this project's shared settings automatically for {', '.join(skill_names)} where they're already configured."
        if skill_names
        else "I'll use this project's shared settings automatically where they're already configured."
    )

    lines = [intro]
    if loaded_from_project:
        lines.append("")
        lines.append("Already loaded from project settings:")
        lines.extend(f"- {item}" for item in loaded_from_project)
    if required_fields:
        lines.append("")
        lines.append("Still needed to start:")
        lines.extend(f"- {INPUT_FIELD_LABELS.get(field_key, field_key)}" for field_key in required_fields)
    else:
        lines.append("")
        lines.append("No extra required fields are missing from the shared project context. Send the draft, brief, or task details whenever you're ready.")
    if optional_fields:
        lines.append("")
        lines.append("Helpful optional inputs:")
        lines.extend(f"- {INPUT_FIELD_LABELS.get(field_key, field_key)}" for field_key in optional_fields)
    if required_fields:
        lines.append("")
        lines.append("You can paste them like this:")
        lines.extend(f"- {INPUT_FIELD_LABELS.get(field_key, field_key)}:" for field_key in required_fields)

    notes = []
    if loaded_from_project:
        notes.append("Project settings were applied automatically, so the chat only asked for missing task-specific inputs.")
    return "\n".join(lines), notes


def _build_missing_fields_response(
    *,
    skill_id: str,
    missing_required: list[str],
    project_loaded: list[str],
) -> tuple[str, list[str]]:
    skill_name = _skill_definition(skill_id).name if _skill_definition(skill_id) else skill_id
    lines = [
        f"I can run {skill_name} directly on the material you pasted. I only need the missing fields below before I start.",
    ]
    if project_loaded:
        lines.append("")
        lines.append("Already loaded from project settings:")
        lines.extend(f"- {item}" for item in project_loaded)
    lines.append("")
    lines.append("Still needed to start:")
    lines.extend(f"- {INPUT_FIELD_LABELS.get(field_key, field_key)}" for field_key in missing_required)
    lines.append("")
    lines.append("You can paste them like this:")
    lines.extend(f"- {INPUT_FIELD_LABELS.get(field_key, field_key)}:" for field_key in missing_required)
    notes = ["Direct skill mode is active, so the chat validated the pasted input against that skill before running it."]
    return "\n".join(lines), notes


def _system_instruction(
    current_user: UserPublic,
    project_id: str,
    *,
    active_skill_ids: list[str],
    workflow_id: Optional[str],
) -> str:
    workflow = _find_workflow(workflow_id)
    instruction_parts = [
        "You are the assistant behind a Content Studio module inside an agency-focused content platform.",
        "Behave like a strong content operator: practical, direct, collaborative, and explicit about tradeoffs.",
        "Use the selected skills and workflow as the active operating context.",
        "If the user asks to update a skill based on feedback, include a short 'Skill update note' section that captures the rule change in reusable form.",
        "Keep outputs useful and production-oriented. Do not invent facts, links, statistics, or product claims.",
        _user_context(current_user),
        _project_context(current_user, project_id),
    ]
    if workflow or len(active_skill_ids) > 1:
        instruction_parts.append(
            "If the user asks for a full workflow, structure your answer by step and make it clear what should happen first."
        )

    if workflow:
        instruction_parts.append(f"Selected workflow: {workflow.name} -> {' -> '.join(workflow.skill_ids)}")
    if active_skill_ids:
        instruction_parts.append("Selected skills:")
        for skill_id in active_skill_ids:
            instruction_parts.append(f"- {skill_id}: {effective_skill_instruction(current_user=current_user, project_id=project_id, skill_id=skill_id)}")
    for skill_id in active_skill_ids:
        runtime_rules = SKILL_RUNTIME_RULES.get(skill_id)
        if runtime_rules:
            instruction_parts.append(runtime_rules.strip())

    instruction_parts.append(
        "When the request is ambiguous, make a reasonable assumption and say what you assumed instead of blocking."
    )
    return "\n".join(instruction_parts)


def _should_generate_content_studio_images(message_text: str, active_skill_ids: list[str]) -> bool:
    lowered = (message_text or "").strip().lower()
    if "image_generation" not in active_skill_ids:
        return False
    if any(term in lowered for term in IMAGE_GENERATION_NEGATIONS):
        return False
    return any(term in lowered for term in IMAGE_GENERATION_VERBS) and any(term in lowered for term in IMAGE_GENERATION_NOUNS)


def _requested_content_studio_image_count(message_text: str) -> int:
    lowered = (message_text or "").strip().lower()
    numeric_match = re.search(r"\b([1-9])\s+(?:image|images|visual|visuals|illustration|illustrations|graphic|graphics)\b", lowered)
    if numeric_match:
        return min(int(numeric_match.group(1)), max(settings.article_image_count, 1))
    for word, count in IMAGE_COUNT_WORDS.items():
        if re.search(rf"\b{word}\s+(?:image|images|visual|visuals|illustration|illustrations|graphic|graphics)\b", lowered):
            return min(count, max(settings.article_image_count, 1))
    return 1


def _render_content_studio_image_markdown(title: str, alt_text: str, public_url: str) -> str:
    caption = title.strip() or alt_text.strip() or "Generated image"
    return f"![{alt_text}]({public_url})\n*{caption}*"


def _generate_content_studio_image_artifacts(
    *,
    current_user: UserPublic,
    project_id: str,
    chat_id: str,
    message_id: str,
    latest_message: str,
    conversation: str,
) -> list[ContentStudioArtifactRecord]:
    if not llm_client.enabled:
        return []

    project_context = _project_context(current_user, project_id)
    count = _requested_content_studio_image_count(latest_message)
    planning_input = "\n".join(
        [
            f"Requested image count: {count}",
            f"Latest user request: {latest_message}",
            "",
            project_context,
            "",
            "Conversation context:",
            conversation,
        ]
    )
    payload = llm_client.complete_json(
        model=settings.writer_model,
        instruction=STUDIO_IMAGE_PLAN_INSTRUCTION,
        input_text=planning_input,
        reasoning_effort=settings.writer_reasoning_effort,
    )
    raw_images = payload.get("images") if isinstance(payload, dict) else None
    if not isinstance(raw_images, list):
        return []

    artifact_records: list[ContentStudioArtifactRecord] = []
    output_dir = Path("exports") / "images" / "content-studio"
    for index, item in enumerate(raw_images[:count], start=1):
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or f"Content Studio image {index}").strip()
        alt_text = str(item.get("alt_text") or title or f"Content Studio image {index}").strip()
        prompt = str(item.get("prompt") or "").strip()
        if not prompt:
            continue
        filename = f"studio-{chat_id[:8]}-{index}-{uuid4().hex[:8]}.png"
        generated = llm_client.generate_image(
            prompt=prompt,
            output_path=output_dir / filename,
            model=settings.image_model,
            size=settings.article_image_size,
            quality=settings.article_image_quality,
        )
        public_url = f"/exports/images/content-studio/{filename}"
        metadata_json = {
            "prompt": prompt,
            "revised_prompt": generated.get("revised_prompt", ""),
            "alt_text": alt_text,
            "public_url": public_url,
            "local_path": generated.get("path", ""),
        }
        artifact_records.append(
            run_store.create_content_studio_artifact(
                current_user.id,
                project_id=project_id,
                chat_id=chat_id,
                message_id=message_id,
                artifact_type="image",
                title=title,
                content_markdown=_render_content_studio_image_markdown(title, alt_text, public_url),
                metadata_json=metadata_json,
            )
        )
    return artifact_records


def generate_content_studio_reply(
    *,
    current_user: UserPublic,
    project_id: str,
    messages: list[WorkspaceMessage],
    selected_skill_ids: list[str],
    workflow_id: Optional[str],
    stream_callback: Optional[Callable[[str], None]] = None,
    should_stop_callback: Optional[Callable[[], bool]] = None,
    direct_skill_mode: bool = True,
) -> ContentStudioMessageResponse:
    active_skill_ids = _active_skill_ids(
        selected_skill_ids=selected_skill_ids,
        workflow_id=workflow_id,
        messages=messages,
    )
    latest_message = messages[-1].content if messages else ""
    if _prefer_brief_for_generic_input_request(
        latest_message,
        selected_skill_ids=selected_skill_ids,
        workflow_id=workflow_id,
    ):
        active_skill_ids = ["content_brief"]
    notes: list[str] = []

    if _looks_like_input_request(latest_message):
        reply, intake_notes = _build_intake_response(
            current_user=current_user,
            project_id=project_id,
            active_skill_ids=active_skill_ids,
            workflow_id=workflow_id,
        )
        if not selected_skill_ids and workflow_id is None:
            notes.append("Skills were auto-inferred from the latest message because none were selected manually.")
        notes.extend(intake_notes)
        notes.append("User-specific skill editing is the next step. This first pass applies a shared default skill catalog.")
        return ContentStudioMessageResponse(
            reply=reply,
            active_skill_ids=active_skill_ids,
            workflow_id=workflow_id,
            export_formats=EXPORT_FORMATS,
            notes=notes,
            artifacts=[],
        )

    direct_skill_id = _resolve_direct_skill_id(
        message_text=latest_message,
        selected_skill_ids=selected_skill_ids,
        active_skill_ids=active_skill_ids,
    ) if direct_skill_mode else None
    if direct_skill_id:
        resolved_inputs, missing_required, project_loaded = _resolve_skill_inputs(
            current_user=current_user,
            project_id=project_id,
            skill_id=direct_skill_id,
            message_text=latest_message,
        )
        if missing_required:
            reply, direct_notes = _build_missing_fields_response(
                skill_id=direct_skill_id,
                missing_required=missing_required,
                project_loaded=project_loaded,
            )
            return ContentStudioMessageResponse(
                reply=reply,
                active_skill_ids=[direct_skill_id],
                workflow_id=None,
                export_formats=EXPORT_FORMATS,
                notes=direct_notes,
                artifacts=[],
            )

        if stream_callback:
            reply = llm_client.stream_complete(
                model=settings.writer_model,
                instruction=_system_instruction(
                    current_user,
                    project_id,
                    active_skill_ids=[direct_skill_id],
                    workflow_id=None,
                ),
                input_text=_format_skill_execution_input(
                    direct_skill_id,
                    resolved_inputs,
                    latest_message,
                ),
                reasoning_effort=settings.writer_reasoning_effort,
                on_text=stream_callback,
                should_stop=should_stop_callback,
            )
        else:
            reply = llm_client.complete(
                model=settings.writer_model,
                instruction=_system_instruction(
                    current_user,
                    project_id,
                    active_skill_ids=[direct_skill_id],
                    workflow_id=None,
                ),
                input_text=_format_skill_execution_input(
                    direct_skill_id,
                    resolved_inputs,
                    latest_message,
                ),
                reasoning_effort=settings.writer_reasoning_effort,
            )
        notes.append("Direct skill mode was used, so the chat ran the selected skill against the resolved input package instead of treating the request as generic conversation.")
        if project_loaded:
            notes.append(f"Project settings auto-filled: {', '.join(project_loaded)}.")
        return ContentStudioMessageResponse(
            reply=reply,
            active_skill_ids=[direct_skill_id],
            workflow_id=None,
            export_formats=EXPORT_FORMATS,
            notes=notes,
            artifacts=[],
        )

    meta_result = answer_content_meta_request(
        message_text=latest_message,
        surface="content_studio",
        run=None,
    )
    if meta_result.status in {"clarify", "answered"} and meta_result.reply:
        notes.extend(meta_result.notes)
        return ContentStudioMessageResponse(
            reply=meta_result.reply,
            active_skill_ids=active_skill_ids,
            workflow_id=workflow_id,
            export_formats=EXPORT_FORMATS,
            notes=notes,
            artifacts=[],
        )

    conversation = "\n\n".join(f"{message.role.upper()}: {message.content}" for message in messages)
    if stream_callback:
        reply = llm_client.stream_complete(
            model=settings.writer_model,
            instruction=_system_instruction(
                current_user,
                project_id,
                active_skill_ids=active_skill_ids,
                workflow_id=workflow_id,
            ),
            input_text=conversation,
            reasoning_effort=settings.writer_reasoning_effort,
            on_text=stream_callback,
            should_stop=should_stop_callback,
        )
    else:
        reply = llm_client.complete(
            model=settings.writer_model,
            instruction=_system_instruction(
                current_user,
                project_id,
                active_skill_ids=active_skill_ids,
                workflow_id=workflow_id,
            ),
            input_text=conversation,
        )

    if not selected_skill_ids and workflow_id is None:
        notes.append("Skills were auto-inferred from the latest message because none were selected manually.")
    notes.append("User-specific skill editing is the next step. This first pass applies a shared default skill catalog.")

    return ContentStudioMessageResponse(
        reply=reply,
        active_skill_ids=active_skill_ids,
        workflow_id=workflow_id,
        export_formats=EXPORT_FORMATS,
        notes=notes,
        artifacts=[],
    )


def send_content_studio_chat_message(
    *,
    current_user: UserPublic,
    chat: ContentStudioChatRecord,
    content: str,
    selected_skill_ids: list[str],
    workflow_id: Optional[str],
) -> ContentStudioChatSendResponse:
    run_store.append_content_studio_message(
        current_user.id,
        project_id=chat.project_id,
        chat_id=chat.id,
        role="user",
        content=content,
        active_skill_ids=selected_skill_ids,
        workflow_id=workflow_id,
        update_title_from_content=True,
    )

    refreshed_chat = run_store.get_content_studio_chat(current_user.id, chat.id)
    if not refreshed_chat:
        raise RuntimeError("Content Studio chat disappeared unexpectedly")

    pending_skill_update, pending_skill_ids, pending_scope = _pending_skill_update_context_from_messages(refreshed_chat.messages[:-1])
    skill_update = save_skill_update_from_chat(
        current_user=current_user,
        project_id=chat.project_id,
        message_text=content,
        selected_skill_ids=selected_skill_ids,
        workflow_id=workflow_id,
        recent_skill_ids=_recent_skill_ids_from_chat(refreshed_chat),
        pending=pending_skill_update,
        pending_skill_ids=pending_skill_ids,
        pending_scope=pending_scope,
    )
    if skill_update.status in {"clarify", "saved"} and skill_update.reply:
        final_chat = run_store.append_content_studio_message(
            current_user.id,
            project_id=chat.project_id,
            chat_id=chat.id,
            role="assistant",
            content=skill_update.reply,
            active_skill_ids=skill_update.skill_ids,
            workflow_id=workflow_id,
            update_title_from_content=False,
        )
        if not final_chat:
            raise RuntimeError("Content Studio chat could not be updated with the skill update reply")
        return ContentStudioChatSendResponse(
            chat=final_chat,
            reply=skill_update.reply,
            active_skill_ids=skill_update.skill_ids,
            workflow_id=workflow_id,
            export_formats=EXPORT_FORMATS,
            notes=skill_update.notes,
            artifacts=[],
        )

    skill_meta = answer_skill_meta_request_from_chat(
        current_user=current_user,
        project_id=chat.project_id,
        message_text=content,
        selected_skill_ids=selected_skill_ids,
        workflow_id=workflow_id,
        recent_skill_ids=_recent_skill_ids_from_chat(refreshed_chat),
    )
    if skill_meta.status in {"clarify", "answered"} and skill_meta.reply:
        final_chat = run_store.append_content_studio_message(
            current_user.id,
            project_id=chat.project_id,
            chat_id=chat.id,
            role="assistant",
            content=skill_meta.reply,
            active_skill_ids=skill_meta.skill_ids,
            workflow_id=workflow_id,
            update_title_from_content=False,
        )
        if not final_chat:
            raise RuntimeError("Content Studio chat could not be updated with the skill instruction reply")
        return ContentStudioChatSendResponse(
            chat=final_chat,
            reply=skill_meta.reply,
            active_skill_ids=skill_meta.skill_ids,
            workflow_id=workflow_id,
            export_formats=EXPORT_FORMATS,
            notes=skill_meta.notes,
            artifacts=[],
        )

    with usage_scope(
        user_id=current_user.id,
        workspace_id=current_user.id,
        project_id=chat.project_id,
        feature="content_studio",
        reference_type="content_studio_chat",
        reference_id=chat.id,
        metadata={"selected_skill_ids": selected_skill_ids, "workflow_id": workflow_id},
    ) as billing_context:
        reply_payload = generate_content_studio_reply(
            current_user=current_user,
            project_id=chat.project_id,
            messages=[WorkspaceMessage(role=message.role, content=message.content) for message in refreshed_chat.messages],
            selected_skill_ids=selected_skill_ids,
            workflow_id=workflow_id,
        )

        final_chat = run_store.append_content_studio_message(
            current_user.id,
            project_id=chat.project_id,
            chat_id=chat.id,
            role="assistant",
            content=reply_payload.reply,
            active_skill_ids=reply_payload.active_skill_ids,
            workflow_id=reply_payload.workflow_id,
            update_title_from_content=False,
        )
        if not final_chat:
            raise RuntimeError("Content Studio chat could not be updated with the assistant reply")

        artifacts: list[ContentStudioArtifactRecord] = []
        if _should_generate_content_studio_images(content, reply_payload.active_skill_ids):
            assistant_message = final_chat.messages[-1] if final_chat.messages else None
            if assistant_message:
                try:
                    conversation = "\n\n".join(
                        f"{message.role.upper()}: {message.content}" for message in refreshed_chat.messages
                    )
                    artifacts = _generate_content_studio_image_artifacts(
                        current_user=current_user,
                        project_id=chat.project_id,
                        chat_id=chat.id,
                        message_id=assistant_message.id,
                        latest_message=content,
                        conversation=conversation,
                    )
                    if artifacts:
                        reply_payload.notes.append(f"Generated and saved {len(artifacts)} image{'s' if len(artifacts) != 1 else ''} for this reply.")
                except Exception as exc:  # noqa: BLE001
                    reply_payload.notes.append(f"Image generation could not complete: {exc}")
            final_chat = run_store.get_content_studio_chat(current_user.id, chat.id) or final_chat

    if int((billing_context or {}).get("logged_usage_count") or 0) > 0 or artifacts:
        record_content_studio_billing(
            user_id=current_user.id,
            project_id=chat.project_id,
            chat_id=chat.id,
            active_skill_ids=reply_payload.active_skill_ids,
            workflow_id=reply_payload.workflow_id,
            generated_images=len(artifacts),
        )

    return ContentStudioChatSendResponse(
        chat=final_chat,
        reply=reply_payload.reply,
        active_skill_ids=reply_payload.active_skill_ids,
        workflow_id=reply_payload.workflow_id,
        export_formats=reply_payload.export_formats,
        notes=reply_payload.notes,
        artifacts=artifacts,
    )

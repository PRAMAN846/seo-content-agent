from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


TaskStatus = Literal["queued", "running", "awaiting_approval", "cancel_requested", "cancelled", "completed", "failed"]
ArticleMode = Literal["from_brief", "from_custom_brief", "quick_draft"]
PersonalityAgentType = Literal["workspace", "brief", "writer", "reviewer"]
VisibilityScheduleFrequency = Literal["disabled", "weekly", "twice_monthly", "monthly"]
VisibilitySurface = Literal["api", "consumer_ui"]
VisibilityRunSource = Literal["manual", "scheduled"]
VisibilityPromptGeneratorProjectType = Literal["b2b_saas", "ecommerce", "services", "local_business"]
ContentSkillOverrideScope = Literal["workspace", "project"]


class UrlContent(BaseModel):
    url: str
    title: str
    text: str


class ArticleSummary(BaseModel):
    url: str
    summary: str


class ArticleImageAsset(BaseModel):
    id: str
    title: str
    alt_text: str
    prompt: str
    revised_prompt: str = ""
    section_heading: str = ""
    placement: str = "inline"
    local_path: str = ""
    public_url: str = ""


class UserPublic(BaseModel):
    id: str
    email: str
    name: Optional[str] = None
    brand_name: Optional[str] = None
    brand_url: Optional[str] = None
    created_at: datetime


class UserSettings(BaseModel):
    id: str
    email: str
    name: Optional[str] = None
    brand_name: Optional[str] = None
    brand_url: Optional[str] = None
    workspace_name: str = ""
    workspace_type: Literal["agency", "individual_brand"] = "agency"
    workspace_role: Literal["admin", "power_user", "standard_user"] = "admin"
    allow_power_user_workspace_skill_updates: bool = False
    model_routing_mode: Literal["balanced", "high_quality", "cost_saver"] = "balanced"
    brief_prompt_override: str = ""
    writer_prompt_override: str = ""
    orchestrator_personality_id: str = "strategist"
    brief_personality_id: str = "seo_strategist"
    writer_personality_id: str = "seo_writer"
    custom_orchestrator_personality: str = ""
    custom_brief_personality: str = ""
    custom_writer_personality: str = ""
    google_docs_connected: bool = False
    google_sheets_connected: bool = False
    created_at: datetime


class UserSettingsUpdateRequest(BaseModel):
    name: str = ""
    brand_name: str = ""
    brand_url: str = ""
    workspace_name: str = ""
    workspace_type: Literal["agency", "individual_brand"] = "agency"
    workspace_role: Literal["admin", "power_user", "standard_user"] = "admin"
    allow_power_user_workspace_skill_updates: bool = False
    model_routing_mode: Literal["balanced", "high_quality", "cost_saver"] = "balanced"
    brief_prompt_override: str = ""
    writer_prompt_override: str = ""
    orchestrator_personality_id: str = "strategist"
    brief_personality_id: str = "seo_strategist"
    writer_personality_id: str = "seo_writer"
    custom_orchestrator_personality: str = ""
    custom_brief_personality: str = ""
    custom_writer_personality: str = ""


class ProviderUsageEventRecord(BaseModel):
    id: str
    workspace_id: str
    user_id: str
    project_id: Optional[str] = None
    feature: str
    provider: str
    provider_surface: str = ""
    provider_model: str = ""
    provider_request_id: Optional[str] = None
    status: str = "completed"
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    reasoning_tokens: int = 0
    total_tokens: int = 0
    provider_cost_usd: float = 0.0
    metadata_json: dict = Field(default_factory=dict)
    created_at: datetime


class CustomerBillingEventRecord(BaseModel):
    id: str
    workspace_id: str
    user_id: str
    project_id: Optional[str] = None
    feature: str
    billing_unit_type: str
    quantity: int = 1
    credits_charged: int = 0
    pricing_version: str = "v1"
    reference_type: Optional[str] = None
    reference_id: Optional[str] = None
    metadata_json: dict = Field(default_factory=dict)
    created_at: datetime


class BillingBreakdownItem(BaseModel):
    key: str
    label: str
    quantity: int = 0
    credits: int = 0


class WorkspaceBillingSummaryResponse(BaseModel):
    workspace_id: str
    current_period_start: datetime
    current_period_end: datetime
    included_credits: int = 0
    total_credits_used: int = 0
    remaining_credits: int = 0
    overage_credits: int = 0
    total_billable_actions: int = 0
    total_provider_requests: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cached_tokens: int = 0
    total_reasoning_tokens: int = 0
    feature_breakdown: list[BillingBreakdownItem] = Field(default_factory=list)
    provider_breakdown: list[BillingBreakdownItem] = Field(default_factory=list)
    recent_events: list[CustomerBillingEventRecord] = Field(default_factory=list)


class PersonalityPreset(BaseModel):
    id: str
    agent_type: PersonalityAgentType
    name: str
    description: str
    role: str
    primary_goal: str
    tone: str
    depth: str
    structure_style: str
    directives: list[str] = Field(default_factory=list)


class TopicDeleteRequest(BaseModel):
    topics: list[str] = Field(default_factory=list, min_length=1)


class TopicDeleteResponse(BaseModel):
    deleted_topics: list[str] = Field(default_factory=list)
    deleted_briefs: int = 0
    deleted_articles: int = 0


ChatRole = Literal["user", "assistant"]
WorkspaceIntent = Literal["brief_only", "write_from_query", "write_from_existing_brief", "clarify"]
WorkspaceActionType = Literal["create_brief", "create_article_from_brief", "create_quick_draft", "none"]


class WorkspaceMessage(BaseModel):
    role: ChatRole
    content: str = Field(min_length=1)


class WorkspaceAction(BaseModel):
    type: WorkspaceActionType = "none"
    query: str = ""
    target_location: str = ""
    brief_id: Optional[str] = None
    seed_urls: list[str] = Field(default_factory=list)
    ai_citations_text: str = ""
    ai_overview_text: str = ""


class WorkspaceArtifact(BaseModel):
    kind: Literal["brief", "article"]
    id: str
    query: str
    status: TaskStatus


class WorkspaceMessageRequest(BaseModel):
    messages: list[WorkspaceMessage] = Field(default_factory=list, min_length=1)
    selected_brief_id: Optional[str] = None
    auto_execute: bool = True


class WorkspaceMessageResponse(BaseModel):
    reply: str
    intent: WorkspaceIntent = "clarify"
    needs_clarification: bool = False
    suggested_next_step: str = ""
    action: WorkspaceAction = Field(default_factory=WorkspaceAction)
    artifact: Optional[WorkspaceArtifact] = None


class ContentStudioSettingHint(BaseModel):
    key: str
    label: str
    description: str
    required: bool = False


class ContentStudioSkillDefinition(BaseModel):
    id: str
    name: str
    category: str
    description: str
    when_to_use: str
    required_inputs: list[ContentStudioSettingHint] = Field(default_factory=list)


class ContentStudioWorkflowDefinition(BaseModel):
    id: str
    name: str
    description: str
    skill_ids: list[str] = Field(default_factory=list)


class ContentStudioCatalogResponse(BaseModel):
    skills: list[ContentStudioSkillDefinition] = Field(default_factory=list)
    workflows: list[ContentStudioWorkflowDefinition] = Field(default_factory=list)
    export_formats: list[str] = Field(default_factory=list)
    recommended_settings: list[ContentStudioSettingHint] = Field(default_factory=list)
    model_strategy: str = ""


class ContentStudioMessageRequest(BaseModel):
    project_id: str = Field(min_length=1)
    messages: list[WorkspaceMessage] = Field(default_factory=list, min_length=1)
    selected_skill_ids: list[str] = Field(default_factory=list)
    workflow_id: Optional[str] = None


class ContentStudioMessageResponse(BaseModel):
    reply: str
    active_skill_ids: list[str] = Field(default_factory=list)
    workflow_id: Optional[str] = None
    export_formats: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    artifacts: list["ContentStudioArtifactRecord"] = Field(default_factory=list)


class ContentStudioChatSummary(BaseModel):
    id: str
    user_id: str
    project_id: str
    title: str
    last_message_preview: str = ""
    message_count: int = 0
    active_skill_ids: list[str] = Field(default_factory=list)
    workflow_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    archived_at: Optional[datetime] = None


ContentStudioArtifactType = Literal["image"]


class ContentStudioArtifactRecord(BaseModel):
    id: str
    chat_id: str
    message_id: str
    user_id: str
    project_id: str
    artifact_type: ContentStudioArtifactType
    title: str
    content_markdown: str = ""
    metadata_json: dict = Field(default_factory=dict)
    created_at: datetime


class ContentStudioChatMessageRecord(BaseModel):
    id: str
    chat_id: str
    role: ChatRole
    content: str
    active_skill_ids: list[str] = Field(default_factory=list)
    workflow_id: Optional[str] = None
    artifacts: list[ContentStudioArtifactRecord] = Field(default_factory=list)
    created_at: datetime


class ContentStudioChatRecord(ContentStudioChatSummary):
    messages: list[ContentStudioChatMessageRecord] = Field(default_factory=list)


class ContentStudioChatsResponse(BaseModel):
    chats: list[ContentStudioChatSummary] = Field(default_factory=list)


class ContentStudioChatCreateRequest(BaseModel):
    project_id: str = Field(min_length=1)
    title: str = ""


class ContentStudioChatUpdateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=160)


class ContentStudioChatSendRequest(BaseModel):
    content: str = Field(min_length=1)
    selected_skill_ids: list[str] = Field(default_factory=list)
    workflow_id: Optional[str] = None


class ContentStudioChatArchiveResponse(BaseModel):
    chat_id: str
    archived: bool = True


class ContentStudioChatSendResponse(BaseModel):
    chat: ContentStudioChatRecord
    reply: str
    active_skill_ids: list[str] = Field(default_factory=list)
    workflow_id: Optional[str] = None
    export_formats: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    artifacts: list[ContentStudioArtifactRecord] = Field(default_factory=list)


class ContentSkillOverrideRecord(BaseModel):
    id: str
    user_id: str
    project_id: Optional[str] = None
    scope: ContentSkillOverrideScope = "project"
    skill_id: str
    instruction: str
    source: str = "chat_feedback"
    created_at: datetime
    updated_at: datetime


class ContentSkillOverridesResponse(BaseModel):
    overrides: list[ContentSkillOverrideRecord] = Field(default_factory=list)


class ContentSkillOverrideCreateRequest(BaseModel):
    skill_id: str = Field(min_length=1)
    instruction: str = Field(min_length=1)
    scope: ContentSkillOverrideScope = "project"


ContentAgentStepType = Literal[
    "plan",
    "research",
    "approval",
    "content_brief",
    "content_writer",
    "content_editor",
    "internal_linking",
    "image_generation",
    "publish_qa",
    "export",
    "finalize",
]
ContentAgentArtifactType = Literal[
    "research_packet",
    "brief",
    "draft",
    "edited_draft",
    "linked_draft",
    "image_plan",
    "image",
    "illustrated_draft",
    "publish_qa",
    "final_article",
    "export",
    "note",
]


class ContentAgentArtifactRecord(BaseModel):
    id: str
    run_id: str
    chat_id: Optional[str] = None
    user_id: str
    project_id: str
    artifact_type: ContentAgentArtifactType
    title: str
    content_markdown: str = ""
    metadata_json: dict = Field(default_factory=dict)
    created_at: datetime


class ContentAgentStepRecord(BaseModel):
    id: str
    run_id: str
    user_id: str
    project_id: str
    step_type: ContentAgentStepType
    skill_id: Optional[str] = None
    status: TaskStatus
    title: str = ""
    input_json: dict = Field(default_factory=dict)
    output_json: dict = Field(default_factory=dict)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


class ContentAgentRunSummary(BaseModel):
    id: str
    user_id: str
    project_id: str
    chat_id: Optional[str] = None
    title: str
    goal: str
    selected_workflow_id: Optional[str] = None
    status: TaskStatus
    stage: str = "queued"
    progress_percent: int = Field(default=0, ge=0, le=100)
    current_step_title: str = ""
    latest_artifact_type: Optional[ContentAgentArtifactType] = None
    latest_artifact_title: str = ""
    error: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    archived_at: Optional[datetime] = None


class ContentAgentRunRecord(ContentAgentRunSummary):
    steps: list[ContentAgentStepRecord] = Field(default_factory=list)
    artifacts: list[ContentAgentArtifactRecord] = Field(default_factory=list)


class ContentAgentRunsResponse(BaseModel):
    runs: list[ContentAgentRunSummary] = Field(default_factory=list)


class ContentAgentRunCreateRequest(BaseModel):
    prompt: str = Field(min_length=1)


class ContentAgentRunStartResponse(BaseModel):
    run: ContentAgentRunRecord


class ContentAgentRunUpdateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=160)


class ContentAgentRunUpdateResponse(BaseModel):
    run: ContentAgentRunSummary


class ContentAgentRunContinueRequest(BaseModel):
    prompt: str = Field(min_length=1)


class ContentAgentRunContinueResponse(BaseModel):
    run: ContentAgentRunRecord


class ContentAgentRunApproveRequest(BaseModel):
    note: str = ""


class ContentAgentRunApproveResponse(BaseModel):
    run: ContentAgentRunRecord


class ContentAgentRunCancelResponse(BaseModel):
    run: ContentAgentRunRecord


class ContentAgentRunArchiveResponse(BaseModel):
    run_id: str
    archived: bool = True


ContentAgentExportFormat = Literal["markdown", "docx", "images_zip"]


class ContentAgentRunExportRequest(BaseModel):
    format: ContentAgentExportFormat = "markdown"


class ContentAgentRunExportResponse(BaseModel):
    run: ContentAgentRunRecord


class RegisterRequest(BaseModel):
    email: str = Field(min_length=5)
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    email: str = Field(min_length=5)
    password: str = Field(min_length=8, max_length=128)


class BriefCreateRequest(BaseModel):
    query: str = Field(min_length=3)
    target_location: str = ""
    seed_urls: list[str] = Field(default_factory=list)
    ai_citations_text: str = ""
    ai_overview_text: str = ""


class BriefUpdateRequest(BaseModel):
    brief_markdown: str = Field(min_length=20)


class BriefArtifacts(BaseModel):
    requested_target_location: str = ""
    requested_seed_urls: list[str] = Field(default_factory=list)
    requested_ai_citations_text: str = ""
    requested_ai_overview_text: str = ""
    sources: list[str] = Field(default_factory=list)
    extracted_articles: list[UrlContent] = Field(default_factory=list)
    summaries: list[ArticleSummary] = Field(default_factory=list)
    seo_analysis: str = ""
    brief_markdown: str = ""


class BriefRecord(BaseModel):
    id: str
    user_id: str
    query: str
    status: TaskStatus
    stage: str = "queued"
    progress_percent: int = Field(default=0, ge=0, le=100)
    created_at: datetime
    updated_at: datetime
    error: Optional[str] = None
    artifacts: BriefArtifacts = Field(default_factory=BriefArtifacts)


class ArticleCreateRequest(BaseModel):
    mode: ArticleMode
    query: str = ""
    target_location: str = ""
    brief_id: Optional[str] = None
    custom_brief_markdown: str = ""
    seed_urls: list[str] = Field(default_factory=list)
    ai_citations_text: str = ""
    ai_overview_text: str = ""


class ArticleArtifacts(BaseModel):
    requested_target_location: str = ""
    requested_seed_urls: list[str] = Field(default_factory=list)
    requested_ai_citations_text: str = ""
    requested_ai_overview_text: str = ""
    source_brief_id: Optional[str] = None
    source_brief_markdown: str = ""
    article_markdown: str = ""
    image_assets: list[ArticleImageAsset] = Field(default_factory=list)
    export_link: Optional[str] = None


class ArticleRecord(BaseModel):
    id: str
    user_id: str
    mode: ArticleMode
    query: str
    status: TaskStatus
    stage: str = "queued"
    progress_percent: int = Field(default=0, ge=0, le=100)
    created_at: datetime
    updated_at: datetime
    error: Optional[str] = None
    artifacts: ArticleArtifacts = Field(default_factory=ArticleArtifacts)


class RunCreateRequest(BaseModel):
    query: str = Field(min_length=3)
    seed_urls: list[str] = Field(default_factory=list)
    ai_citations_text: str = ""
    ai_overview_text: str = ""


class RunArtifacts(BaseModel):
    sources: list[str] = Field(default_factory=list)
    extracted_articles: list[UrlContent] = Field(default_factory=list)
    summaries: list[ArticleSummary] = Field(default_factory=list)
    seo_analysis: str = ""
    article_markdown: str = ""
    export_link: Optional[str] = None


class RunRecord(BaseModel):
    id: str
    user_id: str
    query: str
    status: TaskStatus
    stage: str = "queued"
    progress_percent: int = Field(default=0, ge=0, le=100)
    created_at: datetime
    updated_at: datetime
    error: Optional[str] = None
    artifacts: RunArtifacts = Field(default_factory=RunArtifacts)


class QueuedRun(BaseModel):
    run_id: str
    user_id: str
    query: str
    seed_urls: list[str] = Field(default_factory=list)
    ai_citations_text: str = ""
    ai_overview_text: str = ""


class VisibilityCompetitor(BaseModel):
    id: str
    user_id: str
    project_id: str
    name: str
    domain: str = ""
    created_at: datetime
    updated_at: datetime


class VisibilityProjectSummary(BaseModel):
    id: str
    user_id: str
    name: str = ""
    brand_name: str = ""
    brand_url: str = ""
    default_target_country: str = ""
    target_audience_notes: str = ""
    brand_positioning: str = ""
    editorial_voice: str = ""
    editorial_quality_bar: str = ""
    sitemap_url: str = ""
    approved_domains: str = ""
    approved_internal_urls: str = ""
    product_page_urls: str = ""
    visual_guidelines: str = ""
    allow_standard_skill_updates: bool = False
    default_schedule_frequency: VisibilityScheduleFrequency = "disabled"
    topic_count: int = 0
    prompt_list_count: int = 0
    prompt_count: int = 0
    run_count: int = 0
    last_run_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    competitors: list[VisibilityCompetitor] = Field(default_factory=list)


class VisibilityProjectRecord(VisibilityProjectSummary):
    pass


class VisibilityProjectCreateRequest(BaseModel):
    name: str = Field(min_length=1)
    brand_name: str = ""
    brand_url: str = ""
    default_target_country: str = ""
    target_audience_notes: str = ""
    brand_positioning: str = ""
    editorial_voice: str = ""
    editorial_quality_bar: str = ""
    sitemap_url: str = ""
    approved_domains: str = ""
    approved_internal_urls: str = ""
    product_page_urls: str = ""
    visual_guidelines: str = ""
    allow_standard_skill_updates: bool = False
    default_schedule_frequency: VisibilityScheduleFrequency = "disabled"


class VisibilityProjectUpdateRequest(BaseModel):
    name: str = Field(min_length=1)
    brand_name: str = ""
    brand_url: str = ""
    default_target_country: str = ""
    target_audience_notes: str = ""
    brand_positioning: str = ""
    editorial_voice: str = ""
    editorial_quality_bar: str = ""
    sitemap_url: str = ""
    approved_domains: str = ""
    approved_internal_urls: str = ""
    product_page_urls: str = ""
    visual_guidelines: str = ""
    allow_standard_skill_updates: bool = False
    default_schedule_frequency: VisibilityScheduleFrequency = "disabled"


class VisibilityCompetitorCreateRequest(BaseModel):
    name: str = Field(min_length=1)
    domain: str = ""


class VisibilityDeleteResponse(BaseModel):
    deleted: bool = False
    entity_type: str
    entity_id: str


class VisibilityTopicCreateRequest(BaseModel):
    project_id: str = Field(min_length=1)
    name: str = Field(min_length=1)


class VisibilitySubtopicCreateRequest(BaseModel):
    project_id: str = Field(min_length=1)
    topic_id: str = Field(min_length=1)
    name: str = Field(min_length=1)


class VisibilityPromptListCreateRequest(BaseModel):
    project_id: str = Field(min_length=1)
    subtopic_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    schedule_frequency: VisibilityScheduleFrequency = "disabled"


class VisibilityPromptCreateRequest(BaseModel):
    prompt_list_id: str = Field(min_length=1)
    prompt_text: str = Field(min_length=3)


class VisibilityPromptBulkCreateRequest(BaseModel):
    prompt_list_id: str = Field(min_length=1)
    prompts: list[str] = Field(default_factory=list, min_length=1)


class VisibilityPromptRecord(BaseModel):
    id: str
    user_id: str
    project_id: str
    prompt_list_id: str
    prompt_text: str
    position: int = 0
    run_count: int = 0
    latest_run_at: Optional[datetime] = None
    latest_status: Optional[TaskStatus] = None
    latest_response_text: str = ""
    latest_brands: list[str] = Field(default_factory=list)
    latest_cited_domains: list[str] = Field(default_factory=list)
    latest_cited_urls: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class VisibilityPromptListRecord(BaseModel):
    id: str
    user_id: str
    project_id: str
    subtopic_id: str
    name: str
    schedule_frequency: VisibilityScheduleFrequency = "disabled"
    last_run_at: Optional[datetime] = None
    next_run_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    prompts: list[VisibilityPromptRecord] = Field(default_factory=list)


class VisibilitySubtopicRecord(BaseModel):
    id: str
    user_id: str
    project_id: str
    topic_id: str
    name: str
    created_at: datetime
    updated_at: datetime
    prompt_lists: list[VisibilityPromptListRecord] = Field(default_factory=list)


class VisibilityTopicRecord(BaseModel):
    id: str
    user_id: str
    project_id: str
    name: str
    created_at: datetime
    updated_at: datetime
    subtopics: list[VisibilitySubtopicRecord] = Field(default_factory=list)


class VisibilityPromptRunRecord(BaseModel):
    id: str
    user_id: str
    project_id: str
    job_id: Optional[str] = None
    topic_id: str
    subtopic_id: str
    prompt_list_id: str
    prompt_id: str
    prompt_text: str
    provider: str = "openai"
    model: str = "gpt-5-mini"
    surface: VisibilitySurface = "api"
    run_source: VisibilityRunSource = "manual"
    status: TaskStatus
    response_text: str = ""
    brands: list[str] = Field(default_factory=list)
    cited_domains: list[str] = Field(default_factory=list)
    cited_urls: list[str] = Field(default_factory=list)
    error: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class VisibilityJobRecord(BaseModel):
    id: str
    user_id: str
    project_id: str
    topic_id: str
    subtopic_id: str
    prompt_list_id: str
    provider: str = "openai"
    model: str = "gpt-5-mini"
    surface: VisibilitySurface = "api"
    run_source: VisibilityRunSource = "manual"
    status: TaskStatus
    stage: str = "queued"
    progress_percent: int = Field(default=0, ge=0, le=100)
    total_prompts: int = 0
    completed_prompts: int = 0
    error: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class VisibilityPromptListRunRequest(BaseModel):
    provider: str = "openai"
    model: str = "gpt-5-mini"
    surface: VisibilitySurface = "api"
    run_source: VisibilityRunSource = "manual"


class VisibilityBrandMetric(BaseModel):
    brand: str
    prompt_mentions: int = 0
    share_of_voice: float = 0.0


class VisibilityCitationMetric(BaseModel):
    value: str
    count: int = 0


class VisibilityPromptReference(BaseModel):
    run_id: str
    prompt_id: str
    prompt_text: str
    status: Optional[TaskStatus] = None
    response_text: str = ""
    brands: list[str] = Field(default_factory=list)
    cited_domains: list[str] = Field(default_factory=list)
    cited_urls: list[str] = Field(default_factory=list)
    created_at: datetime


class VisibilityCitationDrilldown(BaseModel):
    value: str
    count: int = 0
    prompts: list[VisibilityPromptReference] = Field(default_factory=list)


class VisibilityDailyMetric(BaseModel):
    date: str
    run_count: int = 0
    brand_mentions: list[VisibilityBrandMetric] = Field(default_factory=list)


class VisibilityReport(BaseModel):
    project_id: str
    level: str
    entity_id: str
    entity_name: str
    total_runs: int = 0
    brand_presence: list[VisibilityBrandMetric] = Field(default_factory=list)
    top_domains: list[VisibilityCitationMetric] = Field(default_factory=list)
    top_urls: list[VisibilityCitationMetric] = Field(default_factory=list)
    domain_drilldown: list[VisibilityCitationDrilldown] = Field(default_factory=list)
    url_drilldown: list[VisibilityCitationDrilldown] = Field(default_factory=list)
    competitor_matrix: list[VisibilityBrandMetric] = Field(default_factory=list)
    daily_metrics: list[VisibilityDailyMetric] = Field(default_factory=list)


class VisibilityProjectWorkspaceResponse(BaseModel):
    project: VisibilityProjectRecord
    topics: list[VisibilityTopicRecord] = Field(default_factory=list)
    recent_jobs: list[VisibilityJobRecord] = Field(default_factory=list)
    recent_runs: list[VisibilityPromptRunRecord] = Field(default_factory=list)
    reports: dict[str, VisibilityReport] = Field(default_factory=dict)


class VisibilityProjectsResponse(BaseModel):
    projects: list[VisibilityProjectSummary] = Field(default_factory=list)


class VisibilityPromptGeneratorGscRow(BaseModel):
    query: str = Field(min_length=1)
    impressions: float = 0.0
    ctr: float = 0.0
    position: float = 0.0


class VisibilityPromptGeneratorRequest(BaseModel):
    project_type: VisibilityPromptGeneratorProjectType
    desired_prompt_count: int = Field(default=20, ge=5, le=50)
    product_name: str = ""
    category: str = ""
    quick_audience: str = ""
    quick_context: str = ""
    quick_use_case: str = ""
    pricing_tier: str = ""
    target_market: str = ""
    target_market_custom: str = ""
    role: str = ""
    company_size: str = ""
    industry: str = ""
    awareness_level: str = ""
    pain_points: list[str] = Field(default_factory=list)
    desired_outcomes: list[str] = Field(default_factory=list)
    fears_objections: list[str] = Field(default_factory=list)
    buying_triggers: list[str] = Field(default_factory=list)
    competitors: list[str] = Field(default_factory=list)
    gsc_rows: list[VisibilityPromptGeneratorGscRow] = Field(default_factory=list)
    price_range: str = ""
    brand_positioning: str = ""
    target_audience: str = ""
    target_audience_custom: str = ""
    age_group: str = ""
    use_case: str = ""
    use_case_custom: str = ""
    intent_triggers: list[str] = Field(default_factory=list)
    decision_factors: list[str] = Field(default_factory=list)
    objections: list[str] = Field(default_factory=list)


class VisibilityGeneratedPrompt(BaseModel):
    id: str
    prompt_text: str
    intent_stage: str
    prompt_type: str
    ai_format_likely: Literal["list", "comparison", "explanation"]
    priority_score: int = Field(default=0, ge=0, le=100)


class VisibilityPromptGeneratorIntentGroup(BaseModel):
    intent_stage: str
    prompt_count: int = 0
    prompts: list[VisibilityGeneratedPrompt] = Field(default_factory=list)


class VisibilityPromptGeneratorTypeSummary(BaseModel):
    prompt_type: str
    prompt_count: int = 0


class VisibilityPromptGeneratorResponse(BaseModel):
    project_id: str
    project_type: VisibilityPromptGeneratorProjectType
    requested_prompt_count: int
    generated_prompt_count: int
    prompts: list[VisibilityGeneratedPrompt] = Field(default_factory=list)
    intent_groups: list[VisibilityPromptGeneratorIntentGroup] = Field(default_factory=list)
    type_summary: list[VisibilityPromptGeneratorTypeSummary] = Field(default_factory=list)


class AppPublicConfig(BaseModel):
    brand_name: str
    product_name: str
    logo_path: str
    wordmark_text: str = ""
    nav_eyebrow: str
    visibility_only: bool = False

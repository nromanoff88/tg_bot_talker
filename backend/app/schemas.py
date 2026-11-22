import uuid
from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


# ==========================================
# Auth
# ==========================================
class LoginRequest(BaseModel):
    username: str
    password: str


class AuthStatusResponse(BaseModel):
    authenticated: bool
    username: str


# ==========================================
# System Config & Stats
# ==========================================
class SystemConfigResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    llm_base_url: str
    llm_api_key_masked: str
    has_llm_api_key: bool
    llm_fast_model: str
    llm_smart_model: str

    embedding_base_url: str
    embedding_api_key_masked: str
    has_embedding_api_key: bool
    embedding_model: str

    bot_telegram_id: Optional[int] = None
    bot_username: Optional[str] = None
    bot_display_name: Optional[str] = None
    bot_online: bool = False
    bot_last_heartbeat: Optional[datetime] = None


class SystemConfigUpdate(BaseModel):
    llm_base_url: str
    llm_api_key: Optional[str] = None  # Если пусто/None — не перезаписывать существующий ключ
    llm_fast_model: str
    llm_smart_model: str

    embedding_base_url: str
    embedding_api_key: Optional[str] = None
    embedding_model: str


class PromptVariableDoc(BaseModel):
    name: str
    description: str


class PromptTemplateSpecOut(BaseModel):
    key: str
    title: str
    description: str
    template: str
    default_template: str
    is_custom: bool
    variables: list[PromptVariableDoc]


class PromptTemplatesUpdateRequest(BaseModel):
    templates: dict[str, str]


class PromptPreviewRequest(BaseModel):
    key: str
    template: str
    group_id: Optional[int] = None


class PromptPreviewResponse(BaseModel):
    rendered: str
    variable_values: dict[str, str] = Field(default_factory=dict)


class DashboardStatsResponse(BaseModel):
    total_groups: int
    active_groups: int
    total_members: int
    total_messages: int
    total_facts: int
    total_episodes: int
    tokens_24h: int
    cost_24h_usd: float
    tokens_total: int
    cost_total_usd: float
    bot_online: bool
    bot_username: Optional[str] = None


# ==========================================
# Persona Templates
# ==========================================
class PersonaTemplateBase(BaseModel):
    name: str
    description: str = ""
    display_name: str = "Макс"
    backstory: str = ""
    communication_style: str = ""
    taboos: str = ""
    example_phrases: list[str] = Field(default_factory=list)
    is_default: bool = False


class PersonaTemplateCreate(PersonaTemplateBase):
    pass


class PersonaTemplateUpdate(PersonaTemplateBase):
    pass


class PersonaTemplateOut(PersonaTemplateBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime
    updated_at: datetime


# ==========================================
# Group, Settings & Context
# ==========================================
class GroupSettingsSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    chattiness_level: float = Field(default=0.35, ge=0.0, le=1.0)
    debounce_seconds: float = Field(default=4.5, ge=1.0, le=30.0)
    cooldown_seconds: int = Field(default=45, ge=0, le=86400)
    max_msgs_per_hour: int = Field(default=18, ge=1, le=300)
    context_window_messages: int = Field(default=35, ge=5, le=150)

    simulate_typing: bool = True
    reply_on_mention: bool = True

    reactions_enabled: bool = True
    allowed_emojis: list[str] = Field(default_factory=lambda: ["👍", "🔥", "😂", "🤔", "👀", "💯", "❤️", "🤝"])

    proactive_enabled: bool = True
    proactive_chance_per_hour: float = Field(default=0.15, ge=0.0, le=1.0)
    min_silence_minutes: int = Field(default=90, ge=5, le=10080)
    proactive_cooldown_hours: float = Field(default=8.0, ge=0.5, le=168.0)
    new_topic_after_hours: float = Field(default=4.0, ge=0.0, le=720.0)

    timezone: str = "Europe/Moscow"
    quiet_hours_enabled: bool = True
    quiet_hours_start: str = "23:30"
    quiet_hours_end: str = "09:30"

    memory_batch_size: int = Field(default=25, ge=5, le=200)


class GroupRuleOrJokeItem(BaseModel):
    id: str
    kind: Literal["meme", "rule", "norm"] = "meme"
    text: str
    is_locked: bool = False


class GroupContextSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    summary: str = ""
    summary_locked: bool = False
    inside_jokes_and_rules: list[GroupRuleOrJokeItem] = Field(default_factory=list)
    active_topics: list[str] = Field(default_factory=list)
    updated_at: Optional[datetime] = None


class GroupPersonaConfigUpdate(BaseModel):
    is_active: bool = True
    persona_template_id: Optional[uuid.UUID] = None
    persona_override: dict[str, Any] = Field(default_factory=dict)


class GroupSummaryOut(BaseModel):
    id: int
    title: str
    chat_type: str
    is_active: bool
    persona_template_id: Optional[uuid.UUID] = None
    persona_name: str = ""
    member_count: int = 0
    message_count: int = 0
    unanalyzed_msg_count: int = 0
    last_message_at: Optional[datetime] = None
    last_bot_message_at: Optional[datetime] = None


class GroupDetailOut(BaseModel):
    id: int
    title: str
    chat_type: str
    is_active: bool
    persona_template_id: Optional[uuid.UUID] = None
    persona_override: dict[str, Any]
    unanalyzed_msg_count: int
    unanswered_initiatives: int
    last_message_at: Optional[datetime] = None
    last_bot_message_at: Optional[datetime] = None
    last_proactive_at: Optional[datetime] = None
    last_analyzed_at: Optional[datetime] = None
    settings: GroupSettingsSchema
    context: GroupContextSchema


# ==========================================
# Members & Facts
# ==========================================
class MemberFactOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    member_id: uuid.UUID
    category: str
    fact_text: str
    confidence: float
    source_message_id: Optional[int] = None
    is_pinned: bool
    created_at: datetime
    updated_at: datetime


class MemberFactCreate(BaseModel):
    category: str = "other"
    fact_text: str
    is_pinned: bool = True


class MemberFactUpdate(BaseModel):
    category: Optional[str] = None
    fact_text: Optional[str] = None
    is_pinned: Optional[bool] = None


class GroupMemberOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    group_id: int
    telegram_user_id: int
    username: Optional[str] = None
    display_name: str
    chat_nickname: str
    nickname_locked: bool
    character_summary: str
    character_locked: bool
    bot_attitude: str
    attitude_locked: bool
    admin_notes: str
    message_count: int
    last_seen_at: Optional[datetime] = None
    facts_count: int = 0


class GroupMemberDetailOut(GroupMemberOut):
    facts: list[MemberFactOut] = Field(default_factory=list)


class GroupMemberUpdate(BaseModel):
    display_name: Optional[str] = None
    chat_nickname: str = ""
    nickname_locked: bool = False
    character_summary: str = ""
    character_locked: bool = False
    bot_attitude: str = ""
    attitude_locked: bool = False
    admin_notes: str = ""


class MergeMemberRequest(BaseModel):
    source_member_id: uuid.UUID


# ==========================================
# Episodes (pgvector)
# ==========================================
class MemoryEpisodeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    group_id: int
    title: str
    episode_summary: str
    participant_names: list[str]
    is_locked: bool
    has_embedding: bool = False
    similarity: Optional[float] = None
    happened_at: datetime
    created_at: datetime


class MemoryEpisodeCreate(BaseModel):
    title: str
    episode_summary: str
    participant_names: list[str] = Field(default_factory=list)
    is_locked: bool = True


class MemoryEpisodeUpdate(BaseModel):
    title: Optional[str] = None
    episode_summary: Optional[str] = None
    participant_names: Optional[list[str]] = None
    is_locked: Optional[bool] = None


# ==========================================
# Messages, Decisions & Manual Actions
# ==========================================
class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    group_id: int
    telegram_message_id: int
    member_id: Optional[uuid.UUID] = None
    sender_name: str
    sender_username: Optional[str] = None
    is_from_bot: bool
    reply_to_tg_msg_id: Optional[int] = None
    text_content: str
    reactions: dict[str, int]
    is_imported: bool
    is_processed_for_memory: bool
    created_at: datetime


class DecisionLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    group_id: int
    trigger_tg_msg_ids: list[int]
    decision_type: str
    router_reasoning: str
    action_payload: dict[str, Any]
    retrieved_context_meta: dict[str, Any]
    model_used: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    created_at: datetime


class ManualSendMessageRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    reply_to_tg_msg_id: Optional[int] = None


class PollPayloadSchema(BaseModel):
    question: str = Field(min_length=1, max_length=300)
    options: list[str] = Field(min_length=2, max_length=10)
    is_anonymous: bool = False
    type: Literal["regular", "quiz"] = "regular"
    allows_multiple_answers: bool = False
    correct_option_id: Optional[int] = None
    explanation: Optional[str] = Field(default=None, max_length=200)
    open_period: Optional[int] = Field(default=None, ge=5, le=600)
    is_closed: bool = False
    protect_content: bool = False
    reply_to_tg_msg_id: Optional[int] = None


class GeneratePollRequest(BaseModel):
    topic_hint: str = ""
    type: Literal["regular", "quiz"] = "regular"
    is_anonymous: bool = False
    allows_multiple_answers: bool = False


class ImportHistoryResponse(BaseModel):
    imported_messages: int
    skipped_duplicates: int
    discovered_members: int
    queued_analysis_batches: int


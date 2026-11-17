import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class SystemConfig(Base):
    """Singleton глобальной конфигурации LLM и статуса бота (id=1)."""

    __tablename__ = "system_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    llm_base_url: Mapped[str] = mapped_column(String(512), default="https://openrouter.ai/api/v1")
    llm_api_key: Mapped[str] = mapped_column(Text, default="")
    llm_fast_model: Mapped[str] = mapped_column(String(255), default="google/gemini-2.5-flash")
    llm_smart_model: Mapped[str] = mapped_column(String(255), default="anthropic/claude-3.7-sonnet")

    embedding_base_url: Mapped[str] = mapped_column(String(512), default="https://openrouter.ai/api/v1")
    embedding_api_key: Mapped[str] = mapped_column(Text, default="")
    embedding_model: Mapped[str] = mapped_column(String(255), default="openai/text-embedding-3-small")

    bot_telegram_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    bot_username: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    bot_display_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    bot_last_heartbeat: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class PersonaTemplate(Base):
    """Шаблон личности (характера/роли) бота, который можно назначать группам."""

    __tablename__ = "persona_templates"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    display_name: Mapped[str] = mapped_column(String(128), default="Макс")
    backstory: Mapped[str] = mapped_column(Text, default="")
    communication_style: Mapped[str] = mapped_column(Text, default="")
    taboos: Mapped[str] = mapped_column(Text, default="")
    example_phrases: Mapped[list[str]] = mapped_column(JSONB, default=list)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Group(Base):
    """Telegram-группа / супергруппа."""

    __tablename__ = "groups"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    chat_type: Mapped[str] = mapped_column(String(64), default="supergroup")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    persona_template_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("persona_templates.id", ondelete="SET NULL"),
        nullable=True,
    )
    persona_override: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)

    unanalyzed_msg_count: Mapped[int] = mapped_column(Integer, default=0)
    unanswered_initiatives: Mapped[int] = mapped_column(Integer, default=0)

    last_message_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_bot_message_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_proactive_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_analyzed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    persona_template: Mapped[Optional["PersonaTemplate"]] = relationship("PersonaTemplate")
    settings: Mapped["GroupSettings"] = relationship(
        "GroupSettings", back_populates="group", uselist=False, cascade="all, delete-orphan"
    )
    context: Mapped["GroupContext"] = relationship(
        "GroupContext", back_populates="group", uselist=False, cascade="all, delete-orphan"
    )
    members: Mapped[list["GroupMember"]] = relationship(
        "GroupMember", back_populates="group", cascade="all, delete-orphan"
    )


class GroupSettings(Base):
    """Настройки поведения, таймингов, реакций и инициативы бота в конкретной группе."""

    __tablename__ = "group_settings"

    group_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True
    )
    chattiness_level: Mapped[float] = mapped_column(Float, default=0.35)
    debounce_seconds: Mapped[float] = mapped_column(Float, default=4.5)
    cooldown_seconds: Mapped[int] = mapped_column(Integer, default=45)
    max_msgs_per_hour: Mapped[int] = mapped_column(Integer, default=18)
    context_window_messages: Mapped[int] = mapped_column(Integer, default=35)

    simulate_typing: Mapped[bool] = mapped_column(Boolean, default=True)
    reply_on_mention: Mapped[bool] = mapped_column(Boolean, default=True)

    reactions_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    allowed_emojis: Mapped[list[str]] = mapped_column(
        JSONB,
        default=lambda: ["👍", "🔥", "😂", "🤔", "👀", "💯", "❤️", "🤝"],
    )

    proactive_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    proactive_chance_per_hour: Mapped[float] = mapped_column(Float, default=0.15)
    min_silence_minutes: Mapped[int] = mapped_column(Integer, default=90)
    proactive_cooldown_hours: Mapped[float] = mapped_column(Float, default=8.0)
    new_topic_after_hours: Mapped[float] = mapped_column(Float, default=4.0)

    timezone: Mapped[str] = mapped_column(String(128), default="Europe/Moscow")
    quiet_hours_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    quiet_hours_start: Mapped[str] = mapped_column(String(16), default="23:30")
    quiet_hours_end: Mapped[str] = mapped_column(String(16), default="09:30")

    memory_batch_size: Mapped[int] = mapped_column(Integer, default=25)

    group: Mapped["Group"] = relationship("Group", back_populates="settings")


class GroupContext(Base):
    """Редактируемый контекст группы: описание атмосферы, внутренние шутки/правила, активные темы."""

    __tablename__ = "group_contexts"

    group_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True
    )
    summary: Mapped[str] = mapped_column(Text, default="")
    summary_locked: Mapped[bool] = mapped_column(Boolean, default=False)

    # [{ "id": "...", "kind": "meme" | "rule" | "norm", "text": "...", "is_locked": false }]
    inside_jokes_and_rules: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    active_topics: Mapped[list[str]] = mapped_column(JSONB, default=list)

    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    group: Mapped["Group"] = relationship("Group", back_populates="context")


class GroupMember(Base):
    """Изолированный профиль участника внутри конкретной группы."""

    __tablename__ = "group_members"
    __table_args__ = (
        UniqueConstraint("group_id", "telegram_user_id", name="uq_group_member_tg_id"),
        Index("ix_group_members_group_id", "group_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    group_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("groups.id", ondelete="CASCADE"), nullable=False
    )
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    username: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)

    chat_nickname: Mapped[str] = mapped_column(String(255), default="")
    nickname_locked: Mapped[bool] = mapped_column(Boolean, default=False)

    character_summary: Mapped[str] = mapped_column(Text, default="")
    character_locked: Mapped[bool] = mapped_column(Boolean, default=False)

    bot_attitude: Mapped[str] = mapped_column(Text, default="")
    attitude_locked: Mapped[bool] = mapped_column(Boolean, default=False)

    # Приватные заметки админа: ИИ их всегда читает с высшим приоритетом, но никогда не перезаписывает
    admin_notes: Mapped[str] = mapped_column(Text, default="")

    message_count: Mapped[int] = mapped_column(Integer, default=0)
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    group: Mapped["Group"] = relationship("Group", back_populates="members")
    facts: Mapped[list["MemberFact"]] = relationship(
        "MemberFact", back_populates="member", cascade="all, delete-orphan"
    )


class MemberFact(Base):
    """Атомарный факт о человеке внутри группы с поддержкой закрепления админом (is_pinned)."""

    __tablename__ = "member_facts"
    __table_args__ = (
        Index("ix_member_facts_member_id", "member_id"),
        Index("ix_member_facts_group_id", "group_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("group_members.id", ondelete="CASCADE"), nullable=False
    )
    group_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("groups.id", ondelete="CASCADE"), nullable=False
    )
    category: Mapped[str] = mapped_column(String(64), default="other")
    fact_text: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.85)
    source_message_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    is_pinned: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    member: Mapped["GroupMember"] = relationship("GroupMember", back_populates="facts")


class MemoryEpisode(Base):
    """Эпизодическая память группы с векторным индексом HNSW (pgvector)."""

    __tablename__ = "memory_episodes"
    __table_args__ = (
        Index("ix_memory_episodes_group_id", "group_id"),
        Index(
            "ix_memory_episodes_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    group_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("groups.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(255), default="")
    episode_summary: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[Optional[list[float]]] = mapped_column(Vector(1536), nullable=True)
    participant_names: Mapped[list[str]] = mapped_column(JSONB, default=list)
    is_locked: Mapped[bool] = mapped_column(Boolean, default=False)

    happened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Message(Base):
    """Бессрочное хранилище сырых сообщений и реакций группы."""

    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("group_id", "telegram_message_id", name="uq_group_tg_message_id"),
        Index("ix_messages_group_created", "group_id", "created_at"),
        Index("ix_messages_unprocessed", "group_id", "is_processed_for_memory"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    group_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("groups.id", ondelete="CASCADE"), nullable=False
    )
    telegram_message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    member_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("group_members.id", ondelete="SET NULL"), nullable=True
    )

    sender_name: Mapped[str] = mapped_column(String(255), default="Unknown")
    sender_username: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    is_from_bot: Mapped[bool] = mapped_column(Boolean, default=False)
    reply_to_tg_msg_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)

    text_content: Mapped[str] = mapped_column(Text, nullable=False)
    reactions: Mapped[dict[str, int]] = mapped_column(JSONB, default=dict)

    is_imported: Mapped[bool] = mapped_column(Boolean, default=False)
    is_processed_for_memory: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DecisionLog(Base):
    """Лог мыслей бота: почему промолчал/ответил/поставил реакцию, что вспомнил и сколько потратил токенов."""

    __tablename__ = "decision_logs"
    __table_args__ = (Index("ix_decision_logs_group_created", "group_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    group_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("groups.id", ondelete="CASCADE"), nullable=False
    )
    trigger_tg_msg_ids: Mapped[list[int]] = mapped_column(JSONB, default=list)
    decision_type: Mapped[str] = mapped_column(String(64), nullable=False)
    router_reasoning: Mapped[str] = mapped_column(Text, default="")
    action_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    retrieved_context_meta: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)

    model_used: Mapped[str] = mapped_column(String(255), default="")
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


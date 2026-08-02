"""ORM-модели (SQLAlchemy 2.0 синтаксис: Mapped / mapped_column)."""

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .config import settings
from .database import Base

# Default values for relationship metrics
DEFAULT_AFFECTION = 50
DEFAULT_TRUST = 50
DEFAULT_ATTRACTION = 0
DEFAULT_RESENTMENT = 0
DEFAULT_JEALOUSY = 0
DEFAULT_RELATIONSHIP_TYPE = "нейтральное"


class Chat(Base):
    """Чат (сессия ролевой игры)."""

    __tablename__ = "chats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    general_prompt: Mapped[str] = mapped_column(Text, default="")
    model_name: Mapped[str] = mapped_column(String(255), default=settings.default_model)
    max_history_length: Mapped[int] = mapped_column(
        Integer, default=settings.default_history_length
    )
    thinking_mode: Mapped[bool] = mapped_column(
        Boolean, default=settings.enable_thinking, nullable=False
    )
    player_location: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    locations: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Каскадное удаление: вместе с чатом удаляются персонажи, сообщения и память
    characters: Mapped[list["Character"]] = relationship(
        back_populates="chat", cascade="all, delete-orphan"
    )
    messages: Mapped[list["Message"]] = relationship(
        back_populates="chat", cascade="all, delete-orphan"
    )
    memories: Mapped[list["Memory"]] = relationship(
        back_populates="chat", cascade="all, delete-orphan"
    )
    character_summaries: Mapped[list["CharacterSummary"]] = relationship(
        back_populates="chat", cascade="all, delete-orphan"
    )
    scene_state: Mapped[Optional["SceneState"]] = relationship(
        back_populates="chat", cascade="all, delete-orphan", uselist=False
    )


class Character(Base):
    """Персонаж, участвующий в чате."""

    __tablename__ = "characters"
    __table_args__ = (Index("ix_characters_chat_order", "chat_id", "order_index"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    chat_id: Mapped[int] = mapped_column(
        ForeignKey("chats.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    personality: Mapped[str] = mapped_column(Text, default="")
    traits: Mapped[str] = mapped_column(Text, default="")
    speech_style: Mapped[str] = mapped_column(Text, default="")
    example_messages: Mapped[str] = mapped_column(Text, default="")
    boundaries: Mapped[str] = mapped_column(Text, default="")
    background: Mapped[str] = mapped_column(Text, default="")
    relationships: Mapped[str] = mapped_column(Text, default="")
    location: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    temperature: Mapped[Optional[float]] = mapped_column(Float, nullable=True, default=None)
    order_index: Mapped[int] = mapped_column(Integer, default=0)
    is_player: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    chat: Mapped["Chat"] = relationship(back_populates="characters")
    messages: Mapped[list["Message"]] = relationship(back_populates="character")
    # Память персонажа удаляется вместе с ним
    memories: Mapped[list["Memory"]] = relationship(
        back_populates="character", cascade="all, delete-orphan"
    )
    summary: Mapped[Optional["CharacterSummary"]] = relationship(
        back_populates="character", cascade="all, delete-orphan", uselist=False
    )


class Message(Base):
    """Сообщение в чате = world event (user / character / system)."""

    __tablename__ = "messages"
    __table_args__ = (
        Index("ix_messages_chat_ts", "chat_id", "timestamp", "id"),
        Index("ix_messages_character_id", "character_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    chat_id: Mapped[int] = mapped_column(
        ForeignKey("chats.id", ondelete="CASCADE"), nullable=False
    )
    # nullable: сообщения пользователя/системы не привязаны к персонажу;
    # при удалении персонажа его сообщения остаются в истории (SET NULL)
    character_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("characters.id", ondelete="SET NULL"), nullable=True
    )
    role: Mapped[str] = mapped_column(String(50), nullable=False)  # user/character/system
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # Event / perception metadata
    visibility: Mapped[str] = mapped_column(
        String(20), default=settings.default_event_visibility, nullable=False
    )
    location: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    # JSON list of character ids for private/targeted events
    target_character_ids: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    # Communication channel: direct | magic | phone | radio | messenger
    channel: Mapped[str] = mapped_column(String(20), default="direct", nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    chat: Mapped["Chat"] = relationship(back_populates="messages")
    character: Mapped[Optional["Character"]] = relationship(back_populates="messages")
    presence_records: Mapped[list["MessagePresence"]] = relationship(
        back_populates="message", cascade="all, delete-orphan"
    )


class MessagePresence(Base):
    """Per-character witness presence for a message."""

    __tablename__ = "message_presence"
    __table_args__ = (
        Index("ix_presence_character_message", "character_id", "message_id"),
        UniqueConstraint("message_id", "character_id", name="uq_presence_message_character"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    message_id: Mapped[int] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), nullable=False
    )
    character_id: Mapped[int] = mapped_column(
        ForeignKey("characters.id", ondelete="CASCADE"), nullable=False
    )
    presence: Mapped[str] = mapped_column(String(20), nullable=False)

    message: Mapped["Message"] = relationship(back_populates="presence_records")
    character: Mapped["Character"] = relationship()


class Memory(Base):
    """Долгосрочная память персонажа в рамках чата."""

    __tablename__ = "memories"
    __table_args__ = (
        Index("ix_memories_char_created", "character_id", "created_at", "id"),
        Index("ix_memories_char_imp_created", "character_id", "importance", "created_at"),
        Index("ix_memories_char_last_accessed", "character_id", "last_accessed_at"),
        UniqueConstraint("character_id", "content_hash", name="uq_memory_char_hash"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    chat_id: Mapped[int] = mapped_column(
        ForeignKey("chats.id", ondelete="CASCADE"), nullable=False
    )
    character_id: Mapped[int] = mapped_column(
        ForeignKey("characters.id", ondelete="CASCADE"), nullable=False
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    importance: Mapped[float] = mapped_column(Float, default=0.5, nullable=True)
    category: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    last_accessed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    source_message_ids: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    embedding: Mapped[Optional[bytes]] = mapped_column(LargeBinary, nullable=True)

    chat: Mapped["Chat"] = relationship(back_populates="memories")
    character: Mapped["Character"] = relationship(back_populates="memories")


class CharacterSummary(Base):
    """Per-character session summary (level-3 memory)."""

    __tablename__ = "character_summaries"
    __table_args__ = (
        Index("ix_summaries_chat_character", "chat_id", "character_id"),
        UniqueConstraint("character_id", name="uq_summary_character"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    chat_id: Mapped[int] = mapped_column(
        ForeignKey("chats.id", ondelete="CASCADE"), nullable=False
    )
    character_id: Mapped[int] = mapped_column(
        ForeignKey("characters.id", ondelete="CASCADE"), nullable=False
    )
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    through_message_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    chat: Mapped["Chat"] = relationship(back_populates="character_summaries")
    character: Mapped["Character"] = relationship(back_populates="summary")


class SceneState(Base):
    """Scene/world state tracking. Time/weather are global; locations are per-character."""

    __tablename__ = "scene_states"
    __table_args__ = (Index("ix_scene_states_chat_id", "chat_id"),)

    chat_id: Mapped[int] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"), primary_key=True)
    time_of_day: Mapped[str] = mapped_column(default="")
    # JSON dict: {character_id: location_name} — cached per-character locations
    character_locations: Mapped[str] = mapped_column(default="{}")
    custom_state: Mapped[str] = mapped_column(default="{}")  # JSON: weather, mood, tension, ...
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    chat: Mapped["Chat"] = relationship(back_populates="scene_state")


class MemoryJob(Base):
    """Persistent job tracking for memory processing (observability)."""

    __tablename__ = "memory_jobs"
    __table_args__ = (Index("ix_memory_jobs_chat_status", "chat_id", "status"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    chat_id: Mapped[int] = mapped_column(Integer, index=True)
    job_type: Mapped[str] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    payload: Mapped[str] = mapped_column(Text)
    result: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    correlation_id: Mapped[str] = mapped_column(String(64), index=True)


class RelationshipIssue(Base):
    """Открытый сюжетный крючок между парой (docs/relations.md §7).

    Пара однозначна через ``relationship_id`` (source+target). Text — это
    ДАННЫЕ сцены, а не инструкция для LLM (§14): ограничен по длине и
    очищается от маркеров prompt injection при создании.
    """

    __tablename__ = "relationship_issues"
    __table_args__ = (
        Index("ix_rel_issues_rel_state", "relationship_id", "state"),
        Index("ix_rel_issues_state", "state"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    relationship_id: Mapped[int] = mapped_column(
        ForeignKey("character_relationships.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    issue_type: Mapped[str] = mapped_column(String(50), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    importance: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    state: Mapped[str] = mapped_column(String(20), default="open", nullable=False)
    created_round_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    resolved_round_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_mention_round_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    # Deterministic salience counter (docs/relations.md §7.4): grows each round
    # the issue is absent from context/analysis, reset to 0 on mention.
    rounds_since_last_mention: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    # Source attribution (Sprint 3 item 18): JSON array of message IDs that
    # originated/led to this issue (validated against round context).
    source_message_ids: Mapped[str] = mapped_column(Text, default="[]", nullable=False)

    relationship: Mapped["CharacterRelationship"] = relationship(
        back_populates="issues"
    )


class CharacterRelationship(Base):
    """Динамическое отношение персонажа к другому персонажу (направленное).

    source_character_id -> target_character_id
    """

    __tablename__ = "character_relationships"
    __table_args__ = (
        Index("ix_rel_source_target", "source_character_id", "target_character_id"),
        Index("ix_rel_chat_source", "chat_id", "source_character_id"),
        UniqueConstraint(
            "source_character_id", "target_character_id",
            name="uq_relationship_pair"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    chat_id: Mapped[int] = mapped_column(
        ForeignKey("chats.id", ondelete="CASCADE"), nullable=False
    )
    source_character_id: Mapped[int] = mapped_column(
        ForeignKey("characters.id", ondelete="CASCADE"), nullable=False
    )
    target_character_id: Mapped[int] = mapped_column(
        ForeignKey("characters.id", ondelete="CASCADE"), nullable=False
    )
    relationship_type: Mapped[str] = mapped_column(
        String(50), default=DEFAULT_RELATIONSHIP_TYPE, nullable=False
    )
    affection: Mapped[int] = mapped_column(Integer, default=DEFAULT_AFFECTION, nullable=False)
    trust: Mapped[int] = mapped_column(Integer, default=DEFAULT_TRUST, nullable=False)
    attraction: Mapped[int] = mapped_column(Integer, default=DEFAULT_ATTRACTION, nullable=False)
    resentment: Mapped[int] = mapped_column(Integer, default=DEFAULT_RESENTMENT, nullable=False)
    jealousy: Mapped[int] = mapped_column(Integer, default=DEFAULT_JEALOUSY, nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    initial_description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    chat: Mapped["Chat"] = relationship()
    source_character: Mapped["Character"] = relationship(foreign_keys=[source_character_id])
    target_character: Mapped["Character"] = relationship(foreign_keys=[target_character_id])
    issues: Mapped[list["RelationshipIssue"]] = relationship(
        back_populates="relationship", cascade="all, delete-orphan"
    )


class RelationshipEvent(Base):
    """Журнал значимых изменений отношений."""

    __tablename__ = "relationship_events"
    __table_args__ = (
        Index("ix_rel_events_rel_id", "relationship_id"),
        Index("ix_rel_events_ts", "relationship_id", "timestamp"),
        Index("ix_rel_events_kind", "kind"),
        Index("ix_rel_events_round", "round_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    relationship_id: Mapped[int] = mapped_column(
        ForeignKey("character_relationships.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kind: Mapped[str] = mapped_column(
        String(20), default="llm", nullable=False
    )  # "llm" | "decay" | "manual" | "archive"
    description: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    delta_affection: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    delta_trust: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    delta_attraction: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    delta_resentment: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    delta_jealousy: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Snapshot of state AFTER this event
    affection_after: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    trust_after: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    attraction_after: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    resentment_after: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    jealousy_after: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    importance: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    source_message_ids: Mapped[str] = mapped_column(Text, default="[]", nullable=False)  # JSON array
    round_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    source_round_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    relationship: Mapped["CharacterRelationship"] = relationship()
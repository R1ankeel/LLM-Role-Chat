"""ORM-модели памяти: Memory, MemoryJob, MemoryAnchor (Sprint 2, §3)."""

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..database import Base


class Memory(Base):
    """Долгосрочная память персонажа в рамках чата.

    Sprint 2 (Plans/update20.md §7): единая таблица памяти дополнена осями типа
    и эмоциональной окраски. ``memory_type`` — semantic | episodic | social |
    story (для существующих строк при миграции — 'semantic'); ``event_id`` —
    проекция на каноническое ``world_events`` (источник события, §15.0);
    ``valence`` [-1..1] / ``intensity`` [0..1] — эмоциональная окраска.
    Дедупликация остаётся по ``(character_id, content_hash)`` — type в hash
    не входит (см. «Риски» Sprint 2). read-path не читает новые поля, пока
    ``MEMORY_TYPES_ENABLED=false``.
    """

    __tablename__ = "memories"
    __table_args__ = (
        Index("ix_memories_char_created", "character_id", "created_at", "id"),
        Index("ix_memories_char_imp_created", "character_id", "importance", "created_at"),
        Index("ix_memories_char_last_accessed", "character_id", "last_accessed_at"),
        Index("ix_memories_char_type", "character_id", "memory_type"),
        Index("ix_memories_event", "event_id"),
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
    # Sprint 2 (Plans/update20.md §7): semantic | episodic | social | story.
    # Default 'semantic' — миграция существующих строк без дата-потерь.
    memory_type: Mapped[str] = mapped_column(
        String(20), default="semantic", nullable=False
    )
    # Проекция на каноническое world_events (§15.0); NULL для фактов без события.
    event_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("world_events.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Эмоциональная окраска: valence [-1..1], intensity [0..1].
    valence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    intensity: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    chat: Mapped["Chat"] = relationship(back_populates="memories")
    character: Mapped["Character"] = relationship(back_populates="memories")


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


class MemoryAnchor(Base):
    """Эмоциональный якорь направленного отношения (§7, Sprint 2/7).

    `relationship_id` — FK на `character_relationships` (source→target);
    `event_id` — FK на каноническое `world_events` (источник события).
    """

    __tablename__ = "memory_anchors"
    __table_args__ = (
        Index("ix_anchors_rel", "relationship_id"),
        Index("ix_anchors_event", "event_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    relationship_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("character_relationships.id", ondelete="CASCADE"), nullable=True
    )
    event_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("world_events.id", ondelete="CASCADE"), nullable=True
    )
    emotion: Mapped[str] = mapped_column(String(50), default="", nullable=False)
    # valence [-1..1], intensity [0..1]
    valence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    intensity: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    importance: Mapped[float] = mapped_column(Float, default=0.5, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

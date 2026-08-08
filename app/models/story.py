"""ORM-модели сюжета: StoryState, StoryThread, StoryEvent, EventLink (Sprint 2, §3)."""

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base


class StoryState(Base):
    """Current Story State (Plans/update20.md §16.2, Sprint 8/9).

    `original_plot` (immutable) дублирует `chats.original_plot` как срез на
    момент версии; `current_story` — структурированный JSON
    (summary/active_threads/completed_goals/progress/phase/characters).
    """

    __tablename__ = "story_states"
    __table_args__ = (Index("ix_story_states_chat_id", "chat_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    chat_id: Mapped[int] = mapped_column(
        ForeignKey("chats.id", ondelete="CASCADE"), nullable=False
    )
    original_plot: Mapped[str] = mapped_column(Text, default="", nullable=False)
    current_story: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    story_phase: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    updated_round_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    # Versioning для rollback (Sprint 9): при консолидации версия растёт,
    # невалидный результат не применяется — предыдущая версия остаётся.
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    # Число раундов (distinct round_id в world_events) на момент последней
    # консолидации — для trigger §17.1 (интервал в раундах).
    last_consolidation_rounds: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class StoryThread(Base):
    """Активная сюжетная линия (Plans/update20.md §16, Sprint 10).

    `actors` — JSON-список имён/ids участников; `importance` — салиентность
    для контекста; `status` — active | archived.
    """

    __tablename__ = "story_threads"
    __table_args__ = (Index("ix_story_threads_chat_status", "chat_id", "status"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    chat_id: Mapped[int] = mapped_column(
        ForeignKey("chats.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    actors: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    importance: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)
    created_round_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class StoryEvent(Base):
    """Проекция канонического `world_events` для сюжета (§15.0, §16.3).

    НЕ дублирует поля `world_events` — ссылается через `event_id`. Поля-
    надстройки (story_thread_id, cause/consequences) — проекционная разметка.
    """

    __tablename__ = "story_events"
    __table_args__ = (
        Index("ix_story_events_chat_id", "chat_id"),
        Index("ix_story_events_event_id", "event_id"),
        Index("ix_story_events_thread", "story_thread_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    event_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("world_events.id", ondelete="CASCADE"), nullable=True
    )
    chat_id: Mapped[int] = mapped_column(
        ForeignKey("chats.id", ondelete="CASCADE"), nullable=False
    )
    round_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    event: Mapped[str] = mapped_column(Text, default="", nullable=False)
    actors: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    location: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    cause: Mapped[str] = mapped_column(Text, default="", nullable=False)
    consequences: Mapped[str] = mapped_column(Text, default="", nullable=False)
    importance: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    story_thread_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("story_threads.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class EventLink(Base):
    """Причинно-следственное ребро событий (§15.1, Sprint 1).

    `event_id` → `caused_by_event_id`; `kind`: causes | consequence | goal_step
    | resolution.
    """

    __tablename__ = "event_links"
    __table_args__ = (
        Index("ix_event_links_chat_id", "chat_id"),
        Index("ix_event_links_event_id", "event_id"),
        Index("ix_event_links_caused_by", "caused_by_event_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    chat_id: Mapped[int] = mapped_column(
        ForeignKey("chats.id", ondelete="CASCADE"), nullable=False
    )
    event_id: Mapped[int] = mapped_column(
        ForeignKey("world_events.id", ondelete="CASCADE"), nullable=False
    )
    caused_by_event_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("world_events.id", ondelete="CASCADE"), nullable=True
    )
    kind: Mapped[str] = mapped_column(
        String(20), default="causes", nullable=False
    )

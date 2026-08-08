"""ORM-модели WPE: WorldEvent, Thread, ThreadParticipantState (Sprint 2, §3)."""

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..database import Base


class WorldEvent(Base):
    """Неизменяемый (append-only) журнал world-событий (WPE 3.0, Фаза 0).

    Заведён, но до Фазы 3 не пишется. Иммутабельность после вставки (И9)
    обеспечивается на уровне кода: нет update-эндпоинтов, значения не
    меняются постфактум. ``message_id`` связывает речевое событие с
    ``messages``; для `move` фиксируются ``location_from``/``location_to``.
    """

    __tablename__ = "world_events"
    __table_args__ = (
        Index("ix_world_events_chat_ts", "chat_id", "created_at", "id"),
        Index("ix_world_events_character_id", "character_id"),
        Index("ix_world_events_round_id", "round_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    chat_id: Mapped[int] = mapped_column(
        ForeignKey("chats.id", ondelete="CASCADE"), nullable=False
    )
    # NULL для system/global событий без автора
    character_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("characters.id", ondelete="SET NULL"), nullable=True
    )
    # Привязка к речевому сообщению (NULL для чистых действий/системных)
    message_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL"), nullable=True
    )
    # speech | move | system_narrator | system
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    # legacy-bridge: строковая локация события (WPE 3.0 → location_id в Фазе 3).
    # Sprint 0 (Plans/update20.md): каноническая локация как FK на
    # `locations.id`. Backfill из строковой `location` — отдельным скриптом
    # (`scripts/backfill_event_location_ids.py`, аналог `characters.location_id`).
    location: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    location_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("locations.id", ondelete="SET NULL"), nullable=True, index=True
    )
    location_from: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    location_to: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    round_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    target_character_ids: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    # Sprint 1 (Plans/update20.md §15): structured event metadata. `action` —
    # JSON {"actor", "action", "target", "object"} (или {}); importance 0..10,
    # story_salience / emotional_salience 0..1. Заполняются раундной event
    # extraction (`event_service.extract_round_events`) под флагом
    # `EVENT_EXTRACTION_ENABLED`; read-path пока не читает.
    action: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    importance: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    story_salience: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    emotional_salience: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    chat: Mapped["Chat"] = relationship(back_populates="world_events")
    character: Mapped[Optional["Character"]] = relationship()


class Thread(Base):
    """Тред/канал общения (мессенджер, звонок и т.д.) (WPE 3.0, Фаза 0).

    Заведён, до Фазы 6 не пишется. ``remote_status=delivered`` адресата
    определяется через ``ThreadParticipantState`` независимо от локации.
    """

    __tablename__ = "threads"
    __table_args__ = (Index("ix_threads_chat_id", "chat_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    chat_id: Mapped[int] = mapped_column(
        ForeignKey("chats.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    channel: Mapped[str] = mapped_column(
        String(20), default="messenger", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    chat: Mapped["Chat"] = relationship(back_populates="threads")
    participants: Mapped[list["ThreadParticipantState"]] = relationship(
        back_populates="thread", cascade="all, delete-orphan"
    )


class ThreadParticipantState(Base):
    """Состояние участника треда (доставка/прочтение) (WPE 3.0, Фаза 0)."""

    __tablename__ = "thread_participant_states"
    __table_args__ = (
        UniqueConstraint("thread_id", "character_id", name="uq_thread_participant"),
        Index("ix_thread_participant_character", "character_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    thread_id: Mapped[int] = mapped_column(
        ForeignKey("threads.id", ondelete="CASCADE"), nullable=False
    )
    character_id: Mapped[int] = mapped_column(
        ForeignKey("characters.id", ondelete="CASCADE"), nullable=False
    )
    last_delivered_message_id: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True
    )
    last_read_message_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    joined_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    thread: Mapped["Thread"] = relationship(back_populates="participants")
    character: Mapped["Character"] = relationship()

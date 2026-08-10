"""ORM-модели Character / CharacterSummary (Sprint 2, decomposition-sprints.md §3)."""

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..database import Base


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
    appearance: Mapped[str] = mapped_column(Text, default="")
    avatar_url: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    # JSON: {"scale": number, "positionX": number, "positionY": number} —
    # параметры кадрирования аватара (docs/avatar_ui_crop_spec.md §4).
    # Пустая строка = кадрирование не задано (стандартное object-fit: cover).
    avatar_crop: Mapped[str] = mapped_column(Text, default="", nullable=False)
    location: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    # WPE 3.0 (Фаза 0): каноническая локация как FK на `locations.id`.
    # Фаза 8 (аудит legacy-полей §6 v2): `location` (строка) — read-only
    # legacy-bridge, все write-path (`update_character_location`,
    # `update_character_locations_batch`) пишут также `location_id`.
    location_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("locations.id", ondelete="SET NULL"), nullable=True, index=True
    )
    temperature: Mapped[Optional[float]] = mapped_column(Float, nullable=True, default=None)
    order_index: Mapped[int] = mapped_column(Integer, default=0)
    is_player: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Ручной переключатель участия NPC в автоматической генерации:
    # is_active=false НЕ удаляет персонажа из мира/локации/World State, а лишь
    # исключает его из sequential generation (ручное включение/выключение).
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
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

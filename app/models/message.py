"""ORM-модель Message (Sprint 2, decomposition-sprints.md §3)."""

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..config import settings
from ..database import Base


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
    # Event / perception metadata.
    # WPE 3.0 Фаза 8 (аудит legacy-полей §6 v2): `visibility` — read-only
    # legacy-bridge, задаётся только при создании сообщения (no update-path).
    visibility: Mapped[str] = mapped_column(
        String(20), default=settings.default_event_visibility, nullable=False
    )
    location: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    # WPE 3.0: canonical identity of the event's location (nullable — legacy
    # rows keep NULL and use the string fallback in perception). `location`
    # stays the string snapshot of the same canonical Location's name.
    location_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("locations.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # JSON list of character ids for private/targeted events
    target_character_ids: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    # Communication channel: direct | magic | phone | radio | messenger
    channel: Mapped[str] = mapped_column(String(20), default="direct", nullable=False)
    # JSON list of world-event stimuli (knock/call/shout/address/loud_sound)
    stimuli: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    chat: Mapped["Chat"] = relationship(back_populates="messages")
    character: Mapped[Optional["Character"]] = relationship(back_populates="messages")
    presence_records: Mapped[list["MessagePresence"]] = relationship(
        back_populates="message", cascade="all, delete-orphan"
    )

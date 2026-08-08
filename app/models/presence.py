"""ORM-модель MessagePresence (Sprint 2, decomposition-sprints.md §3)."""

from typing import Optional

from sqlalchemy import Float, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..database import Base


class MessagePresence(Base):
    """Per-character witness presence for a message.

    Sprint 4 (Plans/update20.md §11): колонка ``attention`` (REAL NULL) — score
    внимания персонажа к событию (0..1), пишется движком детерминированно вместе
    с presence. Фильтрует то, что идёт в память (attention < ATTENTION_LOW → не
    в память) и recency tail; presence-лестницу не меняет. NULL — attention не
    считался (флаг off) → legacy-поведение.
    """

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
    attention: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    message: Mapped["Message"] = relationship(back_populates="presence_records")
    character: Mapped["Character"] = relationship()

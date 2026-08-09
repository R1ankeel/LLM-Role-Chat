"""ORM-модели для адресного вмешательства (interventions / intervention_recipients).

Одноразовое «Вмешательство» игрока для следующего хода. Получатели фиксируются
в ``intervention_recipients`` в момент создания вмешательства (PUT) и не
пересчитываются при генерации: поздно прибывший NPC не узнаёт старое
вмешательство. ``character_id`` — legacy-маркер области действия (None = chat-wide),
источник истины о том, кто слышит инструкцию, — таблица получателей.
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..database import Base


class Intervention(Base):
    """A single-use user directive for the next generation round."""

    __tablename__ = "interventions"
    __table_args__ = (
        Index("ix_interventions_chat_id", "chat_id"),
        UniqueConstraint(
            "chat_id", "character_id", name="uq_intervention_chat_character"
        ),
        # SQLite reuses rowids of deleted rows for a bare INTEGER PRIMARY KEY,
        # which breaks the identity-safe consume contract (a replaced
        # intervention could share the old row's id). AUTOINCREMENT matches the
        # raw schema in app/db/schema.py and guarantees monotonic ids.
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    chat_id: Mapped[int] = mapped_column(
        ForeignKey("chats.id", ondelete="CASCADE"), nullable=False
    )
    character_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("characters.id", ondelete="CASCADE"), nullable=True
    )
    instruction: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow
    )

    recipients: Mapped[list["InterventionRecipient"]] = relationship(
        back_populates="intervention", cascade="all, delete-orphan"
    )


class InterventionRecipient(Base):
    """Per-character recipient of an intervention (frozen at creation)."""

    __tablename__ = "intervention_recipients"
    __table_args__ = (
        Index(
            "ix_intervention_recipients_character_id",
            "character_id",
        ),
        UniqueConstraint(
            "intervention_id", "character_id", name="uq_intervention_recipient"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    intervention_id: Mapped[int] = mapped_column(
        ForeignKey("interventions.id", ondelete="CASCADE"), nullable=False
    )
    character_id: Mapped[int] = mapped_column(
        ForeignKey("characters.id", ondelete="CASCADE"), nullable=False
    )

    intervention: Mapped["Intervention"] = relationship(back_populates="recipients")

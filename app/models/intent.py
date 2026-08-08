"""NPC intent и планы (Sprint 2, decomposition-sprints.md §3)."""

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base


class Intent(Base):
    """Intent NPC на ход (§21, Sprint 10).

    `target` — FK characters (nullable); `approach`: direct | indirect | avoid |
    delay; urgency/risk — 0..1.
    """

    __tablename__ = "intents"
    __table_args__ = (
        Index("ix_intents_chat_character", "chat_id", "character_id"),
        Index("ix_intents_round", "created_round_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    chat_id: Mapped[int] = mapped_column(
        ForeignKey("chats.id", ondelete="CASCADE"), nullable=False
    )
    character_id: Mapped[int] = mapped_column(
        ForeignKey("characters.id", ondelete="CASCADE"), nullable=False
    )
    goal: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    target: Mapped[Optional[int]] = mapped_column(
        ForeignKey("characters.id", ondelete="SET NULL"), nullable=True
    )
    approach: Mapped[str] = mapped_column(
        String(20), default="direct", nullable=False
    )
    urgency: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    emotion: Mapped[str] = mapped_column(String(50), default="", nullable=False)
    risk: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    created_round_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class NpcPlan(Base):
    """Долгоживущий маленький план NPC (§22, Sprint 10).

    «Я хочу сделать X, но сейчас мне мешает Y». НЕ GOAP/planner. Один активный
    план на персонажа (обычно). status: active | blocked | done | abandoned.
    """

    __tablename__ = "npc_plans"
    __table_args__ = (
        Index("ix_npc_plans_chat_character", "chat_id", "character_id"),
        Index("ix_npc_plans_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    chat_id: Mapped[int] = mapped_column(
        ForeignKey("chats.id", ondelete="CASCADE"), nullable=False
    )
    character_id: Mapped[int] = mapped_column(
        ForeignKey("characters.id", ondelete="CASCADE"), nullable=False
    )
    goal: Mapped[str] = mapped_column(String(500), nullable=False)
    next_step: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    blocked_by: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), default="active", nullable=False
    )
    created_round_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

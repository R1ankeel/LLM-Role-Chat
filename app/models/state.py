"""ORM-модели состояния: CharacterState, Belief, ConsolidationState (Sprint 2, §3)."""

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..database import Base


class CharacterState(Base):
    """Runtime-состояние персонажа (Plans/update20.md §8, Sprint 3).

    Хранит ТОЛЬКО то, чего нет в других таблицах: эмоции, стресс, физическое
    состояние, внимание, цели. Локация — из `characters.location_id`,
    отношения — из `character_relationships`, окружение — из `scene_states`.
    """

    __tablename__ = "character_states"
    __table_args__ = (
        UniqueConstraint("character_id", name="uq_character_state_character"),
        Index("ix_character_states_chat_id", "chat_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    chat_id: Mapped[int] = mapped_column(
        ForeignKey("chats.id", ondelete="CASCADE"), nullable=False
    )
    character_id: Mapped[int] = mapped_column(
        ForeignKey("characters.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # JSON map emotion→intensity, e.g. {"suspicion":0.7,"relief":0.2}
    emotional_state: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    # neutral/tense/hopeful/... (из interpreter)
    mood: Mapped[str] = mapped_column(String(50), default="", nullable=False)
    # 0..1
    stress: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    # JSON: energy, wounds, conditions (free-form, пишется LLM)
    physical_state: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    # current_focus (строка) — «следит за Борисом»; NULL = нет фокуса
    attention: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    # на кого смотрит (FK characters, nullable)
    current_focus_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("characters.id", ondelete="SET NULL"), nullable=True
    )
    active_goal: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    # JSON list персональных целей
    personal_goals: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    updated_round_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    character: Mapped["Character"] = relationship(foreign_keys=[character_id])


class Belief(Base):
    """Знание/убеждение персонажа (Plans/update20.md §9, Sprint 5).

    Персонаж НЕ автоматически знает World Truth — в контекст попадают только
    его beliefs. `world_truth_ref` — FK на каноническое `world_events` (NULL,
    если подтверждения миром не было).
    """

    __tablename__ = "beliefs"
    __table_args__ = (
        Index("ix_beliefs_character", "character_id"),
        Index("ix_beliefs_chat_character", "chat_id", "character_id"),
        Index("ix_beliefs_subject", "subject"),
        Index("ix_beliefs_world_truth_ref", "world_truth_ref"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    chat_id: Mapped[int] = mapped_column(
        ForeignKey("chats.id", ondelete="CASCADE"), nullable=False
    )
    character_id: Mapped[int] = mapped_column(
        ForeignKey("characters.id", ondelete="CASCADE"), nullable=False
    )
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    predicate: Mapped[str] = mapped_column(String(255), nullable=False)
    object: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    # direct_observation | heard | told_by | inference | rumor | memory
    source: Mapped[str] = mapped_column(String(30), default="memory", nullable=False)
    # 0..1
    confidence: Mapped[float] = mapped_column(Float, default=0.5, nullable=False)
    # fact | belief | suspicion — различие «знает» vs «полагает»
    type: Mapped[str] = mapped_column(String(20), default="belief", nullable=False)
    world_truth_ref: Mapped[Optional[int]] = mapped_column(
        ForeignKey("world_events.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    character: Mapped["Character"] = relationship()


class ConsolidationState(Base):
    """Счётчики adaptive consolidation (§20, Sprint 12).

    Заменит 24h-таймер: score-пороги soft/hard/critical. Заведена в Sprint 0
    (по §E), write-path — Sprint 12.
    """

    __tablename__ = "consolidation_state"
    __table_args__ = (Index("ix_consolidation_state_chat", "chat_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    chat_id: Mapped[int] = mapped_column(
        ForeignKey("chats.id", ondelete="CASCADE"), nullable=False
    )
    last_soft_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_hard_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    # JSON counters: {"messages": N, "events": N, "facts": N, "rel_events": N,
    #                  "story_events": N, "anchors": N}
    counters: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

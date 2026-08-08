"""ORM-модели отношений: RelationshipIssue, CharacterRelationship, RelationshipEvent."""

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..database import Base

# Default values for relationship metrics
DEFAULT_AFFECTION = 50
DEFAULT_TRUST = 50
DEFAULT_ATTRACTION = 0
DEFAULT_RESENTMENT = 0
DEFAULT_JEALOUSY = 0
DEFAULT_RELATIONSHIP_TYPE = "нейтральное"


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
        Index("ix_rel_events_event_id", "event_id"),
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
    # Sprint 1 (Plans/update20.md §15.2): проекция на каноническое событие.
    # Заполняется только если событие порождено раундной event extraction.
    event_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("world_events.id", ondelete="SET NULL"), nullable=True, index=True
    )
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    relationship: Mapped["CharacterRelationship"] = relationship()

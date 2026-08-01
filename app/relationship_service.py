"""Service layer for character relationship CRUD and delta application."""

import logging
from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .config import settings
from .models import (
    DEFAULT_AFFECTION,
    DEFAULT_ATTRACTION,
    DEFAULT_JEALOUSY,
    DEFAULT_RELATIONSHIP_TYPE,
    DEFAULT_RESENTMENT,
    DEFAULT_TRUST,
    CharacterRelationship,
    RelationshipEvent,
)
from .relationship_interpreter import format_interpretation, interpret
from .schemas import RelationshipDelta

logger = logging.getLogger(__name__)

MAX_DELTA = settings.relationship_max_delta
VALID_TYPES = set(settings.relationship_valid_types)
TRANSITIONS: dict[str, set[str]] = {
    k: set(v) for k, v in settings.relationship_transition_rules.items()
}


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------
async def get_or_create_relationship(
    db: AsyncSession,
    chat_id: int,
    source_id: int,
    target_id: int,
) -> CharacterRelationship:
    stmt = select(CharacterRelationship).where(
        CharacterRelationship.source_character_id == source_id,
        CharacterRelationship.target_character_id == target_id,
    )
    result = await db.execute(stmt)
    rel = result.scalar_one_or_none()
    if rel is not None:
        return rel
    rel = CharacterRelationship(
        chat_id=chat_id,
        source_character_id=source_id,
        target_character_id=target_id,
        relationship_type=DEFAULT_RELATIONSHIP_TYPE,
        affection=DEFAULT_AFFECTION,
        trust=DEFAULT_TRUST,
        attraction=DEFAULT_ATTRACTION,
        resentment=DEFAULT_RESENTMENT,
        jealousy=DEFAULT_JEALOUSY,
    )
    db.add(rel)
    await db.commit()
    await db.refresh(rel)
    return rel


async def get_relationship(
    db: AsyncSession,
    source_id: int,
    target_id: int,
) -> Optional[CharacterRelationship]:
    stmt = select(CharacterRelationship).where(
        CharacterRelationship.source_character_id == source_id,
        CharacterRelationship.target_character_id == target_id,
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def list_relationships_for_character(
    db: AsyncSession,
    character_id: int,
    chat_id: Optional[int] = None,
) -> list[CharacterRelationship]:
    stmt = select(CharacterRelationship).where(
        CharacterRelationship.source_character_id == character_id,
    )
    if chat_id is not None:
        stmt = stmt.where(CharacterRelationship.chat_id == chat_id)
    stmt = stmt.options(selectinload(CharacterRelationship.target_character))
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def list_received_relationships(
    db: AsyncSession,
    character_id: int,
) -> list[CharacterRelationship]:
    stmt = select(CharacterRelationship).where(
        CharacterRelationship.target_character_id == character_id,
    ).options(selectinload(CharacterRelationship.source_character))
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def update_relationship_fields(
    db: AsyncSession,
    rel: CharacterRelationship,
    *,
    relationship_type: Optional[str] = None,
    affection: Optional[int] = None,
    trust: Optional[int] = None,
    attraction: Optional[int] = None,
    resentment: Optional[int] = None,
    jealousy: Optional[int] = None,
    description: Optional[str] = None,
) -> CharacterRelationship:
    if relationship_type is not None:
        rel.relationship_type = relationship_type
    if affection is not None:
        rel.affection = max(0, min(100, affection))
    if trust is not None:
        rel.trust = max(0, min(100, trust))
    if attraction is not None:
        rel.attraction = max(0, min(100, attraction))
    if resentment is not None:
        rel.resentment = max(0, min(100, resentment))
    if jealousy is not None:
        rel.jealousy = max(0, min(100, jealousy))
    if description is not None:
        rel.description = description
    rel.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(rel)
    return rel


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
def validate_transition(
    current_type: str,
    new_type: str,
) -> bool:
    if new_type == current_type:
        return True
    allowed = TRANSITIONS.get(current_type, set())
    return new_type in allowed


def clamp_metric(value: int) -> int:
    return max(0, min(100, value))


# ---------------------------------------------------------------------------
# Apply delta from LLM analysis
# ---------------------------------------------------------------------------
async def apply_delta(
    db: AsyncSession,
    delta: RelationshipDelta,
    chat_id: int,
    round_id: Optional[str] = None,
) -> CharacterRelationship:
    rel = await get_or_create_relationship(
        db, chat_id, delta.source_character_id, delta.target_character_id,
    )

    if delta.importance < settings.relationship_min_importance:
        logger.debug(
            "Skipping relationship delta for %d->%d: importance %d < %d",
            delta.source_character_id, delta.target_character_id,
            delta.importance, settings.relationship_min_importance,
        )
        return rel

    old_type = rel.relationship_type
    new_type = delta.relationship_type or old_type

    if new_type != old_type and not validate_transition(old_type, new_type):
        logger.warning(
            "Invalid transition %s -> %s for rel %d->%d; keeping %s",
            old_type, new_type, delta.source_character_id,
            delta.target_character_id, old_type,
        )
        new_type = old_type

    old_values = (
        rel.affection, rel.trust, rel.attraction, rel.resentment, rel.jealousy,
        rel.relationship_type, rel.description,
    )

    # Apply clamped deltas
    rel.affection = clamp_metric(rel.affection + _clamp_delta(delta.delta_affection))
    rel.trust = clamp_metric(rel.trust + _clamp_delta(delta.delta_trust))
    rel.attraction = clamp_metric(rel.attraction + _clamp_delta(delta.delta_attraction))
    rel.resentment = clamp_metric(rel.resentment + _clamp_delta(delta.delta_resentment))
    rel.jealousy = clamp_metric(rel.jealousy + _clamp_delta(delta.delta_jealousy))
    rel.relationship_type = new_type

    if delta.update_description and delta.description:
        rel.description = delta.description

    new_values = (
        rel.affection, rel.trust, rel.attraction, rel.resentment, rel.jealousy,
        rel.relationship_type, rel.description,
    )

    if old_values == new_values:
        logger.debug(
            "No actual change for rel %d->%d; skipping event",
            delta.source_character_id, delta.target_character_id,
        )
        # Values are identical to what is already persisted, so there is
        # nothing to write. Do not rollback (it would expire the ORM object).
        return rel

    rel.updated_at = datetime.utcnow()
    await db.flush()

    # Create event log
    event = RelationshipEvent(
        relationship_id=rel.id,
        description=delta.description or "",
        reason=delta.reason or "",
        delta_affection=_clamp_delta(delta.delta_affection),
        delta_trust=_clamp_delta(delta.delta_trust),
        delta_attraction=_clamp_delta(delta.delta_attraction),
        delta_resentment=_clamp_delta(delta.delta_resentment),
        delta_jealousy=_clamp_delta(delta.delta_jealousy),
        importance=delta.importance,
        source_round_id=round_id,
    )
    db.add(event)
    await db.commit()
    await db.refresh(rel)
    return rel


def _clamp_delta(value: int) -> int:
    return max(-MAX_DELTA, min(MAX_DELTA, value))


# ---------------------------------------------------------------------------
# Formatting for prompt
# ---------------------------------------------------------------------------
async def get_recent_events(
    db: AsyncSession,
    rel: CharacterRelationship,
    limit: int = 5,
) -> list[RelationshipEvent]:
    stmt = (
        select(RelationshipEvent)
        .where(RelationshipEvent.relationship_id == rel.id)
        .order_by(RelationshipEvent.timestamp.desc())
        .limit(limit)
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


def format_relationship_for_prompt(
    rel: CharacterRelationship,
    target_name: str,
    events: list[RelationshipEvent],
) -> str:
    """Format one relationship for the generation prompt.

    Uses the deterministic interpreter instead of raw metrics: the character
    model gets semantic labels, never numbers (docs/relations.md §4-§5).
    """
    interp = interpret(rel)
    lines = [f"{target_name}: {rel.relationship_type}"]
    text = format_interpretation(interp, target_name)
    if text:
        lines.append(f"  {text}")
    if rel.description:
        lines.append(f"  описание: {rel.description}")
    if events:
        for ev in reversed(events):
            if ev.description:
                lines.append(f"  - {ev.description}")
    return "\n".join(lines)


async def build_relationships_block(
    db: AsyncSession,
    chat_id: int,
    character_id: int,
    character_name: str,
    all_characters: dict[int, str],
    max_events: int = 5,
) -> str:
    rels = await list_relationships_for_character(db, character_id, chat_id=chat_id)
    if not rels:
        return ""

    blocks: list[str] = [f"Отношения {character_name} к другим персонажам:"]
    for rel in rels:
        target_name = all_characters.get(rel.target_character_id, f"ID:{rel.target_character_id}")
        events = await get_recent_events(db, rel, limit=max_events)
        blocks.append(format_relationship_for_prompt(rel, target_name, events))
    return "\n".join(blocks)

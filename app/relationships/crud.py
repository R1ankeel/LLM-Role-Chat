"""CRUD-функции отношений (Milestone 6B, decomposition.md §4.4).

Вынесено из ``app/relationship_service.py`` без изменения поведения (тела
функций перенесены 1:1). Только чтение/запись строк ``CharacterRelationship``;
применение дельт — в ``deltas.py``, валидация типов — в ``validation.py``.
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..models import (
    DEFAULT_AFFECTION,
    DEFAULT_ATTRACTION,
    DEFAULT_JEALOUSY,
    DEFAULT_RELATIONSHIP_TYPE,
    DEFAULT_RESENTMENT,
    DEFAULT_TRUST,
    CharacterRelationship,
    RelationshipEvent,
)


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------
async def get_or_create_relationship(
    db: AsyncSession,
    chat_id: int,
    source_id: int,
    target_id: int,
) -> CharacterRelationship:
    """Get or create the directed edge source -> target (docs/relations.md §10).

    Reciprocity invariant: the edge is directional and NO automatic mirroring
    happens. Only ``source -> target`` is created/returned here; the reverse
    edge ``target -> source`` is a separate row that this function neither
    creates nor touches. A self-loop (source == target) is rejected.
    """
    if source_id == target_id:
        raise ValueError(
            f"Cannot create relationship for self-loop ({source_id} -> {target_id})"
        )
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


async def list_relationships_for_chat(
    db: AsyncSession,
    chat_id: int,
) -> list[CharacterRelationship]:
    """All tracked edges of a chat (NPC -> NPC / NPC -> player), with endpoints.

    Used by the relationship graph UI (Sprint 4 п.24). Player -> NPC edges are
    never tracked in the DB, so they cannot appear here.
    """
    stmt = (
        select(CharacterRelationship)
        .where(CharacterRelationship.chat_id == chat_id)
        .options(
            selectinload(CharacterRelationship.source_character),
            selectinload(CharacterRelationship.target_character),
        )
    )
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

    # Create manual event with snapshot (docs/relations.md §17)
    event = RelationshipEvent(
        relationship_id=rel.id,
        kind="manual",
        description="Ручное обновление через API",
        reason="",
        delta_affection=0,
        delta_trust=0,
        delta_attraction=0,
        delta_resentment=0,
        delta_jealousy=0,
        affection_after=rel.affection,
        trust_after=rel.trust,
        attraction_after=rel.attraction,
        resentment_after=rel.resentment,
        jealousy_after=rel.jealousy,
        importance=1,
        source_message_ids="[]",
        round_id=None,
        source_round_id=None,
    )
    db.add(event)
    await db.flush()
    await db.refresh(rel)
    return rel

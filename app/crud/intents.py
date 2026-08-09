"""NPC intents (Sprint 4)."""



from __future__ import annotations



from sqlalchemy import func, select

from sqlalchemy.ext.asyncio import AsyncSession

from .. import models

async def save_intent(
    db: AsyncSession,
    *,
    chat_id: int,
    character_id: int,
    goal: str,
    target: int | None = None,
    approach: str = "direct",
    urgency: float = 0.0,
    emotion: str = "",
    risk: float = 0.0,
    created_round_id: str | None = None,
) -> models.Intent:
    """Записать intent персонажа на ход (Plans/update20.md §21, Sprint 10).

    Write-path под canary-флагом ``npc_intent_enabled``; read-path для контекста
    НЕ читает intents (блок ACTIVE GOAL рендерится из текущего intent).
    """
    row = models.Intent(
        chat_id=chat_id,
        character_id=character_id,
        goal=(goal or "")[:500],
        target=target,
        approach=approach or "direct",
        urgency=float(urgency),
        emotion=(emotion or "")[:50],
        risk=float(risk),
        created_round_id=created_round_id,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row

async def get_intents_for_character(
    db: AsyncSession,
    chat_id: int,
    character_id: int,
    limit: int = 10,
) -> list[models.Intent]:
    """Последние intent-строки персонажа (новые сначала, топ-N)."""
    stmt = (
        select(models.Intent)
        .where(
            models.Intent.chat_id == chat_id,
            models.Intent.character_id == character_id,
        )
        .order_by(models.Intent.id.desc())
        .limit(max(0, int(limit)))
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())

async def get_relationship_target_id(
    db: AsyncSession, relationship_id: int
) -> int | None:
    """target_character_id направленного отношения (для intent target, §21)."""
    stmt = select(models.CharacterRelationship.target_character_id).where(
        models.CharacterRelationship.id == relationship_id
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()

async def count_pair_interaction_rounds(
    db: AsyncSession,
    chat_id: int,
    source_character_id: int,
    target_character_id: int,
) -> int:
    """Число раундов взаимодействия пары (relationship events, §19 Sprint 11).

    Distinct ``round_id`` направленных events source→target. Детерминированный
    сигнал «продолжительное взаимодействие пары» для crisis candidate.
    """
    stmt = (
        select(func.count(func.distinct(models.RelationshipEvent.round_id)))
        .join(
            models.CharacterRelationship,
            models.CharacterRelationship.id == models.RelationshipEvent.relationship_id,
        )
        .where(
            models.CharacterRelationship.chat_id == chat_id,
            models.CharacterRelationship.source_character_id == source_character_id,
            models.CharacterRelationship.target_character_id == target_character_id,
            models.RelationshipEvent.round_id.isnot(None),
        )
    )
    result = await db.execute(stmt)
    return int(result.scalar_one() or 0)

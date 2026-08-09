"""CRUD для адресного вмешательства (interventions / intervention_recipients).

Получатели записываются в ``intervention_recipients`` в момент создания (PUT)
и не пересчитываются при генерации. ``create_intervention`` сохраняет семантику
«заменить» для ключа ``(chat_id, character_id)``: старая запись (и её
получатели) удаляется, новая создаётся с актуальным списком.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .. import models


def _recipient_ids(intervention: models.Intervention) -> list[int]:
    return [r.character_id for r in intervention.recipients]


async def create_intervention(
    db: AsyncSession,
    chat_id: int,
    instruction: str,
    character_id: int | None = None,
    recipient_ids: list[int] | None = None,
) -> models.Intervention:
    """Store (or replace) a pending one-time intervention with its recipients."""
    # Replace semantics: drop the previous row for the same (chat_id, character_id).
    await db.execute(
        delete(models.Intervention).where(
            models.Intervention.chat_id == chat_id,
            models.Intervention.character_id == character_id,
        )
    )
    intervention = models.Intervention(
        chat_id=chat_id,
        character_id=character_id,
        instruction=instruction,
        created_at=datetime.utcnow(),
    )
    db.add(intervention)
    await db.flush()

    seen: set[int] = set()
    for cid in recipient_ids or []:
        if cid in seen:
            continue
        seen.add(cid)
        db.add(
            models.InterventionRecipient(
                intervention_id=intervention.id, character_id=cid
            )
        )
    await db.commit()

    # Return with recipients eager-loaded: the async session cannot lazily load
    # the relationship after commit (MissingGreenlet outside the greenlet).
    stmt = (
        select(models.Intervention)
        .options(selectinload(models.Intervention.recipients))
        .where(models.Intervention.id == intervention.id)
    )
    result = await db.execute(stmt)
    return result.scalars().first()


async def list_interventions(
    db: AsyncSession, chat_id: int
) -> list[models.Intervention]:
    """All pending interventions for a chat (with recipients loaded)."""
    stmt = (
        select(models.Intervention)
        .options(selectinload(models.Intervention.recipients))
        .where(models.Intervention.chat_id == chat_id)
        .order_by(models.Intervention.created_at, models.Intervention.id)
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_chat_wide_intervention(
    db: AsyncSession, chat_id: int
) -> models.Intervention | None:
    """The chat-wide pending intervention (``character_id IS NULL``), if any."""
    stmt = (
        select(models.Intervention)
        .options(selectinload(models.Intervention.recipients))
        .where(
            models.Intervention.chat_id == chat_id,
            models.Intervention.character_id.is_(None),
        )
    )
    result = await db.execute(stmt)
    return result.scalars().first()


async def delete_intervention(db: AsyncSession, intervention_id: int) -> bool:
    """Delete an intervention by id (recipients cascade)."""
    result = await db.execute(
        delete(models.Intervention).where(models.Intervention.id == intervention_id)
    )
    await db.commit()
    return result.rowcount > 0


async def delete_chat_wide_intervention(db: AsyncSession, chat_id: int) -> bool:
    """Delete the chat-wide pending intervention (user cancels it)."""
    result = await db.execute(
        delete(models.Intervention).where(
            models.Intervention.chat_id == chat_id,
            models.Intervention.character_id.is_(None),
        )
    )
    await db.commit()
    return result.rowcount > 0


async def clear_interventions(db: AsyncSession) -> None:
    """Delete all pending interventions (used by tests)."""
    await db.execute(delete(models.Intervention))
    await db.commit()


__all__ = [
    "_recipient_ids",
    "create_intervention",
    "list_interventions",
    "get_chat_wide_intervention",
    "delete_intervention",
    "delete_chat_wide_intervention",
    "clear_interventions",
]

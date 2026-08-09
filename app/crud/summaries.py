"""Character summary (Sprint 4)."""



from __future__ import annotations



from datetime import datetime

from sqlalchemy import delete, select

from sqlalchemy.ext.asyncio import AsyncSession

from .. import models

# ------------------------ Character Summary ----------------------
async def get_character_summary(
    db: AsyncSession, character_id: int
) -> models.CharacterSummary | None:
    stmt = select(models.CharacterSummary).where(
        models.CharacterSummary.character_id == character_id
    )
    result = await db.execute(stmt)
    return result.scalars().first()

async def get_summaries_for_characters(
    db: AsyncSession, character_ids: list[int]
) -> dict[int, models.CharacterSummary]:
    if not character_ids:
        return {}
    stmt = select(models.CharacterSummary).where(
        models.CharacterSummary.character_id.in_(character_ids)
    )
    result = await db.execute(stmt)
    summaries = list(result.scalars().all())
    return {summary.character_id: summary for summary in summaries}

async def upsert_character_summary(
    db: AsyncSession,
    chat_id: int,
    character_id: int,
    content: str,
    through_message_id: int,
) -> models.CharacterSummary:
    existing = await get_character_summary(db, character_id)
    now = datetime.utcnow()
    if existing is None:
        db_summary = models.CharacterSummary(
            chat_id=chat_id,
            character_id=character_id,
            content=content,
            through_message_id=through_message_id,
            updated_at=now,
        )
        db.add(db_summary)
    else:
        existing.content = content
        existing.through_message_id = through_message_id
        existing.updated_at = now
        db_summary = existing
    await db.commit()
    await db.refresh(db_summary)
    return db_summary

async def reset_character_summaries_for_chat(db: AsyncSession, chat_id: int) -> None:
    await db.execute(
        delete(models.CharacterSummary).where(
            models.CharacterSummary.chat_id == chat_id
        )
    )
    await db.commit()

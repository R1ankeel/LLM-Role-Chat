"""Presence/attention карты (Sprint 4)."""



from __future__ import annotations



from sqlalchemy import select

from sqlalchemy.ext.asyncio import AsyncSession

from .. import models

from .. import schemas

from ..config import settings

# ------------------------ Message Presence -----------------------
async def upsert_message_presence_batch(
    db: AsyncSession, records: list[schemas.MessagePresenceCreate]
) -> None:
    if not records:
        return
    for record in records:
        stmt = select(models.MessagePresence).where(
            models.MessagePresence.message_id == record.message_id,
            models.MessagePresence.character_id == record.character_id,
        )
        result = await db.execute(stmt)
        existing = result.scalars().first()
        if existing is None:
            db.add(models.MessagePresence(**record.model_dump()))
        else:
            existing.presence = record.presence
            # Sprint 4 (§11): attention обновляется только если явно передан
            # (None при выключенном флаге → существующее значение сохраняется).
            if record.attention is not None:
                existing.attention = record.attention
    await db.commit()

async def get_presence_map(
    db: AsyncSession, message_ids: list[int], character_id: int
) -> dict[int, str]:
    if not message_ids:
        return {}
    stmt = select(models.MessagePresence).where(
        models.MessagePresence.character_id == character_id,
        models.MessagePresence.message_id.in_(message_ids),
    )
    result = await db.execute(stmt)
    rows = list(result.scalars().all())
    return {row.message_id: row.presence for row in rows}

async def get_attention_map(
    db: AsyncSession, message_ids: list[int], character_id: int
) -> dict[int, float]:
    """Attention score (Sprint 4, §11) для пары (персонаж, сообщения).

    Пусто при выключенном ``attention_enabled`` — attention не считался
    (NULL в БД) → фильтры ведут себя как legacy. Возвращает
    ``{message_id: attention}`` только для строк с не-NULL score.
    """
    if not settings.attention_enabled or not message_ids:
        return {}
    stmt = select(
        models.MessagePresence.message_id, models.MessagePresence.attention
    ).where(
        models.MessagePresence.character_id == character_id,
        models.MessagePresence.message_id.in_(message_ids),
        models.MessagePresence.attention.is_not(None),
    )
    result = await db.execute(stmt)
    return {mid: attn for mid, attn in result.all()}

async def get_presence_for_message(
    db: AsyncSession, message_id: int, character_id: int
) -> str:
    """Presence одного события для персонажа (belief pipeline §9).

    Нет строки → "absent" (не воспринял — belief не пишется, изоляция R2).
    """
    if message_id is None:
        return "absent"
    stmt = select(models.MessagePresence).where(
        models.MessagePresence.character_id == character_id,
        models.MessagePresence.message_id == message_id,
    )
    result = await db.execute(stmt)
    row = result.scalars().first()
    return row.presence if row is not None else "absent"

async def get_attention_for_message(
    db: AsyncSession, message_id: int, character_id: int
) -> float | None:
    """Attention score одного события для персонажа (belief pipeline §9).

    None при выключенном ``attention_enabled`` / нет строки — gating выключен.
    """
    if not settings.attention_enabled or message_id is None:
        return None
    stmt = select(models.MessagePresence).where(
        models.MessagePresence.character_id == character_id,
        models.MessagePresence.message_id == message_id,
    )
    result = await db.execute(stmt)
    row = result.scalars().first()
    return row.attention if row is not None else None

async def _attention_context_for_chat(
    db: AsyncSession, chat_id: int, character_ids: list[int]
) -> dict[int, dict[str, set[int]]]:
    """Per-character внимание-контекст (§11) одним заходом (2 запроса).

    Для каждого персонажа:
    - ``rel_targets`` — targets его направленных отношений (w_relationship);
    - ``anchor_authors`` — targets отношений с эмоциональным якорем
      (w_emotional: событие с таким автором активирует якорь).
    Пусто при выключенном ``attention_enabled`` — score не считается.
    """
    if not settings.attention_enabled or not character_ids:
        return {}
    rel_stmt = select(models.CharacterRelationship).where(
        models.CharacterRelationship.chat_id == chat_id,
        models.CharacterRelationship.source_character_id.in_(character_ids),
    )
    rels = list((await db.execute(rel_stmt)).scalars().all())
    rel_ids = [r.id for r in rels]

    anchored_rel_ids: set[int] = set()
    if rel_ids:
        anchor_stmt = select(models.MemoryAnchor.relationship_id).where(
            models.MemoryAnchor.relationship_id.in_(rel_ids)
        )
        anchored_rel_ids = set((await db.execute(anchor_stmt)).scalars().all())

    rel_targets: dict[int, set[int]] = {}
    anchor_authors: dict[int, set[int]] = {}
    for rel in rels:
        rel_targets.setdefault(rel.source_character_id, set()).add(
            rel.target_character_id
        )
        if rel.id in anchored_rel_ids:
            anchor_authors.setdefault(rel.source_character_id, set()).add(
                rel.target_character_id
            )
    return {
        cid: {
            "rel_targets": rel_targets.get(cid, set()),
            "anchor_authors": anchor_authors.get(cid, set()),
        }
        for cid in character_ids
    }

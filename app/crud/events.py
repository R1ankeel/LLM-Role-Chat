"""World events / round events / event links (Sprint 4)."""



from __future__ import annotations



import json

from sqlalchemy import select

from sqlalchemy.ext.asyncio import AsyncSession

from .. import models

async def get_relationship_events_for_round(
    db: AsyncSession, round_id: str | None
) -> list[dict]:
    """Relationship события раунда с source/target и дельтами (для emotion_engine).

    Join с ``character_relationships``: только направленные рёбра, которые реально
    изменились в этом раунде (kind='llm'). Пустой round_id → пустой список.
    """
    if not round_id:
        return []
    stmt = (
        select(models.RelationshipEvent, models.CharacterRelationship)
        .join(
            models.CharacterRelationship,
            models.CharacterRelationship.id == models.RelationshipEvent.relationship_id,
        )
        .where(
            models.RelationshipEvent.round_id == round_id,
            models.RelationshipEvent.kind == "llm",
        )
    )
    result = await db.execute(stmt)
    rows = []
    for event, rel in result.all():
        rows.append(
            {
                "source_character_id": rel.source_character_id,
                "target_character_id": rel.target_character_id,
                "delta_affection": event.delta_affection,
                "delta_trust": event.delta_trust,
                "delta_attraction": event.delta_attraction,
                "delta_resentment": event.delta_resentment,
                "delta_jealousy": event.delta_jealousy,
                "importance": event.importance,
            }
        )
    return rows

async def get_world_events_for_round(
    db: AsyncSession, round_id: str | None
) -> list[dict]:
    """World events раунда со структурной разметкой (эмоциональная салиенсность).

    Только extraction-события (emotional_salience/importance заполнены):
    движковые speech/move салиенс не имеют и эмоции не двигают.
    """
    if not round_id:
        return []
    stmt = (
        select(models.WorldEvent)
        .where(
            models.WorldEvent.round_id == round_id,
            models.WorldEvent.emotional_salience.isnot(None),
        )
        .order_by(models.WorldEvent.id)
    )
    result = await db.execute(stmt)
    events: list[dict] = []
    for event in result.scalars().all():
        try:
            target_ids = json.loads(event.target_character_ids or "[]")
        except (json.JSONDecodeError, TypeError):
            target_ids = []
        events.append(
            {
                "character_id": event.character_id,
                "event_type": event.event_type,
                "importance": event.importance,
                "emotional_salience": event.emotional_salience,
                "story_salience": event.story_salience,
                "target_character_ids": target_ids,
                "action": event.action,
            }
        )
    return events

async def get_round_world_events(
    db: AsyncSession, round_id: str | None
) -> list[dict]:
    """ВСЕ world events раунда (включая движковые speech/move, §9 pipeline).

    Для belief pipeline: каждое событие с ``message_id`` (речевое) привязывается
    к presence/attention через ``message_presence`` — так belief пишется ТОЛЬКО
    из событий, которые персонаж реально воспринял (изоляция R2). Возвращает
    ``{message_id, character_id, event_type, target_character_ids, action}``.
    """
    if not round_id:
        return []
    stmt = (
        select(models.WorldEvent)
        .where(models.WorldEvent.round_id == round_id)
        .order_by(models.WorldEvent.id)
    )
    result = await db.execute(stmt)
    events: list[dict] = []
    for event in result.scalars().all():
        try:
            target_ids = json.loads(event.target_character_ids or "[]")
        except (json.JSONDecodeError, TypeError):
            target_ids = []
        try:
            action = json.loads(event.action or "{}")
        except (json.JSONDecodeError, TypeError):
            action = {}
        if not isinstance(action, dict):
            action = {}
        events.append(
            {
                "id": event.id,
                "message_id": event.message_id,
                "character_id": event.character_id,
                "event_type": event.event_type,
                "target_character_ids": target_ids,
                "action": action,
            }
        )
    return events

async def get_story_round_world_events(
    db: AsyncSession, chat_id: int, round_id: str | None
) -> list[dict]:
    """Extraction world_events раунда для сюжета (importance NOT NULL).

    Структурированные события (action/importance/story_salience) — кандидаты
    в ``story_events``. Движковые speech/move салиенс не имеют и сюжет не
    двигают (аналог ``get_world_events_for_round``, но с location/id).
    """
    if not round_id:
        return []
    stmt = (
        select(models.WorldEvent)
        .where(
            models.WorldEvent.chat_id == chat_id,
            models.WorldEvent.round_id == round_id,
            models.WorldEvent.importance.isnot(None),
        )
        .order_by(models.WorldEvent.id)
    )
    result = await db.execute(stmt)
    events: list[dict] = []
    for event in result.scalars().all():
        try:
            target_ids = json.loads(event.target_character_ids or "[]")
        except (json.JSONDecodeError, TypeError):
            target_ids = []
        try:
            action = json.loads(event.action or "{}")
        except (json.JSONDecodeError, TypeError):
            action = {}
        if not isinstance(action, dict):
            action = {}
        events.append(
            {
                "id": event.id,
                "character_id": event.character_id,
                "event_type": event.event_type or "",
                "location": event.location or "",
                "importance": event.importance,
                "story_salience": event.story_salience,
                "target_character_ids": target_ids,
                "action": action,
            }
        )
    return events

async def get_world_events_for_chat(
    db: AsyncSession, chat_id: int, limit: int = 50
) -> list[models.WorldEvent]:
    """World events чата (новые сначала) — для debug/observability (§29.1)."""
    stmt = (
        select(models.WorldEvent)
        .where(models.WorldEvent.chat_id == chat_id)
        .order_by(models.WorldEvent.created_at.desc(), models.WorldEvent.id.desc())
        .limit(max(0, int(limit)))
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())

async def get_world_events_by_ids(
    db: AsyncSession, ids: list[int]
) -> dict[int, dict]:
    """Короткие тексты world_events по id (для cause/связей)."""
    if not ids:
        return {}
    stmt = select(models.WorldEvent).where(models.WorldEvent.id.in_(list(set(ids))))
    result = await db.execute(stmt)
    out: dict[int, dict] = {}
    for event in result.scalars().all():
        try:
            action = json.loads(event.action or "{}")
        except (json.JSONDecodeError, TypeError):
            action = {}
        if not isinstance(action, dict):
            action = {}
        out[event.id] = {"event_type": event.event_type or "", "action": action}
    return out

async def get_event_links_for_events(
    db: AsyncSession, chat_id: int, event_ids: list[int]
) -> list[tuple[int, int]]:
    """Пары (event_id, caused_by_event_id) — причинные связи событий."""
    if not event_ids:
        return []
    stmt = (
        select(models.EventLink.event_id, models.EventLink.caused_by_event_id)
        .where(
            models.EventLink.chat_id == chat_id,
            models.EventLink.event_id.in_(list(set(event_ids))),
        )
    )
    result = await db.execute(stmt)
    return [(int(event_id), int(caused_by)) for event_id, caused_by in result.all()]

async def get_caused_by_ids_for_events(
    db: AsyncSession, chat_id: int, event_ids: list[int]
) -> dict[int, list[int]]:
    """event_id → список caused_by_event_id (события-причины, kind=causes)."""
    if not event_ids:
        return {}
    stmt = (
        select(models.EventLink.event_id, models.EventLink.caused_by_event_id)
        .where(
            models.EventLink.chat_id == chat_id,
            models.EventLink.event_id.in_(list(set(event_ids))),
            models.EventLink.kind == "causes",
        )
    )
    result = await db.execute(stmt)
    out: dict[int, list[int]] = {}
    for event_id, caused_by in result.all():
        out.setdefault(int(event_id), []).append(int(caused_by))
    return out

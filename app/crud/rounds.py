"""Раунды: lookup, save_round_events, clamp-хелперы (Sprint 4)."""



from __future__ import annotations



import json

import logging

from typing import Optional, Tuple

from sqlalchemy import select

from sqlalchemy.orm import selectinload

from sqlalchemy.ext.asyncio import AsyncSession

from .. import models

from .. import schemas

from ..config import settings

from .characters import get_characters_by_chat

from .locations import get_chat_locations, resolve_location_name



logger = logging.getLogger(__name__)

# ------------------------ Relationship round lookup (Sprint 4) ------------------------
def parse_round_id(round_id: str) -> Optional[Tuple[int, int]]:
    """Parse ``r{chat_id}-m{user_message_id}`` → ``(chat_id, user_message_id)``.

    Returns ``None`` for any malformed round id.
    """
    if not round_id or not round_id.startswith("r") or "-m" not in round_id:
        return None
    parts = round_id.split("-m")
    if len(parts) != 2:
        return None
    try:
        chat_id = int(parts[0][1:])
        user_message_id = int(parts[1])
    except (TypeError, ValueError):
        return None
    return chat_id, user_message_id

async def get_latest_round_id(
    db: AsyncSession, chat_id: int
) -> Optional[str]:
    """Most recent non-null ``round_id`` seen for any relationship in the chat.

    Used by the on-demand analyze endpoint when no explicit round is given.
    """
    stmt = (
        select(models.RelationshipEvent.round_id)
        .join(
            models.CharacterRelationship,
            models.CharacterRelationship.id == models.RelationshipEvent.relationship_id,
        )
        .where(
            models.CharacterRelationship.chat_id == chat_id,
            models.RelationshipEvent.round_id.isnot(None),
        )
        .order_by(models.RelationshipEvent.id.desc())
        .limit(1)
    )
    result = await db.execute(stmt)
    return result.scalars().first()

async def get_round_messages_by_round_id(
    db: AsyncSession, round_id: str
) -> list[models.Message]:
    """Resolve ``r{chat_id}-m{user_msg_id}`` to the messages of that round.

    Returns the user message that started the round followed by every later
    message up to (but not including) the next ``role="user"`` message.
    Returns ``[]`` for a malformed or unknown round id.
    """
    parsed = parse_round_id(round_id)
    if parsed is None:
        return []
    chat_id, user_message_id = parsed

    user_msg = await db.get(models.Message, user_message_id)
    if user_msg is None or user_msg.chat_id != chat_id or user_msg.role != "user":
        return []

    stmt = (
        select(models.Message)
        .where(
            models.Message.chat_id == chat_id,
            models.Message.id >= user_message_id,
        )
        .options(selectinload(models.Message.character))
        .order_by(models.Message.timestamp, models.Message.id)
    )
    result = await db.execute(stmt)
    round_messages: list[models.Message] = []
    for message in result.scalars().all():
        if message.role == "user" and message.id != user_message_id:
            break
        round_messages.append(message)
    return round_messages

# ------------------------ Structured World Events (Sprint 1) ------------------------
def _clamp01(value: float | int | None) -> float | None:
    """Clamp 0..1; None passes through (движковые события салиенс не имеют)."""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, min(1.0, f))

async def save_round_events(
    db: AsyncSession,
    chat_id: int,
    events: list[schemas.ExtractedEvent],
    *,
    round_id: str | None = None,
) -> schemas.EventExtractionReport:
    """Записать извлечённые раундные события + causal links (§15, Sprint 1).

    Event extraction (LLM) — отдельная от движковых ``speech``/``move`` запись:
    движковые события ``importance`` не заполняют (NULL), поэтому раунд с уже
    записанной extraction детектируется по ``importance IS NOT NULL`` — повторный
    прогон pipeline идемпотентен и не дублирует события/links.

    Event'ы с ``importance < settings.event_min_importance`` пропускаются
    (стоимостной лимит). Неизвестный персонаж / нерезолвнутая локация деградируют
    в NULL (FK nullable) — никогда не падают. ``causes`` — индексы в переданном
    списке ``events``; из них строятся ``EventLink(kind=causes)``.
    """
    report = schemas.EventExtractionReport(extraction_used=True)
    if not events:
        return report

    if round_id:
        stmt = (
            select(models.WorldEvent.id)
            .where(
                models.WorldEvent.chat_id == chat_id,
                models.WorldEvent.round_id == round_id,
                models.WorldEvent.importance.isnot(None),
            )
            .limit(1)
        )
        existing = (await db.execute(stmt)).scalars().first()
        if existing is not None:
            logger.info(
                "[Sprint1] round %s already has extracted events — skip", round_id
            )
            return report

    characters = await get_characters_by_chat(db, chat_id, include_player=True)
    name_to_id: dict[str, int] = {}
    for character in characters:
        key = (character.name or "").strip().casefold()
        if key:
            name_to_id.setdefault(key, character.id)

    locations = await get_chat_locations(db, chat_id)
    min_importance = float(settings.event_min_importance or 0.0)

    index_to_event: dict[int, models.WorldEvent] = {}
    skipped = 0
    for idx, ev in enumerate(events):
        try:
            imp = float(ev.importance or 0.0)
        except (TypeError, ValueError):
            imp = 0.0
        if imp < min_importance:
            skipped += 1
            continue
        source_id = name_to_id.get((ev.source_character or "").strip().casefold())
        target_ids: list[int] = []
        for target in ev.targets or []:
            tid = name_to_id.get((target or "").strip().casefold())
            if tid is not None:
                target_ids.append(tid)
        loc = resolve_location_name(locations, ev.location or "")
        action_data = ev.action.model_dump() if ev.action else {}
        event = models.WorldEvent(
            chat_id=chat_id,
            character_id=source_id,
            event_type=(ev.event_type or "event").strip() or "event",
            location=(ev.location or "").strip(),
            location_id=loc.id if loc else None,
            round_id=round_id,
            target_character_ids=json.dumps(target_ids, ensure_ascii=False),
            action=json.dumps(action_data, ensure_ascii=False),
            importance=imp,
            story_salience=_clamp01(ev.story_salience),
            emotional_salience=_clamp01(ev.emotional_salience),
        )
        db.add(event)
        index_to_event[idx] = event

    await db.flush()

    links = 0
    for idx, ev in enumerate(events):
        target_event = index_to_event.get(idx)
        if target_event is None:
            continue
        for cause_idx in ev.causes or []:
            if not isinstance(cause_idx, int):
                continue
            cause_event = index_to_event.get(cause_idx)
            if cause_event is None or cause_event.id == target_event.id:
                continue
            db.add(
                models.EventLink(
                    chat_id=chat_id,
                    event_id=target_event.id,
                    caused_by_event_id=cause_event.id,
                    kind="causes",
                )
            )
            links += 1

    await db.commit()
    report.written_events = len(index_to_event)
    report.written_links = links
    report.skipped_below_importance = skipped
    return report

def _clamp_json_number(value, low: float, high: float) -> float:
    """Clamp число для JSON-полей character_states (None проходит как None)."""
    if value is None:
        return None  # type: ignore[return-value]
    try:
        f = float(value)
    except (TypeError, ValueError):
        return low
    return max(low, min(high, f))

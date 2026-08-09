"""Сообщения + world_event builder (Sprint 4)."""



from __future__ import annotations



from datetime import datetime

from sqlalchemy import delete, func, select

from sqlalchemy.orm import selectinload

from sqlalchemy.ext.asyncio import AsyncSession

from .. import models

from .. import schemas

from ..perception_utils import serialize_target_ids

from ..config import settings

from .threads import ensure_message_thread_delivery

# ----------------------------- Message -----------------------------
def _build_world_event(
    db_message: models.Message, *, round_id: str | None = None
) -> models.WorldEvent:
    """Dual-write: `WorldEvent` рядом с `Message` (WPE.md Фаза 3).

    Событие копирует поля сообщения (legacy-bridge: строковая локация).
    ``event_type``: ``speech`` для user/character, ``system`` для системных.
    ``round_id`` для user-сообщения выводится так же, как в chat_engine
    (``r{chat_id}-m{message_id}``) — тот же раунд, что и остальные реплики.
    """
    role = (db_message.role or "").strip().lower()
    event_type = "system" if role == "system" else "speech"
    if round_id is None and role == "user":
        round_id = f"r{db_message.chat_id}-m{db_message.id}"
    return models.WorldEvent(
        chat_id=db_message.chat_id,
        character_id=db_message.character_id,
        message_id=db_message.id,
        event_type=event_type,
        location=db_message.location or "",
        round_id=round_id,
        target_character_ids=db_message.target_character_ids or "[]",
    )

async def create_message(
    db: AsyncSession,
    message: schemas.MessageCreate,
    *,
    round_id: str | None = None,
) -> models.Message:
    """Create a message; with `WORLD_ENGINE_EVENTS_ENABLED` also writes its
    `WorldEvent` atomically (same transaction, WPE.md Фаза 3 dual-write).

    The dual-write is a no-op by default (flag off). Shadow perception is
    triggered by the service layer (`wpe_shadow.maybe_run_shadow_perception`)
    after the message is persisted — never affects the result (Sprint 1, §7.1).
    """
    kwargs = message.orm_kwargs() if hasattr(message, "orm_kwargs") else message.model_dump()
    if "target_character_ids" in kwargs and not isinstance(
        kwargs["target_character_ids"], str
    ):
        kwargs["target_character_ids"] = serialize_target_ids(
            kwargs["target_character_ids"]
        )
    db_message = models.Message(**kwargs)
    db.add(db_message)
    await db.flush()
    if settings.world_engine_events_enabled:
        db.add(_build_world_event(db_message, round_id=round_id))
    if settings.world_engine_threads_enabled:
        await ensure_message_thread_delivery(db, db_message)
    await db.commit()
    await db.refresh(db_message)
    return db_message

async def get_messages_by_chat(
    db: AsyncSession, chat_id: int, limit: int | None = None
) -> list[models.Message]:
    stmt = (
        select(models.Message)
        .where(models.Message.chat_id == chat_id)
        .options(selectinload(models.Message.character))
    )
    if limit is None:
        stmt = stmt.order_by(models.Message.timestamp, models.Message.id)
        result = await db.execute(stmt)
        return list(result.scalars().all())
    stmt = stmt.order_by(
        models.Message.timestamp.desc(), models.Message.id.desc()
    ).limit(limit)
    result = await db.execute(stmt)
    messages = list(result.scalars().all())
    messages.reverse()
    return messages

async def get_messages_paginated(
    db: AsyncSession, chat_id: int, limit: int = 50, offset: int = 0
) -> list[models.Message]:
    stmt = (
        select(models.Message)
        .where(models.Message.chat_id == chat_id)
        .options(selectinload(models.Message.character))
        .order_by(models.Message.timestamp, models.Message.id)
        .offset(offset)
        .limit(limit)
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())

async def count_messages_after(
    db: AsyncSession, chat_id: int, after_message_id: int
) -> int:
    """Count messages in chat with id strictly greater than after_message_id."""
    stmt = (
        select(func.count())
        .select_from(models.Message)
        .where(
            models.Message.chat_id == chat_id,
            models.Message.id > after_message_id,
        )
    )
    result = await db.execute(stmt)
    return result.scalar() or 0

async def get_messages_since(
    db: AsyncSession,
    chat_id: int,
    after_message_id: int,
    limit: int | None = None,
) -> list[models.Message]:
    """Return messages with id > after_message_id in chronological order."""
    stmt = (
        select(models.Message)
        .where(
            models.Message.chat_id == chat_id,
            models.Message.id > after_message_id,
        )
        .options(selectinload(models.Message.character))
        .order_by(models.Message.timestamp, models.Message.id)
    )
    if limit is not None:
        stmt = stmt.limit(limit)
    result = await db.execute(stmt)
    return list(result.scalars().all())

async def get_messages_since_ts(
    db: AsyncSession,
    chat_id: int,
    since: datetime,
    *,
    role: str | None = None,
    character_id: int | None = None,
    limit: int | None = None,
) -> list[models.Message]:
    """Messages of a chat created strictly after ``since`` (Sprint 12).

    Used by the adaptive-consolidation summary component to gather new dialogue
    since the last consolidation. ``character_id`` restricts to one character.
    """
    stmt = (
        select(models.Message)
        .where(
            models.Message.chat_id == chat_id,
            models.Message.timestamp > since,
        )
        .order_by(models.Message.timestamp, models.Message.id)
    )
    if role is not None:
        stmt = stmt.where(models.Message.role == role)
    if character_id is not None:
        stmt = stmt.where(models.Message.character_id == character_id)
    if limit is not None:
        stmt = stmt.limit(limit)
    result = await db.execute(stmt)
    return list(result.scalars().all())

async def delete_message(
    db: AsyncSession, message_id: int, *, cascade_after: bool = False
) -> bool:
    """Delete a single message from the chat.

    ``cascade_after`` deletes the message and every message that follows it in
    the same chat (used for player messages: replies without the player's line
    are meaningless). Presence records are removed by the FK ``CASCADE``.
    Returns False if the message does not exist.
    """
    db_message = await db.get(models.Message, message_id)
    if db_message is None:
        return False
    if cascade_after:
        await db.execute(
            delete(models.Message).where(
                models.Message.chat_id == db_message.chat_id,
                models.Message.id >= message_id,
            )
        )
    else:
        await db.delete(db_message)
    await db.commit()
    return True

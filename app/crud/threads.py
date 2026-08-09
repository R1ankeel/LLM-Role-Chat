"""Messenger-нити и доставка сообщений (Sprint 4)."""



from __future__ import annotations



from sqlalchemy import select

from sqlalchemy.ext.asyncio import AsyncSession

from .. import models

from ..perception_utils import REMOTE_CHANNELS, parse_target_ids

from ..config import settings

# -------------------- WPE 3.0: Threads / мессенджер (Фаза 6) --------------------
async def _get_thread(
    db: AsyncSession, chat_id: int, channel: str
) -> models.Thread | None:
    """Существующий тред чата по каналу (без создания)."""
    stmt = (
        select(models.Thread)
        .where(models.Thread.chat_id == chat_id, models.Thread.channel == channel)
        .order_by(models.Thread.id)
        .limit(1)
    )
    return (await db.execute(stmt)).scalars().first()

async def get_or_create_thread(
    db: AsyncSession,
    chat_id: int,
    channel: str,
    *,
    name: str = "",
) -> models.Thread:
    """Тред/канал общения чата (мессенджер, звонок и т.д.) (Фаза 6)."""
    channel = (channel or "messenger").strip().lower()
    thread = await _get_thread(db, chat_id, channel)
    if thread is None:
        thread = models.Thread(chat_id=chat_id, channel=channel, name=name)
        db.add(thread)
        await db.flush()
    return thread

async def ensure_thread_participant(
    db: AsyncSession, thread: models.Thread, character_id: int
) -> models.ThreadParticipantState:
    """Гарантировать участие персонажа в треде (создаёт при отсутствии)."""
    stmt = select(models.ThreadParticipantState).where(
        models.ThreadParticipantState.thread_id == thread.id,
        models.ThreadParticipantState.character_id == character_id,
    )
    state = (await db.execute(stmt)).scalars().first()
    if state is None:
        state = models.ThreadParticipantState(
            thread_id=thread.id, character_id=character_id
        )
        db.add(state)
        await db.flush()
    return state

async def mark_thread_delivered(
    db: AsyncSession,
    thread: models.Thread,
    character_ids: list[int],
    message_id: int,
) -> None:
    """Отметить доставку сообщения адресатам треда (`remote_status=delivered`)."""
    for cid in character_ids:
        state = await ensure_thread_participant(db, thread, cid)
        if (
            state.last_delivered_message_id is None
            or message_id > state.last_delivered_message_id
        ):
            state.last_delivered_message_id = message_id

async def _ensure_thread_for_action(
    db: AsyncSession,
    chat_id: int,
    channel: str,
    author_id: int | None,
    targets: list[int],
) -> None:
    """Создать тред и участников по действию ``send_message`` (Фаза 6).

    Доставка (`mark_thread_delivered`) проставляется позже в ``create_message``
    для реального сообщения с ``message.id``.
    """
    if channel not in REMOTE_CHANNELS:
        return
    thread = await get_or_create_thread(db, chat_id, channel)
    members = set(targets)
    if author_id is not None:
        members.add(author_id)
    for cid in members:
        await ensure_thread_participant(db, thread, cid)

async def ensure_message_thread_delivery(db: AsyncSession, message) -> None:
    """Создать/обновить ``Thread`` + ``ThreadParticipantState`` для события.

    Фаза 6 (WPE.md §4, Golden #6/#15): сообщение по удалённому каналу
    (magic/phone/radio/messenger) создаёт/обновляет тред; адресат получает
    ``remote_status=delivered`` независимо от локации. Вызывается из
    ``create_message`` в той же транзакции (атомарно).
    """
    if not settings.world_engine_threads_enabled:
        return
    channel = (getattr(message, "channel", None) or "direct").strip().lower()
    if channel not in REMOTE_CHANNELS:
        return
    chat_id = getattr(message, "chat_id", None)
    message_id = getattr(message, "id", None)
    if chat_id is None or message_id is None:
        return
    thread = await get_or_create_thread(db, chat_id, channel)
    author_id = getattr(message, "character_id", None)
    targets = parse_target_ids(
        getattr(message, "target_character_ids", None)
    )
    members = set(targets)
    if author_id is not None:
        members.add(author_id)
    for cid in members:
        await ensure_thread_participant(db, thread, cid)
    await mark_thread_delivered(db, thread, targets, message_id)

async def thread_delivery_ids_for_message(
    db: AsyncSession, message
) -> frozenset[int]:
    """Id персонажей, которым событие доставлено через тред (Фаза 6).

    Пустое множество, когда Threads выключены или сообщение не по удалённому
    каналу — тогда ``perceive()`` не отдаёт ``remote_status=delivered``.
    """
    if not settings.world_engine_threads_enabled:
        return frozenset()
    channel = (getattr(message, "channel", None) or "direct").strip().lower()
    if channel not in REMOTE_CHANNELS:
        return frozenset()
    chat_id = getattr(message, "chat_id", None)
    message_id = getattr(message, "id", None)
    if chat_id is None or message_id is None:
        return frozenset()
    thread = await _get_thread(db, chat_id, channel)
    if thread is None:
        return frozenset()
    stmt = select(models.ThreadParticipantState).where(
        models.ThreadParticipantState.thread_id == thread.id,
        models.ThreadParticipantState.last_delivered_message_id.is_not(None),
        models.ThreadParticipantState.last_delivered_message_id >= message_id,
    )
    rows = (await db.execute(stmt)).scalars().all()
    return frozenset(state.character_id for state in rows)

async def _known_voices_for_chat(
    db: AsyncSession, chat_id: int
) -> dict[int, set[int]]:
    """``{observer_id: set(author_ids)}`` из отношений (источник voice familiarity).

    Голос автора считается знакомым наблюдателю, если у наблюдателя есть
    направленное ``CharacterRelationship`` к автору (WPE.md §4, Фаза 6).
    """
    stmt = select(models.CharacterRelationship).where(
        models.CharacterRelationship.chat_id == chat_id
    )
    rows = (await db.execute(stmt)).scalars().all()
    result: dict[int, set[int]] = {}
    for rel in rows:
        result.setdefault(rel.source_character_id, set()).add(
            rel.target_character_id
        )
    return result

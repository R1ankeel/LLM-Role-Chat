"""Story state / story events / story threads (Sprint 4)."""



from __future__ import annotations



import json

from typing import Any

from sqlalchemy import func, select, update

from sqlalchemy.ext.asyncio import AsyncSession

from .. import models

def _parse_json_list(value: Any) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except (json.JSONDecodeError, TypeError):
            return []
    return []

async def get_story_state(
    db: AsyncSession, chat_id: int
) -> models.StoryState | None:
    """Прочитать Current Story State чата (одна строка на чат)."""
    stmt = select(models.StoryState).where(models.StoryState.chat_id == chat_id)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()

async def get_or_create_story_state(
    db: AsyncSession, chat_id: int, round_id: str | None = None
) -> models.StoryState:
    """Получить story_state чата или создать пустую строку (Sprint 8).

    ``original_plot`` копируется из ``chats.original_plot`` как срез на момент
    версии (§16.2). Пустой ``current_story`` = '{}', ``story_phase`` = ''.
    """
    from .chats import get_chat  # против цикла модулей (Sprint 4)
    state = await get_story_state(db, chat_id)
    if state is not None:
        return state
    chat = await get_chat(db, chat_id)
    state = models.StoryState(
        chat_id=chat_id,
        original_plot=(chat.original_plot if chat is not None else "") or "",
        current_story="{}",
        story_phase="",
        updated_round_id=round_id,
    )
    db.add(state)
    await db.commit()
    await db.refresh(state)
    return state

async def update_story_state(
    db: AsyncSession,
    chat_id: int,
    *,
    original_plot: str | None = None,
    current_story: dict | str | None = None,
    story_phase: str | None = None,
    updated_round_id: str | None = None,
    version: int | None = None,
    last_consolidation_rounds: int | None = None,
) -> models.StoryState | None:
    """Обновить story_state чата (частичное; None-поля НЕ сбрасываются).

    ``version``/``last_consolidation_rounds`` — для Sprint 9: консолидация
    пишет новую версию (rollback = предыдущая версия остаётся при сбое).
    """
    state = await get_story_state(db, chat_id)
    if state is None:
        return None
    if original_plot is not None:
        state.original_plot = str(original_plot)
    if current_story is not None:
        state.current_story = (
            json.dumps(current_story, ensure_ascii=False)
            if isinstance(current_story, dict)
            else str(current_story)
        )
    if story_phase is not None:
        state.story_phase = str(story_phase)
    if updated_round_id is not None:
        state.updated_round_id = updated_round_id
    if version is not None:
        state.version = int(version)
    if last_consolidation_rounds is not None:
        state.last_consolidation_rounds = int(last_consolidation_rounds)
    await db.commit()
    await db.refresh(state)
    return state

async def get_story_events_for_chat(
    db: AsyncSession, chat_id: int, limit: int = 50
) -> list[models.StoryEvent]:
    """Story events чата (новые сначала)."""
    stmt = (
        select(models.StoryEvent)
        .where(models.StoryEvent.chat_id == chat_id)
        .order_by(models.StoryEvent.id.desc())
        .limit(limit)
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())

async def count_story_events(db: AsyncSession, chat_id: int) -> int:
    """Сколько story_events записано для чата (progress)."""
    stmt = (
        select(func.count())
        .select_from(models.StoryEvent)
        .where(models.StoryEvent.chat_id == chat_id)
    )
    result = await db.execute(stmt)
    return int(result.scalar_one() or 0)

async def count_distinct_rounds(db: AsyncSession, chat_id: int) -> int:
    """Число раундов чата (distinct round_id в world_events, Sprint 9 trigger).

    У каждого раунда, дошедшего до event-этапа, есть world_events — это
    детерминированная мера «сколько раундов прошло» для §17.1.
    """
    stmt = (
        select(func.count(func.distinct(models.WorldEvent.round_id)))
        .where(
            models.WorldEvent.chat_id == chat_id,
            models.WorldEvent.round_id.isnot(None),
        )
    )
    result = await db.execute(stmt)
    return int(result.scalar_one() or 0)

async def get_story_event_ids_for_chat(db: AsyncSession, chat_id: int) -> set[int]:
    """event_id уже записанных story_events чата (идемпотентность записи)."""
    stmt = select(models.StoryEvent.event_id).where(
        models.StoryEvent.chat_id == chat_id,
        models.StoryEvent.event_id.isnot(None),
    )
    result = await db.execute(stmt)
    return {int(eid) for eid in result.scalars().all() if eid is not None}

async def create_story_event(
    db: AsyncSession,
    *,
    event_id: int | None,
    chat_id: int,
    round_id: str | None,
    event: str,
    actors: list,
    location: str,
    cause: str,
    consequences: str,
    importance: float,
    story_thread_id: int | None = None,
) -> models.StoryEvent:
    """Проекция канонического world_event для сюжета (§16.3, Sprint 8)."""
    row = models.StoryEvent(
        event_id=event_id,
        chat_id=chat_id,
        round_id=round_id,
        event=(event or "").strip()[:4000],
        actors=json.dumps(actors, ensure_ascii=False),
        location=(location or "").strip()[:255],
        cause=(cause or "").strip()[:4000],
        consequences=(consequences or "").strip()[:4000],
        importance=int(round(float(importance))),
        story_thread_id=story_thread_id,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row

async def get_active_story_threads(
    db: AsyncSession, chat_id: int, top_k: int | None = None
) -> list[models.StoryThread]:
    """Активные story_threads чата (importance desc; top-K для контекста)."""
    stmt = (
        select(models.StoryThread)
        .where(
            models.StoryThread.chat_id == chat_id,
            models.StoryThread.status == "active",
        )
        .order_by(models.StoryThread.importance.desc(), models.StoryThread.id.desc())
    )
    if top_k:
        stmt = stmt.limit(top_k)
    result = await db.execute(stmt)
    return list(result.scalars().all())

async def get_story_threads_for_chat(
    db: AsyncSession, chat_id: int
) -> list[models.StoryThread]:
    """Все story_threads чата (active + archived) — для debug (§29.1)."""
    stmt = (
        select(models.StoryThread)
        .where(models.StoryThread.chat_id == chat_id)
        .order_by(models.StoryThread.importance.desc(), models.StoryThread.id.desc())
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())

async def get_story_threads_by_status(
    db: AsyncSession, chat_id: int, status: str
) -> list[models.StoryThread]:
    """story_threads чата по статусу (active|archived) — для debug (§29.1)."""
    stmt = (
        select(models.StoryThread)
        .where(
            models.StoryThread.chat_id == chat_id,
            models.StoryThread.status == status,
        )
        .order_by(models.StoryThread.importance.desc(), models.StoryThread.id.desc())
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())

async def find_story_thread_by_name(
    db: AsyncSession, chat_id: int, name: str
) -> models.StoryThread | None:
    """Найти thread по точному имени (casefold) — дедупликация линий."""
    needle = (name or "").strip().casefold()
    if not needle:
        return None
    stmt = (
        select(models.StoryThread)
        .where(models.StoryThread.chat_id == chat_id)
        .order_by(models.StoryThread.id.desc())
    )
    result = await db.execute(stmt)
    for thread in result.scalars().all():
        if (thread.name or "").strip().casefold() == needle:
            return thread
    return None

async def create_story_thread(
    db: AsyncSession,
    *,
    chat_id: int,
    name: str,
    actors: list,
    importance: float,
    status: str = "active",
    created_round_id: str | None = None,
) -> models.StoryThread:
    """Новая активная сюжетная линия (Sprint 8 write-path)."""
    row = models.StoryThread(
        chat_id=chat_id,
        name=(name or "").strip()[:500],
        actors=json.dumps(actors, ensure_ascii=False),
        importance=int(round(float(importance))),
        status=status,
        created_round_id=created_round_id,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row

async def update_story_thread(
    db: AsyncSession,
    thread_id: int,
    *,
    importance: float | None = None,
    actors: list | None = None,
    status: str | None = None,
) -> models.StoryThread | None:
    """Обновить thread: importance растёт (max), actors объединяются без дублей.

    ``status`` — для Sprint 9 (archived после завершения цели).
    """
    thread = await db.get(models.StoryThread, thread_id)
    if thread is None:
        return None
    if importance is not None:
        thread.importance = max(
            int(thread.importance or 0), int(round(float(importance)))
        )
    if actors is not None:
        merged = list(
            dict.fromkeys(
                [str(a) for a in _parse_json_list(thread.actors)]
                + [str(a) for a in actors]
            )
        )
        thread.actors = json.dumps(merged, ensure_ascii=False)
    if status is not None:
        thread.status = status
    await db.commit()
    await db.refresh(thread)
    return thread

async def set_story_event_thread(db: AsyncSession, event_id: int, thread_id: int) -> None:
    """Привязать story_event к story_thread (разметка проекции)."""
    await db.execute(
        update(models.StoryEvent)
        .where(models.StoryEvent.id == event_id)
        .values(story_thread_id=thread_id)
    )
    await db.commit()

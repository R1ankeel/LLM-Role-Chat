"""Character state + beliefs (Sprint 4)."""



from __future__ import annotations



import json

from datetime import datetime

from sqlalchemy import select

from sqlalchemy.ext.asyncio import AsyncSession

from .. import models

from ..config import settings

from .rounds import _clamp_json_number

async def get_character_state(
    db: AsyncSession, character_id: int
) -> models.CharacterState | None:
    """Прочитать состояние персонажа (одна строка на персонажа)."""
    stmt = select(models.CharacterState).where(
        models.CharacterState.character_id == character_id
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()

async def get_character_states_for_chat(
    db: AsyncSession, chat_id: int
) -> list[models.CharacterState]:
    """Прочитать состояния всех персонажей чата (для сводки/debug)."""
    stmt = (
        select(models.CharacterState)
        .where(models.CharacterState.chat_id == chat_id)
        .order_by(models.CharacterState.character_id)
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())

async def get_or_create_character_state(
    db: AsyncSession,
    chat_id: int,
    character_id: int,
    round_id: str | None = None,
) -> models.CharacterState:
    """Получить состояние персонажа или создать пустую строку (Sprint 3).

    Пустая строка: emotional_state '{}', mood '', stress NULL, physical_state
    '{}', attention NULL, active_goal '', personal_goals '[]'. Локация/отношения
    в state НЕ хранятся (берутся из существующих таблиц).
    """
    state = await get_character_state(db, character_id)
    if state is not None:
        return state
    state = models.CharacterState(
        chat_id=chat_id,
        character_id=character_id,
        emotional_state="{}",
        mood="",
        stress=None,
        physical_state="{}",
        attention=None,
        current_focus_id=None,
        active_goal="",
        personal_goals="[]",
        updated_round_id=round_id,
    )
    db.add(state)
    await db.commit()
    await db.refresh(state)
    return state

async def update_character_state(
    db: AsyncSession,
    character_id: int,
    *,
    emotional_state: dict | str | None = None,
    mood: str | None = None,
    stress: float | None = None,
    physical_state: dict | str | None = None,
    attention: str | None = None,
    current_focus_id: int | None = None,
    active_goal: str | None = None,
    personal_goals: list | str | None = None,
    updated_round_id: str | None = None,
) -> models.CharacterState | None:
    """Обновить состояние персонажа (частичное; None-поля НЕ сбрасываются,
    кроме явного attention/current_focus_id, передаваемых как есть)."""
    state = await get_character_state(db, character_id)
    if state is None:
        return None

    if emotional_state is not None:
        state.emotional_state = (
            json.dumps(emotional_state, ensure_ascii=False)
            if isinstance(emotional_state, dict)
            else str(emotional_state)
        )
    if mood is not None:
        state.mood = str(mood)
    if stress is not None:
        state.stress = _clamp_json_number(stress, 0.0, 1.0)
    if physical_state is not None:
        state.physical_state = (
            json.dumps(physical_state, ensure_ascii=False)
            if isinstance(physical_state, dict)
            else str(physical_state)
        )
    if attention is not None:
        state.attention = attention or None
    if current_focus_id is not None:
        state.current_focus_id = current_focus_id or None
    if active_goal is not None:
        state.active_goal = str(active_goal)
    if personal_goals is not None:
        state.personal_goals = (
            json.dumps(personal_goals, ensure_ascii=False)
            if isinstance(personal_goals, list)
            else str(personal_goals)
        )
    if updated_round_id is not None:
        state.updated_round_id = updated_round_id

    await db.commit()
    await db.refresh(state)
    return state

async def get_beliefs_for_character(
    db: AsyncSession,
    character_id: int,
    *,
    top_k: int | None = None,
    min_confidence: float = 0.0,
) -> list[models.Belief]:
    """Beliefs персонажа (топ-K по confidence, §9).

    read-path: в контекст попадают ТОЛЬКО свои beliefs; пусто при выключенном
    ``beliefs_enabled`` (canary — никто не читает таблицу до включения флага).
    """
    if not settings.beliefs_enabled:
        return []
    stmt = select(models.Belief).where(
        models.Belief.character_id == character_id,
        models.Belief.confidence >= min_confidence,
    )
    if top_k is None:
        top_k = settings.beliefs_top_k
    stmt = stmt.order_by(models.Belief.confidence.desc(), models.Belief.id.desc())
    if top_k:
        stmt = stmt.limit(top_k)
    result = await db.execute(stmt)
    return list(result.scalars().all())

async def get_beliefs_for_chat(
    db: AsyncSession, chat_id: int
) -> list[models.Belief]:
    """Все beliefs чата (для debug/API; §29.1)."""
    stmt = (
        select(models.Belief)
        .where(models.Belief.chat_id == chat_id)
        .order_by(models.Belief.character_id, models.Belief.confidence.desc())
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())

async def _find_belief(
    db: AsyncSession,
    character_id: int,
    subject: str,
    predicate: str,
    object: str,
) -> models.Belief | None:
    stmt = select(models.Belief).where(
        models.Belief.character_id == character_id,
        models.Belief.subject == subject,
        models.Belief.predicate == predicate,
        models.Belief.object == object,
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()

def merge_confidence(current: float, new: float) -> float:
    """Слияние уверенности при повторном наблюдении (детерминированное).

    Sprint 1 (§7.1): перенесено из ``belief_service`` — crud не импортирует
    сервисный слой. Повторное наблюдение усиливает (идём к новому значению),
    но не выходит за 0..1; слабый новый источник не обнуляет сильное
    существующее убеждение.
    """
    return min(1.0, max(float(current), float(new)))

async def upsert_belief(
    db: AsyncSession,
    chat_id: int,
    character_id: int,
    *,
    subject: str,
    predicate: str,
    object: str = "",
    source: str = "memory",
    confidence: float = 0.5,
    type: str = "belief",
    world_truth_ref: int | None = None,
) -> models.Belief:
    """Создать/обновить belief персонажа (Sprint 5).

    Ключ — (character_id, subject, predicate, object): повторное наблюдение
    повышает confidence (детерминированное слияние ``merge_confidence``),
    источник обновляется только на более сильный. Невалидные значения
    обрезаются (confidence → 0..1, source/type → известные значения).
    """
    if source not in ("direct_observation", "heard", "told_by", "inference", "rumor", "memory"):
        source = "memory"
    if type not in ("fact", "belief", "suspicion"):
        type = "belief"
    confidence = _clamp_json_number(confidence, 0.0, 1.0)
    subject = (subject or "").strip()
    predicate = (predicate or "").strip()
    if not subject or not predicate:
        raise ValueError("belief subject and predicate are required")

    belief = await _find_belief(db, character_id, subject, predicate, object)
    if belief is not None:
        belief.confidence = merge_confidence(belief.confidence, confidence)
        belief.updated_at = datetime.utcnow()
        if world_truth_ref is not None:
            belief.world_truth_ref = world_truth_ref
        await db.commit()
        await db.refresh(belief)
        return belief
    belief = models.Belief(
        chat_id=chat_id,
        character_id=character_id,
        subject=subject,
        predicate=predicate,
        object=object,
        source=source,
        confidence=confidence,
        type=type,
        world_truth_ref=world_truth_ref,
    )
    db.add(belief)
    await db.commit()
    await db.refresh(belief)
    return belief

async def delete_belief(db: AsyncSession, belief_id: int) -> bool:
    """Удалить belief (для отката/debug)."""
    belief = await db.get(models.Belief, belief_id)
    if belief is None:
        return False
    await db.delete(belief)
    await db.commit()
    return True

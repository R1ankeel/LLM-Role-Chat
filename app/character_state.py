"""Character State — единое runtime-состояние персонажа (Plans/update20.md §8, Sprint 3).

Хранит ТОЛЬКО то, чего нет в других таблицах: эмоции, стресс, физическое
состояние, внимание, цели. Локация — из ``characters.location_id``, отношения —
из ``character_relationships``, окружение — из ``scene_states`` (НЕ дублируются).

Обновление — пост-раунд детерминированно через ``emotion_engine``:
relationship deltas (из ``relationship_events`` раунда) + события раунда
(``world_events`` с emotional_salience) → новые эмоции/стресс/mood. Опциональная
**Sensors-нормализация** (§5.1.3): при ``sensors_emotion_enabled`` SensorsService
предлагает ``{emotion, intensity, confidence}``; ``emotion_engine`` применяет его
только в рамках caps и правил, Sensors НЕ задаёт настроение напрямую.

Потребители: ``context_builder`` (блок ``YOUR STATE``, рендер по флагу
``character_state_enabled``) и будущий intent-слой (Sprint 10).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from . import crud
from .emotion_engine import (
    RENDER_INTENSITY_THRESHOLD,
    compute_state_update,
    normalize_emotional_state,
)
from .config import settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Сериализация / рендер
# ---------------------------------------------------------------------------

def state_to_dict(state: Any) -> dict[str, Any]:
    """Плоский dict состояния для API/debug (без лишних полей ORM)."""
    if state is None:
        return {}
    emotional_state = _load_json(state.emotional_state, "{}")
    return {
        "character_id": getattr(state, "character_id", None),
        "chat_id": getattr(state, "chat_id", None),
        "emotional_state": normalize_emotional_state(emotional_state),
        "mood": (getattr(state, "mood", "") or ""),
        "stress": getattr(state, "stress", None),
        "physical_state": _load_json(state.physical_state, "{}"),
        "attention": getattr(state, "attention", None),
        "current_focus_id": getattr(state, "current_focus_id", None),
        "active_goal": (getattr(state, "active_goal", "") or ""),
        "personal_goals": _load_json(state.personal_goals, "[]"),
        "updated_round_id": getattr(state, "updated_round_id", None),
    }


def build_your_state_block(state: Any) -> str:
    """Рендер блока ``YOUR STATE`` (§23, Sprint 3).

    Рендерится только при включённом ``character_state_enabled`` (решает
    контекст-билдер); эмоции показываются только выше порога. Пустой/отсутствующий
    state → пустой блок.
    """
    if state is None:
        return ""
    emotional_state = normalize_emotional_state(_load_json(state.emotional_state, "{}"))
    lines: list[str] = []

    emotions = [
        f"{name} ({value:.2f})"
        for name, value in sorted(
            emotional_state.items(), key=lambda kv: kv[1], reverse=True
        )
        if value >= RENDER_INTENSITY_THRESHOLD
    ]
    if emotions:
        lines.append("Эмоции: " + ", ".join(emotions))

    mood = (getattr(state, "mood", "") or "").strip()
    if mood:
        lines.append(f"Настроение: {mood}")

    stress = getattr(state, "stress", None)
    if stress is not None:
        lines.append(f"Стресс: {float(stress):.2f}")

    physical = _load_json(state.physical_state, "{}")
    if isinstance(physical, dict) and physical:
        physical_text = ", ".join(
            f"{key}: {value}" for key, value in physical.items() if value
        )
        if physical_text:
            lines.append(f"Физическое состояние: {physical_text}")

    attention = (getattr(state, "attention", "") or "").strip()
    if attention:
        lines.append(f"Фокус: {attention}")

    goal = (getattr(state, "active_goal", "") or "").strip()
    if goal:
        lines.append(f"Цель: {goal}")

    if not lines:
        return ""
    return "<your_state>\n" + "\n".join(lines) + "\n</your_state>"


def _load_json(raw: Any, fallback: Any) -> Any:
    if not raw:
        return fallback
    if isinstance(raw, dict) or isinstance(raw, list):
        return raw
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        return fallback


# ---------------------------------------------------------------------------
# Сбор входов раунда
# ---------------------------------------------------------------------------

async def collect_round_inputs(
    db: AsyncSession,
    chat_id: int,
    round_id: str,
) -> dict[str, list[dict[str, Any]]]:
    """Собрать входы emotion_engine за раунд: relationship deltas + world events.

    - relationship deltas: ``relationship_events`` раунда (только kind='llm'),
      с source/target персонажем и дельтами метрик;
    - world events: ``world_events`` раунда со структурной разметкой
      (emotional_salience/importance НЕ NULL — только extraction-события).
    """
    return {
        "relationship_deltas": await crud.get_relationship_events_for_round(
            db, round_id
        ),
        "world_events": await crud.get_world_events_for_round(db, round_id),
    }


# ---------------------------------------------------------------------------
# Обновление состояний за раунд
# ---------------------------------------------------------------------------

async def update_states_from_round(
    db: AsyncSession,
    chat_id: int,
    round_id: str,
    characters: list[Any],
    *,
    inputs: dict[str, list[dict[str, Any]]] | None = None,
    client: Any = None,
) -> dict[str, Any]:
    """Детерминированно обновить состояния всех персонажей за раунд.

    Для каждого персонажа: relationship deltas, где он источник (его чувства
    изменились) + события раунда, где он участник (эмоциональная салиенсность).
    Sensors-предложение применяется только в caps (emotion_engine), Sensors не
    задаёт mood. Возвращает отчёт {states, updated, sensors_used}.
    """
    report = {"states": 0, "updated": 0, "sensors_used": 0}
    if not characters:
        return report

    if inputs is None:
        inputs = await collect_round_inputs(db, chat_id, round_id)

    deltas_by_char: dict[int, list[dict[str, Any]]] = {}
    for delta in inputs.get("relationship_deltas", []):
        source_id = delta.get("source_character_id")
        if source_id is None:
            continue
        deltas_by_char.setdefault(source_id, []).append(delta)

    events_by_char: dict[int, list[dict[str, Any]]] = {}
    for event in inputs.get("world_events", []):
        ids = []
        if event.get("character_id") is not None:
            ids.append(event["character_id"])
        ids.extend(event.get("target_character_ids") or [])
        for cid in ids:
            if cid is not None:
                events_by_char.setdefault(cid, []).append(event)

    # Sensors-предложение эмоции применяется только если задача "emotion"
    # активна (SensorsService проверяет SENSORS_MODEL + мастер + per-task флаги).
    # Доступ к settings.sensors_* остаётся внутри Sensors-слоя (R15).
    from .sensors_service import sensors_service

    sensors_enabled = sensors_service.is_enabled("emotion")

    for character in characters:
        state = await crud.get_or_create_character_state(
            db, chat_id, character.id, round_id=round_id
        )
        report["states"] += 1

        deltas = deltas_by_char.get(character.id, [])
        events = events_by_char.get(character.id, [])

        sensors_proposal: dict[str, Any] | None = None
        if sensors_enabled and client is not None:
            sensors_proposal = await _sensors_emotion_proposal(
                client, character, state, events, deltas
            )
            if sensors_proposal:
                report["sensors_used"] += 1

        update = compute_state_update(
            emotional_state=state.emotional_state,
            stress=state.stress,
            mood=state.mood,
            relationship_deltas=deltas,
            round_events=events,
            sensors_proposal=sensors_proposal,
            emotion_round_cap=settings.emotion_round_cap,
            stress_round_cap=settings.stress_round_cap,
            sensors_intensity_cap=settings.sensors_emotion_intensity_cap,
        )

        if _state_changed(state, update):
            await crud.update_character_state(
                db,
                character.id,
                emotional_state=update["emotional_state"],
                mood=update["mood"],
                stress=update["stress"],
                updated_round_id=round_id,
            )
            report["updated"] += 1
        else:
            logger.debug(
                "[chat_id=%d] character %s state unchanged in round %s",
                chat_id, character.id, round_id,
            )
    return report


def _state_changed(state: Any, update: dict[str, Any]) -> bool:
    current = normalize_emotional_state(_load_json(state.emotional_state, "{}"))
    new = update["emotional_state"]
    if current != new:
        return True
    if (state.mood or "") != (update["mood"] or ""):
        return True
    current_stress = state.stress
    new_stress = update["stress"]
    if current_stress is None or new_stress is None:
        return current_stress != new_stress
    return abs(float(current_stress) - float(new_stress)) >= 1e-6


# ---------------------------------------------------------------------------
# Sensors emotion hook (§5.1.3)
# ---------------------------------------------------------------------------

async def _sensors_emotion_proposal(
    client: Any,
    character: Any,
    state: Any,
    events: list[dict[str, Any]],
    deltas: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Предложение Sensors ``{emotion, intensity, confidence, mood_delta}``.

    Только предложение: emotion_engine применит его в рамках caps; Sensors НЕ
    задаёт настроение напрямую (mood всегда выводит движок). При любой ошибке /
    недоступности / невалидном JSON — None (§5.1.8), детерминированный путь.
    """
    try:
        from .sensors_service import sensors_service

        minimal_context = _round_context_text(character, events, deltas)
        current_state = _current_state_text(state)
        return await sensors_service.run(
            client,
            task="emotion",
            minimal_context=minimal_context,
            current_state=current_state,
        )
    except Exception as exc:  # noqa: BLE001 — Sensors никогда не роняет раунд
        logger.warning(
            "Sensors emotion proposal failed for character %s: %s",
            getattr(character, "id", "?"), exc,
        )
        return None


def _round_context_text(
    character: Any, events: list[dict[str, Any]], deltas: list[dict[str, Any]]
) -> str:
    parts: list[str] = []
    for event in events:
        event_type = event.get("event_type", "событие")
        action = event.get("action") or {}
        if isinstance(action, str):
            try:
                action = json.loads(action)
            except (json.JSONDecodeError, TypeError):
                action = {}
        description = (
            f"{action.get('action', '')} {action.get('object', '')}".strip()
            if isinstance(action, dict) and action.get("action")
            else ""
        )
        parts.append(f"{event_type}: {description or 'значимое событие'}")
    if deltas:
        parts.append(f"изменения отношений: {len(deltas)}")
    return "\n".join(parts) or "событий раунда не зафиксировано"


def _current_state_text(state: Any) -> str:
    emotional_state = normalize_emotional_state(_load_json(state.emotional_state, "{}"))
    parts = []
    if emotional_state:
        parts.append("Эмоции: " + ", ".join(
            f"{name}={value:.2f}" for name, value in sorted(emotional_state.items())
        ))
    if getattr(state, "mood", ""):
        parts.append(f"Настроение: {state.mood}")
    if getattr(state, "stress", None) is not None:
        parts.append(f"Стресс: {state.stress:.2f}")
    return "; ".join(parts) or "нейтральное состояние"

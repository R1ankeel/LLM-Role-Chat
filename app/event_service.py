"""Round event extraction service (Plans/update20.md §15, Sprint 1).

Пост-раундная стадия ``event extraction``: LLM (или Sensors-hook §5.1.3)
предлагает структурированные события раунда (action, importance,
story/emotional_salience) и причинно-следственные links. Движок записывает их
в ``world_events`` / ``event_links`` через ``crud.save_round_events``.

Ключевой принцип §15: extraction — НЕ источник истины и НЕ обязательный путь.
При отключённом ``EVENT_EXTRACTION_ENABLED`` стадия ничего не делает; при любой
ошибке возвращается пустой результат, и раунд не ломается.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from . import crud, ollama_client, schemas
from .config import settings

logger = logging.getLogger(__name__)


def _format_round_history(messages: list[Any]) -> str:
    """Сжать сообщения раунда в текст для extraction (роли: Игрок/Имя/Система)."""
    lines: list[str] = []
    for m in messages:
        role = (getattr(m, "role", "") or "").strip().lower()
        content = getattr(m, "content", "") or ""
        if role == "user":
            lines.append(f"Игрок: {content}")
        elif role == "character":
            name = ""
            if getattr(m, "character", None) is not None:
                name = getattr(m.character, "name", "") or ""
            if not name:
                name = f"Персонаж {getattr(m, 'character_id', '')}"
            lines.append(f"{name}: {content}")
        elif role == "system":
            lines.append(f"Система: {content}")
    return "\n".join(lines)


def _coerce_event(raw: dict) -> schemas.ExtractedEvent | None:
    """Прогнать одно событие через схему; None при невалидности (не губит стадию)."""
    try:
        return schemas.ExtractedEvent.model_validate(raw)
    except Exception as exc:  # noqa: BLE001 — одно плохое событие не роняет стадию
        logger.warning("Sprint1: некорректное событие extraction отброшено: %s", exc)
        return None


def _sensors_proposal_to_event(sensors_result: dict[str, Any]) -> schemas.ExtractedEvent:
    """Sensors event-classification (§5.1.3) → ExtractedEvent (движковые правила).

    Sensors предлагает {event_type, source_character, targets, importance,
    audibility, visibility, requires_processing}. Салиенсы движок задаёт
    детерминированно (в Sprint 1 — нейтральные 0.5), audibility/visibility
    используются в §6 (Sprint 2). Sensors никогда не пишет в БД.
    """
    return schemas.ExtractedEvent(
        event_type=str(sensors_result.get("event_type") or "event"),
        description="",
        source_character=str(sensors_result.get("source_character") or ""),
        targets=list(sensors_result.get("targets") or []),
        location="",
        action=schemas.EventAction(),
        importance=float(sensors_result.get("importance") or 5.0),
        story_salience=0.5,
        emotional_salience=0.5,
        causes=[],
    )


async def extract_round_events(
    client: Any,
    db,
    chat_id: int,
    round_messages: list[Any],
    *,
    round_id: str | None = None,
    character_names: dict[int, str] | None = None,
    model_name: str | None = None,
) -> schemas.EventExtractionResult:
    """Post-round event extraction stage (§15).

    Возвращает извлечённые (но ещё НЕ записанные) события. При отключённом
    флаге, пустой истории или недоступности LLM — пустой результат.
    Sensors-hook (если включён) предлагает classification; при его успехе
    движок использует его как источник событий, иначе — обычный LLM-вызов.
    """
    result = schemas.EventExtractionResult()
    if not settings.event_extraction_enabled:
        return result
    if not round_messages:
        return result
    history = _format_round_history(round_messages)
    if not history.strip():
        return result

    if character_names is None:
        characters = await crud.get_characters_by_chat(
            db, chat_id, include_player=True
        )
        character_names = {c.id: c.name for c in characters}

    locations_json = "[]"
    try:
        locations = await crud.get_chat_locations(db, chat_id)
        locations_json = json.dumps(
            [loc.name for loc in locations], ensure_ascii=False
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("Sprint1: не удалось загрузить локации для extraction: %s", exc)

    # Sensors hook (§5.1.3): Sensors предлагает event classification, движок
    # применяет свои правила (салиенс/запись). Предложение НЕ пишется напрямую.
    sensors_used = False
    try:
        from .sensors_service import sensors_service

        sensors_result = await sensors_service.run(
            client,
            task="event",
            minimal_context=history,
        )
        if sensors_result is not None:
            event = _sensors_proposal_to_event(sensors_result)
            if event.importance >= float(settings.event_min_importance or 0.0):
                result.events.append(event)
            sensors_used = True
    except Exception as exc:  # noqa: BLE001
        logger.warning("Sprint1: Sensors event hook failed: %s", exc)
    result.sensors_used = sensors_used

    # Sensors дал событие — дополнительный LLM-вызов не нужен (экономия).
    if result.events:
        return result

    eff_model = (model_name or "").strip() or (
        settings.event_extraction_model or settings.default_model
    )
    raw_events = await ollama_client.extract_round_events(
        client=client,
        model_name=eff_model,
        round_history_text=history,
        character_names=character_names,
        locations=locations_json,
    )
    if not raw_events:
        return result
    for raw in raw_events:
        event = _coerce_event(raw)
        if event is not None:
            result.events.append(event)
    return result

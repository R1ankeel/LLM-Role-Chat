"""Память: witness-слой — наблюдаемый контекст персонажа (Sprint 6C).

Sprint 6C (§4.5 decomposition.md): перенос из ``memory_service.py``
`get_observable_context_for_character` и helpers. Направление:
memory/ → witness_model (без обратных импортов).
"""

from types import SimpleNamespace

import structlog

from .. import schemas
from .. import witness_model

logger = structlog.get_logger(__name__)


_CHARACTER_CARD_FIELDS = (
    "name",
    "personality",
    "traits",
    "speech_style",
    "example_messages",
    "boundaries",
    "background",
    "relationships",
    "location",
)


def _get_attr(obj, key: str):
    return obj[key] if isinstance(obj, dict) else getattr(obj, key)


def _character_from_snapshot(snapshot: dict) -> SimpleNamespace:
    return SimpleNamespace(
        **{field: snapshot.get(field, "") for field in _CHARACTER_CARD_FIELDS}
    )


def _format_messages_as_text(
    messages: list,
    character_names: dict[int, str] | None = None,
) -> str:
    lines = []
    for message in messages:
        role = _get_attr(message, "role")
        content = _get_attr(message, "content")
        if role == "user":
            lines.append(f"Игрок: {content}")
        elif role == "character":
            character_id = _get_attr(message, "character_id")
            if character_names and character_id:
                name = character_names.get(character_id, "Персонаж")
            elif not isinstance(message, dict) and getattr(message, "character", None):
                name = message.character.name
            else:
                name = "Персонаж"
            lines.append(f"{name}: {content}")
        elif role == "system":
            lines.append(f"Система: {content}")
    return "\n".join(lines)


def _format_round_as_text(
    messages: list,
    character_names: dict[int, str] | None = None,
) -> str:
    return _format_messages_as_text(messages, character_names)


def _witness_filtered_text(
    messages: list,
    viewer_character_id: int,
    character_names: dict[int, str],
    presence_map: dict[int, str] | None = None,
    *,
    same_round_ids: set[int] | None = None,
    viewer_location: str | None = None,
    character_locations: dict[int, str] | None = None,
) -> str:
    """RP-style witness filter (includes mentioned snippets). Prefer memory filter for extraction."""
    return witness_model.filter_history_for_character(
        messages,
        viewer_character_id,
        character_names,
        presence_map,
        same_round_ids=same_round_ids,
        max_len=len(messages) or 1,
        viewer_location=viewer_location,
        character_locations=character_locations,
    )


def get_observable_context_for_character(
    messages: list,
    viewer_character_id: int,
    character_names: dict[int, str],
    presence_map: dict[int, str] | None = None,
    *,
    same_round_ids: set[int] | None = None,
    viewer_location: str | None = None,
    character_locations: dict[int, str] | None = None,
    attention_map: dict[int, float] | None = None,
) -> witness_model.ObservableContext:
    """Perception-filtered context safe for memory extraction (present/told only).

    Sprint 4 (§11): ``attention_map`` — attention score пары (персонаж,
    сообщения) из ``crud.get_attention_map``; события с attention < LOW в память
    не идут даже при present/told (воспринято ≠ вошло в сознание).
    """
    return witness_model.filter_history_for_memory_extraction(
        messages,
        viewer_character_id,
        character_names,
        presence_map,
        same_round_ids=same_round_ids,
        max_len=len(messages) or 1,
        viewer_location=viewer_location,
        character_locations=character_locations,
        attention_map=attention_map,
    )


def _log_memory_perception(
    *,
    chat_id: int,
    character_name: str,
    character_id: int,
    context: witness_model.ObservableContext,
) -> None:
    for line in context.lines:
        logger.debug(
            "[Memory] character=%s id=%s event=%s location=%r perceived=true "
            "presence=%s memory_candidate=eligible preview=%r",
            character_name,
            character_id,
            line.message_id,
            line.location,
            line.presence,
            line.content_preview,
        )
    for item in context.skipped:
        logger.debug(
            "[Memory] character=%s id=%s event=%s location=%r perceived=false "
            "presence=%s memory_candidate=skipped reason=%s preview=%r",
            character_name,
            character_id,
            item.get("message_id"),
            item.get("location"),
            item.get("presence"),
            item.get("reason"),
            item.get("preview"),
        )
    if not context.has_observable_events:
        logger.debug(
            "[Memory] character=%s id=%s chat_id=%s memory_candidate=skipped "
            "reason=no_observable_events",
            character_name,
            character_id,
            chat_id,
        )


def _sensors_proposal_to_facts(sensors_result: dict) -> list[schemas.ExtractedFact]:
    """Sensors memory-candidates (§5.1.3) → ExtractedFact (движок валидирует).

    Sensors предлагает ``{facts: [{text, importance}]}``; категория по умолчанию
    «событие» (fallback-классификатор уточнит тип). Sensors память НЕ пишет.
    """
    facts: list[schemas.ExtractedFact] = []
    for item in sensors_result.get("facts") or []:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        importance = item.get("importance")
        try:
            imp = float(importance) if importance is not None else 0.5
        except (TypeError, ValueError):
            imp = 0.5
        facts.append(
            schemas.ExtractedFact(
                fact=text,
                category="событие",
                importance=max(0.0, min(1.0, imp)),
                witnessed=True,
            )
        )
    return facts

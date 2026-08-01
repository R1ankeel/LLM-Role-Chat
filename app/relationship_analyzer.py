"""LLM-powered analyzer that proposes relationship deltas from roleplay rounds."""

import json
import logging
import re

import httpx

from .config import settings
from .ollama_client import _invoke_llm, _extract_json_payload
from .schemas import RelationshipDelta

logger = logging.getLogger(__name__)

ANALYSIS_TEMP = 0.3
MAX_DELTA = settings.relationship_max_delta


def _build_analyzer_prompt(
    source_name: str,
    target_name: str,
    source_character_id: int,
    target_character_id: int,
    current_type: str,
    affection: int,
    trust: int,
    attraction: int,
    resentment: int,
    jealousy: int,
    recent_events_text: str,
    round_text: str,
    interaction_summary: str = "",
    direct_interaction: bool = False,
    observed_target: bool = False,
) -> str:
    valid_types = ", ".join(settings.relationship_valid_types)
    transitions_text = _format_transitions_for_prompt()
    reflection_cap = settings.relationship_reflection_delta_cap

    if not direct_interaction and not observed_target:
        delta_hint = (
            f"{source_name} и {target_name} в этом раунде НЕ взаимодействовали, "
            f"и {source_name} не получал(а) никаких сведений о {target_name}. "
            "Поэтому ВСЕ дельты должны быть 0, relationship_type прежним, importance = 1."
        )
        delta_range = "0"
    elif not direct_interaction:
        delta_hint = (
            f"{source_name} лишь наблюдал(а) события, связанные с {target_name}, "
            f"без прямого взаимодействия. Разрешены только малые дельты "
            f"(|дельты| <= {reflection_cap}), relationship_type НЕ менять."
        )
        delta_range = f"{-reflection_cap}..{reflection_cap}"
    else:
        delta_hint = (
            f"{source_name} и {target_name} взаимодействовали напрямую в этом раунде. "
            "Оцени изменения, вызванные именно этим взаимодействием."
        )
        delta_range = "-20..20"

    return (
        f"Проанализируй, как меняются отношения {source_name} к {target_name} "
        f"после этого раунда.\n\n"
        f"ID персонажей:\n"
        f"  {source_name} -> {source_character_id}\n"
        f"  {target_name} -> {target_character_id}\n\n"
        f"Текущий тип отношений: {current_type}\n"
        f"Текущие метрики:\n"
        f"  привязанность={affection}, доверие={trust}\n"
        f"  влечение={attraction}, обида={resentment}\n"
        f"  ревность={jealousy}\n\n"
        f"Взаимодействие в этом раунде:\n{interaction_summary or 'нет данных'}\n\n"
        "ВАЖНО: Анализируй ТОЛЬКО отношения "
        f"{source_name} к {target_name}. События, адресованные другим "
        f"персонажам или происходящие без участия {source_name}, не меняют "
        f"отношения этой пары.\n"
        f"{delta_hint}\n\n"
        f"Допустимые типы отношений: {valid_types}\n"
        f"Разрешённые переходы:\n{transitions_text}\n"
        f"Недавние события:\n{recent_events_text}\n\n"
        f"Текст раунда (только строки, относящиеся к этой паре):\n{round_text}\n\n"
        "Верни ТОЛЬКО валидный JSON (без markdown и лишнего текста):\n"
        "{\n"
        '  "deltas": [\n'
        "    {\n"
        f'      "source_character_id": {source_character_id},\n'
        f'      "target_character_id": {target_character_id},\n'
        f'      "delta_affection": <int {delta_range}>,\n'
        f'      "delta_trust": <int {delta_range}>,\n'
        f'      "delta_attraction": <int {delta_range}>,\n'
        f'      "delta_resentment": <int {delta_range}>,\n'
        f'      "delta_jealousy": <int {delta_range}>,\n'
        '      "relationship_type": "<новый тип из допустимых>",\n'
        '      "description": "<краткое описание текущих отношений>",\n'
        '      "reason": "<причина изменений>",\n'
        '      "importance": <int 1..10>,\n'
        '      "update_description": <true|false>\n'
        "    }\n"
        "  ]\n"
        "}"
    )


def _format_transitions_for_prompt() -> str:
    lines = []
    for current, allowed in settings.relationship_transition_rules.items():
        lines.append(f"  {current} -> {', '.join(allowed)}")
    return "\n".join(lines)


def _parse_analysis_response(
    raw: str,
    *,
    source_character_id: int,
    target_character_id: int,
) -> list[RelationshipDelta]:
    payload = _extract_json_payload(raw)
    if payload is None:
        logger.warning("Failed to extract JSON from relationship analysis: %s", raw[:200])
        return []

    items: list = []
    if isinstance(payload, dict):
        deltas = payload.get("deltas") or payload.get("relationships") or []
        if isinstance(deltas, list):
            items = deltas
        elif any(k in payload for k in ("delta_affection", "source_character_id")):
            items = [payload]
    elif isinstance(payload, list):
        items = payload

    results: list[RelationshipDelta] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            # The model may echo character names or wrong ids in these fields;
            # the analyzer knows the exact source/target pair, so override them.
            d = RelationshipDelta.model_validate(
                {
                    **item,
                    "source_character_id": source_character_id,
                    "target_character_id": target_character_id,
                }
            )
            results.append(d)
        except Exception as exc:
            logger.warning("Invalid relationship delta item: %s — %s", item, exc)
    return results


async def analyze_relationships(
    client: httpx.AsyncClient,
    model_name: str,
    source_name: str,
    target_name: str,
    current_type: str,
    affection: int,
    trust: int,
    attraction: int,
    resentment: int,
    jealousy: int,
    recent_events_text: str,
    round_text: str,
    source_character_id: int,
    target_character_id: int,
    interaction_summary: str = "",
    direct_interaction: bool = False,
    observed_target: bool = False,
) -> list[RelationshipDelta]:
    analyzer_model = settings.relationship_analyzer_model or model_name

    prompt = _build_analyzer_prompt(
        source_name=source_name,
        target_name=target_name,
        source_character_id=source_character_id,
        target_character_id=target_character_id,
        current_type=current_type,
        affection=affection,
        trust=trust,
        attraction=attraction,
        resentment=resentment,
        jealousy=jealousy,
        recent_events_text=recent_events_text,
        round_text=round_text,
        interaction_summary=interaction_summary,
        direct_interaction=direct_interaction,
        observed_target=observed_target,
    )

    messages = [
        {"role": "system", "content": "Ты — анализатор отношений в ролевой игре. Верни ТОЛЬКО валидный JSON."},
        {"role": "user", "content": prompt},
    ]

    try:
        raw = await _invoke_llm(
            client, analyzer_model, messages, temperature=ANALYSIS_TEMP,
        )
    except RuntimeError:
        logger.warning(
            "Relationship analysis LLM call failed for %s -> %s",
            source_name, target_name,
        )
        return []

    return _parse_analysis_response(
        raw,
        source_character_id=source_character_id,
        target_character_id=target_character_id,
    )

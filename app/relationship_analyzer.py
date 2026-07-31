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
) -> str:
    valid_types = ", ".join(settings.relationship_valid_types)
    transitions_text = _format_transitions_for_prompt()

    return (
        f"Analyze how {source_name}'s relationship with {target_name} changes "
        f"after this round.\n\n"
        f"Character IDs:\n"
        f"  {source_name} -> {source_character_id}\n"
        f"  {target_name} -> {target_character_id}\n\n"
        f"Current relationship type: {current_type}\n"
        f"Current metrics:\n"
        f"  affection={affection}, trust={trust}\n"
        f"  attraction={attraction}, resentment={resentment}\n"
        f"  jealousy={jealousy}\n\n"
        f"Valid relationship types: {valid_types}\n"
        f"Allowed transitions:\n{transitions_text}\n"
        f"Recent events:\n{recent_events_text}\n\n"
        f"Round text:\n{round_text}\n\n"
        "Return ONLY valid JSON (no markdown, no extra text):\n"
        "{\n"
        '  "deltas": [\n'
        "    {\n"
        f'      "source_character_id": {source_character_id},\n'
        f'      "target_character_id": {target_character_id},\n'
        '      "delta_affection": <int -20..20>,\n'
        '      "delta_trust": <int -20..20>,\n'
        '      "delta_attraction": <int -20..20>,\n'
        '      "delta_resentment": <int -20..20>,\n'
        '      "delta_jealousy": <int -20..20>,\n'
        '      "relationship_type": "<new type>",\n'
        '      "description": "<short summary of current relationship>",\n'
        '      "reason": "<why this change>",\n'
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
    )

    messages = [
        {"role": "system", "content": "You are a relationship analyst for a roleplay game. Return ONLY valid JSON."},
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

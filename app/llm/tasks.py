"""LLM-задачи: извлечение памяти, суммаризация, scene-state, event extraction.

Перенесено 1:1 из ``app/ollama_client.py`` (диапазоны §4.3: 2335–2874 +
константы). Парсинг JSON-ответов (``_extract_json_payload``,
``parse_extracted_facts`` и др.) живёт здесь же; ``_invoke_llm``/``llm_request``
подтягиваются function-level, чтобы не создавать цикл
``tasks → generation → wpe → tasks``.
"""

from __future__ import annotations

import json
import logging
import re

import httpx

from ..config import settings
from .. import schemas
from ..prompt_builder import (
    build_event_extraction_system,
    build_event_extraction_user,
    build_extraction_system,
    build_extraction_user,
    build_scene_state_system,
    build_scene_state_user,
    build_summary_system,
    build_summary_user,
    format_character_descriptor,
)
from .prompting import ChatMessage

logger = logging.getLogger(__name__)

MEMORY_EXTRACTION_TEMP = 0.3
SUMMARY_TEMP = 0.3

# Scene State JSON Schema for Ollama structured output (P3)
SCENE_STATE_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "time_of_day": {"type": "string"},
        "character_locations": {
            "type": "object",
            "additionalProperties": {"type": "string"},
            "description": "Карта имён персонажей -> текущая локация. Укажи КАЖДОГО персонажа. Пустая строка, если неизвестно."
        }
    },
    "required": ["time_of_day", "character_locations"]
}

# Round event extraction JSON Schema (Sprint 1, Plans/update20.md §15)
EVENT_EXTRACTION_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "events": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "event_type": {"type": "string"},
                    "description": {"type": "string"},
                    "source_character": {"type": "string"},
                    "targets": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "location": {"type": "string"},
                    "action": {
                        "type": "object",
                        "properties": {
                            "actor": {"type": "string"},
                            "action": {"type": "string"},
                            "target": {"type": "string"},
                            "object": {"type": "string"},
                        },
                        "required": ["actor", "action", "target", "object"],
                    },
                    "importance": {"type": "number"},
                    "story_salience": {"type": "number"},
                    "emotional_salience": {"type": "number"},
                    "causes": {
                        "type": "array",
                        "items": {"type": "integer"},
                    },
                },
                "required": [
                    "event_type",
                    "description",
                    "source_character",
                    "targets",
                    "location",
                    "importance",
                    "story_salience",
                    "emotional_salience",
                ],
            }
        }
    },
    "required": ["events"]
}


def _extract_json_payload(raw: str) -> object | None:
    """Pull the first JSON value (array/object) from model output."""
    if not raw or not raw.strip():
        return None

    text = raw.strip()
    code_block_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if code_block_match:
        text = code_block_match.group(1).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    for pattern in (r"\[.*\]", r"\{.*\}"):
        match = re.search(pattern, text, re.DOTALL)
        if not match:
            continue
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            continue
    return None


def _coerce_extracted_fact(item: object) -> schemas.ExtractedFact | None:
    """Normalize one LLM item into ExtractedFact (structured or legacy string)."""
    if item is None:
        return None
    if isinstance(item, schemas.ExtractedFact):
        return item
    if isinstance(item, str):
        text = item.strip()
        if not text:
            return None
        try:
            return schemas.ExtractedFact(fact=text)
        except Exception:
            return None
    if isinstance(item, dict):
        payload = dict(item)
        if "fact" not in payload:
            for key in ("content", "text", "memory", "value"):
                if key in payload:
                    payload["fact"] = payload[key]
                    break
        try:
            return schemas.ExtractedFact.model_validate(payload)
        except Exception:
            return None
    return None


def parse_extracted_facts(raw: str) -> list[schemas.ExtractedFact]:
    """Parse extraction LLM output into structured facts.

    Supports:
    - [{"fact": "...", "category": "событие", "importance": 0.7, "witnessed": true}]
    - legacy ["fact string", ...]
    """
    payload = _extract_json_payload(raw)
    if payload is None:
        lines = re.findall(r'"([^"]+)"', raw or "")
        facts: list[schemas.ExtractedFact] = []
        for line in lines:
            fact = _coerce_extracted_fact(line)
            if fact is not None:
                facts.append(fact)
        return facts

    items: list = []
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        if "fact" in payload or "content" in payload:
            items = [payload]
        else:
            for key in ("facts", "memories", "items", "data"):
                if isinstance(payload.get(key), list):
                    items = payload[key]
                    break

    facts: list[schemas.ExtractedFact] = []
    for item in items:
        fact = _coerce_extracted_fact(item)
        if fact is not None and fact.fact:
            facts.append(fact)
    return facts


def _parse_json_array(raw: str) -> list[str]:
    """Legacy helper: return fact strings only."""
    return [f.fact for f in parse_extracted_facts(raw)]


def _parse_json_object(raw: str) -> dict[str, list[str]]:
    if not raw or not raw.strip():
        return {}

    text = raw.strip()
    code_block_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if code_block_match:
        text = code_block_match.group(1).strip()

    try:
        result = json.loads(text)
        if isinstance(result, dict):
            return {
                str(key): [str(item) for item in value if item]
                for key, value in result.items()
                if isinstance(value, list)
            }
        return {}
    except json.JSONDecodeError:
        pass

    object_match = re.search(r"\{.*\}", text, re.DOTALL)
    if object_match:
        try:
            result = json.loads(object_match.group(0))
            if isinstance(result, dict):
                return {
                    str(key): [str(item) for item in value if item]
                    for key, value in result.items()
                    if isinstance(value, list)
                }
        except json.JSONDecodeError:
            pass
    return {}


def build_extraction_messages(character, round_history_text: str) -> list[ChatMessage]:
    """System/user messages for per-character memory extraction using localized templates (P1)."""
    char_desc = format_character_descriptor(character)
    system = build_extraction_system(character.name)
    user = build_extraction_user(char_desc, round_history_text, character.name)
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def build_summary_messages(
    character,
    new_dialogue_text: str,
    existing_summary: str = "",
) -> list[ChatMessage]:
    """System/user messages for per-character session summary using localized templates (P1)."""
    char_desc = format_character_descriptor(character)
    system = build_summary_system(character.name, settings.summary_max_paragraphs)
    user = build_summary_user(
        char_desc, new_dialogue_text, existing_summary, character.name
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def build_unified_extraction_messages(
    characters: list,
    round_history_text: str,
) -> list[ChatMessage]:
    """System/user messages for legacy unified memory extraction."""
    character_lines = [
        f"- {format_character_descriptor(character)}" for character in characters
    ]
    system = (
        "Проанализируй диалог и извлеки 0-3 факта для каждого персонажа.\n"
        "Факт только если персонаж мог его узнать напрямую.\n"
        "Формат: JSON-объект."
    )
    user = (
        f"Персонажи:\n" + "\n".join(character_lines) + "\n\n"
        f"Диалог:\n{round_history_text}"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


async def extract_memories_for_character(
    client: httpx.AsyncClient,
    model_name: str,
    character,
    round_history_text: str,
) -> list[schemas.ExtractedFact]:
    """Per-character memory extraction returning structured facts (P1)."""
    from .generation import _invoke_llm

    messages = build_extraction_messages(character, round_history_text)

    try:
        raw = await _invoke_llm(
            client,
            model_name,
            messages,
            temperature=MEMORY_EXTRACTION_TEMP,
        )
    except RuntimeError:
        logger.warning("Per-character memory extraction failed for %s", character.name)
        return []

    facts = parse_extracted_facts(raw)
    return facts[:settings.memory_max_facts_per_round]


async def summarize_for_character(
    client: httpx.AsyncClient,
    model_name: str,
    character,
    new_dialogue_text: str,
    existing_summary: str = "",
) -> str:
    """Update per-character session summary from new dialogue (now localized)."""
    from .generation import _invoke_llm

    existing = (existing_summary or "").strip()
    messages = build_summary_messages(character, new_dialogue_text, existing)

    try:
        raw = await _invoke_llm(
            client,
            model_name,
            messages,
            temperature=SUMMARY_TEMP,
        )
    except RuntimeError:
        logger.warning("Summary generation failed for %s", character.name)
        return existing

    summary = raw.strip()
    if not summary:
        return existing
    return summary


async def extract_memories_unified(
    client: httpx.AsyncClient,
    model_name: str,
    characters: list,
    round_history_text: str,
) -> dict[str, list[str]]:
    """Legacy unified extraction. Prefer per-character version."""
    from .generation import _invoke_llm

    if not characters:
        return {}

    messages = build_unified_extraction_messages(characters, round_history_text)

    try:
        raw = await _invoke_llm(
            client,
            model_name,
            messages,
            temperature=MEMORY_EXTRACTION_TEMP,
        )
    except RuntimeError:
        return {}

    return _parse_json_object(raw)


def _build_scene_state_messages(current_state: dict, history: str, character_names: list[str], locations: str = "[]") -> list[ChatMessage]:
    """Build messages for scene state extraction using localized template."""
    char_names_str = "\n".join(f"- {name}" for name in character_names) if character_names else "(нет персонажей)"
    system = build_scene_state_system()
    user = build_scene_state_user(
        current_state=current_state,
        history=history,
        character_names=char_names_str,
        locations=locations,
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


async def extract_scene_state(
    client: httpx.AsyncClient,
    model_name: str,
    round_history_text: str,
    current_scene_state: schemas.SceneStateRead | None,
    character_names: dict[int, str],
    locations: str = "[]",
    num_ctx: int | None = None,
    num_predict: int | None = None,
) -> dict | None:
    """
    Extract updated scene state (location + time only) from round history using LLM with JSON Schema.

    Args:
        client: HTTP client for Ollama
        model_name: Model to use
        round_history_text: Text of the current round's dialogue
        current_scene_state: Current scene state (None if first round)
        character_names: Map of character_id -> name for reference
        locations: JSON array of allowed locations
        num_ctx: KV window (defaults to ``SENSORS_SCENE_STATE_NUM_CTX``)
        num_predict: max output tokens (defaults to ``SENSORS_SCENE_STATE_NUM_PREDICT``)

    Returns:
        Dict with time_of_day, character_locations or None on failure
    """
    from .transport import llm_request

    # По умолчанию (из .env) — только если вызывающий не передал явные значения.
    if not num_ctx:
        num_ctx = settings.sensors_scene_state_num_ctx
    if not num_predict:
        num_predict = settings.sensors_scene_state_num_predict

    # Build current state dict for prompt
    if current_scene_state:
        current_state = {
            "time_of_day": current_scene_state.time_of_day,
            "character_locations": current_scene_state.character_locations,
        }
    else:
        current_state = {
            "time_of_day": "",
            "character_locations": {},
        }

    char_names_list = list(character_names.values())
    messages = _build_scene_state_messages(current_state, round_history_text, char_names_list, locations=locations)

    # Все sensor-задачи строго в режиме instant (без think) — §5.1.
    scene_options: dict = {"temperature": 0.3}
    if num_ctx and num_ctx > 0:
        scene_options["num_ctx"] = num_ctx
    if num_predict and num_predict > 0:
        scene_options["num_predict"] = num_predict

    async with llm_request(model_name, "/api/chat"):
        try:
            # Try with JSON Schema first (Ollama native)
            if settings.use_chat_api:
                payload = {
                    "model": model_name,
                    "messages": messages,
                    "stream": False,
                    "options": scene_options,
                    "format": SCENE_STATE_JSON_SCHEMA,
                }
                response = await client.post("/api/chat", json=payload)
                response.raise_for_status()
                data = response.json()
                content = data.get("message", {}).get("content", "")
            else:
                prompt = "\n\n".join(msg["content"] for msg in messages if msg.get("content"))
                payload = {
                    "model": model_name,
                    "prompt": prompt,
                    "stream": False,
                    "options": scene_options,
                    "format": SCENE_STATE_JSON_SCHEMA,
                }
                response = await client.post("/api/generate", json=payload)
                response.raise_for_status()
                data = response.json()
                content = data.get("response", "")
        except (httpx.HTTPStatusError, httpx.RequestError, json.JSONDecodeError) as exc:
            logger.warning("Scene state extraction with JSON schema failed: %s", exc)
            # Fallback: prompted JSON without schema
            try:
                if settings.use_chat_api:
                    payload = {
                        "model": model_name,
                        "messages": messages,
                        "stream": False,
                        "options": scene_options,
                    }
                    response = await client.post("/api/chat", json=payload)
                    response.raise_for_status()
                    data = response.json()
                    content = data.get("message", {}).get("content", "")
                else:
                    prompt = "\n\n".join(msg["content"] for msg in messages if msg.get("content"))
                    payload = {
                        "model": model_name,
                        "prompt": prompt,
                        "stream": False,
                        "options": scene_options,
                    }
                    response = await client.post("/api/generate", json=payload)
                    response.raise_for_status()
                    data = response.json()
                    content = data.get("response", "")
            except Exception as fallback_exc:
                logger.warning("Scene state extraction fallback also failed: %s", fallback_exc)
                return None

    # Parse JSON response
    try:
        result = json.loads(content)
        # Validate structure
        if not isinstance(result, dict):
            return None
        char_locs_raw = result.get("character_locations", {})
        # Filter locations against allowed list
        allowed_locs: set[str] = set()
        try:
            loc_list = json.loads(locations) if locations and locations != "[]" else []
            if isinstance(loc_list, list):
                allowed_locs = {loc.strip().casefold() for loc in loc_list if loc.strip()}
        except (json.JSONDecodeError, TypeError):
            pass
        character_locations = {}
        if isinstance(char_locs_raw, dict):
            for k, v in char_locs_raw.items():
                if not v:
                    continue
                loc_str = str(v).strip()
                # Only allow locations from the allowed list (case-insensitive)
                if not allowed_locs or loc_str.casefold() in allowed_locs:
                    character_locations[str(k)] = loc_str
        return {
            "time_of_day": str(result.get("time_of_day", "")),
            "character_locations": character_locations,
        }
    except (json.JSONDecodeError, AttributeError) as exc:
        logger.warning("Failed to parse scene state JSON: %s", exc)
        return None


def _build_event_extraction_messages(
    history: str,
    character_names: list[str],
    locations: str = "[]",
) -> list[ChatMessage]:
    """Build messages for round event extraction (Sprint 1, §15)."""
    char_names_str = "\n".join(f"- {name}" for name in character_names) if character_names else "(нет персонажей)"
    try:
        loc_list = json.loads(locations) if locations and locations != "[]" else []
        loc_str = ", ".join(loc_list) if isinstance(loc_list, list) and loc_list else "(не указаны)"
    except (json.JSONDecodeError, TypeError):
        loc_str = "(не указаны)"
    system = build_event_extraction_system()
    user = build_event_extraction_user(
        history=history,
        character_names=char_names_str,
        locations=loc_str,
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


async def extract_round_events(
    client: httpx.AsyncClient,
    model_name: str,
    round_history_text: str,
    character_names: dict[int, str] | list[str],
    locations: str = "[]",
    num_ctx: int | None = None,
) -> list[dict] | None:
    """Extract structured events from a round's history via LLM (Sprint 1, §15).

    Returns a list of event dicts matching ``ExtractedEvent`` or ``None`` on
    failure (caller decides: skip the stage, never break the round). The
    extraction writes no rows — persistence happens in ``crud.save_round_events``.
    """
    from .transport import llm_request

    if isinstance(character_names, dict):
        char_list = list(character_names.values())
    else:
        char_list = list(character_names)

    messages = _build_event_extraction_messages(
        round_history_text, char_list, locations=locations
    )
    options: dict = {"temperature": 0.3}
    if num_ctx and num_ctx > 0:
        options["num_ctx"] = num_ctx

    def _payload(use_format: bool) -> dict:
        if settings.use_chat_api:
            payload: dict = {
                "model": model_name,
                "messages": messages,
                "stream": False,
                "options": options,
            }
            if use_format:
                payload["format"] = EVENT_EXTRACTION_JSON_SCHEMA
            return payload
        prompt = "\n\n".join(msg["content"] for msg in messages if msg.get("content"))
        payload = {
            "model": model_name,
            "prompt": prompt,
            "stream": False,
            "options": options,
        }
        if use_format:
            payload["format"] = EVENT_EXTRACTION_JSON_SCHEMA
        return payload

    def _read_content(data: dict) -> str:
        if settings.use_chat_api:
            return data.get("message", {}).get("content", "")
        return data.get("response", "")

    async with llm_request(model_name, "/api/chat"):
        try:
            payload = _payload(use_format=True)
            if settings.use_chat_api:
                response = await client.post("/api/chat", json=payload)
            else:
                response = await client.post("/api/generate", json=payload)
            response.raise_for_status()
            content = _read_content(response.json())
        except (httpx.HTTPStatusError, httpx.RequestError, json.JSONDecodeError) as exc:
            logger.warning("Round event extraction with JSON schema failed: %s", exc)
            try:
                payload = _payload(use_format=False)
                if settings.use_chat_api:
                    response = await client.post("/api/chat", json=payload)
                else:
                    response = await client.post("/api/generate", json=payload)
                response.raise_for_status()
                content = _read_content(response.json())
            except Exception as fallback_exc:
                logger.warning("Round event extraction fallback failed: %s", fallback_exc)
                return None

    try:
        result = json.loads(content)
    except (json.JSONDecodeError, TypeError) as exc:
        logger.warning("Failed to parse round event extraction JSON: %s", exc)
        return None

    raw_events = result.get("events", []) if isinstance(result, dict) else []
    if not isinstance(raw_events, list):
        return None
    return [e for e in raw_events if isinstance(e, dict)]

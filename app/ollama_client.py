"""Client for Ollama API (local LLM) with memory extraction and retry logic."""

import asyncio
import json
import logging
import random
import re
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx

from .config import settings
from .context_state import ctx_state
from .token_counter import get_token_counter
from .prompt_builder import (
    build_anti_mimicry_block,
    build_character_summary_block,
    build_consistency_feedback_block,
    build_extraction_system,
    build_extraction_user,
    build_intervention_block,
    build_isolated_block,
    build_memories_block,
    build_negative_prompting_block,
    build_personality_block,
    build_personality_consistency_block,
    build_recent_dialogue_block,
    build_reinforcement_block,
    build_scene_advancement_block,
    build_scene_block,
    build_scene_state_system,
    build_scene_state_user,
    build_summary_system,
    build_summary_user,
    build_system_prompt,
    build_take_actions_instruction,
    build_user_context_message,
    build_vocabulary_block,
    build_world_block,
    format_character_descriptor,
    merge_char_locations,
)
from .repetition_detector import (
    RepetitionAnalysis,
    analyze_response,
    build_repetition_feedback,
    build_repetition_feedback_block,
)
from .role_isolation import (
    build_generation_cue,
    build_generation_cue_for_chat,
    build_stop_sequences,
    find_foreign_speaker_marker,
    sanitize_and_validate_response,
)
from . import schemas
from . import action_resolution
from .witness_model import Presence, filter_history_for_character, filter_history_for_character_with_presence

logger = logging.getLogger(__name__)

OLLAMA_BASE_URL = settings.ollama_base_url
DEFAULT_TEMPERATURE = settings.default_temperature
MEMORY_EXTRACTION_TEMP = 0.3
SUMMARY_TEMP = 0.3

# ---------------------------------------------------------------------------
# WPE 3.0 Фаза 2 (Plans/WPE.md §8, Ул.4): tool-calling take_actions (shadow).
# Действия извлекаются из tool_calls/JSON-Schema, логируются, НЕ применяются.
# Кэш возможностей модели (один раз на имя модели, §12) + shadow-метрики для
# критерия выхода §10 (доля move_to/send_message/адресации, латентность).
# ---------------------------------------------------------------------------

# model_name -> "tools" | "format" | "text" (лучший поддерживаемый режим)
_MODEL_TOOL_MODE_CACHE: dict[str, str] = {}

WPE_TOOLS_STATS: dict[str, Any] = {
    "calls": 0,
    "by_mode": {},
    "schema_valid": 0,
    "with_move_to": 0,
    "with_send_message": 0,
    "with_addressing": 0,
    "latency_ms": [],
}


def _tool_mode_chain(model_name: str, preferred: str) -> list[str]:
    """Порядок попыток для модели: tools → format (§8, И14), с кэшем (§12).

    Фаза 8: deprecated text-only fallback удалён — при запросе
    структурированных действий (preferred tools/format) генерация больше не
    деградирует к тексту. ``preferred="text"`` остаётся только для обычного
    (нетools) пути генерации, где tools/format не запрашивались.
    """
    cached = _MODEL_TOOL_MODE_CACHE.get(model_name)
    if cached == "tools":
        return ["tools"]
    if cached == "format":
        return ["format"]
    return {
        "tools": ["tools", "format"],
        "format": ["format"],
        "text": ["text"],
    }.get(preferred, ["text"])


def _next_tool_mode(model_name: str, current: str, wants_format: bool) -> str:
    """Понизить режим tools→format и запомнить в кэш (после 400 от Ollama).

    Фаза 8: text-only fallback удалён (И14). Если структурированный режим
    недоступен и дальнейшего фоллбэка нет — выбрасывается RuntimeError:
    модель обязана поддерживать tools или format при включённом флаге tools.
    """
    if current == "tools":
        if not wants_format:
            raise RuntimeError(
                f"Модель {model_name} не поддерживает tools, а format не "
                "запрошен: структурированные действия обязательны (И14, Фаза 8)"
            )
        nxt = "format"
    elif current == "format":
        raise RuntimeError(
            f"Модель {model_name} не поддерживает format: структурированные "
            "действия обязательны (И14, Фаза 8)"
        )
    else:
        nxt = current
    _MODEL_TOOL_MODE_CACHE[model_name] = nxt
    return nxt


def _tools_unsupported_error(body: str) -> bool:
    lowered = body.lower()
    return "tool" in lowered and any(
        k in lowered for k in ("not support", "unsupported", "unknown field", "no tool")
    )


def _format_unsupported_error(body: str) -> bool:
    lowered = body.lower()
    if "format" in lowered and any(
        k in lowered for k in ("not support", "unsupported", "unknown field")
    ):
        return True
    return "failed to parse" in lowered or "unexpected json" in lowered


def wpe_tools_stats_snapshot() -> dict[str, Any]:
    """Снимок shadow-метрик WPE Фазы 2 для canary-измерений (§10, §12)."""
    lats = list(WPE_TOOLS_STATS["latency_ms"])
    return {
        "calls": WPE_TOOLS_STATS["calls"],
        "by_mode": dict(WPE_TOOLS_STATS["by_mode"]),
        "schema_valid": WPE_TOOLS_STATS["schema_valid"],
        "with_move_to": WPE_TOOLS_STATS["with_move_to"],
        "with_send_message": WPE_TOOLS_STATS["with_send_message"],
        "with_addressing": WPE_TOOLS_STATS["with_addressing"],
        "latency_ms": lats,
        "latency_avg_ms": sum(lats) / len(lats) if lats else 0.0,
        "latency_max_ms": max(lats) if lats else 0.0,
    }


def _record_shadow_turn(
    chat_id: int,
    character_name: str,
    mode: str,
    turn_output: schemas.TurnOutput | None,
    latency_ms: float,
) -> None:
    """Логирует и накапливает shadow-результат хода (Фаза 2). Действия не применяются."""
    stats = WPE_TOOLS_STATS
    stats["calls"] += 1
    stats["by_mode"][mode] = stats["by_mode"].get(mode, 0) + 1
    stats["latency_ms"].append(latency_ms)

    if turn_output is None:
        logger.warning(
            "[WPE-P2] shadow chat_id=%d character=%s mode=%s: схема-невалидно/нет "
            "tool_calls, действия не извлечены (латентность %.1f ms)",
            chat_id,
            character_name,
            mode,
            latency_ms,
        )
        return

    stats["schema_valid"] += 1
    actions = turn_output.actions
    targets = turn_output.reply_target_character_ids
    if any(a.type == "move_to" for a in actions):
        stats["with_move_to"] += 1
    if any(a.type == "send_message" for a in actions):
        stats["with_send_message"] += 1
    if targets:
        stats["with_addressing"] += 1

    logger.info(
        "[WPE-P2] shadow chat_id=%d character=%s mode=%s schema_valid=yes "
        "targets=%s actions=%s (латентность %.1f ms)",
        chat_id,
        character_name,
        mode,
        targets,
        [a.model_dump(exclude_none=True) for a in actions],
        latency_ms,
    )


def _parse_tool_calls(raw: list) -> schemas.TurnOutput | None:
    """Разобрать `message.tool_calls` (Ollama chat) в TurnOutput. И14: только нативно."""
    for call in raw or []:
        fn = call.get("function") if isinstance(call, dict) else None
        if not isinstance(fn, dict) or fn.get("name") != "take_actions":
            continue
        args = fn.get("arguments")
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                continue
        if isinstance(args, dict):
            try:
                return schemas.TurnOutput.model_validate(args)
            except Exception as exc:
                logger.warning("[WPE-P2] невалидные take_actions аргументы: %s (%s)", args, exc)
                return None
    return None


def _parse_turn_output_json(text: str) -> schemas.TurnOutput | None:
    """Разобрать JSON-ответ формат-пути (Ollama `format` / response_format) в TurnOutput."""
    payload = _extract_json_payload(text)
    if not isinstance(payload, dict):
        return None
    try:
        return schemas.TurnOutput.model_validate(payload)
    except Exception as exc:
        logger.warning("[WPE-P2] невалидный take_actions JSON: %s (%s)", payload, exc)
        return None

# Backward compatibility for tests and external patches - use properties to read from settings dynamically
class _ConfigProxy:
    @property
    def USE_CHAT_API(self):
        return settings.use_chat_api
    
    @property
    def ENABLE_THINKING(self):
        return settings.enable_thinking
    
    @property
    def ENABLE_WITNESS_FILTER(self):
        return settings.enable_witness_filter
    
    @property
    def ENABLE_POST_HISTORY_REINFORCEMENT(self):
        return settings.enable_post_history_reinforcement
    
    @property
    def FALLBACK_ON_ISOLATION_FAILURE(self):
        return settings.fallback_on_isolation_failure
    
    @property
    def MAX_ROLE_ISOLATION_RETRIES(self):
        return settings.max_role_isolation_retries
    
    @property
    def MAX_REPETITION_RETRIES(self):
        return settings.max_repetition_retries
    
    @property
    def REPETITION_DETECTION_ENABLED(self):
        return settings.repetition_detection_enabled
    
    @property
    def MIN_CHARACTER_RESPONSE_LENGTH(self):
        return settings.min_character_response_length
    
    @property
    def GENERATE_TIMEOUT(self):
        return settings.generate_timeout
    
    @property
    def ENABLE_ANTI_MIMICRY(self):
        return settings.enable_anti_mimicry
    
    @property
    def ENABLE_RELEVANT_MEMORY_SELECTION(self):
        return settings.enable_relevant_memory_selection
    
    @property
    def MEMORY_RELEVANCE_TOP_K(self):
        return settings.memory_relevance_top_k
    
    @property
    def MEMORY_MAX_FACTS_PER_ROUND(self):
        return settings.memory_max_facts_per_round
    
    @property
    def SUMMARY_MAX_PARAGRAPHS(self):
        return settings.summary_max_paragraphs
    
    @property
    def DEFAULT_EVENT_VISIBILITY(self):
        return settings.default_event_visibility

# Create module-level proxy for backward compatibility
_config = _ConfigProxy()
USE_CHAT_API = _config.USE_CHAT_API
ENABLE_THINKING = _config.ENABLE_THINKING
ENABLE_WITNESS_FILTER = _config.ENABLE_WITNESS_FILTER
ENABLE_POST_HISTORY_REINFORCEMENT = _config.ENABLE_POST_HISTORY_REINFORCEMENT
FALLBACK_ON_ISOLATION_FAILURE = _config.FALLBACK_ON_ISOLATION_FAILURE
MAX_ROLE_ISOLATION_RETRIES = _config.MAX_ROLE_ISOLATION_RETRIES
MAX_REPETITION_RETRIES = _config.MAX_REPETITION_RETRIES
REPETITION_DETECTION_ENABLED = _config.REPETITION_DETECTION_ENABLED
MIN_CHARACTER_RESPONSE_LENGTH = _config.MIN_CHARACTER_RESPONSE_LENGTH
GENERATE_TIMEOUT = _config.GENERATE_TIMEOUT
ENABLE_ANTI_MIMICRY = _config.ENABLE_ANTI_MIMICRY
ENABLE_RELEVANT_MEMORY_SELECTION = _config.ENABLE_RELEVANT_MEMORY_SELECTION
MEMORY_RELEVANCE_TOP_K = _config.MEMORY_RELEVANCE_TOP_K
MEMORY_MAX_FACTS_PER_ROUND = _config.MEMORY_MAX_FACTS_PER_ROUND
SUMMARY_MAX_PARAGRAPHS = _config.SUMMARY_MAX_PARAGRAPHS
DEFAULT_EVENT_VISIBILITY = _config.DEFAULT_EVENT_VISIBILITY

MAX_RETRIES = 3
RETRY_DELAY = 1.0

ChatMessage = dict[str, str]

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


def _resolve_thinking(enable_thinking: bool | None) -> bool:
    """Per-call override falls back to global ENABLE_THINKING."""
    if enable_thinking is None:
        return settings.enable_thinking
    return bool(enable_thinking)


def _character_temperature(character) -> float:
    temp = getattr(character, "temperature", None)
    if temp is not None:
        base = float(temp)
    else:
        base = settings.default_temperature

    # Character inertia: strong convictions → lower jitter (more consistent)
    # Volatile/emotional → higher jitter (more unpredictable)
    text = (
        (getattr(character, "personality", "") or " ") + " " +
        (getattr(character, "traits", "") or " ") + " " +
        (getattr(character, "boundaries", "") or " ") + " " +
        (getattr(character, "background", "") or " ")
    ).lower()

    conviction_keywords = [
        "убеждённ", "принципиальн", "твёрд", "стойк", "непреклонн",
        "консервативн", "решительн", "непоколебим", "жёстк", "строг",
        "верен", "предан", "целеустремлённ", "дисциплинирован",
    ]
    volatile_keywords = [
        "импульсивн", "эмоциональн", "переменчив", "капризн",
        "непредсказуем", "спонтанн", "ветрен", "изменчив",
        "хаотичн", "неуравновешен", "вспыльчив", "порывист",
    ]

    conviction_score = sum(1 for kw in conviction_keywords if kw in text)
    volatile_score = sum(1 for kw in volatile_keywords if kw in text)

    net = volatile_score - conviction_score
    if net > 0:
        jitter = random.uniform(0.0, 0.2)  # more unpredictable
    elif net < 0:
        jitter = random.uniform(-0.15, 0.05)  # more consistent
    else:
        jitter = random.uniform(-0.1, 0.1)  # neutral

    return round(max(0.1, min(1.5, base + jitter)), 3)


def _character_name(m, character_names: dict[int, str] | None = None) -> str:
    if character_names and m.character_id:
        return character_names.get(m.character_id, "Персонаж")
    if m.character:
        return m.character.name
    return "Персонаж"


def _format_history(
    messages: list,
    max_len: int,
    character_names: dict[int, str] | None = None,
) -> str:
    recent = messages[-max_len:] if len(messages) > max_len else messages
    lines = []
    for m in recent:
        if m.role == "user":
            lines.append(f"Игрок: {m.content}")
        elif m.role == "character":
            lines.append(f"{_character_name(m, character_names)}: {m.content}")
        elif m.role == "system":
            lines.append(f"Система: {m.content}")
    return "\n".join(lines)


def format_history_for_character(
    messages: list,
    max_len: int,
    current_character_name: str,
    character_names: dict[int, str] | None = None,
    *,
    viewer_character_id: int | None = None,
    presence_map: dict[int, Presence] | None = None,
    same_round_message_ids: set[int] | None = None,
    viewer_location: str | None = None,
    character_locations: dict[int, str] | None = None,
    max_replies_per_character: int = 0,
) -> str:
    if (
        settings.enable_witness_filter
        and viewer_character_id is not None
        and character_names is not None
    ):
        return filter_history_for_character(
            messages,
            viewer_character_id,
            character_names,
            presence_map,
            same_round_ids=same_round_message_ids,
            max_len=max_len,
            viewer_location=viewer_location,
            character_locations=character_locations,
            max_replies_per_character=max_replies_per_character,
        )

    history_text = _format_history(messages, max_len, character_names)
    if not history_text:
        return ""

    note = (
        f"\n\n[Важно для {current_character_name}: "
        "Ты видишь только то, что произошло в присутствии твоего персонажа "
        "или что тебе явно рассказали. Не предполагай знания о событиях, "
        "в которых ты не участвовал.]"
    )
    return history_text + note


def filter_history_for_character_messages(
    messages: list,
    viewer_character_id: int,
    character_names: dict[int, str],
    presence_map: dict[int, Presence] | None = None,
    *,
    same_round_ids: set[int] | None = None,
    viewer_location: str | None = None,
    character_locations: dict[int, str] | None = None,
    max_replies_per_character: int = 0,
    max_len: int | None = None,
) -> list:
    """Return a list of messages filtered by witness perception for the given character.

    Unlike filter_history_for_character which returns a text string, this returns
    the actual message objects that the character can perceive (present/told/mentioned).
    """
    if not settings.enable_witness_filter:
        return messages

    return filter_history_for_character_with_presence(
        messages,
        viewer_character_id,
        character_names,
        presence_map,
        same_round_ids=same_round_ids,
        viewer_location=viewer_location,
        character_locations=character_locations,
        max_replies_per_character=max_replies_per_character,
        max_len=max_len,
    )


def _messages_to_prompt(messages: list[ChatMessage]) -> str:
    return "\n\n".join(msg["content"] for msg in messages if msg.get("content"))


def _count_prompt_tokens(
    chat_messages: list[ChatMessage],
    full_prompt: str,
) -> int:
    """Count the actual tokens that will be sent to the model.

    Uses the token counter configured via ``TOKEN_COUNT_MODE``; falls back to a
    character-based estimate. chat-messages (chat API) include per-message
    framing overhead, plain prompts are counted directly.
    """
    counter = get_token_counter()
    if chat_messages:
        return counter.count_messages(chat_messages)
    return counter.count(full_prompt)


def _build_generate_payload(
    model_name: str,
    prompt: str,
    temperature: float,
    stop: list[str] | None,
    *,
    stream: bool,
    enable_thinking: bool | None = None,
    num_ctx: int | None = None,
    format_schema: dict | None = None,
) -> dict:
    options: dict = {"temperature": temperature}
    if stop:
        options["stop"] = stop
    if num_ctx and num_ctx > 0:
        options["num_ctx"] = num_ctx

    payload: dict = {
        "model": model_name,
        "prompt": prompt,
        "stream": stream,
        "options": options,
    }
    if _resolve_thinking(enable_thinking) and stream:
        payload["think"] = True
    if format_schema:
        payload["format"] = format_schema
    return payload


def _build_chat_payload(
    model_name: str,
    messages: list[ChatMessage],
    temperature: float,
    stop: list[str] | None,
    *,
    stream: bool,
    enable_thinking: bool | None = None,
    num_ctx: int | None = None,
    tools: list | None = None,
    format_schema: dict | None = None,
) -> dict:
    options: dict = {"temperature": temperature}
    if stop:
        options["stop"] = stop
    if num_ctx and num_ctx > 0:
        options["num_ctx"] = num_ctx

    payload: dict = {
        "model": model_name,
        "messages": messages,
        "stream": stream,
        "options": options,
    }
    if _resolve_thinking(enable_thinking) and stream:
        payload["think"] = True
    if tools:
        payload["tools"] = tools
    if format_schema:
        payload["format"] = format_schema
    return payload


async def _call_ollama(
    client: httpx.AsyncClient,
    model_name: str,
    prompt: str,
    temperature: float = DEFAULT_TEMPERATURE,
    stop: list[str] | None = None,
    num_ctx: int | None = None,
) -> str:
    payload = _build_generate_payload(
        model_name,
        prompt,
        temperature,
        stop,
        stream=False,
        enable_thinking=False,
        num_ctx=num_ctx,
    )

    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = await client.post("/api/generate", json=payload)
            response.raise_for_status()
            data = response.json()
            return data.get("response", "")
        except httpx.TimeoutException:
            last_error = RuntimeError(
                f"Ollama не отвечает (таймаут {settings.generate_timeout} сек)"
            )
            logger.warning("Ollama timeout (attempt %d/%d)", attempt, MAX_RETRIES)
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(f"Ollama вернула ошибку: {exc.response.text}")
        except httpx.RequestError as exc:
            last_error = RuntimeError(
                f"Ollama недоступна. Убедитесь, что сервер запущен на {settings.ollama_base_url}"
            )
            logger.warning(
                "Ollama connection error (attempt %d/%d): %s",
                attempt,
                MAX_RETRIES,
                exc,
            )

        if attempt < MAX_RETRIES:
            await asyncio.sleep(RETRY_DELAY)

    raise last_error or RuntimeError("Ollama недоступна после всех попыток")


async def _call_ollama_chat(
    client: httpx.AsyncClient,
    model_name: str,
    messages: list[ChatMessage],
    temperature: float = DEFAULT_TEMPERATURE,
    stop: list[str] | None = None,
    num_ctx: int | None = None,
) -> str:
    payload = _build_chat_payload(
        model_name,
        messages,
        temperature,
        stop,
        stream=False,
        enable_thinking=False,
        num_ctx=num_ctx,
    )

    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = await client.post("/api/chat", json=payload)
            response.raise_for_status()
            data = response.json()
            message = data.get("message") or {}
            return message.get("content", "")
        except httpx.TimeoutException:
            last_error = RuntimeError(
                f"Ollama не отвечает (таймаут {settings.generate_timeout} сек)"
            )
            logger.warning("Ollama chat timeout (attempt %d/%d)", attempt, MAX_RETRIES)
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(f"Ollama вернула ошибку: {exc.response.text}")
        except httpx.RequestError as exc:
            last_error = RuntimeError(
                f"Ollama недоступна. Убедитесь, что сервер запущен на {settings.ollama_base_url}"
            )
            logger.warning(
                "Ollama chat connection error (attempt %d/%d): %s",
                attempt,
                MAX_RETRIES,
                exc,
            )

        if attempt < MAX_RETRIES:
            await asyncio.sleep(RETRY_DELAY)

    raise last_error or RuntimeError("Ollama недоступна после всех попыток")


async def _read_ollama_error(response: httpx.Response) -> str:
    """Read the body of an error response (works for streaming responses too)."""
    try:
        await response.aread()
    except Exception:
        pass
    try:
        return response.text.strip()
    except Exception:
        return response.reason_phrase or ""


async def _stream_ollama_generate(
    client: httpx.AsyncClient,
    model_name: str,
    prompt: str,
    temperature: float = DEFAULT_TEMPERATURE,
    stop: list[str] | None = None,
    *,
    enable_thinking: bool | None = None,
    num_ctx: int | None = None,
    format_schema: dict | None = None,
) -> AsyncIterator[dict]:
    think_sent = _resolve_thinking(enable_thinking)
    last_error = None
    mode_chain = _tool_mode_chain(model_name, "format" if format_schema else "text")
    current_mode = mode_chain[0]
    for attempt in range(1, MAX_RETRIES + 1):
        full_response = ""
        full_thinking = ""
        payload = _build_generate_payload(
            model_name,
            prompt,
            temperature,
            stop,
            stream=True,
            enable_thinking=think_sent,
            num_ctx=num_ctx,
            format_schema=(format_schema if current_mode == "format" else None),
        )

        try:
            async with client.stream(
                "POST", "/api/generate", json=payload
            ) as response:
                if response.status_code >= 400:
                    body = await _read_ollama_error(response)
                    if think_sent and "does not support thinking" in body:
                        logger.warning(
                            "Ollama: модель не поддерживает thinking, повторяю без think (%s)",
                            model_name,
                        )
                        think_sent = False
                        continue
                    if current_mode == "format" and _format_unsupported_error(body):
                        nxt = _next_tool_mode(model_name, current_mode, wants_format=True)
                        logger.warning(
                            "Ollama: модель не поддерживает format, повторяю в режиме %s (%s)",
                            nxt,
                            model_name,
                        )
                        current_mode = nxt
                        continue
                    raise RuntimeError(
                        f"Ollama вернула ошибку {response.status_code}: {body}"
                    )
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        logger.warning("Ollama stream: unparseable JSON line")
                        continue

                    if chunk := data.get("thinking"):
                        full_thinking += chunk

                    if chunk := data.get("response"):
                        full_response += chunk
                        yield {"type": "token", "content": chunk}

            if full_thinking and not full_response:
                logger.warning(
                    "Ollama returned thinking (%d chars) but empty response",
                    len(full_thinking),
                )

            yield {
                "type": "complete",
                "text": full_response,
                "thinking_len": len(full_thinking),
                "tool_calls": [],
                "tool_mode": current_mode,
            }
            return

        except httpx.TimeoutException:
            last_error = RuntimeError(
                f"Ollama не отвечает (таймаут {settings.generate_timeout} сек)"
            )
            logger.warning("Ollama stream timeout (attempt %d/%d)", attempt, MAX_RETRIES)
        except httpx.RequestError as exc:
            last_error = RuntimeError(
                f"Ollama недоступна. Убедитесь, что сервер запущен на {settings.ollama_base_url}"
            )
            logger.warning(
                "Ollama stream connection error (attempt %d/%d): %s",
                attempt,
                MAX_RETRIES,
                exc,
            )

        if attempt < MAX_RETRIES:
            await asyncio.sleep(RETRY_DELAY)

    raise last_error or RuntimeError("Ollama недоступна после всех попыток")


async def _stream_ollama_chat(
    client: httpx.AsyncClient,
    model_name: str,
    messages: list[ChatMessage],
    temperature: float = DEFAULT_TEMPERATURE,
    stop: list[str] | None = None,
    *,
    enable_thinking: bool | None = None,
    num_ctx: int | None = None,
    tools: list | None = None,
    format_schema: dict | None = None,
) -> AsyncIterator[dict]:
    think_sent = _resolve_thinking(enable_thinking)
    last_error = None
    mode_chain = _tool_mode_chain(
        model_name, "tools" if tools else ("format" if format_schema else "text")
    )
    current_mode = mode_chain[0]
    tool_calls: list[dict[str, Any]] = []
    for attempt in range(1, MAX_RETRIES + 1):
        full_response = ""
        full_thinking = ""
        payload = _build_chat_payload(
            model_name,
            messages,
            temperature,
            stop,
            stream=True,
            enable_thinking=think_sent,
            num_ctx=num_ctx,
            tools=(tools if current_mode == "tools" else None),
            format_schema=(format_schema if current_mode == "format" else None),
        )

        try:
            async with client.stream(
                "POST", "/api/chat", json=payload
            ) as response:
                if response.status_code >= 400:
                    body = await _read_ollama_error(response)
                    if think_sent and "does not support thinking" in body:
                        logger.warning(
                            "Ollama: модель не поддерживает thinking, повторяю без think (%s)",
                            model_name,
                        )
                        think_sent = False
                        continue
                    if current_mode == "tools" and _tools_unsupported_error(body):
                        nxt = _next_tool_mode(
                            model_name, current_mode, wants_format=bool(format_schema)
                        )
                        logger.warning(
                            "Ollama: модель не поддерживает tools, повторяю в режиме %s (%s)",
                            nxt,
                            model_name,
                        )
                        current_mode = nxt
                        continue
                    if current_mode == "format" and _format_unsupported_error(body):
                        nxt = _next_tool_mode(model_name, current_mode, wants_format=True)
                        logger.warning(
                            "Ollama: модель не поддерживает format, повторяю в режиме %s (%s)",
                            nxt,
                            model_name,
                        )
                        current_mode = nxt
                        continue
                    raise RuntimeError(
                        f"Ollama вернула ошибку {response.status_code}: {body}"
                    )
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        logger.warning("Ollama chat stream: unparseable JSON line")
                        continue

                    message = data.get("message") or {}
                    if chunk := message.get("thinking"):
                        full_thinking += chunk
                    if chunk := message.get("content"):
                        full_response += chunk
                        yield {"type": "token", "content": chunk}
                    if message.get("tool_calls"):
                        tool_calls.extend(message["tool_calls"])

            if full_thinking and not full_response:
                logger.warning(
                    "Ollama chat returned thinking (%d chars) but empty content",
                    len(full_thinking),
                )

            yield {
                "type": "complete",
                "text": full_response,
                "thinking_len": len(full_thinking),
                "tool_calls": tool_calls,
                "tool_mode": current_mode,
            }
            return

        except httpx.TimeoutException:
            last_error = RuntimeError(
                f"Ollama не отвечает (таймаут {settings.generate_timeout} сек)"
            )
            logger.warning(
                "Ollama chat stream timeout (attempt %d/%d)", attempt, MAX_RETRIES
            )
        except httpx.RequestError as exc:
            last_error = RuntimeError(
                f"Ollama недоступна. Убедитесь, что сервер запущен на {settings.ollama_base_url}"
            )
            logger.warning(
                "Ollama chat stream connection error (attempt %d/%d): %s",
                attempt,
                MAX_RETRIES,
                exc,
            )

        if attempt < MAX_RETRIES:
            await asyncio.sleep(RETRY_DELAY)

    raise last_error or RuntimeError("Ollama недоступна после всех попыток")


async def _invoke_llm(
    client: httpx.AsyncClient,
    model_name: str,
    messages: list[ChatMessage],
    *,
    temperature: float = DEFAULT_TEMPERATURE,
    stop: list[str] | None = None,
) -> str:
    """Route a non-streaming LLM call to Chat or Generate API."""
    if settings.use_chat_api:
        return await _call_ollama_chat(
            client, model_name, messages, temperature=temperature, stop=stop
        )
    prompt = _messages_to_prompt(messages)
    return await _call_ollama(
        client, model_name, prompt, temperature=temperature, stop=stop
    )


def _build_generation_messages(
    system_prompt: str,
    summary_block: str,
    memories_block: str,
    dialogue_block: str,
    scene_block: str,
    reinforcement: str,
    generation_cue: str,
    *,
    repetition_feedback: str = "",
    consistency_feedback: str = "",
    anti_mimicry_block: str = "",
    personality_block: str = "",
    consistency_block: str = "",
    vocabulary_block: str = "",
    scene_advancement_block: str = "",
    isolated_block: str = "",
    behavior_drivers_block: str = "",
    open_issues_block: str = "",
    epistemic_mask_block: str = "",
    directive_block: str = "",
    recency_tail_block: str = "",
    your_state_block: str = "",
    what_you_know_block: str = "",
    story_block: str = "",
    active_goal_block: str = "",
    active_plan_block: str = "",
    crisis_block: str = "",
    perceive_block: str = "",
    relationship_block: str = "",
) -> list[ChatMessage]:
    """Build messages for /api/chat with localized blocks (P1 complete).

    Context Builder v2 (Sprint 13, §23): ``perceive_block`` (WHAT YOU
    PERCEIVE) и ``relationship_block`` (RELATIONSHIP) — отдельные user-блоки,
    ``scene_block`` в v2 несёт WORLD.
    """
    feedback_block = build_repetition_feedback_block(repetition_feedback)
    consistency_feedback_block = build_consistency_feedback_block(
        consistency_feedback
    )
    user_content = build_user_context_message(
        summary_block,
        memories_block,
        dialogue_block,
        anti_mimicry_block,
        vocabulary_block,
        scene_advancement_block,
        isolated_block,
        personality_block,
        consistency_block,
        reinforcement,
        feedback_block,
        consistency_feedback_block,
        scene_block,
        perceive_block,
        your_state_block,
        what_you_know_block,
        story_block,
        active_goal_block,
        active_plan_block,
        crisis_block,
        relationship_block,
        behavior_drivers_block,
        open_issues_block,
        epistemic_mask_block,
        directive_block,
        recency_tail_block,
        generation_cue,
        build_negative_prompting_block(),
    )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]


def _log_repetition(
    chat_id: int,
    character_name: str,
    analysis: RepetitionAnalysis,
    retry_number: int,
) -> None:
    logger.info(
        "[REPETITION] chat_id=%d character=%s score=%.2f progression=%.2f "
        "stagnation=%s actions=%s interaction=%s retry=%d reason=%s",
        chat_id,
        character_name,
        analysis.score,
        analysis.progression_score,
        analysis.stagnation,
        analysis.repeated_actions,
        analysis.interaction_pattern or "-",
        retry_number,
        analysis.reason,
    )


async def _generate_once(
    client: httpx.AsyncClient,
    *,
    chat_id: int,
    character,
    messages_history: list,
    general_prompt: str,
    memories: list,
    other_character_names: list[str],
    max_history_length: int,
    model_name: str,
    character_names: dict[int, str] | None,
    summary: str | None,
    viewer_character_id: int | None,
    presence_map: dict[int, Presence] | None,
    same_round_message_ids: set[int] | None,
    enable_thinking: bool | None,
    viewer_location: str | None,
    character_locations: dict[int, str] | None,
    stop: list[str],
    temperature: float,
    strict_isolation: bool,
    repetition_feedback: str,
    attempt_label: str,
    prior_replies: list[tuple[str, str]] | None = None,
    scene_state=None,
    present_character_names: list[str] | None = None,
    stagnation_rounds: int = 0,
    is_isolated: bool = False,
    locations: str = "[]",
    location_descriptions: dict[str, str] | None = None,
    relationships_block: str = "",
    behavior_drivers_block: str = "",
    open_issues_block: str = "",
    built_context: schemas.BuiltContext | None = None,
    proactive_boost: float = 0.0,
    epistemic_mask_block: str = "",
    directive: str | None = None,
    recency_tail_block: str = "",
    consistency_feedback: str = "",
    what_you_know_block: str = "",
    story_block: str = "",
    active_goal_block: str = "",
    active_plan_block: str = "",
    crisis_block: str = "",
) -> tuple[str, str, bool, int, list[str], schemas.TurnOutput | None]:
    """One LLM call + isolation sanitize.

    Returns (raw, sanitized, isolation_ok, thinking_len, tokens_list,
    shadow_turn_output) — последний элемент: структурированный `TurnOutput`
    из tool_calls/JSON-схемы (WPE Фаза 2), `None`, если инструменты выключены
    или ответ невалиден. Фаза 5 использует его для Action Resolution.
    """
    api_mode = "chat" if settings.use_chat_api else "generate"
    thinking = _resolve_thinking(enable_thinking)
    if settings.world_engine_recency_tail_enabled and not recency_tail_block:
        recency_tail_block = (
            built_context.recency_tail_text
            if built_context is not None
            else ""
        )
    if not settings.world_engine_recency_tail_enabled:
        recency_tail_block = ""
    # Context Builder v2 (Sprint 13, §23): при включённом флаге relationships
    # уходят из system-промпта в отдельный user-блок RELATIONSHIP; legacy
    # `<relationships>` в system остаётся только при off.
    v2 = bool(settings.context_v2_enabled)
    system_prompt = build_system_prompt(
        character, general_prompt, strict=strict_isolation,
        relationships_block="" if v2 else (relationships_block or ""),
        take_actions_instruction=(
            build_take_actions_instruction()
            if settings.world_engine_tools_enabled
            else ""
        ),
    )

    if built_context is not None:
        dialogue_text = built_context.dialogue_text
        history_text = ""
    else:
        history_text = format_history_for_character(
            messages_history,
            max_history_length,
            character.name,
            character_names,
            viewer_character_id=viewer_character_id or character.id,
            presence_map=presence_map,
            same_round_message_ids=same_round_message_ids,
            viewer_location=viewer_location
            if viewer_location is not None
            else getattr(character, "location", "") or "",
            character_locations=character_locations,
            max_replies_per_character=settings.max_replies_per_character,
        )
        dialogue_text = history_text
    summary_block = build_character_summary_block(
        (built_context.summary_text if built_context is not None else summary) or ""
    )
    # RELEVANT MEMORY (v2, §23): reranked memories в отдельном блоке; при off —
    # legacy `<character_memories>`.
    if v2 and built_context is not None:
        memories_block = built_context.relevant_memory_text
    else:
        memories_block = build_memories_block(
            built_context.memories if built_context is not None else memories
        )
    dialogue_block = build_recent_dialogue_block(dialogue_text)

    # Anti-mimicry block for sequential generation
    anti_mimicry_block = ""
    if settings.enable_anti_mimicry and prior_replies:
        anti_mimicry_block = build_anti_mimicry_block(character.name, prior_replies)

    # Personality reinforcement block — prevents role drift (Phase 3)
    personality_block = build_personality_block(character, scene_state)
    consistency_block = build_personality_consistency_block(character)

    reinforcement = ""
    if settings.enable_post_history_reinforcement:
        reinforcement = build_reinforcement_block(character.name)

    # Scene block with world tracking — per-character location (P3).
    # WORLD (v2, §23): заменяет legacy `<scene>`; legacy рендерится только off.
    if built_context is not None:
        scene_block = built_context.world_text if v2 else built_context.scene_text
    else:
        if v2:
            scene_block = build_world_block(
                general_prompt,
                scene_state,
                present_character_names,
                current_character_name=character.name,
                character_locations=merge_char_locations(
                    scene_state, character_locations, character_names
                ),
                locations=locations,
                location_descriptions=location_descriptions,
            )
        else:
            char_locs = merge_char_locations(scene_state, character_locations, character_names)
            scene_block = build_scene_block(
                general_prompt,
                scene_state,
                present_character_names,
                current_character_name=character.name,
                character_locations=char_locs,
                locations=locations,
                location_descriptions=location_descriptions,
            )

    # WHAT YOU PERCEIVE / RELATIONSHIP (v2, §23): user-блоки из built_context;
    # при off — пустые (legacy-пути не меняются).
    perceive_block = built_context.perceive_text if (v2 and built_context is not None) else ""
    relationship_user_block = (
        built_context.relationship_text
        if (v2 and built_context is not None)
        else ""
    )

    # YOUR STATE block (Sprint 3, §23) — runtime-состояние персонажа.
    # Рендер только когда context_builder получил state (флаг
    # character_state_enabled + включённый билдер); иначе блок пуст.
    your_state_block = built_context.state_text if built_context is not None else ""

    # WHAT YOU KNOW block (Sprint 5, §9) — beliefs персонажа. Рендер только при
    # beliefs_enabled; фолбэк на переданный параметр (non-context-путь).
    if not what_you_know_block and built_context is not None:
        what_you_know_block = built_context.what_you_know_text

    # STORY block (Sprint 8, §16) — сюжет чата. Рендер только при
    # story_enabled; фолбэк на переданный параметр (non-context-путь).
    if not story_block and built_context is not None:
        story_block = built_context.story_text

    # ACTIVE GOAL / ACTIVE PLAN (Sprint 10, §21/§22) — intent и план NPC.
    # Рендер только при включённых флагах (решает chat_engine); фолбэк на
    # переданные параметры (non-context-путь).
    if not active_goal_block and built_context is not None:
        active_goal_block = built_context.active_goal_text
    if not active_plan_block and built_context is not None:
        active_plan_block = built_context.active_plan_text

    # CRISIS block (Sprint 11, §19) — активные кризисные линии («давление в
    # контексте», data-only). Рендер только при crisis_engine_enabled; фолбэк
    # на переданный параметр (non-context-путь).
    if not crisis_block and built_context is not None:
        crisis_block = built_context.crisis_text

    # Vocabulary fingerprinting block — prevents style contamination (Phase 5)
    vocabulary_block = build_vocabulary_block(character, prior_replies)

    # Scene advancement block — breaks stagnation loops (Phase 6)
    # Weighted proactive boost (Sprint 1 п.7, docs/relations.md §7.4) raises the
    # *probability* of a proactive action when the character has salient open
    # issues; it never guarantees one. Default 0.0 keeps the old behavior.
    scene_advancement_block = ""
    if settings.scene_advancement_enabled:
        proactive_chance = min(
            settings.proactive_action_chance + max(0.0, float(proactive_boost)),
            1.0,
        )
        proactive_action = (
            stagnation_rounds == 0
            and random.random() < proactive_chance
        )
        scene_advancement_block = build_scene_advancement_block(
            stagnation_rounds,
            max_stagnation_rounds=settings.stagnation_max_rounds,
            proactive_action=proactive_action,
        )

    isolated_block = build_isolated_block() if is_isolated else ""
    directive_block = build_intervention_block(directive) if directive else ""

    tokens_collected = []

    if settings.use_chat_api:
        generation_cue = build_generation_cue_for_chat(character.name)
        chat_messages = _build_generation_messages(
            system_prompt,
            summary_block,
            memories_block,
            dialogue_block,
            scene_block,
            reinforcement,
            generation_cue,
            repetition_feedback=repetition_feedback,
            consistency_feedback=consistency_feedback,
            anti_mimicry_block=anti_mimicry_block,
            vocabulary_block=vocabulary_block,
            personality_block=personality_block,
            consistency_block=consistency_block,
            scene_advancement_block=scene_advancement_block,
            isolated_block=isolated_block,
            behavior_drivers_block=behavior_drivers_block,
            open_issues_block=open_issues_block,
            epistemic_mask_block=epistemic_mask_block,
            directive_block=directive_block,
            recency_tail_block=recency_tail_block,
            your_state_block=your_state_block,
            what_you_know_block=what_you_know_block,
            story_block=story_block,
            active_goal_block=active_goal_block,
            active_plan_block=active_plan_block,
            crisis_block=crisis_block,
            perceive_block=perceive_block,
            relationship_block=relationship_user_block,
        )
        prompt_len = sum(len(msg["content"]) for msg in chat_messages)
        full_prompt = _messages_to_prompt(chat_messages)
    else:
        generation_cue = build_generation_cue(character.name)
        context_parts = [system_prompt]
        if summary_block:
            context_parts.append(summary_block)
        if memories_block:
            context_parts.append(memories_block)
        if dialogue_block:
            context_parts.append(dialogue_block)
        if scene_block:
            context_parts.append(scene_block)
        if perceive_block:
            context_parts.append(perceive_block)
        if your_state_block:
            context_parts.append(your_state_block)
        if what_you_know_block:
            context_parts.append(what_you_know_block)
        if story_block:
            context_parts.append(story_block)
        if active_goal_block:
            context_parts.append(active_goal_block)
        if active_plan_block:
            context_parts.append(active_plan_block)
        if crisis_block:
            context_parts.append(crisis_block)
        if relationship_user_block:
            context_parts.append(relationship_user_block)
        if anti_mimicry_block:
            context_parts.append(anti_mimicry_block)
        if vocabulary_block:
            context_parts.append(vocabulary_block)
        if scene_advancement_block:
            context_parts.append(scene_advancement_block)
        if isolated_block:
            context_parts.append(isolated_block)
        if personality_block:
            context_parts.append(personality_block)
        if consistency_block:
            context_parts.append(consistency_block)
        if reinforcement:
            context_parts.append(reinforcement)
        feedback_block = build_repetition_feedback_block(repetition_feedback)
        if feedback_block:
            context_parts.append(feedback_block)
        consistency_feedback_block = build_consistency_feedback_block(
            consistency_feedback
        )
        if consistency_feedback_block:
            context_parts.append(consistency_feedback_block)
        if behavior_drivers_block:
            context_parts.append(behavior_drivers_block)
        if open_issues_block:
            context_parts.append(open_issues_block)
        if epistemic_mask_block:
            context_parts.append(epistemic_mask_block)
        if directive_block:
            context_parts.append(directive_block)
        if recency_tail_block:
            context_parts.append(recency_tail_block)
        context_parts.append(generation_cue)
        full_prompt = "\n\n".join(context_parts)
        prompt_len = len(full_prompt)
        chat_messages = []

    prompt_tokens = _count_prompt_tokens(chat_messages, full_prompt)
    num_ctx = ctx_state.apply_prompt(chat_id, prompt_tokens)

    logger.info(
        "[chat_id=%d] Ollama request (api=%s, model=%s, character=%s, %s, "
        "prompt_len=%d, prompt_tokens=%d, history=%d msgs, memories=%d, has_summary=%s, stop=%d, "
        "thinking=%s, has_rep_feedback=%s, num_ctx=%d, context=%s)",
        chat_id,
        api_mode,
        model_name,
        character.name,
        attempt_label,
        prompt_len,
        prompt_tokens,
        len(messages_history),
        len(memories),
        bool(summary_block),
        len(stop),
        thinking,
        bool(repetition_feedback),
        num_ctx,
        "builder" if built_context is not None else "legacy",
    )

    generated = ""
    thinking_len = 0
    tools_enabled = settings.world_engine_tools_enabled
    shadow_turn_output: schemas.TurnOutput | None = None
    shadow_tool_mode = "text"

    if tools_enabled:
        # WPE 3.0 Фаза 2: tool-calling take_actions (shadow). Токены стримятся
        # как раньше, tool_calls в терминальном сообщении не рендерятся (§8).
        shadow_tool_mode = "tools" if settings.use_chat_api else "format"
        _t0 = time.perf_counter()
        if settings.use_chat_api:
            tool_calls: list[dict[str, Any]] = []
            token_buffer = ""
            async for event in _stream_ollama_chat(
                client,
                model_name,
                chat_messages,
                temperature=temperature,
                stop=stop,
                enable_thinking=thinking,
                num_ctx=num_ctx,
                tools=[schemas.build_take_actions_tool()],
                format_schema=schemas.build_take_actions_json_schema(),
            ):
                if event.get("content"):
                    token_buffer += event["content"]
                    if len(token_buffer) >= 10:
                        tokens_collected.append(token_buffer)
                        token_buffer = ""
                elif event["type"] == "complete":
                    if token_buffer:
                        tokens_collected.append(token_buffer)
                        token_buffer = ""
                    generated = event["text"]
                    thinking_len = event.get("thinking_len", 0)
                    shadow_tool_mode = event.get("tool_mode", "tools")
                    tool_calls = event.get("tool_calls") or []
            shadow_turn_output = _parse_tool_calls(tool_calls)
        else:
            token_buffer = ""
            async for event in _stream_ollama_generate(
                client,
                model_name,
                full_prompt,
                temperature=temperature,
                stop=stop,
                enable_thinking=thinking,
                num_ctx=num_ctx,
                format_schema=schemas.build_take_actions_json_schema(),
            ):
                if event.get("content"):
                    token_buffer += event["content"]
                    if len(token_buffer) >= 10:
                        tokens_collected.append(token_buffer)
                        token_buffer = ""
                elif event["type"] == "complete":
                    if token_buffer:
                        tokens_collected.append(token_buffer)
                        token_buffer = ""
                    generated = event["text"]
                    thinking_len = event.get("thinking_len", 0)
                    shadow_tool_mode = event.get("tool_mode", "format")
            shadow_turn_output = _parse_turn_output_json(generated)
        _record_shadow_turn(
            chat_id,
            character.name,
            shadow_tool_mode,
            shadow_turn_output,
            (time.perf_counter() - _t0) * 1000.0,
        )
    elif settings.use_chat_api:
        if thinking:
            token_buffer = ""
            async for event in _stream_ollama_chat(
                client,
                model_name,
                chat_messages,
                temperature=temperature,
                stop=stop,
                enable_thinking=True,
                num_ctx=num_ctx,
            ):
                if event.get("content"):
                    token_buffer += event["content"]
                    if len(token_buffer) >= 10:
                        tokens_collected.append(token_buffer)
                        token_buffer = ""
                elif event["type"] == "complete":
                    if token_buffer:
                        tokens_collected.append(token_buffer)
                        token_buffer = ""
                    generated = event["text"]
                    thinking_len = event.get("thinking_len", 0)
        else:
            generated = await _call_ollama_chat(
                client,
                model_name,
                chat_messages,
                temperature=temperature,
                stop=stop,
                num_ctx=num_ctx,
            )
    elif thinking:
        token_buffer = ""
        async for event in _stream_ollama_generate(
            client,
            model_name,
            full_prompt,
            temperature=temperature,
            stop=stop,
            enable_thinking=True,
            num_ctx=num_ctx,
        ):
            if event.get("content"):
                token_buffer += event["content"]
                if len(token_buffer) >= 10:
                    tokens_collected.append(token_buffer)
                    token_buffer = ""
            elif event["type"] == "complete":
                if token_buffer:
                    tokens_collected.append(token_buffer)
                    token_buffer = ""
                generated = event["text"]
                thinking_len = event.get("thinking_len", 0)
    else:
        generated = await _call_ollama(
            client, model_name, full_prompt, temperature=temperature, stop=stop,
            num_ctx=num_ctx,
        )

    validation_result = sanitize_and_validate_response(
        generated,
        character.name,
        other_character_names,
        settings.min_character_response_length,
    )
    sanitized = validation_result.cleaned_text
    is_valid = validation_result.is_valid
    hard_violation = validation_result.hard_violation
    soft_violation = validation_result.soft_violation
    had_foreign_marker = (
        find_foreign_speaker_marker(generated, other_character_names) is not None
    )

    logger.info(
        "[chat_id=%d] Response (api=%s, char=%s, %s, raw=%d, sanitized=%d, "
        "thinking=%d, foreign_marker=%s, isolation_valid=%s, soft_violation=%s, hard_violation=%s)",
        chat_id,
        api_mode,
        character.name,
        attempt_label,
        len(generated),
        len(sanitized),
        thinking_len,
        had_foreign_marker,
        is_valid,
        soft_violation,
        hard_violation,
    )

    if had_foreign_marker:
        logger.warning(
            "[chat_id=%d] Foreign speaker marker for %s (%s)",
            chat_id,
            character.name,
            attempt_label,
        )

    if soft_violation:
        logger.debug(
            "[chat_id=%d] Soft perspective violation for %s (%s)",
            chat_id,
            character.name,
            attempt_label,
        )

    if built_context is not None and settings.context_debug:
        d = built_context.diagnostics
        logger.debug(
            "[chat_id=%d] Built context (%s): total=%d budget=%d oldest=%s "
            "newest=%s summary_through=%s recent=%d retrieved=%d excluded=%d "
            "memories=%d/%d dropped=%d",
            chat_id,
            character.name,
            built_context.total_tokens,
            built_context.budget.total_tokens,
            d.oldest_included_message_id,
            d.newest_included_message_id,
            d.summary_through_message_id,
            len(d.recent_message_ids),
            len(d.retrieved_message_ids),
            len(d.excluded_message_ids),
            d.memories_selected,
            d.memories_candidates,
            len(built_context.dropped_items),
        )

    return (
        generated,
        sanitized,
        is_valid,
        thinking_len,
        tokens_collected,
        num_ctx,
        shadow_turn_output,
    )


def _check_vocabulary_borrowing(
    text: str,
    character: Any,
    other_character_names: list[str],
    messages_history: list,
    character_names: dict[int, str] | None = None,
) -> str:
    """Check if the character's response borrows vocabulary from other characters.

    Returns a description of the character's own speech style if borrowing
    is detected, or empty string if clean.
    """
    if not text or not other_character_names:
        return ""

    own_style = (getattr(character, "speech_style", "") or "").strip()
    if not own_style:
        return ""

    # Extract distinctive words from character's own style
    own_words = set(re.findall(r'\b[а-яёa-z-]{4,}\b', own_style.lower()))

    # Collect words from other characters' recent replies
    foreign_words: set[str] = set()
    for msg in messages_history:
        if getattr(msg, "role", None) != "character":
            continue
        cid = getattr(msg, "character_id", None)
        if cid is None:
            continue
        name = (character_names or {}).get(int(cid), "")
        if not name or name == character.name or name not in other_character_names:
            continue
        content = getattr(msg, "content", "") or ""
        foreign_words.update(re.findall(r'\b[а-яёa-z-]{4,}\b', content.lower()))

    if not foreign_words:
        return ""

    # Check if the response uses foreign words not in own style
    response_words = set(re.findall(r'\b[а-яёa-z-]{4,}\b', text.lower()))
    borrowed = (response_words & foreign_words) - own_words
    common_stop = {
        "что", "это", "так", "вот", "все", "или", "как", "его", "она", "они",
        "только", "если", "нет", "да", "уже", "еще", "там", "тут", "когда",
        "даже", "меня", "тебя", "него", "нее", "них", "мой", "твой", "свой",
        "наш", "ваш", "эти", "этот", "было", "будет", "стал", "сказал",
        "такое", "сейчас", "здесь", "тогда", "потом", "вдруг", "опять",
    }
    borrowed -= common_stop

    # Require at least 3 borrowed words that aren't common
    if len(borrowed) < 3:
        return ""

    # Check proportion: if borrowed < 20% of response vocab, it's mild
    if response_words and len(borrowed) / len(response_words) < 0.2:
        return ""

    return own_style


async def generate(
    client: httpx.AsyncClient,
    chat_id: int,
    character,
    messages_history: list,
    general_prompt: str,
    memories: list,
    other_character_names: list[str],
    max_history_length: int = 30,
    model_name: str = "default",
    character_names: dict[int, str] | None = None,
    summary: str | None = None,
    viewer_character_id: int | None = None,
    presence_map: dict[int, Presence] | None = None,
    same_round_message_ids: set[int] | None = None,
    enable_thinking: bool | None = None,
    viewer_location: str | None = None,
    character_locations: dict[int, str] | None = None,
    prior_replies: list[tuple[str, str]] | None = None,
    scene_state: schemas.SceneStateRead | None = None,
    present_character_names: list[str] | None = None,
    stagnation_rounds: int = 0,
    is_isolated: bool = False,
    locations: str = "[]",
    location_descriptions: dict[str, str] | None = None,
    relationships_block: str = "",
    behavior_drivers_block: str = "",
    open_issues_block: str = "",
    built_context: schemas.BuiltContext | None = None,
    proactive_boost: float = 0.0,
    epistemic_mask_block: str = "",
    directive: str | None = None,
    recency_tail_block: str = "",
    what_you_know_block: str = "",
    story_block: str = "",
    active_goal_block: str = "",
    active_plan_block: str = "",
    crisis_block: str = "",
) -> AsyncIterator[dict]:
    """Send a request to Ollama and yield the sanitized response.

    Pipeline:
      generate → sanitize/role-isolation → repetition check → accept or targeted retry.

    Isolation retries and repetition retries are tracked separately.
    Streaming still completes fully before validation; only the final text is yielded.
    """
    stop = build_stop_sequences(other_character_names)
    temperature = _character_temperature(character)

    # Create witness-filtered history for vocabulary borrowing and repetition checks
    filtered_history = messages_history
    if (
        settings.enable_witness_filter
        and viewer_character_id is not None
        and character_names is not None
    ):
        filtered_history = filter_history_for_character_messages(
            messages_history,
            viewer_character_id,
            character_names,
            presence_map,
            same_round_ids=same_round_message_ids,
            viewer_location=viewer_location,
            character_locations=character_locations,
            max_replies_per_character=settings.max_replies_per_character,
            max_len=max_history_length,
        )

    isolation_attempt = 0
    repetition_attempt = 0
    repetition_feedback = ""
    strict_isolation = False

    # WPE Фаза 5: Action<->Text Consistency Validator (Ул.1, §5). Стоик
    # contradiction-ретраев ≤1 (в рамках общего бюджета вызовов); молчаливое
    # действие (minor_ambiguity) НЕ вызывает retry (И16).
    consistency_attempt = 0
    consistency_feedback = ""

    # Isolation-valid candidates ranked for best-of on exhaustion
    candidates: list[tuple[str, RepetitionAnalysis | None]] = []

    # Bound total LLM calls: isolation budget + repetition budget + small slack
    max_total_calls = settings.max_role_isolation_retries + settings.max_repetition_retries + 1

    for call_idx in range(1, max_total_calls + 1):
        isolation_attempt += 1
        label = (
            f"call={call_idx} isolation={isolation_attempt}/"
            f"{settings.max_role_isolation_retries} rep={repetition_attempt}/"
            f"{settings.max_repetition_retries}"
        )

        (
            _raw,
            sanitized,
            isolation_ok,
            _thinking_len,
            token_chunks,
            used_num_ctx,
            turn_output,
        ) = await _generate_once(
            client,
            chat_id=chat_id,
            character=character,
            messages_history=messages_history,
            general_prompt=general_prompt,
            memories=memories,
            other_character_names=other_character_names,
            max_history_length=max_history_length,
            model_name=model_name,
            character_names=character_names,
            summary=summary,
            viewer_character_id=viewer_character_id,
            presence_map=presence_map,
            same_round_message_ids=same_round_message_ids,
            enable_thinking=enable_thinking,
            viewer_location=viewer_location,
            character_locations=character_locations,
            stop=stop,
            temperature=temperature,
            strict_isolation=strict_isolation,
            repetition_feedback=repetition_feedback,
            attempt_label=label,
            prior_replies=prior_replies,
            scene_state=scene_state,
            present_character_names=present_character_names,
            stagnation_rounds=stagnation_rounds,
            is_isolated=is_isolated,
            locations=locations,
            location_descriptions=location_descriptions,
            relationships_block=relationships_block,
            behavior_drivers_block=behavior_drivers_block,
            open_issues_block=open_issues_block,
            built_context=built_context,
            proactive_boost=proactive_boost,
            epistemic_mask_block=epistemic_mask_block,
            directive=directive,
            recency_tail_block=recency_tail_block,
            consistency_feedback=consistency_feedback,
            what_you_know_block=what_you_know_block,
            story_block=story_block,
            active_goal_block=active_goal_block,
            active_plan_block=active_plan_block,
            crisis_block=crisis_block,
        )

        if not isolation_ok:
            if isolation_attempt < settings.max_role_isolation_retries:
                strict_isolation = True
                logger.warning(
                    "[chat_id=%d] Isolation failure for %s — retrying (%d/%d)",
                    chat_id,
                    character.name,
                    isolation_attempt,
                    settings.max_role_isolation_retries,
                )
                continue
            # Isolation budget exhausted → fallback path below
            break

        # --- isolation OK: vocabulary borrowing validation ---
        borrowing_issue = ""
        if settings.enable_vocabulary_control:
            borrowing_issue = _check_vocabulary_borrowing(
                sanitized, character, other_character_names, filtered_history,
                character_names,
            )
        if borrowing_issue:
            if repetition_attempt < settings.max_repetition_retries:
                repetition_attempt += 1
                repetition_feedback = (
                    "ОБНАРУЖЕНО ЗАИМСТВОВАНИЕ СТИЛЯ.\n\n"
                    f"Твой ответ содержит слова и выражения, не характерные для {character.name}. "
                    f"{character.name} говорит так: {borrowing_issue}\n\n"
                    "Перепиши ответ строго в стиле своего персонажа. "
                    "Не используй лексикон других персонажей."
                )
                logger.warning(
                    "[chat_id=%d] Borrowing detected for %s — retry (%d/%d)",
                    chat_id, character.name, repetition_attempt,
                    settings.max_repetition_retries,
                )
                continue

        # --- isolation OK: repetition validation ---
        analysis: RepetitionAnalysis | None = None
        if settings.repetition_detection_enabled:
            analysis = analyze_response(
                sanitized,
                character_id=int(character.id),
                messages=filtered_history,
                character_names=character_names,
            )
            if analysis.is_repetitive:
                _log_repetition(
                    chat_id, character.name, analysis, repetition_attempt + 1
                )
                candidates.append((sanitized, analysis))
                if repetition_attempt < settings.max_repetition_retries:
                    repetition_attempt += 1
                    repetition_feedback = build_repetition_feedback(analysis)
                    # Keep isolation non-strict for pure repetition retries
                    # unless we already needed strict mode.
                    logger.warning(
                        "[chat_id=%d] Repetition failure for %s — targeted retry "
                        "(%d/%d) score=%.2f",
                        chat_id,
                        character.name,
                        repetition_attempt,
                        settings.max_repetition_retries,
                        analysis.score,
                    )
                    continue
                # Retries exhausted: pick best candidate below
                break

        # --- WPE Фаза 5: Action<->Text Consistency Validator (Ул.1, §5) ---
        # Действия извлекаются только из tool_calls/JSON-схемы (И4), не из
        # текста. contradiction -> ретрай ≤1 с фидбеком; minor_ambiguity
        # (молчаливое действие) НЕ ретраится (И16).
        if turn_output is not None and turn_output.actions:
            verdict = action_resolution.classify_consistency(turn_output, sanitized)
            if (
                verdict == "contradiction"
                and consistency_attempt < settings.wpe_action_consistency_max_retries
            ):
                consistency_attempt += 1
                consistency_feedback = action_resolution.build_consistency_feedback(
                    turn_output, sanitized, character.name
                )
                logger.warning(
                    "[chat_id=%d] Action/Text contradiction for %s — retry "
                    "(%d/%d) actions=%s",
                    chat_id,
                    character.name,
                    consistency_attempt,
                    settings.wpe_action_consistency_max_retries,
                    action_resolution.describe_actions(turn_output),
                )
                continue
        else:
            verdict = "no_actions"

        # Clean accept - yield token events first, then response
        if token_chunks:
            for chunk in token_chunks:
                yield {"type": "token", "text": chunk, "character_id": character.id}
                await asyncio.sleep(0.01)  # small delay for streaming feel
        yield {
            "type": "response",
            "text": sanitized,
            "turn": turn_output,
            "verdict": verdict,
        }
        return

    # Best isolation-valid candidate after repetition exhaustion
    if candidates:
        def _rank(item: tuple[str, RepetitionAnalysis | None]) -> tuple:
            text, ana = item
            if ana is None:
                return (0.0, -1.0, -len(text))
            bonus = settings.scene_twist_retry_bonus if stagnation_rounds >= settings.stagnation_max_rounds else 0.0
            return (ana.score, -(ana.progression_score + bonus), -len(text))

        best_text, best_ana = min(candidates, key=_rank)
        logger.warning(
            "[chat_id=%d] Accepting best candidate for %s after repetition limit "
            "(score=%s progression=%s)",
            chat_id,
            character.name,
            getattr(best_ana, "score", None),
            getattr(best_ana, "progression_score", None),
        )
        yield {
            "type": "response",
            "text": best_text,
            "turn": None,
            "verdict": "no_actions",
        }
        return

    if settings.fallback_on_isolation_failure:
        logger.warning(
            "[chat_id=%d] All isolation retries failed for %s — attempting "
            "full-context fallback with relaxed isolation",
            chat_id,
            character.name,
        )
        try:
            (
                _raw,
                sanitized,
                _fallback_isolation_ok,
                _thinking_len,
                fallback_token_chunks,
                _used_num_ctx,
                fallback_turn_output,
            ) = await _generate_once(
                client,
                chat_id=chat_id,
                character=character,
                messages_history=messages_history,
                general_prompt=general_prompt,
                memories=memories,
                other_character_names=other_character_names,
                max_history_length=max_history_length,
                model_name=model_name,
                character_names=character_names,
                summary=summary,
                viewer_character_id=viewer_character_id,
                presence_map=presence_map,
                same_round_message_ids=same_round_message_ids,
                enable_thinking=enable_thinking,
                viewer_location=viewer_location,
                character_locations=character_locations,
                stop=stop,
                temperature=0.6,
                strict_isolation=False,
                repetition_feedback=repetition_feedback,
                attempt_label=f"call={call_idx} fallback (relaxed)",
                prior_replies=prior_replies,
                scene_state=scene_state,
                present_character_names=present_character_names,
                stagnation_rounds=stagnation_rounds,
                is_isolated=is_isolated,
                locations=locations,
                location_descriptions=location_descriptions,
                relationships_block=relationships_block,
                behavior_drivers_block=behavior_drivers_block,
                open_issues_block=open_issues_block,
                built_context=built_context,
                proactive_boost=proactive_boost,
                epistemic_mask_block=epistemic_mask_block,
                directive=directive,
                recency_tail_block=recency_tail_block,
                consistency_feedback=consistency_feedback,
            )
            if sanitized:
                fallback_verdict = "no_actions"
                if (
                    fallback_turn_output is not None
                    and fallback_turn_output.actions
                ):
                    fallback_verdict = action_resolution.classify_consistency(
                        fallback_turn_output, sanitized
                    )
                logger.info(
                    "[chat_id=%d] Full-context fallback succeeded for %s",
                    chat_id,
                    character.name,
                )
                if fallback_token_chunks:
                    for chunk in fallback_token_chunks:
                        yield {
                            "type": "token",
                            "text": chunk,
                            "character_id": character.id,
                        }
                        await asyncio.sleep(0.01)
                yield {
                    "type": "response",
                    "text": sanitized,
                    "turn": fallback_turn_output,
                    "verdict": fallback_verdict,
                }
                return
        except Exception as exc:
            logger.warning(
                "[chat_id=%d] Fallback also failed for %s: %s",
                chat_id,
                character.name,
                exc,
            )

    raise RuntimeError(
        f"Не удалось получить изолированный ответ для персонажа {character.name}"
    )


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
    from .prompt_builder import build_scene_state_system, build_scene_state_user
    
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
    
    Returns:
        Dict with time_of_day, character_locations or None on failure
    """
    import json
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

    scene_options: dict = {"temperature": 0.3}
    if num_ctx and num_ctx > 0:
        scene_options["num_ctx"] = num_ctx

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
    from .prompt_builder import (
        build_event_extraction_system,
        build_event_extraction_user,
    )

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

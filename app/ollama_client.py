"""Client for Ollama API (local LLM) with memory extraction and retry logic."""

import asyncio
import json
import logging
import random
import re
from collections.abc import AsyncIterator
from typing import Any

import httpx

from .config import settings
from .context_state import ctx_state
from .token_counter import get_token_counter
from .prompt_builder import (
    build_anti_mimicry_block,
    build_character_summary_block,
    build_extraction_system,
    build_extraction_user,
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
    build_user_context_message,
    build_vocabulary_block,
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
    build_fallback_chat_messages,
    build_fallback_prompt,
    build_generation_cue,
    build_generation_cue_for_chat,
    build_isolated_generation_cue,
    build_stop_sequences,
    find_foreign_speaker_marker,
    sanitize_and_validate_response,
)
from . import schemas
from .witness_model import Presence, filter_history_for_character, filter_history_for_character_with_presence

logger = logging.getLogger(__name__)

OLLAMA_BASE_URL = settings.ollama_base_url
DEFAULT_TEMPERATURE = settings.default_temperature
MEMORY_EXTRACTION_TEMP = 0.3
SUMMARY_TEMP = 0.3

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
            "description": "Map of character name -> current location. Include EVERY character. Empty string if unknown."
        }
    },
    "required": ["time_of_day", "character_locations"]
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
) -> AsyncIterator[dict]:
    think_sent = _resolve_thinking(enable_thinking)
    last_error = None
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
) -> AsyncIterator[dict]:
    think_sent = _resolve_thinking(enable_thinking)
    last_error = None
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

            if full_thinking and not full_response:
                logger.warning(
                    "Ollama chat returned thinking (%d chars) but empty content",
                    len(full_thinking),
                )

            yield {
                "type": "complete",
                "text": full_response,
                "thinking_len": len(full_thinking),
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
    anti_mimicry_block: str = "",
    personality_block: str = "",
    consistency_block: str = "",
    vocabulary_block: str = "",
    scene_advancement_block: str = "",
    isolated_block: str = "",
    behavior_drivers_block: str = "",
    open_issues_block: str = "",
) -> list[ChatMessage]:
    """Build messages for /api/chat with localized blocks (P1 complete)."""
    feedback_block = build_repetition_feedback_block(repetition_feedback)
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
        scene_block,
        behavior_drivers_block,
        open_issues_block,
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
    relationships_block: str = "",
    behavior_drivers_block: str = "",
    open_issues_block: str = "",
    built_context: schemas.BuiltContext | None = None,
) -> tuple[str, str, bool, int, list[str]]:
    """One LLM call + isolation sanitize. Returns (raw, sanitized, isolation_ok, thinking_len, tokens_list)."""
    api_mode = "chat" if settings.use_chat_api else "generate"
    thinking = _resolve_thinking(enable_thinking)
    system_prompt = build_system_prompt(
        character, general_prompt, strict=strict_isolation,
        relationships_block=relationships_block,
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

    # Scene block with world tracking — per-character location (P3)
    if built_context is not None:
        scene_block = built_context.scene_text
    else:
        char_locs = merge_char_locations(scene_state, character_locations, character_names)
        scene_block = build_scene_block(
            general_prompt,
            scene_state,
            present_character_names,
            current_character_name=character.name,
            character_locations=char_locs,
            locations=locations,
        )

    # Vocabulary fingerprinting block — prevents style contamination (Phase 5)
    vocabulary_block = build_vocabulary_block(character, prior_replies)

    # Scene advancement block — breaks stagnation loops (Phase 6)
    scene_advancement_block = ""
    if settings.scene_advancement_enabled:
        proactive_action = (
            stagnation_rounds == 0
            and random.random() < settings.proactive_action_chance
        )
        scene_advancement_block = build_scene_advancement_block(
            stagnation_rounds,
            max_stagnation_rounds=settings.stagnation_max_rounds,
            proactive_action=proactive_action,
        )

    isolated_block = build_isolated_block() if is_isolated else ""

    tokens_collected = []

    if settings.use_chat_api:
        generation_cue = (
            build_isolated_generation_cue(character.name)
            if is_isolated
            else build_generation_cue_for_chat(character.name)
        )
        chat_messages = _build_generation_messages(
            system_prompt,
            summary_block,
            memories_block,
            dialogue_block,
            scene_block,
            reinforcement,
            generation_cue,
            repetition_feedback=repetition_feedback,
            anti_mimicry_block=anti_mimicry_block,
            vocabulary_block=vocabulary_block,
            personality_block=personality_block,
            consistency_block=consistency_block,
            scene_advancement_block=scene_advancement_block,
            isolated_block=isolated_block,
            behavior_drivers_block=behavior_drivers_block,
            open_issues_block=open_issues_block,
        )
        prompt_len = sum(len(msg["content"]) for msg in chat_messages)
        full_prompt = _messages_to_prompt(chat_messages)
    else:
        generation_cue = (
            build_isolated_generation_cue(character.name)
            if is_isolated
            else build_generation_cue(character.name)
        )
        context_parts = [system_prompt]
        if summary_block:
            context_parts.append(summary_block)
        if memories_block:
            context_parts.append(memories_block)
        if dialogue_block:
            context_parts.append(dialogue_block)
        if scene_block:
            context_parts.append(scene_block)
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
        if behavior_drivers_block:
            context_parts.append(behavior_drivers_block)
        if open_issues_block:
            context_parts.append(open_issues_block)
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

    if settings.use_chat_api:
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

    return generated, sanitized, is_valid, thinking_len, tokens_collected, num_ctx


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
    relationships_block: str = "",
    behavior_drivers_block: str = "",
    open_issues_block: str = "",
    built_context: schemas.BuiltContext | None = None,
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

        _raw, sanitized, isolation_ok, _thinking_len, token_chunks, used_num_ctx = await _generate_once(
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
            relationships_block=relationships_block,
            behavior_drivers_block=behavior_drivers_block,
            open_issues_block=open_issues_block,
            built_context=built_context,
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

        # Clean accept - yield token events first, then response
        if token_chunks:
            for chunk in token_chunks:
                yield {"type": "token", "text": chunk, "character_id": character.id}
                await asyncio.sleep(0.01)  # small delay for streaming feel
        yield {"type": "response", "text": sanitized}
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
        yield {"type": "response", "text": best_text}
        return

    if settings.fallback_on_isolation_failure:
        logger.warning(
            "[chat_id=%d] All isolation retries failed for %s — attempting fallback",
            chat_id,
            character.name,
        )
        try:
            if settings.use_chat_api:
                system_content, user_content = build_fallback_chat_messages(
                    character.name,
                    general_prompt,
                )
                fallback_messages = [
                    {"role": "system", "content": system_content},
                    {"role": "user", "content": user_content},
                ]
                fallback_raw = await _call_ollama_chat(
                    client,
                    model_name,
                    fallback_messages,
                    temperature=0.6,
                    stop=stop,
                    num_ctx=used_num_ctx,
                )
            else:
                fallback_prompt = build_fallback_prompt(character.name, general_prompt)
                fallback_raw = await _call_ollama(
                    client,
                    model_name,
                    fallback_prompt,
                    temperature=0.6,
                    stop=stop,
                    num_ctx=used_num_ctx,
                )
            fallback_result = sanitize_and_validate_response(
                fallback_raw,
                character.name,
                other_character_names,
                min_length=3,
            )
            if fallback_result.is_valid and fallback_result.cleaned_text:
                logger.info(
                    "[chat_id=%d] Fallback succeeded for %s", chat_id, character.name
                )
                yield {"type": "response", "text": fallback_result.cleaned_text}
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
        import json
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

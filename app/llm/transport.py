"""HTTP-транспорт Ollama: нестриминговые ``_call_*`` и SSE-стриминг ``_stream_*``.

Перенесено 1:1 из ``app/ollama_client.py`` (диапазон §4.3: 705–1103 +
``llm_request`` 91–107 + константы). ``_ConfigProxy`` удалён — покрывается
``config.py`` (решение Sprint 5A); legacy-константы ``USE_CHAT_API`` и др.
остались в фасаде ``app/ollama_client.py`` для совместимости.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx

from ..config import settings
from .lock import _llm_lock_for
from .prompting import (
    ChatMessage,
    _build_chat_payload,
    _build_generate_payload,
    _resolve_thinking,
)
from .wpe import (
    _format_unsupported_error,
    _next_tool_mode,
    _tool_mode_chain,
    _tools_unsupported_error,
)

logger = logging.getLogger(__name__)

OLLAMA_BASE_URL = settings.ollama_base_url
DEFAULT_TEMPERATURE = settings.default_temperature

MAX_RETRIES = 3
RETRY_DELAY = 1.0


class _ConfigProxy:
    """Legacy module-level proxy over ``settings`` (backward compatibility)."""

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


@asynccontextmanager
async def llm_request(model_name: str | None = None, endpoint: str = ""):
    """Serialize one Ollama HTTP request behind the global LLM lock.

    Holds the lock for the whole exchange, so concurrent callers queue up FIFO
    and each next request is only sent once the previous one has completed.
    """
    label = f"{model_name or '?'}/{endpoint}"
    lock = _llm_lock_for()
    logger.debug("LLM request queueing (%s)", label)
    async with lock:
        logger.debug("LLM request started (%s)", label)
        try:
            yield
        finally:
            logger.debug("LLM request finished (%s)", label)


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
    async with llm_request(model_name, "/api/generate"):
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
    async with llm_request(model_name, "/api/chat"):
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
    """Stream ``/api/generate`` holding the global LLM lock for the whole exchange."""
    async with llm_request(model_name, "/api/generate"):
        async for event in _stream_ollama_generate_unlocked(
            client,
            model_name,
            prompt,
            temperature=temperature,
            stop=stop,
            enable_thinking=enable_thinking,
            num_ctx=num_ctx,
            format_schema=format_schema,
        ):
            yield event


async def _stream_ollama_generate_unlocked(
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
    """Stream ``/api/chat`` holding the global LLM lock for the whole exchange."""
    async with llm_request(model_name, "/api/chat"):
        async for event in _stream_ollama_chat_unlocked(
            client,
            model_name,
            messages,
            temperature=temperature,
            stop=stop,
            enable_thinking=enable_thinking,
            num_ctx=num_ctx,
            tools=tools,
            format_schema=format_schema,
        ):
            yield event


async def _stream_ollama_chat_unlocked(
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

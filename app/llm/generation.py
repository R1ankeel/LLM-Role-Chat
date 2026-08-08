"""Публичный фасад LLM-вызовов (Sprint 1, §7.2 decomposition.md).

Закрывает приватные функции ``ollama_client``: ``_invoke_llm``,
``_extract_json_payload``, ``_build_chat_payload``, ``_build_generate_payload``,
``llm_request``. После этого потребители (relationship_analyzer,
sensors_service) работают только с публичным API ``llm.generation``.
"""

from __future__ import annotations

import asyncio
from typing import Any

from .. import ollama_client
from ..config import settings
from ..ollama_client import DEFAULT_TEMPERATURE

__all__ = ["invoke_json", "extract_json_payload"]


def extract_json_payload(raw: str) -> object | None:
    """Публичная обёртка над извлечением первого JSON из ответа модели."""
    return ollama_client._extract_json_payload(raw)


async def invoke_json(
    client: Any,
    model_name: str,
    messages: list[dict[str, str]],
    *,
    temperature: float = DEFAULT_TEMPERATURE,
    format_schema: dict | None = None,
    timeout: float | None = None,
    enable_thinking: bool = False,
    num_ctx: int | None = None,
    num_predict: int | None = None,
) -> str | None:
    """Нестриминговый LLM-вызов с JSON-контрактом.

    - ``format_schema`` задан — JSON-mode (структурированный вывод) через
      ``/api/chat`` или ``/api/generate`` (сенсорный путь). Возвращает текст
      ответа модели (``content``/``response``) или ``None``, если контента нет.
    - иначе — обычный нестриминговый вызов (``_invoke_llm``), возвращает сырой
      текст (путь relationship_analyzer).

    Исключения (RuntimeError от ``_invoke_llm``, ``asyncio.TimeoutError``,
    httpx-ошибки) пробрасываются наружу — вызывающий решает, как обрабатывать.
    """
    if format_schema is not None:
        return await _invoke_json_mode(
            client=client,
            model_name=model_name,
            messages=messages,
            temperature=temperature,
            format_schema=format_schema,
            timeout=timeout,
            enable_thinking=enable_thinking,
            num_ctx=num_ctx,
            num_predict=num_predict,
        )
    return await ollama_client._invoke_llm(
        client, model_name, messages, temperature=temperature
    )


async def _invoke_json_mode(
    *,
    client: Any,
    model_name: str,
    messages: list[dict[str, str]],
    temperature: float,
    format_schema: dict,
    timeout: float | None,
    enable_thinking: bool,
    num_ctx: int | None,
    num_predict: int | None,
) -> str | None:
    """JSON-mode вызов (перенесён 1:1 из ``sensors_service.SensorsService.invoke``)."""
    if settings.use_chat_api:
        payload = ollama_client._build_chat_payload(
            model_name,
            messages,
            temperature,
            [],
            stream=False,
            enable_thinking=enable_thinking,
            num_ctx=num_ctx,
            num_predict=num_predict,
            format_schema=format_schema,
        )
        endpoint = "/api/chat"
        async with ollama_client.llm_request(model_name, endpoint):
            response = await asyncio.wait_for(
                client.post(endpoint, json=payload),
                timeout=timeout,
            )
        response.raise_for_status()
        return response.json().get("message", {}).get("content", "") or None

    prompt = "\n\n".join(m["content"] for m in messages if m.get("content"))
    payload = ollama_client._build_generate_payload(
        model_name,
        prompt,
        temperature,
        [],
        stream=False,
        enable_thinking=enable_thinking,
        num_ctx=num_ctx,
        num_predict=num_predict,
        format_schema=format_schema,
    )
    endpoint = "/api/generate"
    async with ollama_client.llm_request(model_name, endpoint):
        response = await asyncio.wait_for(
            client.post(endpoint, json=payload),
            timeout=timeout,
        )
    response.raise_for_status()
    return response.json().get("response", "") or None

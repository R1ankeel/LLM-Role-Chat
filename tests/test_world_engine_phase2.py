"""WPE 3.0 (Plans/WPE.md) Фаза 2 — tool-calling генерация take_actions (shadow).

Покрывает:
- системный промпт + инструкция `take_actions` (гейт `WORLD_ENGINE_TOOLS_ENABLED`);
- payload chat/generate: `tools` / `format` (JSON-Schema), §8;
- streaming-контракт: токены стримятся, `tool_calls` в терминальном сообщении
  не рендерятся (тест #22);
- фоллбэк tools → format (строго нативный, И14); deprecated text-only путь
  удалён в Фазе 8 — при недоступных tools/format генерация падает с ошибкой;
- shadow: действия извлекаются, логируются, НЕ применяются; текст прежний;
- кэш возможностей модели (§12);
- метрики критерия выхода §10 (`wpe_tools_stats_snapshot`).
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app import ollama_client
from app import schemas
from app.config import settings
from app.context_state import ctx_state
from app.prompt_builder import (
    build_system_prompt,
    build_take_actions_instruction,
)

# Защита от известной утечки мока: test_stream_disconnect.py падает (pre-existing
# `SessionLocal` bug) внутри `with patch("app.chat_engine.ollama_client.generate")`,
# и мок остаётся на глобальном `app.ollama_client.generate`. Восстанавливаем
# реальную функцию в autouse-фикстуре, чтобы интеграционные тесты Фазы 2 не
# каскадно падали после него.
_REAL_GENERATE = ollama_client.generate


def _make_character(name: str = "Alice") -> SimpleNamespace:
    return SimpleNamespace(
        id=1,
        name=name,
        personality="Curious",
        traits="Brave",
        background="",
        speech_style="",
        example_messages="",
        boundaries="",
        relationships="",
        temperature=None,
    )


@pytest.fixture(autouse=True)
def _reset_wpe_state():
    ollama_client.WPE_TOOLS_STATS = {
        "calls": 0,
        "by_mode": {},
        "schema_valid": 0,
        "with_move_to": 0,
        "with_send_message": 0,
        "with_addressing": 0,
        "latency_ms": [],
    }
    ollama_client._MODEL_TOOL_MODE_CACHE.clear()
    ollama_client.generate = _REAL_GENERATE
    yield


class FakeStreamResponse:
    def __init__(self, lines=None, status=200, error_body=None):
        self._lines = lines or []
        self.status_code = status
        self._error_body = error_body

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def aread(self):
        return (self._error_body or "").encode()

    @property
    def text(self):
        return self._error_body or ""

    async def aiter_lines(self):
        for line in self._lines:
            yield line


def _tool_calls_json(targets, actions):
    return {
        "message": {
            "tool_calls": [
                {
                    "function": {
                        "name": "take_actions",
                        "arguments": json.dumps(
                            {
                                "reply_target_character_ids": targets,
                                "actions": actions,
                            },
                            ensure_ascii=False,
                        ),
                    }
                }
            ],
            "done": True,
        }
    }


# ---------------------------------------------------------------------------
# Системный промпт + инструкция take_actions
# ---------------------------------------------------------------------------

def test_take_actions_instruction_text():
    block = build_take_actions_instruction()
    assert "take_actions" in block
    assert "Текст реплики и действия — в одном ответе" in block


def test_system_prompt_instruction_only_when_passed(monkeypatch):
    char = _make_character()
    default = build_system_prompt(char, "")
    assert "take_actions" not in default

    with_instr = build_system_prompt(
        char, "", take_actions_instruction=build_take_actions_instruction()
    )
    assert "take_actions" in with_instr
    # гейт флагом: пустая строка при выключенном флаге → блок отсутствует
    assert default == build_system_prompt(char, "")


# ---------------------------------------------------------------------------
# Payload: tools / format (§8)
# ---------------------------------------------------------------------------

def test_chat_payload_includes_tools_and_format():
    tool = schemas.build_take_actions_tool()
    fmt = schemas.build_take_actions_json_schema()
    payload = ollama_client._build_chat_payload(
        "m",
        [{"role": "user", "content": "hi"}],
        0.8,
        None,
        stream=True,
        tools=[tool],
        format_schema=fmt,
    )
    assert payload["tools"] == [tool]
    assert payload["format"] == fmt


def test_chat_payload_omits_tools_when_none():
    payload = ollama_client._build_chat_payload(
        "m", [{"role": "user", "content": "hi"}], 0.8, None, stream=True
    )
    assert "tools" not in payload
    assert "format" not in payload


def test_generate_payload_includes_format():
    fmt = schemas.build_take_actions_json_schema()
    payload = ollama_client._build_generate_payload(
        "m", "prompt", 0.8, None, stream=True, format_schema=fmt
    )
    assert payload["format"] == fmt


# ---------------------------------------------------------------------------
# Парсинг tool_calls / JSON-Schema (И14: только нативно, без regex-JSON)
# ---------------------------------------------------------------------------

def test_parse_tool_calls_valid():
    raw = [
        {
            "function": {
                "name": "take_actions",
                "arguments": {
                    "reply_target_character_ids": [2],
                    "actions": [{"type": "move_to", "location": "кухня"}],
                },
            }
        }
    ]
    out = ollama_client._parse_tool_calls(raw)
    assert isinstance(out, schemas.TurnOutput)
    assert out.reply_target_character_ids == [2]
    assert out.actions[0].type == "move_to"
    assert out.actions[0].location == "кухня"


def test_parse_tool_calls_ignores_other_tool():
    raw = [{"function": {"name": "other_tool", "arguments": {}}}]
    assert ollama_client._parse_tool_calls(raw) is None


def test_parse_tool_calls_invalid_args_returns_none():
    raw = [
        {
            "function": {
                "name": "take_actions",
                "arguments": {"actions": [{"type": "teleport"}]},
            }
        }
    ]
    assert ollama_client._parse_tool_calls(raw) is None


def test_parse_turn_output_json_valid():
    text = json.dumps(
        {
            "reply_target_character_ids": [3],
            "actions": [
                {"type": "send_message", "message": "Иду.", "target_character_ids": [3]}
            ],
        },
        ensure_ascii=False,
    )
    out = ollama_client._parse_turn_output_json(text)
    assert isinstance(out, schemas.TurnOutput)
    assert out.actions[0].type == "send_message"


def test_parse_turn_output_json_invalid_returns_none():
    assert ollama_client._parse_turn_output_json("not json at all") is None


# ---------------------------------------------------------------------------
# Streaming-контракт: токены как раньше, tool_calls не рендерятся (#22)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_stream_chat_yields_tokens_and_tool_calls_not_rendered():
    captured = []
    lines = [
        json.dumps(
            {
                "message": {
                    "content": "Борис, подожди, я уже иду к тебе, надо всё обсудить срочно."
                },
                "done": False,
            }
        ),
        json.dumps(_tool_calls_json([2], [{"type": "move_to", "location": "кухня"}])),
    ]

    def fake_stream(method, url, json=None, **kwargs):
        captured.append(json)
        return FakeStreamResponse(lines=lines)

    client = httpx.AsyncClient(base_url="http://test")
    client.stream = MagicMock(side_effect=fake_stream)

    messages = [{"role": "user", "content": "hi"}]
    events = []
    async for event in ollama_client._stream_ollama_chat(
        client,
        "test-model",
        messages,
        temperature=0.8,
        tools=[schemas.build_take_actions_tool()],
        format_schema=schemas.build_take_actions_json_schema(),
    ):
        events.append(event)

    # payload содержит tools (format уходит только в format-режиме фоллбэка)
    assert captured[0]["tools"]
    assert "format" not in captured[0]

    # токены стримятся как раньше, tool_calls не рендерятся как текст
    assert events[0]["type"] == "token"
    assert "подожди" in events[0]["content"]
    assert "take_actions" not in events[0]["content"]
    assert "move_to" not in events[0]["content"]

    complete = events[-1]
    assert complete["type"] == "complete"
    assert "Борис, подожди" in complete["text"]
    assert "take_actions" not in complete["text"]
    assert complete["tool_mode"] == "tools"
    assert len(complete["tool_calls"]) == 1


# ---------------------------------------------------------------------------
# Фоллбэк tools → format (строго нативный, §8); text-only удалён в Фазе 8
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_stream_chat_tools_unsupported_falls_back_to_format():
    captured = []
    ok_lines = [
        json.dumps(
            {
                "message": {
                    "content": "{\"reply_target_character_ids\":[],\"actions\":[]}"
                },
                "done": True,
            }
        )
    ]
    tool_error = FakeStreamResponse(
        status=400, error_body='{"error":"model does not support tools"}'
    )
    ok_resp = FakeStreamResponse(lines=ok_lines)

    def fake_stream(method, url, json=None, **kwargs):
        captured.append(json)
        return tool_error if len(captured) == 1 else ok_resp

    client = httpx.AsyncClient(base_url="http://test")
    client.stream = MagicMock(side_effect=fake_stream)

    events = []
    async for event in ollama_client._stream_ollama_chat(
        client,
        "test-model",
        [{"role": "user", "content": "hi"}],
        temperature=0.8,
        tools=[schemas.build_take_actions_tool()],
        format_schema=schemas.build_take_actions_json_schema(),
    ):
        events.append(event)

    assert len(captured) == 2
    assert captured[0]["tools"] and not captured[0].get("format")
    assert captured[1]["format"] and not captured[1].get("tools")
    assert events[-1]["type"] == "complete"
    assert events[-1]["tool_mode"] == "format"


@pytest.mark.asyncio
async def test_stream_chat_tools_and_format_unsupported_raises():
    """Фаза 8: text-only fallback удалён (И14) — если ни tools, ни format не
    поддерживаются, генерация не деградирует к тексту, а падает с ошибкой."""
    captured = []
    tool_error = FakeStreamResponse(
        status=400, error_body='{"error":"model does not support tools"}'
    )
    format_error = FakeStreamResponse(
        status=400, error_body='{"error":"model does not support format"}'
    )

    def fake_stream(method, url, json=None, **kwargs):
        captured.append(json)
        return [tool_error, format_error][len(captured) - 1]

    client = httpx.AsyncClient(base_url="http://test")
    client.stream = MagicMock(side_effect=fake_stream)

    with pytest.raises(RuntimeError, match="не поддерживает format"):
        async for event in ollama_client._stream_ollama_chat(
            client,
            "test-model",
            [{"role": "user", "content": "hi"}],
            temperature=0.8,
            tools=[schemas.build_take_actions_tool()],
            format_schema=schemas.build_take_actions_json_schema(),
        ):
            pass

    assert len(captured) == 2
    assert captured[0].get("tools")
    assert captured[1].get("format")


@pytest.mark.asyncio
async def test_stream_chat_non_tool_error_still_raises():
    def fake_stream(method, url, json=None, **kwargs):
        return FakeStreamResponse(status=400, error_body='{"error":"bad num_ctx"}')

    client = httpx.AsyncClient(base_url="http://test")
    client.stream = MagicMock(side_effect=fake_stream)

    with pytest.raises(RuntimeError, match="bad num_ctx"):
        async for event in ollama_client._stream_ollama_chat(
            client,
            "test-model",
            [{"role": "user", "content": "hi"}],
            temperature=0.8,
            tools=[schemas.build_take_actions_tool()],
            format_schema=schemas.build_take_actions_json_schema(),
        ):
            pass


def test_model_capability_cache_skips_tools():
    ollama_client._MODEL_TOOL_MODE_CACHE["m1"] = "format"
    assert ollama_client._tool_mode_chain("m1", "tools") == ["format"]
    ollama_client._MODEL_TOOL_MODE_CACHE["m2"] = "text"  # legacy-кэш (Фаза 8)
    assert ollama_client._tool_mode_chain("m2", "tools") == ["tools", "format"]
    assert ollama_client._tool_mode_chain("m3", "tools") == ["tools", "format"]
    assert ollama_client._tool_mode_chain("m4", "text") == ["text"]


# ---------------------------------------------------------------------------
# Shadow: generate() извлекает и логирует действия, не применяя их; текст прежний
# ---------------------------------------------------------------------------

def _tool_stream_payload_capture():
    captured = {}

    async def fake_post(url, json=None, **kwargs):
        captured["payload"] = json
        response = MagicMock()
        response.raise_for_status = MagicMock()
        response.json.return_value = {
            "message": {"role": "assistant", "content": "text"}
        }
        return response

    return captured, fake_post


@pytest.mark.asyncio
async def test_generate_tools_branch_shadow_and_text_preserved():
    character = _make_character()
    captured: dict = {}
    lines = [
        json.dumps(
            {
                "message": {
                    "content": "Борис, подожди меня, я иду к тебе, надо поговорить об этом сейчас же."
                },
                "done": False,
            }
        ),
        json.dumps(
            _tool_calls_json(
                [2],
                [
                    {"type": "move_to", "location": "кухня"},
                    {
                        "type": "send_message",
                        "message": "Иду к тебе.",
                        "target_character_ids": [2],
                    },
                ],
            )
        ),
    ]

    def fake_stream(method, url, json=None, **kwargs):
        captured["payload"] = json
        return FakeStreamResponse(lines=lines)

    client = httpx.AsyncClient(base_url="http://test")
    client.stream = MagicMock(side_effect=fake_stream)

    ctx_state.remove(1)
    with patch("app.ollama_client.settings.use_chat_api", True), patch(
        "app.ollama_client.settings.world_engine_tools_enabled", True
    ), patch("app.ollama_client.settings.enable_thinking", False):
        events = []
        async for event in ollama_client.generate(
            client,
            chat_id=1,
            character=character,
            messages_history=[],
            general_prompt="Scene",
            memories=[],
            other_character_names=["Bob"],
        ):
            events.append(event)

    # payload ушёл с tools (format — только в format-режиме фоллбэка),
    # системный промпт содержит инструкцию
    assert captured["payload"]["tools"]
    assert "format" not in captured["payload"]
    system_content = captured["payload"]["messages"][0]["content"]
    assert "take_actions" in system_content

    # текст прежний (только реплика), tool_calls не отрендерены в текст
    text = events[-1]["text"]
    assert "Борис, подожди меня" in text
    assert "take_actions" not in text
    assert "move_to" not in text
    assert "кухня" not in text

    # shadow: действия извлечены и залогированы в метриках, не применены
    stats = ollama_client.wpe_tools_stats_snapshot()
    assert stats["calls"] == 1
    assert stats["schema_valid"] == 1
    assert stats["with_move_to"] == 1
    assert stats["with_send_message"] == 1
    assert stats["with_addressing"] == 1
    assert stats["by_mode"]["tools"] == 1
    assert stats["latency_ms"] and stats["latency_avg_ms"] >= 0


@pytest.mark.asyncio
async def test_tools_flag_off_text_only_no_shadow():
    character = _make_character()
    captured: dict = {}

    async def fake_post(url, json=None, **kwargs):
        captured["payload"] = json
        response = MagicMock()
        response.raise_for_status = MagicMock()
        response.json.return_value = {
            "message": {
                "role": "assistant",
                "content": "Просто текст без действий, довольно длинный для валидации.",
            }
        }
        return response

    client = httpx.AsyncClient(base_url="http://test")
    client.post = fake_post  # type: ignore[method-assign]

    ctx_state.remove(1)
    with patch("app.ollama_client.settings.use_chat_api", True), patch(
        "app.ollama_client.settings.world_engine_tools_enabled", False
    ), patch("app.ollama_client.settings.enable_thinking", False):
        events = []
        async for event in ollama_client.generate(
            client,
            chat_id=1,
            character=character,
            messages_history=[],
            general_prompt="Scene",
            memories=[],
            other_character_names=["Bob"],
        ):
            events.append(event)

    assert "tools" not in captured["payload"]
    assert "format" not in captured["payload"]
    assert "take_actions" not in captured["payload"]["messages"][0]["content"]
    assert events[-1]["type"] == "response"
    stats = ollama_client.wpe_tools_stats_snapshot()
    assert stats["calls"] == 0


@pytest.mark.asyncio
async def test_generate_format_branch_generate_api_shadow():
    character = _make_character()
    captured: dict = {}
    turn_json = json.dumps(
        {
            "reply_target_character_ids": [],
            "actions": [
                {"type": "send_message", "message": "Я на связи.", "channel": "phone"}
            ],
        },
        ensure_ascii=False,
    )
    lines = [
        json.dumps({"response": turn_json, "done": True}),
    ]

    def fake_stream(method, url, json=None, **kwargs):
        captured["payload"] = json
        return FakeStreamResponse(lines=lines)

    client = httpx.AsyncClient(base_url="http://test")
    client.stream = MagicMock(side_effect=fake_stream)

    ctx_state.remove(1)
    with patch("app.ollama_client.settings.use_chat_api", False), patch(
        "app.ollama_client.settings.world_engine_tools_enabled", True
    ), patch("app.ollama_client.settings.enable_thinking", False):
        events = []
        async for event in ollama_client.generate(
            client,
            chat_id=1,
            character=character,
            messages_history=[],
            general_prompt="Scene",
            memories=[],
            other_character_names=["Bob"],
        ):
            events.append(event)

    assert captured["payload"]["format"] == schemas.build_take_actions_json_schema()
    assert "tools" not in captured["payload"]
    stats = ollama_client.wpe_tools_stats_snapshot()
    assert stats["calls"] == 1
    assert stats["schema_valid"] == 1
    assert stats["with_send_message"] == 1
    assert stats["by_mode"]["format"] == 1


def test_shadow_stats_snapshot_empty():
    snap = ollama_client.wpe_tools_stats_snapshot()
    assert snap["calls"] == 0
    assert snap["latency_avg_ms"] == 0.0
    assert snap["latency_max_ms"] == 0.0

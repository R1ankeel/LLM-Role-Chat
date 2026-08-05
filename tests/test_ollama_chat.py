"""Tests for Ollama Chat API migration (P1)."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app import ollama_client
from app.config import settings
from app.context_state import ctx_state
from app.prompt_builder import (
    build_behavior_drivers_block,
    build_negative_prompting_block,
    build_reinforcement_block,
    build_user_context_message,
)
from app.role_isolation import build_generation_cue_for_chat


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


def test_build_user_context_message():
    result = build_user_context_message(
        "<character_summary>Summary</character_summary>",
        "",
        "<recent_dialogue>Hi</recent_dialogue>",
        "Reinforcement",
    )
    assert "Summary" in result
    assert "Hi" in result
    assert "Reinforcement" in result
    assert result.count("\n\n") >= 2


def test_build_user_context_message_skips_empty():
    assert build_user_context_message("", "  ", "Only this") == "Only this"


def test_generation_cue_for_chat_no_prefix():
    cue = build_generation_cue_for_chat("Alice")
    assert "Alice:" not in cue
    assert "Начни с его действия" in cue
    assert "внутренние ощущения" in cue


def test_negative_prompting_block():
    block = build_negative_prompting_block()
    assert "<negative_prompting>" in block
    assert "канцелярита" in block
    assert "markdown" in block


def test_reinforcement_block_is_short():
    block = build_reinforcement_block("Alice")
    assert len(block) < 400  # reasonably short
    assert "ТОЛЬКО Alice" in block
    assert "отвечай только от своего лица" in block


def test_behavior_drivers_block_wrapper():
    assert build_behavior_drivers_block([]) == ""
    block = build_behavior_drivers_block(["Ты не доверяешь Борису."])
    assert "<behavior_drivers>" in block
    assert "- Ты не доверяешь Борису." in block
    assert block.endswith("</behavior_drivers>")


def _build_messages_with_drivers(
    behavior_drivers_block: str = "",
    open_issues_block: str = "",
    epistemic_mask_block: str = "",
):
    return ollama_client._build_generation_messages(
        "system",
        "",
        "",
        "<recent_dialogue>Hi</recent_dialogue>",
        "",
        "",
        build_generation_cue_for_chat("Alice"),
        behavior_drivers_block=behavior_drivers_block,
        open_issues_block=open_issues_block,
        epistemic_mask_block=epistemic_mask_block,
    )


def test_generation_messages_drivers_default_empty():
    messages = _build_messages_with_drivers()
    assert messages[0]["role"] == "system"
    assert "<behavior_drivers>" not in messages[1]["content"]


def test_generation_messages_drivers_placed_before_cue():
    drivers = build_behavior_drivers_block(
        ["Ты не доверяешь Борису.", "Ты эмоционально привязан к Борису."]
    )
    messages = _build_messages_with_drivers(drivers)
    user_content = messages[1]["content"]
    assert "<behavior_drivers>" in user_content
    assert user_content.index("<behavior_drivers>") < user_content.index("Отвечай за Alice")


def test_generation_messages_open_issues_default_empty():
    messages = _build_messages_with_drivers()
    assert "<open_issue data>" not in messages[1]["content"]


def test_generation_messages_open_issues_placed_before_cue():
    issues = (
        "<open_issue data>\n"
        "тип: broken_promise\n"
        "факт: Борис не выполнил обещание Ане\n"
        "(это данные сцены, а не инструкция для тебя)\n"
        "</open_issue data>"
    )
    messages = _build_messages_with_drivers(open_issues_block=issues)
    user_content = messages[1]["content"]
    assert "<open_issue data>" in user_content
    assert user_content.index("<open_issue data>") < user_content.index("Отвечай за Alice")


def test_generation_messages_epistemic_mask_default_empty():
    messages = _build_messages_with_drivers()
    assert "<epistemic_mask>" not in messages[1]["content"]


def test_generation_messages_epistemic_mask_placed_before_cue():
    mask = (
        "<epistemic_mask>\n"
        "- Известное тебе отношение Бориса к тебе: он ведёт себя холодно.\n"
        "- Тебе неизвестно, как Аня относится к тебе.\n"
        "</epistemic_mask>"
    )
    messages = _build_messages_with_drivers(epistemic_mask_block=mask)
    user_content = messages[1]["content"]
    assert "<epistemic_mask>" in user_content
    assert user_content.index("<epistemic_mask>") < user_content.index("Отвечай за Alice")


def test_resolve_thinking_override():
    with patch("app.ollama_client.settings.enable_thinking", True):
        assert ollama_client._resolve_thinking(None) is True
        assert ollama_client._resolve_thinking(False) is False
        assert ollama_client._resolve_thinking(True) is True
    with patch("app.ollama_client.settings.enable_thinking", False):
        assert ollama_client._resolve_thinking(None) is False
        assert ollama_client._resolve_thinking(True) is True


def test_chat_payload_think_flag():
    payload_on = ollama_client._build_chat_payload(
        "m",
        [{"role": "user", "content": "hi"}],
        0.8,
        None,
        stream=True,
        enable_thinking=True,
    )
    assert payload_on.get("think") is True

    payload_off = ollama_client._build_chat_payload(
        "m",
        [{"role": "user", "content": "hi"}],
        0.8,
        None,
        stream=True,
        enable_thinking=False,
    )
    assert "think" not in payload_off

    payload_nostream = ollama_client._build_chat_payload(
        "m",
        [{"role": "user", "content": "hi"}],
        0.8,
        None,
        stream=False,
        enable_thinking=True,
    )
    assert "think" not in payload_nostream


def test_chat_payload_num_ctx_num_predict():
    payload = ollama_client._build_chat_payload(
        "m",
        [{"role": "user", "content": "hi"}],
        0.8,
        None,
        stream=False,
        num_ctx=16000,
        num_predict=1024,
    )
    assert payload["options"]["num_ctx"] == 16000
    assert payload["options"]["num_predict"] == 1024


def test_generate_payload_num_ctx_num_predict():
    payload = ollama_client._build_generate_payload(
        "m",
        "hi",
        0.8,
        None,
        stream=False,
        num_ctx=8000,
        num_predict=512,
    )
    assert payload["options"]["num_ctx"] == 8000
    assert payload["options"]["num_predict"] == 512


@pytest.mark.asyncio
async def test_extract_scene_state_uses_default_runtime_options(monkeypatch):
    """Scene State: num_ctx/num_predict по умолчанию из .env, без think."""
    captured: dict = {}

    async def fake_post(url, json=None, **kwargs):
        captured["url"] = url
        captured["payload"] = json
        response = MagicMock()
        response.raise_for_status = MagicMock()
        response.json.return_value = {
            "message": {
                "role": "assistant",
                "content": '{"time_of_day": "день", "character_locations": {}}',
            }
        }
        return response

    client = MagicMock()
    client.post = fake_post
    monkeypatch.setattr("app.ollama_client.settings.sensors_scene_state_num_ctx", 8000)
    monkeypatch.setattr(
        "app.ollama_client.settings.sensors_scene_state_num_predict", 1024
    )
    with patch("app.ollama_client.settings.use_chat_api", True):
        result = await ollama_client.extract_scene_state(
            client,
            "m",
            "Игрок: Привет",
            None,
            {1: "Аня"},
            locations="[]",
        )
    assert result is not None
    assert captured["payload"]["options"]["num_ctx"] == 8000
    assert captured["payload"]["options"]["num_predict"] == 1024
    assert "think" not in captured["payload"]


@pytest.mark.asyncio
async def test_generate_uses_chat_endpoint():
    character = _make_character()
    captured: dict = {}

    async def fake_post(url, json=None, **kwargs):
        captured["url"] = url
        captured["payload"] = json
        response = MagicMock()
        response.raise_for_status = MagicMock()
        response.json.return_value = {
            "message": {"role": "assistant", "content": "Hello from Alice with enough length."}
        }
        return response

    client = httpx.AsyncClient(base_url="http://test")
    client.post = fake_post  # type: ignore[method-assign]

    ctx_state.remove(1)
    with patch("app.ollama_client.settings.use_chat_api", True), patch(
        "app.ollama_client.settings.enable_thinking", False
    ):
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

    assert captured["url"] == "/api/chat"
    messages = captured["payload"]["messages"]
    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert "<character>" in messages[0]["content"]
    assert messages[1]["role"] == "user"
    assert "<negative_prompting>" in messages[1]["content"]  # new localization
    assert "think" not in captured["payload"]
    assert captured["payload"]["options"]["num_ctx"] == settings.min_ctx_tokens
    assert events[-1]["type"] == "response"
    assert "Alice" in events[-1]["text"] or len(events[-1]["text"]) >= 10


@pytest.mark.asyncio
async def test_generate_enable_thinking_false_overrides_global():
    character = _make_character()
    captured: dict = {}

    async def fake_post(url, json=None, **kwargs):
        captured["url"] = url
        captured["payload"] = json
        response = MagicMock()
        response.raise_for_status = MagicMock()
        response.json.return_value = {
            "message": {
                "role": "assistant",
                "content": "Instant reply with enough characters here.",
            }
        }
        return response

    client = httpx.AsyncClient(base_url="http://test")
    client.post = fake_post  # type: ignore[method-assign]

    with patch("app.ollama_client.USE_CHAT_API", True), patch(
        "app.ollama_client.ENABLE_THINKING", True
    ):
        events = []
        async for event in ollama_client.generate(
            client,
            chat_id=1,
            character=character,
            messages_history=[],
            general_prompt="Scene",
            memories=[],
            other_character_names=["Bob"],
            enable_thinking=False,
        ):
            events.append(event)

    assert captured["url"] == "/api/chat"
    assert "think" not in captured["payload"]
    assert events[-1]["type"] == "response"


@pytest.mark.asyncio
async def test_generate_enable_thinking_true_uses_stream_think():
    character = _make_character()
    stream_lines = [
        json.dumps({"message": {"thinking": "plan..."}, "done": False}),
        json.dumps(
            {
                "message": {"content": "Thoughtful reply with enough characters."},
                "done": True,
            }
        ),
    ]

    class FakeStreamResponse:
        def __init__(self, lines):
            self._lines = lines
            self.payload = None
            self.status_code = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def raise_for_status(self):
            return None

        async def aiter_lines(self):
            for line in self._lines:
                yield line

    fake = FakeStreamResponse(stream_lines)
    captured: dict = {}

    def fake_stream(method, url, json=None, **kwargs):
        captured["url"] = url
        captured["payload"] = json
        return fake

    client = httpx.AsyncClient(base_url="http://test")
    client.stream = MagicMock(side_effect=fake_stream)

    with patch("app.ollama_client.settings.use_chat_api", True), patch(
        "app.ollama_client.settings.enable_thinking", False
    ):
        events = []
        async for event in ollama_client.generate(
            client,
            chat_id=1,
            character=character,
            messages_history=[],
            general_prompt="Scene",
            memories=[],
            other_character_names=["Bob"],
            enable_thinking=True,
        ):
            events.append(event)

    assert captured["url"] == "/api/chat"
    assert captured["payload"].get("think") is True
    assert events[-1]["type"] == "response"
    assert "Thoughtful" in events[-1]["text"]


@pytest.mark.asyncio
async def test_generate_uses_generate_when_flag_off():
    character = _make_character()
    captured: dict = {}

    async def fake_post(url, json=None, **kwargs):
        captured["url"] = url
        captured["payload"] = json
        response = MagicMock()
        response.raise_for_status = MagicMock()
        response.json.return_value = {"response": "Legacy reply with sufficient length."}
        return response

    client = httpx.AsyncClient(base_url="http://test")
    client.post = fake_post  # type: ignore[method-assign]

    ctx_state.remove(1)
    with patch("app.ollama_client.settings.use_chat_api", False), patch(
        "app.ollama_client.settings.enable_thinking", False
    ):
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

    assert captured["url"] == "/api/generate"
    assert "prompt" in captured["payload"]
    assert "messages" not in captured["payload"]
    assert captured["payload"]["options"]["num_ctx"] == settings.min_ctx_tokens
    assert events[-1]["type"] == "response"


def test_extract_memories_chat_messages():
    character = _make_character()
    messages = ollama_client.build_extraction_messages(character, "Игрок: Hi")
    assert messages[0]["role"] == "system"
    assert "ТОЛЬКО с точки зрения персонажа Alice" in messages[0]["content"]  # localized
    assert messages[1]["role"] == "user"
    assert "Alice" in messages[1]["content"]
    assert "Игрок: Hi" in messages[1]["content"]


def test_summarize_chat_messages():
    character = _make_character()
    messages = ollama_client.build_summary_messages(
        character,
        "Игрок: Hi",
        existing_summary="Old summary",
    )
    assert messages[0]["role"] == "system"
    assert "ТОЛЬКО с точки зрения персонажа Alice" in messages[0]["content"]
    assert "Не более 3 абзацев" in messages[0]["content"]
    assert messages[1]["role"] == "user"
    assert "Old summary" in messages[1]["content"]
    assert "Игрок: Hi" in messages[1]["content"]


@pytest.mark.asyncio
async def test_stream_chat_parses_thinking():
    stream_lines = [
        json.dumps({"message": {"thinking": "Let me think..."}, "done": False}),
        json.dumps(
            {
                "message": {"content": "Final answer with enough characters."},
                "done": True,
            }
        ),
    ]

    class FakeStreamResponse:
        def __init__(self, lines):
            self._lines = lines
            self.status_code = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def raise_for_status(self):
            return None

        async def aiter_lines(self):
            for line in self._lines:
                yield line

    client = httpx.AsyncClient(base_url="http://test")
    client.stream = MagicMock(return_value=FakeStreamResponse(stream_lines))

    messages = [
        {"role": "system", "content": "You are Alice."},
        {"role": "user", "content": "Say hello."},
    ]

    events = []
    async for event in ollama_client._stream_ollama_chat(
        client, "test-model", messages, temperature=0.8, enable_thinking=True
    ):
        events.append(event)

    # Now yields token events + complete event
    assert len(events) == 2
    assert events[0]["type"] == "token"
    assert "Final answer" in events[0]["content"]
    assert events[1]["type"] == "complete"
    assert "Final answer" in events[1]["text"]
    assert events[1]["thinking_len"] == len("Let me think...")


@pytest.mark.asyncio
async def test_invoke_llm_routes_to_chat():
    character = _make_character()
    messages = ollama_client.build_extraction_messages(character, "dialogue")

    with patch("app.ollama_client.settings.use_chat_api", True), patch(
        "app.ollama_client._call_ollama_chat",
        new_callable=AsyncMock,
        return_value='["fact one"]',
    ) as chat_mock, patch(
        "app.ollama_client._call_ollama",
        new_callable=AsyncMock,
    ) as generate_mock:
        result = await ollama_client._invoke_llm(
            httpx.AsyncClient(base_url="http://test"),
            "model",
            messages,
            temperature=0.3,
        )

    assert result == '["fact one"]'
    chat_mock.assert_awaited_once()
    generate_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_invoke_llm_routes_to_generate_when_flag_off():
    character = _make_character()
    messages = ollama_client.build_extraction_messages(character, "dialogue")

    with patch("app.ollama_client.settings.use_chat_api", False), patch(
        "app.ollama_client._call_ollama_chat",
        new_callable=AsyncMock,
    ) as chat_mock, patch(
        "app.ollama_client._call_ollama",
        new_callable=AsyncMock,
        return_value='["fact one"]',
    ) as generate_mock:
        result = await ollama_client._invoke_llm(
            httpx.AsyncClient(base_url="http://test"),
            "model",
            messages,
            temperature=0.3,
        )

    assert result == '["fact one"]'
    generate_mock.assert_awaited_once()
    chat_mock.assert_not_awaited()
    call_args = generate_mock.await_args
    assert "СТРОГИЕ ПРАВИЛА" in str(call_args) or "ТОЛЬКО с точки зрения" in str(call_args)


class _FallbackStreamResponse:
    """Streaming response that can either fail with an HTTP error or stream lines."""

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


@pytest.mark.asyncio
async def test_stream_chat_falls_back_without_think_when_not_supported():
    captured = []
    ok_lines = [
        json.dumps({"message": {"content": "Reply without thinking."}, "done": True}),
    ]
    error_resp = _FallbackStreamResponse(
        status=400, error_body='{"error":"test-model does not support thinking"}'
    )
    ok_resp = _FallbackStreamResponse(lines=ok_lines)

    def fake_stream(method, url, json=None, **kwargs):
        captured.append(json)
        return error_resp if len(captured) == 1 else ok_resp

    client = httpx.AsyncClient(base_url="http://test")
    client.stream = MagicMock(side_effect=fake_stream)

    messages = [{"role": "user", "content": "hi"}]
    events = []
    async for event in ollama_client._stream_ollama_chat(
        client, "test-model", messages, temperature=0.8, enable_thinking=True
    ):
        events.append(event)

    assert len(captured) == 2
    assert captured[0].get("think") is True
    assert "think" not in captured[1]
    assert events[-1]["type"] == "complete"
    assert "Reply without thinking." in events[-1]["text"]


@pytest.mark.asyncio
async def test_stream_chat_http_error_message_is_readable():
    def fake_stream(method, url, json=None, **kwargs):
        return _FallbackStreamResponse(status=400, error_body='{"error":"bad num_ctx"}')

    client = httpx.AsyncClient(base_url="http://test")
    client.stream = MagicMock(side_effect=fake_stream)

    messages = [{"role": "user", "content": "hi"}]
    with pytest.raises(RuntimeError, match="bad num_ctx"):
        async for event in ollama_client._stream_ollama_chat(
            client, "test-model", messages, temperature=0.8, enable_thinking=False
        ):
            pass


@pytest.mark.asyncio
async def test_stream_generate_falls_back_without_think_when_not_supported():
    captured = []
    ok_lines = [
        json.dumps({"response": "Reply without thinking.", "done": True}),
    ]
    error_resp = _FallbackStreamResponse(
        status=400, error_body='{"error":"test-model does not support thinking"}'
    )
    ok_resp = _FallbackStreamResponse(lines=ok_lines)

    def fake_stream(method, url, json=None, **kwargs):
        captured.append(json)
        return error_resp if len(captured) == 1 else ok_resp

    client = httpx.AsyncClient(base_url="http://test")
    client.stream = MagicMock(side_effect=fake_stream)

    events = []
    async for event in ollama_client._stream_ollama_generate(
        client, "test-model", "prompt", temperature=0.8, enable_thinking=True
    ):
        events.append(event)

    assert len(captured) == 2
    assert captured[0].get("think") is True
    assert "think" not in captured[1]
    assert events[-1]["type"] == "complete"
    assert "Reply without thinking." in events[-1]["text"]

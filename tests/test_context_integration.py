"""Integration tests: ContextBuilder wired into process_user_message_streaming (TZ §28/§15.3).

Verifies that with CONTEXT_ENABLED=true every NPC receives its own token-aware
``built_context`` that stays within the budget, and that the legacy path
(built_context=None) is preserved when CONTEXT_ENABLED=false.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app import chat_engine
from app import crud
from app import schemas
from app.config import settings
from tests.conftest import create_characters


async def _run_round(
    db_session,
    chat_id: int,
    user_text: str,
    *,
    fake_generate,
    extract_scene_state=None,
):
    """Run one streaming round with a mocked generate, collecting events."""

    def _noop_create_task(coro):
        coro.close()
        return MagicMock()

    events = []
    scene_return = (
        extract_scene_state if extract_scene_state is not None else {}
    )
    with (
        patch("app.chat_engine.ollama_client.generate", side_effect=fake_generate),
        patch("app.chat_engine.asyncio.create_task", new=_noop_create_task),
        patch("app.chat_engine.asyncio.to_thread"),
        patch(
            "app.chat_engine.ollama_client.extract_scene_state",
            new_callable=AsyncMock,
            return_value=scene_return,
        ),
    ):
        async for event in chat_engine.process_user_message_streaming(
            MagicMock(),
            db_session,
            chat_id,
            user_text,
        ):
            events.append(event)
    return events


async def _default_fake_generate(call_log):
    async def fake_generate(**kwargs):
        call_log.append(kwargs)
        yield {
            "type": "response",
            "text": "Ответ персонажа с достаточной длиной текста.",
        }

    return fake_generate


@pytest.mark.asyncio
async def test_context_enabled_passes_built_context_to_each_npc(
    db_session, chat, monkeypatch,
):
    monkeypatch.setattr(settings, "context_enabled", True)
    characters = await create_characters(db_session, chat.id, 3)
    for index in range(20):
        await crud.create_message(
            db_session,
            schemas.MessageCreate(
                chat_id=chat.id,
                role="user" if index % 2 == 0 else "character",
                character_id=characters[index % 3].id,
                content=f"Историческое сообщение {index} без специфики.",
            ),
        )

    call_log: list[dict] = []
    fake_generate = await _default_fake_generate(call_log)

    await _run_round(
        db_session,
        chat.id,
        "Новый вопрос от игрока.",
        fake_generate=fake_generate,
        extract_scene_state={},
    )

    assert len(call_log) == 3
    built_contexts = []
    for call in call_log:
        built = call["built_context"]
        assert built is not None
        built_contexts.append(built)
        assert built.total_tokens <= built.budget.total_tokens
        assert built.total_tokens <= settings.max_context_tokens
        assert "Новый вопрос от игрока." in built.dialogue_text
        assert call["viewer_character_id"] in {c.id for c in characters}

    assert len({id(b) for b in built_contexts}) == 3


@pytest.mark.asyncio
async def test_context_disabled_keeps_legacy_generate_signature(
    db_session, chat, monkeypatch,
):
    await create_characters(db_session, chat.id, 2)
    monkeypatch.setattr(settings, "context_enabled", False)

    call_log: list[dict] = []
    fake_generate = await _default_fake_generate(call_log)

    await _run_round(
        db_session,
        chat.id,
        "Проверка обратной совместимости.",
        fake_generate=fake_generate,
        extract_scene_state={},
    )

    assert len(call_log) == 2
    for call in call_log:
        assert call["built_context"] is None
        assert call["messages_history"]  # legacy path still passes history


@pytest.mark.asyncio
async def test_context_budget_holds_with_long_history(
    db_session, chat, monkeypatch,
):
    monkeypatch.setattr(settings, "context_enabled", True)
    characters = await create_characters(db_session, chat.id, 3)
    for index in range(60):
        await crud.create_message(
            db_session,
            schemas.MessageCreate(
                chat_id=chat.id,
                role="user",
                content=f"Длинное сообщение {index} " + "фрагмент " * 60,
            ),
        )

    call_log: list[dict] = []
    fake_generate = await _default_fake_generate(call_log)

    await _run_round(
        db_session,
        chat.id,
        "Продолжим разговор.",
        fake_generate=fake_generate,
        extract_scene_state={},
    )

    for call in call_log:
        built = call["built_context"]
        assert built.total_tokens <= built.budget.total_tokens
        assert built.total_tokens <= settings.max_context_tokens

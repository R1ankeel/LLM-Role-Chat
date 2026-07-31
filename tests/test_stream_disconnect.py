"""Tests that message generation persists when the SSE consumer disconnects."""

from __future__ import annotations

import asyncio
from contextlib import ExitStack
from unittest.mock import patch

import httpx
import pytest
from sqlalchemy.orm import sessionmaker

from app import crud
from app.routers.chat_engine import _run_generation
from tests.test_chat_engine import _run_in_current_thread


@pytest.fixture
def mock_client():
    return httpx.AsyncClient(base_url="http://test")


@pytest.fixture
def test_session_factory(db_engine):
    return sessionmaker(bind=db_engine)


async def _fake_generate(**kwargs):
    character = kwargs["character"]
    yield {
        "type": "response",
        "text": f"Reply from {character.name} with enough text for validation.",
    }


async def _noop_post_round(*_args, **_kwargs):
    return None


def _enter_generation_patches(test_session_factory):
    stack = ExitStack()
    stack.enter_context(
        patch("app.chat_engine.ollama_client.generate", side_effect=_fake_generate)
    )
    stack.enter_context(
        patch("app.chat_engine.memory_service.process_post_round", _noop_post_round)
    )
    stack.enter_context(
        patch("app.chat_engine.asyncio.to_thread", side_effect=_run_in_current_thread)
    )
    stack.enter_context(
        patch("app.routers.chat_engine.SessionLocal", test_session_factory)
    )
    return stack


@pytest.mark.asyncio
async def test_detached_generation_persists_when_consumer_stops_early(
    db_session, chat, mock_client, test_session_factory, three_characters
):
    """Generation saves all messages even if only the first SSE event is consumed."""
    queue: asyncio.Queue = asyncio.Queue()

    with _enter_generation_patches(test_session_factory):
        consumer = asyncio.create_task(queue.get())
        await _run_generation(queue, mock_client, chat.id, "Hello everyone")
        first_event = await consumer

    assert first_event["type"] == "message"
    assert first_event["message"]["role"] == "user"

    saved = crud.get_messages_by_chat(db_session, chat.id)
    assert len(saved) == 4  # user + 3 characters
    character_messages = [m for m in saved if m.role == "character"]
    assert len(character_messages) == 3


@pytest.mark.asyncio
async def test_background_generation_completes_after_partial_sse_read(
    db_session, chat, mock_client, test_session_factory, three_characters
):
    """Background generation task finishes even if the client stops reading SSE."""
    queue: asyncio.Queue = asyncio.Queue()

    with _enter_generation_patches(test_session_factory):
        gen_task = asyncio.create_task(
            _run_generation(queue, mock_client, chat.id, "Trigger generation")
        )

        first_event = await asyncio.wait_for(queue.get(), timeout=5)
        assert first_event["message"]["role"] == "user"

        await asyncio.wait_for(gen_task, timeout=5)

    saved = crud.get_messages_by_chat(db_session, chat.id)
    character_messages = [m for m in saved if m.role == "character"]
    assert len(character_messages) == 3

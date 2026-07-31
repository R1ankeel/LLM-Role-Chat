"""Integration tests for sequential multi-character chat generation."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import httpx
import pytest
from sqlalchemy.orm import sessionmaker

from app import chat_engine
from app import crud
from app import memory_service
from app import schemas
from tests.conftest import create_characters


async def _run_in_current_thread(func, /, *args, **kwargs):
    return func(*args, **kwargs)


@pytest.fixture
def mock_client():
    return httpx.AsyncClient(base_url="http://test")


@pytest.mark.asyncio
async def test_sequential_generation_order_and_context(
    db_session, chat, mock_client
):
    """TEST 5: characters generate in order_index order and see prior replies."""
    characters = create_characters(db_session, chat.id, 3)
    call_log: list[dict] = []

    async def fake_generate(**kwargs):
        character = kwargs["character"]
        history = kwargs["messages_history"]
        character_messages = [
            m for m in history if m.role == "character"
        ]
        call_log.append(
            {
                "name": character.name,
                "order_index": character.order_index,
                "character_message_count": len(character_messages),
                "character_names_in_history": [
                    m.character_id for m in character_messages
                ],
                "viewer_character_id": kwargs.get("viewer_character_id"),
            }
        )
        yield {
            "type": "response",
            "text": f"Reply from {character.name} with enough text for validation.",
        }

    with patch(
        "app.chat_engine.ollama_client.generate",
        side_effect=fake_generate,
    ), patch("app.chat_engine.asyncio.create_task"), patch(
        "app.chat_engine.asyncio.to_thread",
        side_effect=_run_in_current_thread,
    ):
        messages = []
        async for event in chat_engine.process_user_message_streaming(
            mock_client,
            db_session,
            chat.id,
            "Hello everyone",
        ):
            if event.get("type") == "message":
                messages.append(event["message"])

    assert len(messages) == 4  # user + 3 characters
    assert [entry["name"] for entry in call_log] == [
        "Character A",
        "Character B",
        "Character C",
    ]
    assert call_log[0]["character_message_count"] == 0
    assert call_log[1]["character_message_count"] == 1
    assert call_log[2]["character_message_count"] == 2
    assert call_log[0]["viewer_character_id"] == characters[0].id
    assert call_log[1]["viewer_character_id"] == characters[1].id
    assert call_log[2]["viewer_character_id"] == characters[2].id

    saved = crud.get_messages_by_chat(db_session, chat.id)
    character_messages = [m for m in saved if m.role == "character"]
    assert [m.character_id for m in character_messages] == [
        characters[0].id,
        characters[1].id,
        characters[2].id,
    ]


@pytest.mark.asyncio
async def test_memory_isolation_per_character(db_session, chat, mock_client):
    """TEST 8: each character receives only its own memories in generate()."""
    characters = create_characters(db_session, chat.id, 3)
    memory_map = {}
    for character, content in zip(
        characters,
        [
            "Memory unique to Character A",
            "Memory unique to Character B",
            "Memory unique to Character C",
        ],
    ):
        crud.create_memory(
            db_session,
            schemas.MemoryCreate(
                chat_id=chat.id,
                character_id=character.id,
                content=content,
            ),
        )
        memory_map[character.id] = content

    crud.upsert_character_summary(
        db_session,
        chat.id,
        characters[0].id,
        "Summary only for Character A",
        through_message_id=0,
    )
    crud.upsert_character_summary(
        db_session,
        chat.id,
        characters[1].id,
        "Summary only for Character B",
        through_message_id=0,
    )

    captured_memories: dict[str, list[str]] = {}
    captured_summaries: dict[str, str | None] = {}

    async def fake_generate(**kwargs):
        character = kwargs["character"]
        memories = kwargs["memories"]
        captured_memories[character.name] = [m.content for m in memories]
        captured_summaries[character.name] = kwargs.get("summary")
        yield {
            "type": "response",
            "text": f"Reply from {character.name} with enough text for validation.",
        }

    with patch(
        "app.chat_engine.ollama_client.generate",
        side_effect=fake_generate,
    ), patch("app.chat_engine.asyncio.create_task"), patch(
        "app.chat_engine.asyncio.to_thread",
        side_effect=_run_in_current_thread,
    ):
        async for _ in chat_engine.process_user_message_streaming(
            mock_client,
            db_session,
            chat.id,
            "Trigger generation",
        ):
            pass

    for index, character in enumerate(characters):
        label = chr(ord("A") + index)
        contents = captured_memories[f"Character {label}"]
        assert contents == [memory_map[character.id]]
        for other in characters:
            if other.id != character.id:
                assert memory_map[other.id] not in contents

    assert captured_summaries["Character A"] == "Summary only for Character A"
    assert captured_summaries["Character B"] == "Summary only for Character B"
    assert captured_summaries["Character C"] is None


@pytest.mark.asyncio
async def test_per_character_memory_extraction_called(
    db_session, chat, mock_client, db_engine
):
    """Verify per-character memory extraction runs via background task with snapshots."""
    create_characters(db_session, chat.id, 2)
    background_tasks: list = []

    extracted_calls = []

    async def fake_extract_for_character(client, model, character, text):
        extracted_calls.append(character.name)
        return [f"Fact for {character.name}"]

    real_create_task = asyncio.create_task

    def schedule_task(coro):
        task = real_create_task(coro)
        background_tasks.append(task)
        return task

    test_session_factory = sessionmaker(bind=db_engine)

    async def fake_generate(**kwargs):
        yield {"type": "response", "text": "Valid reply here."}

    with patch(
        "app.memory_service.ollama_client.extract_memories_for_character",
        side_effect=fake_extract_for_character,
    ), patch("app.chat_engine.asyncio.create_task", side_effect=schedule_task), patch(
        "app.chat_engine.asyncio.to_thread", side_effect=_run_in_current_thread
    ), patch(
        "app.chat_engine.ollama_client.generate",
        side_effect=fake_generate,
    ), patch("app.database.get_session_factory", lambda: test_session_factory):
        async for _ in chat_engine.process_user_message_streaming(
            mock_client, db_session, chat.id, "Test message"
        ):
            pass

        await asyncio.gather(*background_tasks)

    assert len(extracted_calls) == 2
    assert "Character A" in extracted_calls
    assert "Character B" in extracted_calls


@pytest.mark.asyncio
async def test_memory_extraction_with_snapshots_after_session_closed(
    db_session, chat, mock_client, db_engine
):
    """Regression: background memory extraction must not touch detached ORM objects."""
    characters = create_characters(db_session, chat.id, 2)
    character_snapshots = [
        chat_engine._character_to_snapshot(c) for c in characters
    ]
    round_snapshots = [
        {
            "role": "user",
            "content": "Hello everyone",
            "character_id": None,
        },
        {
            "role": "character",
            "content": "Reply from Character A with enough text.",
            "character_id": characters[0].id,
        },
        {
            "role": "character",
            "content": "Reply from Character B with enough text.",
            "character_id": characters[1].id,
        },
    ]
    chat_id = chat.id
    model_name = chat.model_name

    db_session.close()

    test_session_factory = sessionmaker(bind=db_engine)

    async def fake_extract_for_character(client, model, character, text):
        return [f"Fact for {character.name}"]

    with patch(
        "app.memory_service.ollama_client.extract_memories_for_character",
        side_effect=fake_extract_for_character,
    ), patch("app.memory_service.SessionLocal", test_session_factory):
        await memory_service._extract_and_save_memories(
            mock_client,
            chat_id,
            round_snapshots,
            character_snapshots,
            model_name,
        )

    verify_session = test_session_factory()
    try:
        for snapshot in character_snapshots:
            memories = crud.get_memories_by_character(
                verify_session, snapshot["id"]
            )
            assert len(memories) == 1
            assert memories[0].content == f"Fact for {snapshot['name']}"
    finally:
        verify_session.close()

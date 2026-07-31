"""Tests for witness-aware history filtering."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest

from app import crud
from app import memory_service
from app import schemas
from app import witness_model
from tests.conftest import create_characters


def _msg(
    message_id: int,
    role: str,
    content: str,
    *,
    character_id: int | None = None,
    location: str = "",
    visibility: str = "local",
    target_character_ids: str | list | None = "[]",
) -> SimpleNamespace:
    return SimpleNamespace(
        id=message_id,
        role=role,
        content=content,
        character_id=character_id,
        location=location,
        visibility=visibility,
        target_character_ids=target_character_ids if target_character_ids is not None else "[]",
    )


@pytest.fixture
def character_names() -> dict[int, str]:
    return {1: "Character A", 2: "Character B"}


def test_user_message_present_same_empty_location(character_names):
    """Empty locations share one default scene — everyone hears LOCAL."""
    message = _msg(1, "user", "Hello everyone")
    assert (
        witness_model.compute_mvp_presence(message, 1, character_names) == "present"
    )
    assert (
        witness_model.compute_mvp_presence(message, 2, character_names) == "present"
    )


def test_own_character_message_present(character_names):
    message = _msg(2, "character", "My reply", character_id=1)
    assert (
        witness_model.compute_mvp_presence(message, 1, character_names) == "present"
    )


def test_other_character_same_location_present(character_names):
    message = _msg(
        3, "character", "Secret from A", character_id=1, location="hall"
    )
    presence = witness_model.compute_mvp_presence(
        message,
        2,
        character_names,
        viewer_location="hall",
    )
    assert presence == "present"


def test_other_character_different_location_absent(character_names):
    message = _msg(
        3, "character", "Secret from A", character_id=1, location="hall"
    )
    filtered = witness_model.filter_history_for_character(
        [message],
        viewer_character_id=2,
        character_names=character_names,
        max_len=10,
        viewer_location="street",
    )
    assert filtered == ""


def test_same_round_no_longer_forces_visibility(character_names):
    """same_round_ids must not override location isolation."""
    message = _msg(
        4, "character", "Reply from A", character_id=1, location="room"
    )
    filtered = witness_model.filter_history_for_character(
        [message],
        viewer_character_id=2,
        character_names=character_names,
        same_round_ids={4},
        max_len=10,
        viewer_location="street",
    )
    assert filtered == ""


def test_mentioned_when_name_in_content_remote(character_names):
    message = _msg(
        5,
        "character",
        "Character B, come here quickly!",
        character_id=1,
        location="room",
    )
    presence = witness_model.compute_mvp_presence(
        message,
        2,
        character_names,
        viewer_location="street",
    )
    assert presence == "mentioned"

    line = witness_model.format_line_for_presence(
        message, "mentioned", character_names
    )
    assert line is not None
    assert line.startswith("[Тебя упомянули:")
    assert "Character B" in line


def test_format_history_integration_with_locations(character_names):
    messages = [
        _msg(1, "user", "Hello", location="room"),
        _msg(2, "character", "A speaks", character_id=1, location="room"),
        _msg(3, "character", "B speaks", character_id=2, location="street"),
    ]
    filtered_for_b = witness_model.filter_history_for_character(
        messages,
        viewer_character_id=2,
        character_names=character_names,
        max_len=10,
        viewer_location="street",
    )
    assert "Игрок: Hello" not in filtered_for_b
    assert "Character B: B speaks" in filtered_for_b
    assert "Character A: A speaks" not in filtered_for_b


def test_presence_persisted_in_crud(db_session, chat):
    characters = create_characters(db_session, chat.id, 2)
    user_message = crud.create_message(
        db_session,
        schemas.MessageCreate(chat_id=chat.id, role="user", content="Hi"),
    )
    char_message = crud.create_message(
        db_session,
        schemas.MessageCreate(
            chat_id=chat.id,
            role="character",
            character_id=characters[0].id,
            content="Reply A",
        ),
    )

    crud.upsert_message_presence_batch(
        db_session,
        [
            schemas.MessagePresenceCreate(
                message_id=user_message.id,
                character_id=characters[1].id,
                presence="present",
            ),
            schemas.MessagePresenceCreate(
                message_id=char_message.id,
                character_id=characters[1].id,
                presence="absent",
            ),
        ],
    )

    presence_for_b = crud.get_presence_map(
        db_session,
        [user_message.id, char_message.id],
        characters[1].id,
    )
    assert presence_for_b[user_message.id] == "present"
    assert presence_for_b[char_message.id] == "absent"


@pytest.mark.asyncio
async def test_memory_service_uses_filtered_text(db_session, chat, db_engine):
    characters = create_characters(db_session, chat.id, 2)
    character_a, character_b = characters
    user_message = crud.create_message(
        db_session,
        schemas.MessageCreate(chat_id=chat.id, role="user", content="Hello"),
    )
    message_a = crud.create_message(
        db_session,
        schemas.MessageCreate(
            chat_id=chat.id,
            role="character",
            character_id=character_a.id,
            content="Only A should fully know this secret.",
        ),
    )
    round_snapshots = [
        schemas.MessageRead.model_validate(user_message).model_dump(mode="json"),
        schemas.MessageRead.model_validate(message_a).model_dump(mode="json"),
    ]
    character_snapshots = [
        schemas.CharacterRead.model_validate(c).model_dump(mode="python")
        for c in characters
    ]

    crud.upsert_message_presence_batch(
        db_session,
        [
            schemas.MessagePresenceCreate(
                message_id=user_message.id,
                character_id=character_b.id,
                presence="present",
            ),
            schemas.MessagePresenceCreate(
                message_id=message_a.id,
                character_id=character_b.id,
                presence="absent",
            ),
        ],
    )

    captured_texts: dict[str, str] = {}

    async def fake_extract(client, model, character, text):
        captured_texts[character.name] = text
        return []

    from sqlalchemy.orm import sessionmaker

    test_session_factory = sessionmaker(bind=db_engine)

    with patch(
        "app.memory_service.ollama_client.extract_memories_for_character",
        side_effect=fake_extract,
    ), patch("app.memory_service.SessionLocal", test_session_factory):
        await memory_service._extract_and_save_memories(
            httpx.AsyncClient(base_url="http://test"),
            chat.id,
            round_snapshots,
            character_snapshots,
            chat.model_name,
        )

    assert "Only A should fully know this secret." in captured_texts["Character A"]
    assert "Only A should fully know this secret." not in captured_texts.get(
        "Character B", ""
    )
    assert "Игрок: Hello" in captured_texts["Character B"]

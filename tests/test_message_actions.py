"""Tests for message deletion and single-reply regeneration."""

from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest

from app import chat_engine
from app import crud
from app import schemas
from tests.conftest import create_characters


@pytest.fixture
def mock_client():
    return httpx.AsyncClient(base_url="http://test")


async def _make_round(db, chat, characters):
    """Create a player message + one reply per character. Returns (user_msg, replies)."""
    user_msg = await crud.create_message(
        db,
        schemas.MessageCreate(chat_id=chat.id, role="user", content="Hello everyone"),
    )
    replies = []
    for c in characters:
        msg = await crud.create_message(
            db,
            schemas.MessageCreate(
                chat_id=chat.id,
                character_id=c.id,
                role="character",
                content=f"Reply from {c.name}.",
            ),
        )
        replies.append(msg)
    return user_msg, replies


# ----------------------------- Deletion -----------------------------
@pytest.mark.asyncio
async def test_delete_single_character_message(db_session, chat):
    characters = await create_characters(db_session, chat.id, 2)
    _, replies = await _make_round(db_session, chat, characters)

    assert await crud.delete_message(db_session, replies[0].id) is True

    remaining = await crud.get_messages_by_chat(db_session, chat.id)
    ids = [m.id for m in remaining]
    assert replies[0].id not in ids
    assert replies[1].id in ids


@pytest.mark.asyncio
async def test_delete_user_message_cascades(db_session, chat):
    characters = await create_characters(db_session, chat.id, 2)
    user_msg, replies = await _make_round(db_session, chat, characters)

    assert await crud.delete_message(db_session, user_msg.id, cascade_after=True) is True

    remaining = await crud.get_messages_by_chat(db_session, chat.id)
    assert remaining == []


@pytest.mark.asyncio
async def test_delete_missing_message_returns_false(db_session, chat):
    assert await crud.delete_message(db_session, 999999) is False


@pytest.mark.asyncio
async def test_delete_message_removes_presence(db_session, chat):
    characters = await create_characters(db_session, chat.id, 1)
    _, replies = await _make_round(db_session, chat, characters)
    msg = replies[0]
    char_names = {c.id: c.name for c in characters}

    await crud.compute_and_save_presence_for_message(
        db_session, msg, characters, char_names
    )
    before = await crud.get_presence_map(db_session, [msg.id], characters[0].id)
    assert msg.id in before

    await crud.delete_message(db_session, msg.id)

    after = await crud.get_presence_map(db_session, [msg.id], characters[0].id)
    assert msg.id not in after


# --------------------------- Regeneration ---------------------------
@pytest.mark.asyncio
async def test_regenerate_rejects_user_message(db_session, chat, mock_client):
    characters = await create_characters(db_session, chat.id, 1)
    user_msg, _ = await _make_round(db_session, chat, characters)

    with pytest.raises(ValueError):
        async for _ in chat_engine.regenerate_message_streaming(
            mock_client, db_session, chat.id, user_msg.id
        ):
            pass


@pytest.mark.asyncio
async def test_regenerate_rejects_non_last_character_message(db_session, chat, mock_client):
    characters = await create_characters(db_session, chat.id, 1)
    c = characters[0]

    await crud.create_message(
        db_session,
        schemas.MessageCreate(chat_id=chat.id, role="user", content="First"),
    )
    first_reply = await crud.create_message(
        db_session,
        schemas.MessageCreate(
            chat_id=chat.id, character_id=c.id, role="character", content="First reply"
        ),
    )
    await crud.create_message(
        db_session,
        schemas.MessageCreate(chat_id=chat.id, role="user", content="Second"),
    )
    await crud.create_message(
        db_session,
        schemas.MessageCreate(
            chat_id=chat.id, character_id=c.id, role="character", content="Second reply"
        ),
    )

    with pytest.raises(ValueError):
        async for _ in chat_engine.regenerate_message_streaming(
            mock_client, db_session, chat.id, first_reply.id
        ):
            pass


@pytest.mark.asyncio
async def test_regenerate_success(db_session, chat, mock_client):
    characters = await create_characters(db_session, chat.id, 2)
    _, replies = await _make_round(db_session, chat, characters)
    target = replies[1]

    async def fake_generate(**kwargs):
        character = kwargs["character"]
        yield {"type": "token", "text": "New "}
        yield {
            "type": "response",
            "text": f"New reply from {character.name} with enough text.",
        }

    with patch("app.chat_engine.ollama_client.generate", side_effect=fake_generate), patch.object(
        chat_engine.settings, "embedding_enabled", False
    ), patch.object(chat_engine.settings, "context_enabled", False):
        events = []
        async for event in chat_engine.regenerate_message_streaming(
            mock_client, db_session, chat.id, target.id
        ):
            events.append(event)

    token_events = [e for e in events if e["type"] == "token"]
    message_events = [e for e in events if e["type"] == "message"]

    assert token_events == [
        {"type": "token", "text": "New ", "character_id": characters[1].id}
    ]
    assert len(message_events) == 1
    new_msg = message_events[0]["message"]
    assert new_msg["content"] == f"New reply from {characters[1].name} with enough text."
    assert new_msg["id"] != target.id

    remaining = await crud.get_messages_by_chat(db_session, chat.id)
    ids = [m.id for m in remaining]
    assert target.id not in ids
    assert new_msg["id"] in ids
    assert replies[0].id in ids  # the other character's reply stays untouched


@pytest.mark.asyncio
async def test_regenerate_failure_keeps_old_message(db_session, chat, mock_client):
    characters = await create_characters(db_session, chat.id, 1)
    _, replies = await _make_round(db_session, chat, characters)
    target = replies[0]

    async def fake_generate(**kwargs):
        raise RuntimeError("ollama down")
        yield

    with patch("app.chat_engine.ollama_client.generate", side_effect=fake_generate), patch.object(
        chat_engine.settings, "embedding_enabled", False
    ), patch.object(chat_engine.settings, "context_enabled", False):
        with pytest.raises(RuntimeError):
            async for _ in chat_engine.regenerate_message_streaming(
                mock_client, db_session, chat.id, target.id
            ):
                pass

    remaining = await crud.get_messages_by_chat(db_session, chat.id)
    assert target.id in [m.id for m in remaining]

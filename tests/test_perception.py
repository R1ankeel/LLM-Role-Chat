"""Tests for location/visibility-based character perception."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest

from app import chat_engine
from app import crud
from app import ollama_client
from app import perception
from app import schemas
from app import witness_model
from tests.conftest import create_characters


async def _run_in_current_thread(func, /, *args, **kwargs):
    return func(*args, **kwargs)


def _msg(
    mid: int,
    role: str,
    content: str,
    *,
    character_id: int | None = None,
    location: str = "",
    visibility: str = "local",
    targets: list[int] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=mid,
        role=role,
        content=content,
        character_id=character_id,
        location=location,
        visibility=visibility,
        target_character_ids=perception.serialize_target_ids(targets or []),
    )


def test_different_locations_hide_local_user_event():
    names = {1: "Alice", 2: "Bob"}
    event = _msg(1, "user", "Ты красивая.", location="living_room")
    assert (
        witness_model.compute_mvp_presence(
            event, 1, names, viewer_location="living_room"
        )
        == "present"
    )
    assert (
        witness_model.compute_mvp_presence(
            event, 2, names, viewer_location="street"
        )
        == "absent"
    )
    filtered_bob = witness_model.filter_history_for_character(
        [event],
        viewer_character_id=2,
        character_names=names,
        max_len=10,
        viewer_location="street",
    )
    assert "красивая" not in filtered_bob


def test_same_location_shows_local_user_event():
    names = {1: "Alice", 2: "Bob"}
    event = _msg(1, "user", "Привет всем", location="tavern")
    for cid in (1, 2):
        assert (
            witness_model.compute_mvp_presence(
                event, cid, names, viewer_location="tavern"
            )
            == "present"
        )


def test_private_only_target_sees():
    names = {1: "Alice", 2: "Bob", 3: "Carol"}
    event = _msg(
        10,
        "user",
        "Только для Алисы",
        location="living_room",
        visibility="private",
        targets=[1],
    )
    assert (
        witness_model.compute_mvp_presence(
            event, 1, names, viewer_location="living_room"
        )
        == "present"
    )
    assert (
        witness_model.compute_mvp_presence(
            event, 2, names, viewer_location="living_room"
        )
        == "absent"
    )
    assert (
        witness_model.compute_mvp_presence(
            event, 3, names, viewer_location="street"
        )
        == "absent"
    )


def test_targeted_across_locations():
    names = {1: "Alice", 2: "Bob"}
    event = _msg(
        11,
        "character",
        "Шёпот Бобу",
        character_id=1,
        location="room",
        visibility="targeted",
        targets=[2],
    )
    assert (
        witness_model.compute_mvp_presence(
            event, 2, names, viewer_location="street"
        )
        == "present"
    )
    # Author still sees own message
    assert (
        witness_model.compute_mvp_presence(
            event, 1, names, viewer_location="room"
        )
        == "present"
    )


def test_public_and_global_visible_everywhere():
    names = {1: "Alice", 2: "Bob"}
    public = _msg(12, "user", "Объявление", location="square", visibility="public")
    global_ev = _msg(13, "system", "Гром", location="sky", visibility="global")
    assert (
        witness_model.compute_mvp_presence(
            public, 2, names, viewer_location="basement"
        )
        == "present"
    )
    assert (
        witness_model.compute_mvp_presence(
            global_ev, 1, names, viewer_location="basement"
        )
        == "present"
    )


def test_information_transfer_via_telling():
    """Bob never sees the original private event, only Alice's later telling."""
    names = {1: "Alice", 2: "Bob"}
    secret = _msg(
        1,
        "user",
        "Ты красивая.",
        location="living_room",
        visibility="private",
        targets=[1],
    )
    telling = _msg(
        2,
        "character",
        "Игрок сказал, что я красивая.",
        character_id=1,
        location="street",
        visibility="local",
    )
    history = [secret, telling]
    bob_text = witness_model.filter_history_for_character(
        history,
        viewer_character_id=2,
        character_names=names,
        max_len=20,
        viewer_location="street",
    )
    assert "Ты красивая." not in bob_text
    assert "Игрок сказал, что я красивая." in bob_text


def test_empty_locations_backward_compatible():
    """Legacy chats with empty locations keep shared-scene behavior."""
    names = {1: "A", 2: "B"}
    event = _msg(1, "user", "Hello")
    assert witness_model.compute_mvp_presence(event, 1, names) == "present"
    assert witness_model.compute_mvp_presence(event, 2, names) == "present"


def test_compute_and_save_presence_uses_locations(db_session, chat):
    crud.update_chat(
        db_session,
        chat.id,
        schemas.ChatUpdate(player_location="living_room"),
    )
    a = crud.create_character(
        db_session,
        chat.id,
        schemas.CharacterCreate(name="Alice", location="living_room", order_index=1),
    )
    b = crud.create_character(
        db_session,
        chat.id,
        schemas.CharacterCreate(name="Bob", location="street", order_index=2),
    )
    msg = crud.create_message(
        db_session,
        schemas.MessageCreate(
            chat_id=chat.id,
            role="user",
            content="Ты красивая.",
            location="living_room",
            visibility="local",
        ),
    )
    result = crud.compute_and_save_presence_for_message(
        db_session, msg, [a, b], {a.id: a.name, b.id: b.name}
    )
    assert result[a.id] == "present"
    assert result[b.id] == "absent"
    presence = crud.get_presence_map(db_session, [msg.id], b.id)
    assert presence[msg.id] == "absent"


@pytest.mark.asyncio
async def test_sequential_generation_respects_locations(db_session, chat, mock_client=None):
    """Character B must not receive Alice's reply when in another location."""
    client = httpx.AsyncClient(base_url="http://test")
    crud.update_chat(
        db_session, chat.id, schemas.ChatUpdate(player_location="living_room")
    )
    alice = crud.create_character(
        db_session,
        chat.id,
        schemas.CharacterCreate(
            name="Alice", location="living_room", order_index=1
        ),
    )
    bob = crud.create_character(
        db_session,
        chat.id,
        schemas.CharacterCreate(name="Bob", location="street", order_index=2),
    )

    captured_history: dict[str, str] = {}

    async def fake_generate(**kwargs):
        character = kwargs["character"]
        history_text = ollama_client.format_history_for_character(
            kwargs["messages_history"],
            kwargs.get("max_history_length", 30),
            character.name,
            kwargs.get("character_names"),
            viewer_character_id=kwargs.get("viewer_character_id"),
            presence_map=kwargs.get("presence_map"),
            same_round_message_ids=kwargs.get("same_round_message_ids"),
            viewer_location=kwargs.get("viewer_location"),
            character_locations=kwargs.get("character_locations"),
        )
        captured_history[character.name] = history_text
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
            client,
            db_session,
            chat.id,
            "Ты красивая.",
        ):
            pass

    assert "Ты красивая." in captured_history["Alice"]
    assert "Ты красивая." not in captured_history["Bob"]
    # Bob must not see Alice's same-round reaction either
    assert "Reply from Alice" not in captured_history["Bob"]

    # Presence rows persisted
    messages = crud.get_messages_by_chat(db_session, chat.id)
    user_msg = next(m for m in messages if m.role == "user")
    alice_msg = next(
        m for m in messages if m.role == "character" and m.character_id == alice.id
    )
    bob_map = crud.get_presence_map(
        db_session, [user_msg.id, alice_msg.id], bob.id
    )
    assert bob_map[user_msg.id] == "absent"
    assert bob_map[alice_msg.id] == "absent"


def test_can_character_perceive_event_api():
    presence, reason = perception.can_character_perceive_event(
        viewer_character_id=2,
        viewer_location="street",
        event={
            "role": "user",
            "character_id": None,
            "location": "living_room",
            "visibility": "local",
            "target_character_ids": [],
            "content": "hi",
        },
    )
    assert presence == "absent"
    assert reason == "DIFFERENT_LOCATION"

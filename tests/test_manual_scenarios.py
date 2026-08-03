"""Sprint 7: programmatic verification of manual scenarios §23 (1-5).

Plans/locations2.md §23 describes manual scenarios that confirm invariants §24
and role isolation §23.5. These tests drive the real engine path
(``process_user_message_streaming``) with a deterministic fake LLM and assert
the same observable behavior a human would check in the UI.
"""

from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest

from app import chat_engine
from app import crud
from app import perception
from app import role_isolation
from app import schemas


async def _run_in_current_thread(func, /, *args, **kwargs):
    return func(*args, **kwargs)


@pytest.fixture
def mock_client():
    return httpx.AsyncClient(base_url="http://test")


async def _run_round(db_session, chat_id, text, mock_client, fake_generate):
    with patch(
        "app.chat_engine.ollama_client.generate", side_effect=fake_generate
    ), patch("app.chat_engine.asyncio.create_task"), patch(
        "app.chat_engine.asyncio.to_thread", side_effect=_run_in_current_thread
    ):
        async for _ in chat_engine.process_user_message_streaming(
            mock_client, db_session, chat_id, text
        ):
            pass


def _capture_setup(captured_history, captured_prior, captured_isolated):
    async def fake_generate(**kwargs):
        char = kwargs["character"]
        captured_history[char.name] = (
            chat_engine.ollama_client.format_history_for_character(
                kwargs["messages_history"],
                kwargs.get("max_history_length", 30),
                char.name,
                kwargs.get("character_names"),
                viewer_character_id=kwargs.get("viewer_character_id"),
                presence_map=kwargs.get("presence_map"),
                viewer_location=kwargs.get("viewer_location"),
                character_locations=kwargs.get("character_locations"),
            )
        )
        captured_prior[char.name] = [
            name for name, _ in kwargs.get("prior_replies") or []
        ]
        captured_isolated[char.name] = kwargs.get("is_isolated", False)
        yield {"type": "response", "text": f"Ответ {char.name} достаточно длинный."}

    return fake_generate


async def _setup_living_room_plus_kitchen(db_session, chat):
    await crud.update_chat(
        db_session, chat.id, schemas.ChatUpdate(player_location="living_room")
    )
    await crud.create_character(
        db_session, chat.id,
        schemas.CharacterCreate(name="Anna", location="living_room", order_index=1),
    )
    await crud.create_character(
        db_session, chat.id,
        schemas.CharacterCreate(name="Boris", location="living_room", order_index=2),
    )
    await crud.create_character(
        db_session, chat.id,
        schemas.CharacterCreate(name="Viktor", location="kitchen", order_index=3),
    )


@pytest.mark.asyncio
async def test_scenario_1_2_same_room_interact_and_kitchen_hidden(db_session, chat, mock_client):
    """§23.1: living-room NPCs talk to each other and the player.
    §23.2: Viktor in the kitchen does not see the living-room conversation."""
    await _setup_living_room_plus_kitchen(db_session, chat)

    captured_history: dict[str, str] = {}
    captured_prior: dict[str, list[str]] = {}
    captured_isolated: dict[str, bool] = {}
    fake = _capture_setup(captured_history, captured_prior, captured_isolated)

    await _run_round(db_session, chat.id, "Всем привет в гостиной!", mock_client, fake)

    # §23.1: Anna and Boris see the player and each other.
    assert "Игрок: Всем привет в гостиной!" in captured_history["Anna"]
    assert "Игрок: Всем привет в гостиной!" in captured_history["Boris"]
    assert captured_prior["Boris"] == ["Anna"]

    # §23.2: Viktor (kitchen) sees none of the living-room scene.
    assert captured_prior["Viktor"] == []
    assert "Anna" not in captured_history["Viktor"]
    assert "Boris" not in captured_history["Viktor"]
    assert "Всем привет в гостиной!" not in captured_history["Viktor"]


@pytest.mark.asyncio
async def test_scenario_3_npc_scene_continues_without_player(db_session, chat, mock_client):
    """§23.3: player moves to the kitchen; living-room NPCs keep their own scene."""
    await _setup_living_room_plus_kitchen(db_session, chat)

    captured_history: dict[str, str] = {}
    captured_prior: dict[str, list[str]] = {}
    captured_isolated: dict[str, bool] = {}
    fake = _capture_setup(captured_history, captured_prior, captured_isolated)
    await _run_round(db_session, chat.id, "Всем привет!", mock_client, fake)

    # Player walks to the kitchen.
    await crud.update_chat(
        db_session, chat.id, schemas.ChatUpdate(player_location="kitchen")
    )

    captured2_prior: dict[str, list[str]] = {}
    captured2_isolated: dict[str, bool] = {}

    async def fake_round2(**kwargs):
        char = kwargs["character"]
        captured2_prior[char.name] = [
            name for name, _ in kwargs.get("prior_replies") or []
        ]
        captured2_isolated[char.name] = kwargs.get("is_isolated", False)
        yield {"type": "response", "text": f"Ответ {char.name} второй раунд."}

    await _run_round(db_session, chat.id, "Я на кухне.", mock_client, fake_round2)

    # Living-room NPCs still interact without the player.
    assert captured2_prior["Boris"] == ["Anna"]
    assert captured2_isolated["Anna"] is False
    assert captured2_isolated["Boris"] is False
    # Viktor now has the player in the kitchen — not isolated either.
    assert captured2_isolated["Viktor"] is False


@pytest.mark.asyncio
async def test_scenario_4_remote_message_across_locations(db_session, chat, mock_client):
    """§23.4: message to Vasily in another location via a remote channel."""
    await crud.update_chat(
        db_session, chat.id, schemas.ChatUpdate(player_location="living_room")
    )
    vasily = await crud.create_character(
        db_session, chat.id,
        schemas.CharacterCreate(name="Vasily", location="garden", order_index=1),
    )

    event = {
        "role": "user",
        "character_id": None,
        "location": "living_room",
        "visibility": "local",
        "channel": "messenger",
        "target_character_ids": [vasily.id],
        "content": "Василий, когда выйдешь?",
    }
    presence, reason = perception.can_character_perceive_event(
        viewer_character_id=vasily.id,
        viewer_location="garden",
        event=event,
        viewer_name="Vasily",
    )
    assert presence == "present"
    assert reason == "REMOTE_CHANNEL_MESSENGER"


@pytest.mark.asyncio
async def test_scenario_5_speaker_isolation(db_session, chat, mock_client):
    """§23.5: speaker isolation — a character never speaks as another NPC."""
    await _setup_living_room_plus_kitchen(db_session, chat)

    result = role_isolation.sanitize_and_validate_response(
        "Анна: Борис, ты куда?\nБорис: На кухню, а потом вернусь.",
        "Борис",
        ["Анна", "Борис"],
    )
    # Response starting with a foreign speaker marker is truncated to empty (invalid).
    assert result.cleaned_text == ""
    assert result.is_valid is False

    # A response that begins with the current character's own prefix keeps its content.
    ok = role_isolation.sanitize_and_validate_response(
        "Борис: На кухню, а потом вернусь.",
        "Борис",
        ["Анна", "Борис"],
    )
    assert "Борис:" not in ok.cleaned_text
    assert ok.cleaned_text == "На кухню, а потом вернусь."
    assert ok.is_valid is True

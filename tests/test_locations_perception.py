"""Sprint 4 tests (Plans/locations2.md §22, items 1-4, 7, 9).

Per-character history views and per-viewer prior-reply filtering via the
unified perception mechanism (``can_character_perceive_event``).
"""

from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest

from app import chat_engine
from app import crud
from app import perception
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


def _reply_event(char):
    yield {"type": "response", "text": f"Ответ {char.name} с достаточной длиной."}


@pytest.mark.asyncio
async def test_same_location_sees_alt_reply_prior_reply(db_session, chat, mock_client):
    """§22 item 1: A + B in one location -> B's effective prior replies contain A's."""
    await crud.update_chat(
        db_session, chat.id, schemas.ChatUpdate(player_location="kitchen")
    )
    a = await crud.create_character(
        db_session, chat.id,
        schemas.CharacterCreate(name="Anna", location="living_room", order_index=1),
    )
    b = await crud.create_character(
        db_session, chat.id,
        schemas.CharacterCreate(name="Boris", location="living_room", order_index=2),
    )

    captured: dict[str, list[tuple[str, str]]] = {}

    async def fake_generate(**kwargs):
        char = kwargs["character"]
        captured[char.name] = list(kwargs.get("prior_replies") or [])
        yield {"type": "response", "text": f"Ответ {char.name} с достаточной длиной."}

    await _run_round(db_session, chat.id, "Всем привет", mock_client, fake_generate)

    # Anna (first) has no prior replies.
    assert captured["Anna"] == []
    # Boris shares a location with Anna -> perceives Anna's reply.
    assert len(captured["Boris"]) == 1
    assert captured["Boris"][0][0] == "Anna"


@pytest.mark.asyncio
async def test_three_same_location_all_see_valid(db_session, chat, mock_client):
    """§22 item 2: A+B+C in one location each sees the valid replies of prior NPCs."""
    for i, name in enumerate(["Anna", "Boris", "Vera"], start=1):
        await crud.create_character(
            db_session, chat.id,
            schemas.CharacterCreate(name=name, location="hall", order_index=i),
        )

    captured: dict[str, list[str]] = {}

    async def fake_generate(**kwargs):
        char = kwargs["character"]
        captured[char.name] = [name for name, _ in kwargs.get("prior_replies") or []]
        yield {"type": "response", "text": f"Ответ {char.name} длины."}

    await _run_round(db_session, chat.id, "Всем привет!", mock_client, fake_generate)

    assert captured["Anna"] == []
    assert captured["Boris"] == ["Anna"]
    assert captured["Vera"] == ["Anna", "Boris"]


@pytest.mark.asyncio
async def test_cross_location_hidden_from_prior_replies(db_session, chat, mock_client):
    """§22 item 3: C in location_2 must NOT see events / prior replies of location_1."""
    await crud.update_chat(
        db_session, chat.id, schemas.ChatUpdate(player_location="kitchen")
    )
    await crud.create_character(
        db_session, chat.id,
        schemas.CharacterCreate(name="Anna", location="living_room", order_index=1),
    )
    await crud.create_character(
        db_session, chat.id,
        schemas.CharacterCreate(name="Boris", location="living_room", order_index=2),
    )
    c = await crud.create_character(
        db_session, chat.id,
        schemas.CharacterCreate(name="Cid", location="garden", order_index=3),
    )

    captured_history: dict[str, str] = {}
    captured_prior: dict[str, list[str]] = {}

    async def fake_generate(**kwargs):
        char = kwargs["character"]
        history = chat_engine.ollama_client.format_history_for_character(
            kwargs["messages_history"],
            kwargs.get("max_history_length", 30),
            char.name,
            kwargs.get("character_names"),
            viewer_character_id=kwargs.get("viewer_character_id"),
            presence_map=kwargs.get("presence_map"),
            viewer_location=kwargs.get("viewer_location"),
            character_locations=kwargs.get("character_locations"),
        )
        captured_history[char.name] = history
        captured_prior[char.name] = [
            name for name, _ in kwargs.get("prior_replies") or []
        ]
        yield {"type": "response", "text": f"Ответ {char.name} достаточной длины."}

    await _run_round(db_session, chat.id, "Всем привет в гостиной!", mock_client, fake_generate)

    # Generation order is Anna -> Boris -> Cid.
    # Anna (first) has no prior replies yet; Boris (same room) sees Anna; Cid (garden)
    # must not see anything from the living_room nor prior replies.
    assert captured_prior["Anna"] == []
    assert captured_prior["Boris"] == ["Anna"]
    # Cid (garden) must not see events or prior replies from the living_room.
    assert captured_prior["Cid"] == []
    assert "Boris" not in captured_history["Cid"]
    assert "Игрок" not in captured_history["Cid"]

    # Witness presence rows: Cid absent for living_room events (excluding his own).
    msgs = await crud.get_messages_by_chat(db_session, chat.id)
    living_ids = [
        m.id for m in msgs
        if m.role == "character" and m.character_id != c.id
    ]
    cid_map = await crud.get_presence_map(db_session, living_ids, c.id)
    assert all(v == "absent" for v in cid_map.values())


@pytest.mark.asyncio
async def test_move_recalculates_perception(db_session, chat, mock_client):
    """§22 item 4 + §19: moving A recomputes its view (context & perception)."""
    await crud.update_chat(
        db_session, chat.id, schemas.ChatUpdate(player_location="kitchen")
    )
    a = await crud.create_character(
        db_session, chat.id,
        schemas.CharacterCreate(name="Anna", location="living_room", order_index=1),
    )
    await crud.create_character(
        db_session, chat.id,
        schemas.CharacterCreate(name="Boris", location="living_room", order_index=2),
    )

    async def fake_generate(**kwargs):
        char = kwargs["character"]
        yield {"type": "response", "text": f"Ответ {char.name} длины."}

    # Round 1: Anna in living_room.
    await _run_round(db_session, chat.id, "Привет!", mock_client, fake_generate)

    # Move Anna to the garden.
    await crud.update_character(
        db_session, a.id, schemas.CharacterUpdate(location="garden")
    )
    a2 = await crud.get_character(db_session, a.id)

    # Round 2: Anna (now in garden) must NOT perceive Boris's (living_room) reply.
    captured_prior: dict[str, list[str]] = {}

    async def fake_generate_round2(**kwargs):
        char = kwargs["character"]
        captured_prior[char.name] = [
            name for name, _ in kwargs.get("prior_replies") or []
        ]
        yield {"type": "response", "text": f"Ответ {char.name} второй."}

    with patch(
        "app.chat_engine.ollama_client.generate", side_effect=fake_generate_round2
    ), patch("app.chat_engine.asyncio.create_task"), patch(
        "app.chat_engine.asyncio.to_thread", side_effect=_run_in_current_thread
    ):
        async for _ in chat_engine.process_user_message_streaming(
            mock_client, db_session, chat.id, "Что нового?"
        ):
            pass

    # Anna, moved away, no longer perceives Boris (still in living_room).
    assert captured_prior["Anna"] == []
    assert a2 is not None


@pytest.mark.asyncio
async def test_audible_prior_reply_reaches_next_npc(db_session, chat, mock_client):
    """§18 item 5 + §10: an audible knock from an adjacent location surfaces as
    a sensory line in the next NPC's prior replies (no full-content leak)."""
    await crud.create_location(
        db_session, chat.id,
        schemas.LocationCreate(name="Кухня", adjacent_to=["Гостиная"]),
    )
    await crud.create_location(
        db_session, chat.id,
        schemas.LocationCreate(name="Гостиная", adjacent_to=["Кухня"]),
    )
    await crud.create_character(
        db_session, chat.id,
        schemas.CharacterCreate(name="Аня", location="Кухня", order_index=1),
    )
    await crud.create_character(
        db_session, chat.id,
        schemas.CharacterCreate(name="Борис", location="Гостиная", order_index=2),
    )

    captured: dict[str, list[tuple[str, str]]] = {}

    async def fake_generate(**kwargs):
        char = kwargs["character"]
        captured[char.name] = list(kwargs.get("prior_replies") or [])
        if char.name == "Аня":
            yield {"type": "response", "text": "Я стучу в дверь, кто-нибудь дома?"}
        else:
            yield {"type": "response", "text": f"Ответ {char.name} с достаточной длиной."}

    await _run_round(db_session, chat.id, "Всем привет", mock_client, fake_generate)

    # Boris is in the adjacent Гостиная: Anna's knock reaches him as audible.
    anna_lines = [line for name, line in captured["Борис"] if name == "Аня"]
    assert anna_lines, "Boris should hear Anna's knock as a prior reply"
    assert "стук" in anna_lines[0]
    # Full content must NOT leak into the audible line (§7/§8).
    assert "кто-нибудь дома" not in anna_lines[0]


@pytest.mark.asyncio
async def test_remote_channel_bridges_locations(db_session, chat, mock_client):
    """§22 item 9: a targeted remote event reaches a character in another location."""
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
async def test_co_located_viewer_perceives_in_person_ignoring_remote_channel():
    """Isolation hardening: a viewer in the SAME location as the author hears the
    speech in person even when the message carries a remote channel label."""
    event = {
        "role": "character",
        "character_id": 10,
        "location": "onsen",
        "visibility": "local",
        "channel": "magic",
        "target_character_ids": [99],
        "content": "Наслаждаюсь горячим источником",
    }
    presence, reason = perception.can_character_perceive_event(
        viewer_character_id=11,
        viewer_location="onsen",
        event=event,
        viewer_name="A",
    )
    assert presence == "present"
    assert reason == "SAME_LOCATION"


def test_remote_channel_requires_named_addressee():
    """A keyword alone (магия/звонок/сообщение) is not a remote channel: without a
    named addressee the reply stays in-person ``direct`` (isolation hardening)."""
    names = {26: "Анастасия", 27: "Елизавета", 28: "Кирк"}
    # "магией" is the fantasy world, not a magic-channel call.
    ch, targets = chat_engine._detect_communication_channel(
        "Она оглядела зал и усмехнулась, сверкая магией в глазах.",
        "Анастасия",
        names,
    )
    assert ch == "direct"
    assert targets == []
    # A real remote call must name the addressee.
    ch2, targets2 = chat_engine._detect_communication_channel(
        "Кирк, ты слышишь меня? Звоню по магической связи.",
        "Анастасия",
        names,
    )
    assert ch2 == "magic"
    assert targets2 == [28]
"""Sprint 3 tests (Plans/isolation-fix.md §18, items 17-20): stimuli.

Stimuli are metadata attached to messages, never separate DB entities. They
are extracted at message creation and consumed by the perception system.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest

from app import chat_engine
from app import crud
from app import perception
from app import schemas
from app.stimuli import (
    Stimulus,
    build_audible_line,
    extract_stimuli,
    has_stimulus,
    parse_stimuli,
    serialize_stimuli,
)


def _types(text: str, names: list[str]) -> set[str]:
    return {s.type for s in extract_stimuli(text, names)}


# ------------------------------ unit: extraction ------------------------------
def test_knock_stimulus():
    """§18 item 17: «стучу в дверь» → stimulus knock."""
    assert "knock" in _types("Борис громко стучит в дверь.", ["Борис", "Ольга"])
    assert "knock" in _types("Слышен стук из соседней комнаты.", [])
    assert "knock" in _types("Кто-то постучал.", [])


def test_address_stimulus():
    """§18 item 18: «Ольга, ты дома?» → stimulus address (target = Ольга)."""
    stimuli = extract_stimuli("Ольга, ты дома?", ["Борис", "Ольга"])
    assert any(
        s.type == "address" and s.target_character == "Ольга" for s in stimuli
    )


def test_call_stimulus():
    stimuli = extract_stimuli("Ольга! Я зову тебя!", ["Борис", "Ольга"])
    assert any(s.type == "call" for s in stimuli)


def test_shout_and_loud_sound():
    types = _types("Кто-то громко кричит и шумит на весь дом.", [])
    assert "shout" in types
    assert "loud_sound" in types


def test_no_address_for_simple_narration():
    """Повествование с именем без звательной конструкции → НЕ address."""
    assert "address" not in _types("Вчера Антон ходил в магазин.", ["Антон"])


def test_stimulus_serialization_roundtrip():
    stimuli = [
        Stimulus(type="knock", audibility="high"),
        Stimulus(type="address", target_character="Ольга"),
    ]
    raw = serialize_stimuli(stimuli)
    parsed = parse_stimuli(raw)
    assert len(parsed) == 2
    assert parsed[0].type == "knock"
    assert parsed[1].target_character == "Ольга"
    # Serialize a plain dict list too (schema path)
    assert parse_stimuli(serialize_stimuli([{"type": "shout"}]))[0].type == "shout"


def test_has_stimulus():
    assert has_stimulus(serialize_stimuli([Stimulus(type="knock")]), "knock")
    assert not has_stimulus("[]", "knock")


def test_build_audible_line_uses_stimuli():
    msg = SimpleNamespace(
        content="Ольга спрятала ключ и шумит за стеной.",
        stimuli=[{"type": "loud_sound", "audibility": "high"}],
    )
    line = build_audible_line(msg)
    assert "ключ" not in line
    assert "громкий звук" in line


# ------------------------------ integration ------------------------------
async def _run_in_current_thread(func, /, *args, **kwargs):
    return func(*args, **kwargs)


@pytest.fixture
def mock_client():
    return httpx.AsyncClient(base_url="http://test")


@pytest.mark.asyncio
async def test_stimuli_do_not_create_extra_messages(db_session, chat, mock_client):
    """§18 item 19: стимул не создаёт отдельное сообщение в БД."""
    await crud.update_chat(
        db_session, chat.id, schemas.ChatUpdate(player_location="living_room")
    )
    await crud.create_character(
        db_session,
        chat.id,
        schemas.CharacterCreate(name="Ольга", location="living_room", order_index=1),
    )

    before = len(await crud.get_messages_by_chat(db_session, chat.id))

    async def fake_generate(**kwargs):
        yield {
            "type": "response",
            "text": "Ольга, ты здесь? Я стучу в дверь.",
        }

    with patch(
        "app.chat_engine.ollama_client.generate", side_effect=fake_generate
    ), patch("app.chat_engine.asyncio.create_task"), patch(
        "app.chat_engine.asyncio.to_thread", side_effect=_run_in_current_thread
    ):
        async for _ in chat_engine.process_user_message_streaming(
            mock_client, db_session, chat.id, "Привет!"
        ):
            pass

    after = await crud.get_messages_by_chat(db_session, chat.id)
    # user message + 1 character reply only; stimuli ride on those messages.
    assert len(after) == before + 2


@pytest.mark.asyncio
async def test_user_message_stimuli_persisted(db_session, chat, mock_client):
    """Stimuli extracted from user text are saved in messages.stimuli."""
    await crud.update_chat(
        db_session, chat.id, schemas.ChatUpdate(player_location="living_room")
    )
    await crud.create_character(
        db_session,
        chat.id,
        schemas.CharacterCreate(name="Ольга", location="living_room", order_index=1),
    )

    async def fake_generate(**kwargs):
        yield {"type": "response", "text": "Отвечаю."}

    with patch(
        "app.chat_engine.ollama_client.generate", side_effect=fake_generate
    ), patch("app.chat_engine.asyncio.create_task"), patch(
        "app.chat_engine.asyncio.to_thread", side_effect=_run_in_current_thread
    ):
        async for _ in chat_engine.process_user_message_streaming(
            mock_client, db_session, chat.id, "Я стучу в дверь, Ольга!"
        ):
            pass

    messages = await crud.get_messages_by_chat(db_session, chat.id)
    user_msg = next(m for m in messages if m.role == "user")
    user_stimuli = parse_stimuli(user_msg.stimuli)
    assert any(s.type == "knock" for s in user_stimuli)
    assert any(
        s.type == "address" and s.target_character == "Ольга" for s in user_stimuli
    )


@pytest.mark.asyncio
async def test_stimulus_reaches_perception(db_session, chat):
    """§18 item 20: стимул доступен perception-системе (address → MENTIONED)."""
    olga = await crud.create_character(
        db_session,
        chat.id,
        schemas.CharacterCreate(name="Ольга", location="living_room", order_index=1),
    )
    boris = await crud.create_character(
        db_session,
        chat.id,
        schemas.CharacterCreate(name="Борис", location="kitchen", order_index=2),
    )
    msg = await crud.create_message(
        db_session,
        schemas.MessageCreate(
            chat_id=chat.id,
            role="character",
            character_id=boris.id,
            content="Ольга, ты дома?",
            location="kitchen",
            stimuli=[{"type": "address", "target_character": "Ольга"}],
        ),
    )
    event = perception.event_from_message(msg)
    presence, reason = perception.can_character_perceive_event(
        viewer_character_id=olga.id,
        viewer_location="living_room",
        event=event,
        viewer_name="Ольга",
        adjacency_index={"living_room": {"kitchen"}},
    )
    assert presence == "mentioned"
    assert reason == "MENTIONED_ADDRESS"

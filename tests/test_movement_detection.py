"""Sprint 4 tests (Plans/isolation-fix.md §18 items 11-16, §9-§11).

Deterministic movement detection (``app/movement.py``) and its integration into
``process_user_message_streaming``: the new location is applied before the next
NPC generates and is persisted to the DB.
"""

from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest

from app import chat_engine
from app import crud
from app import schemas
from app.movement import detect_character_movement


def _run_in_current_thread(func, /, *args, **kwargs):
    return func(*args, **kwargs)


KNOWN = ["Гостиная", "Кухня", "Коридор", "Магазин"]
CLOCS = {1: "Гостиная", 2: "Кухня"}
CNAMES = {1: "Анна", 2: "Борис"}


class TestDetectCharacterMovement:
    """§18 items 11-15 (unit-level)."""

    def test_arrival_to_explicit_location(self):
        # item 11: «Я вошёл в кухню» → Кухня
        assert detect_character_movement("Я вошёл в кухню", "Анна", KNOWN, CLOCS, CNAMES) == "Кухня"

    def test_arrival_verb_variants(self):
        for text in (
            "Я пошёл в коридор",
            "Я вышел в коридор",
            "Направился на кухню",
            "Захожу в гостиную",
        ):
            assert detect_character_movement(text, "Анна", KNOWN, CLOCS, CNAMES), text

    def test_departure_without_target_no_change(self):
        # item 12: «Я вышел из комнаты» (нет цели) → без изменений
        assert detect_character_movement("Я вышел из комнаты", "Анна", KNOWN, CLOCS, CNAMES) is None

    def test_intent_no_change(self):
        # item 13: «Я хочу пойти в кухню» → без изменений
        assert detect_character_movement("Я хочу пойти в кухню", "Анна", KNOWN, CLOCS, CNAMES) is None
        assert detect_character_movement("Я пойду в кухню", "Анна", KNOWN, CLOCS, CNAMES) is None
        assert detect_character_movement("Я собираюсь в магазин", "Анна", KNOWN, CLOCS, CNAMES) is None

    def test_negation_no_change(self):
        # item 14: «Я не пошёл в кухню» → без изменений
        assert detect_character_movement("Я не пошёл в кухню", "Анна", KNOWN, CLOCS, CNAMES) is None
        assert detect_character_movement("Я не зашла к Борису", "Анна", KNOWN, CLOCS, CNAMES) is None

    def test_memory_no_change(self):
        # item 15: «Я вспоминаю, как ходил в магазин» → без изменений
        assert detect_character_movement("Я вспоминаю, как ходил в магазин", "Анна", KNOWN, CLOCS, CNAMES) is None
        assert detect_character_movement("Вчера я ходила в магазин", "Анна", KNOWN, CLOCS, CNAMES) is None

    def test_conditional_no_change(self):
        assert detect_character_movement("Я бы пошёл в магазин", "Анна", KNOWN, CLOCS, CNAMES) is None

    def test_arrival_to_character_resolves_their_location(self):
        assert detect_character_movement("Я зашла к Борису", "Анна", KNOWN, CLOCS, CNAMES) == "Кухня"
        assert detect_character_movement("Я вошёл к Борису", "Анна", KNOWN, CLOCS, CNAMES) == "Кухня"

    def test_arrival_to_character_requires_arrival_verb(self):
        # «подошёл к Борису» — не прибытие (§11)
        assert detect_character_movement("Я подошёл к Борису", "Анна", KNOWN, CLOCS, CNAMES) is None

    def test_no_movement_at_all(self):
        assert detect_character_movement("Я стою у окна и смотрю на дождь.", "Анна", KNOWN, CLOCS, CNAMES) is None

    def test_empty_destination_scene_not_triggered(self):
        clocs_empty = {1: "", 2: "Кухня"}
        assert detect_character_movement("Я зашёл в коридор", "Анна", KNOWN, clocs_empty, CNAMES) == "Коридор"


@pytest.fixture
def mock_client():
    return httpx.AsyncClient(base_url="http://test")


async def _run_round(db_session, chat_id, text, mock_client, fake_generate):
    with patch(
        "app.chat_engine.ollama_client.generate", side_effect=fake_generate
    ), patch("app.chat_engine.ollama_client.extract_scene_state", return_value={}), patch(
        "app.chat_engine.asyncio.create_task"
    ), patch("app.chat_engine.asyncio.to_thread", side_effect=_run_in_current_thread):
        async for _ in chat_engine.process_user_message_streaming(
            mock_client, db_session, chat_id, text
        ):
            pass


@pytest.mark.asyncio
async def test_movement_updates_db_before_next_npc(db_session, chat, mock_client):
    """§18 item 16: движение обновляет БД до генерации следующего NPC.

    Anna (living_room) moves to the kitchen; Boris (already in the kitchen)
    must see the updated location during his generation in the same round.
    """
    await crud.update_chat(
        db_session, chat.id, schemas.ChatUpdate(player_location="Кухня")
    )
    anna = await crud.create_character(
        db_session, chat.id,
        schemas.CharacterCreate(name="Анна", location="Гостиная", order_index=1),
    )
    await crud.create_character(
        db_session, chat.id,
        schemas.CharacterCreate(name="Борис", location="Кухня", order_index=2),
    )

    captured: dict[str, dict] = {}

    async def fake_generate(**kwargs):
        char = kwargs["character"]
        captured[char.name] = {
            "locs": dict(kwargs.get("character_locations") or {}),
            "prior": [n for n, _ in kwargs.get("prior_replies") or []],
        }
        if char.name == "Анна":
            yield {"type": "response", "text": "Я вошла в кухню. Привет, Борис!"}
        else:
            yield {"type": "response", "text": "Ответ Бориса с достаточной длиной."}

    await _run_round(db_session, chat.id, "Всем привет", mock_client, fake_generate)

    # Boris's generation ran AFTER Anna moved — the engine passed the new location.
    assert captured["Борис"]["locs"][anna.id] == "Кухня"
    # Boris, now sharing the kitchen with Anna, perceives her reply.
    assert "Анна" in captured["Борис"]["prior"]

    # The movement was persisted to the DB.
    anna_db = await crud.get_character(db_session, anna.id)
    assert anna_db is not None
    assert anna_db.location == "Кухня"

    # A global system message announcing the move was emitted.
    msgs = await crud.get_messages_by_chat(db_session, chat.id)
    sys_msgs = [m for m in msgs if m.role == "system"]
    assert any("переместился" in (m.content or "") for m in sys_msgs)


@pytest.mark.asyncio
async def test_no_movement_keeps_location(db_session, chat, mock_client):
    """Intent-only text must NOT change the character's location."""
    await crud.update_chat(
        db_session, chat.id, schemas.ChatUpdate(player_location="Кухня")
    )
    anna = await crud.create_character(
        db_session, chat.id,
        schemas.CharacterCreate(name="Анна", location="Гостиная", order_index=1),
    )
    await crud.create_character(
        db_session, chat.id,
        schemas.CharacterCreate(name="Борис", location="Кухня", order_index=2),
    )

    async def fake_generate(**kwargs):
        char = kwargs["character"]
        if char.name == "Анна":
            yield {"type": "response", "text": "Я хочу пойти в кухню."}
        else:
            yield {"type": "response", "text": "Ответ Бориса с достаточной длиной."}

    await _run_round(db_session, chat.id, "Всем привет", mock_client, fake_generate)

    anna_db = await crud.get_character(db_session, anna.id)
    assert anna_db is not None
    assert anna_db.location == "Гостиная"

    msgs = await crud.get_messages_by_chat(db_session, chat.id)
    assert not any(m.role == "system" for m in msgs)

"""Синхронизация локации игрока: chats.player_location <-> player-персонаж.

Локация игрока живёт в двух местах UI: правая панель ("Локация игрока",
chats.player_location) и карточка игрока ("Локация", location player-персонажа).
Оба значения попадают в промпты (presence/isolation и блок сцены), поэтому
должны оставаться одинаковыми.
"""

from app import crud, schemas


async def _setup(db, player_location: str = "") -> tuple:
    chat = await crud.create_chat(
        db,
        schemas.ChatCreate(
            name="Test Chat",
            general_prompt="scene",
            player_location=player_location,
        ),
    )
    player = await crud.create_player_character(db, chat.id, name="Игрок")
    return chat, player


async def test_create_player_character_init_from_chat_location(db_session):
    chat, player = await _setup(db_session, player_location="Таверна")
    assert chat.player_location == "Таверна"
    assert player.location == "Таверна"


async def test_update_chat_syncs_player_character_location(db_session):
    chat, player = await _setup(db_session)
    assert player.location == ""

    updated = await crud.update_chat(
        db_session, chat.id, schemas.ChatUpdate(player_location="Кухня")
    )
    assert updated.player_location == "Кухня"

    fresh_player = await crud.get_character(db_session, player.id)
    assert fresh_player.location == "Кухня"


async def test_update_chat_resolves_location_id(db_session):
    chat = await crud.create_chat(
        db_session, schemas.ChatCreate(name="Test Chat", general_prompt="scene")
    )
    await crud.create_location(db_session, chat.id, schemas.LocationCreate(name="Кухня"))
    player = await crud.create_player_character(db_session, chat.id)

    await crud.update_chat(db_session, chat.id, schemas.ChatUpdate(player_location="Кухня"))

    fresh_player = await crud.get_character(db_session, player.id)
    assert fresh_player.location == "Кухня"
    assert fresh_player.location_id is not None


async def test_update_character_location_syncs_chat(db_session):
    chat, player = await _setup(db_session)

    await crud.update_character_location(db_session, player.id, "Сад")

    fresh_chat = await crud.get_chat(db_session, chat.id)
    assert fresh_chat.player_location == "Сад"


async def test_update_character_syncs_chat_for_player(db_session):
    chat, player = await _setup(db_session)

    await crud.update_character(
        db_session, player.id, schemas.CharacterUpdate(location="Гостиная")
    )

    fresh_chat = await crud.get_chat(db_session, chat.id)
    assert fresh_chat.player_location == "Гостиная"


async def test_npc_location_update_does_not_touch_chat(db_session):
    chat, _player = await _setup(db_session, player_location="Таверна")
    npc = await crud.create_character(
        db_session, chat.id, schemas.CharacterCreate(name="Alice", order_index=1)
    )

    await crud.update_character_location(db_session, npc.id, "Улица")

    fresh_chat = await crud.get_chat(db_session, chat.id)
    assert fresh_chat.player_location == "Таверна"

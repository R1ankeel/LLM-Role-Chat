"""WPE 3.0 (Plans/WPE.md) Фаза 0 — фундамент без изменения поведения.

Покрывает:
- резолвер строка -> location_id (написан, НЕ подключён) на реальных
  значениях локаций из текущих чатов (Новоселье/Студ/МММ);
- двухканальный ``perceive()`` (И13) — visual/audio/addressed/remote_status;
- контракт ``Action[]`` + tool/JSON-Schema (И14, §8);
- флаги ``WORLD_ENGINE_*`` (все False);
- миграцию: новые таблицы + ``characters.location_id``.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import inspect

from app import crud
from app import models
from app import perception
from app import schemas

# Реальные значения локаций из текущих чатов (ai_chat.db):
# чат 3 «Новоселье», чат 7 «Студ», чат 8 «МММ».
REAL_LOCATIONS = [
    # Новоселье
    "Комната Марины",
    "Коридор",
    "ванная комната",
    "гостиная",
    "комната Ольги",
    "кухня",
    "пустая спальня",
    # Студ
    "Аудитория Университета",
    "Дворик Университета",
    "Квартира Анны",
    "Квартира Антона и Инны",
    "Квартира Боба",
    "Коридор Университета",
    "Парк",
    "Туалет Университета",
    "Улицы",
    # МММ
    "Квартира Николая",
    "Квартира Ольги",
    "Улицы - Магазин",
    "Улицы - спортивная площадка",
]


def _loc_objs(names: list[str]) -> list[SimpleNamespace]:
    return [SimpleNamespace(name=n, adjacent_to="[]") for n in names]


# ---------------------------------------------------------------------------
# Резолвер строка -> location_id (чистый и async)
# ---------------------------------------------------------------------------

def test_resolver_exact_real_location():
    locations = _loc_objs(REAL_LOCATIONS)
    loc = crud.resolve_location_name(locations, "кухня")
    assert loc is not None
    assert loc.name == "кухня"


def test_resolver_case_insensitive_real_location():
    locations = _loc_objs(REAL_LOCATIONS)
    assert crud.resolve_location_name(locations, "КУХНЯ").name == "кухня"
    assert crud.resolve_location_name(locations, "КОРИДОР").name == "Коридор"
    assert crud.resolve_location_name(locations, "квартира ольги").name == "Квартира Ольги"


def test_resolver_synonym_normalization():
    locations = _loc_objs(REAL_LOCATIONS)
    assert crud.resolve_location_name(locations, "  Кухня  ").name == "кухня"


def test_resolver_unknown_returns_none():
    locations = _loc_objs(REAL_LOCATIONS)
    assert crud.resolve_location_name(locations, "Некоторой локации нет") is None
    assert crud.resolve_location_name(locations, "") is None


def test_resolver_shared_scene_returns_none():
    locations = _loc_objs(REAL_LOCATIONS)
    assert crud.resolve_location_name(locations, "") is None
    assert crud.resolve_location_name(locations, "Общая сцена") is None
    assert crud.resolve_location_name(locations, None) is None


def test_resolver_distinguishes_similar_real_names():
    # «Улицы» vs «Улицы - Магазин» — не должны коллидировать
    locations = _loc_objs(REAL_LOCATIONS)
    assert crud.resolve_location_name(locations, "Улицы").name == "Улицы"
    assert crud.resolve_location_name(locations, "Улицы - Магазин").name == "Улицы - Магазин"
    assert crud.resolve_location_name(locations, "Улицы - спортивная площадка").name == (
        "Улицы - спортивная площадка"
    )


@pytest.mark.asyncio
async def test_resolver_async_against_db(db_session, chat):
    for name in REAL_LOCATIONS[:5]:
        await crud.create_location(
            db_session, chat.id, schemas.LocationCreate(name=name)
        )
    loc = await crud.resolve_location_string(db_session, chat.id, "гостиная")
    assert loc is not None
    assert loc.name == "гостиная"
    assert await crud.resolve_location_string(db_session, chat.id, "нет такой") is None
    assert await crud.resolve_location_string(db_session, chat.id, "") is None


# ---------------------------------------------------------------------------
# perceive() — двухканальное восприятие (И13)
# ---------------------------------------------------------------------------

def _perceive(event, observer, adjacency=None, thread_deliveries=()):
    world = perception.PerceptionWorldState(
        adjacency=adjacency or {},
        thread_deliveries=frozenset(thread_deliveries),
    )
    return perception.perceive(world_state=world, event=event, observer=observer)


def test_perceive_same_location_full():
    res = _perceive(
        {"location": "кухня", "stimuli": [], "target_character_ids": []},
        {"location": "Кухня", "character_id": 1},
    )
    assert res.visual_level == "full"
    assert res.audio_level == "full"


def test_perceive_shared_scene_full():
    for loc in ("", "Общая сцена"):
        res = _perceive(
            {"location": loc, "stimuli": [], "target_character_ids": []},
            {"location": "Кухня", "character_id": 1},
        )
        assert (res.visual_level, res.audio_level) == ("full", "full")
    res = _perceive(
        {"location": "кухня", "stimuli": [], "target_character_ids": []},
        {"location": "", "character_id": 1},
    )
    assert (res.visual_level, res.audio_level) == ("full", "full")


def test_perceive_own_speech_full():
    res = _perceive(
        {"location": "кухня", "character_id": 5, "stimuli": [], "target_character_ids": []},
        {"location": "улицы", "character_id": 5},
    )
    assert (res.visual_level, res.audio_level) == ("full", "full")


def test_perceive_distant_none():
    res = _perceive(
        {"location": "Парк", "stimuli": [], "target_character_ids": []},
        {"location": "Кухня", "character_id": 1},
    )
    assert res.visual_level == "none"
    assert res.audio_level == "none"


def test_perceive_adjacent_default_edge_is_wall():
    # Ребро без явных значений: visual=none, audio=muffled (обратная совместимость)
    locs = [
        SimpleNamespace(name="Коридор", adjacent_to='["кухня"]'),
        SimpleNamespace(name="кухня", adjacent_to="[]"),
    ]
    idx = perception.build_permeability_index(locs)
    res = _perceive(
        {"location": "кухня", "stimuli": [], "target_character_ids": []},
        {"location": "Коридор", "character_id": 1},
        adjacency=idx,
    )
    assert res.visual_level == "none"
    assert res.audio_level == "muffled"


def test_perceive_adjacent_glass_full_visual_no_audio():
    # Стекло: visual=full, audio=none (WPE.md тест #17)
    locs = [
        SimpleNamespace(
            name="Гостиная",
            adjacent_to='[{"name": "Терраса", "visual_permeability": "full", '
            '"audio_permeability": "none"}]',
        ),
        SimpleNamespace(name="Терраса", adjacent_to="[]"),
    ]
    idx = perception.build_permeability_index(locs)
    res = _perceive(
        {"location": "Терраса", "stimuli": [], "target_character_ids": []},
        {"location": "Гостиная", "character_id": 1},
        adjacency=idx,
    )
    assert (res.visual_level, res.audio_level) == ("full", "none")


def test_perceive_adjacent_loud_stimulus_raises_audio():
    # Крик из-за стены: audio muffled -> full (WPE.md тест #18)
    locs = [
        SimpleNamespace(
            name="Кухня",
            adjacent_to='[{"name": "Спальня", "visual_permeability": "none", '
            '"audio_permeability": "muffled"}]',
        ),
        SimpleNamespace(name="Спальня", adjacent_to="[]"),
    ]
    idx = perception.build_permeability_index(locs)
    res = _perceive(
        {"location": "Спальня", "stimuli": [{"type": "loud_sound"}], "target_character_ids": []},
        {"location": "Кухня", "character_id": 1},
        adjacency=idx,
    )
    assert (res.visual_level, res.audio_level) == ("none", "full")


def test_perceive_quiet_adjacent_stays_muffled():
    locs = [
        SimpleNamespace(
            name="Кухня",
            adjacent_to='[{"name": "Спальня", "visual_permeability": "none", '
            '"audio_permeability": "muffled"}]',
        ),
        SimpleNamespace(name="Спальня", adjacent_to="[]"),
    ]
    idx = perception.build_permeability_index(locs)
    res = _perceive(
        {"location": "Спальня", "stimuli": [], "target_character_ids": []},
        {"location": "Кухня", "character_id": 1},
        adjacency=idx,
    )
    assert res.audio_level == "muffled"


def test_perceive_addressed_from_target_ids():
    res = _perceive(
        {"location": "Парк", "stimuli": [], "target_character_ids": [7]},
        {"location": "Кухня", "character_id": 7},
    )
    assert res.addressed is True
    res = _perceive(
        {"location": "Парк", "stimuli": [], "target_character_ids": [7]},
        {"location": "Кухня", "character_id": 8},
    )
    assert res.addressed is False


def test_perceive_remote_status_delivered():
    res = _perceive(
        {"location": "Парк", "stimuli": [], "target_character_ids": []},
        {"location": "Кухня", "character_id": 3},
        thread_deliveries=(3,),
    )
    assert res.remote_status == "delivered"
    res = _perceive(
        {"location": "Парк", "stimuli": [], "target_character_ids": []},
        {"location": "Кухня", "character_id": 4},
        thread_deliveries=(3,),
    )
    assert res.remote_status == "none"


def test_perceive_real_chat3_kitchen_character():
    # Чат 3: Ольга/Тимур в «Коридор», кухня — соседняя по ребру
    locs = [
        SimpleNamespace(
            name="Коридор",
            adjacent_to='[{"name": "кухня", "visual_permeability": "none", '
            '"audio_permeability": "muffled"}]',
        ),
        SimpleNamespace(name="кухня", adjacent_to="[]"),
    ]
    idx = perception.build_permeability_index(locs)
    res = _perceive(
        {"location": "кухня", "stimuli": [{"type": "shout"}], "target_character_ids": []},
        {"location": "Коридор", "character_id": 7},
        adjacency=idx,
    )
    assert res.visual_level == "none"
    assert res.audio_level == "full"


def test_build_permeability_index_symmetric():
    locs = [
        SimpleNamespace(name="Комната Марины", adjacent_to='["Коридор"]'),
        SimpleNamespace(name="Коридор", adjacent_to="[]"),
    ]
    idx = perception.build_permeability_index(locs)
    assert "комната марины" in idx["коридор"]
    assert "коридор" in idx["комната марины"]
    # ребро по умолчанию — стена (visual=none, audio=muffled)
    edge = idx["коридор"]["комната марины"]
    assert (edge.visual, edge.audio) == ("none", "muffled")


def test_serialize_adjacency_edges_roundtrip():
    raw = [
        "Коридор",
        {"name": "Терраса", "visual_permeability": "full", "audio_permeability": "none"},
    ]
    serialized = perception.serialize_adjacency_edges(raw)
    edges = perception.parse_adjacency_edges(serialized)
    assert edges["коридор"].visual == "none"
    assert edges["коридор"].audio == "muffled"
    assert edges["терраса"].visual == "full"
    assert edges["терраса"].audio == "none"
    # legacy-имя без объекта не ломается
    assert perception.serialize_adjacency_edges(["Кухня"]) == '["Кухня"]'


# ---------------------------------------------------------------------------
# Контракт Action[] + tool/JSON-Schema (И14, §8)
# ---------------------------------------------------------------------------

def test_action_contract_valid():
    action = schemas.Action(type="move_to", location="кухня")
    assert action.location == "кухня"
    msg = schemas.Action(
        type="send_message",
        message="Привет",
        channel="messenger",
        target_character_ids=[3, 4],
    )
    assert msg.channel == "messenger"
    assert msg.target_character_ids == [3, 4]


def test_action_contract_rejects_unknown_type():
    with pytest.raises(Exception):
        schemas.Action(type="teleport", location="кухня")


def test_turn_output_shape():
    out = schemas.TurnOutput(
        reply_target_character_ids=[2],
        actions=[schemas.Action(type="move_to", location="кухня")],
    )
    assert out.reply_target_character_ids == [2]
    assert len(out.actions) == 1


def test_take_actions_tool_schema_shape():
    tool = schemas.build_take_actions_tool()
    assert tool["type"] == "function"
    fn = tool["function"]
    assert fn["name"] == "take_actions"
    params = fn["parameters"]
    assert params["required"] == ["reply_target_character_ids", "actions"]
    action_schema = params["properties"]["actions"]["items"]
    assert "move_to" in action_schema["properties"]["type"]["enum"]
    assert "send_message" in action_schema["properties"]["type"]["enum"]


def test_take_actions_json_schema_shape():
    schema = schemas.build_take_actions_json_schema()
    assert schema["type"] == "object"
    assert set(schema["required"]) == {"reply_target_character_ids", "actions"}
    assert schema["additionalProperties"] is False


def test_world_engine_flags_default_off():
    s = schemas.settings
    flag_names = [k for k in vars(s) if k.startswith("world_engine_")]
    assert len(flag_names) >= 9
    for name in flag_names:
        assert getattr(s, name) is False, name


# ---------------------------------------------------------------------------
# Миграция / ORM: новые таблицы создаются
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_phase0_tables_created(db_engine):
    async with db_engine.begin() as conn:
        tables = set(
            await conn.run_sync(
                lambda sync_conn: inspect(sync_conn).get_table_names()
            )
        )
    assert {"world_events", "threads", "thread_participant_states"} <= tables


@pytest.mark.asyncio
async def test_character_location_id_column_exists(db_engine):
    async with db_engine.begin() as conn:
        cols = {
            col["name"]
            for col in await conn.run_sync(
                lambda sync_conn: inspect(sync_conn).get_columns("characters")
            )
        }
    assert "location_id" in cols


@pytest.mark.asyncio
async def test_new_models_insertable(db_session, chat):
    we = models.WorldEvent(
        chat_id=chat.id,
        event_type="move",
        location_from="Гостиная",
        location_to="Кухня",
    )
    db_session.add(we)
    thread = models.Thread(chat_id=chat.id, name="Семья", channel="messenger")
    db_session.add(thread)
    await db_session.commit()
    assert we.id is not None
    assert thread.id is not None

"""WPE 3.0 (Plans/WPE.md) Фаза 1 — канонические локации (read-path).

Покрывает:
- golden #1 «синонимичная локация → одинаковый present»: `perceive()`
  сравнивает канонические `location_id` (флаг `WORLD_ENGINE_LOCATIONS_ENABLED`);
- откат: при выключенном флаге — строковое сравнение (legacy-bridge);
- backfill `characters.location_id` из строковой `characters.location`
  (`crud.backfill_character_location_ids`), неоднозначные случаи → отчёт.
"""

from __future__ import annotations

import pytest

from app import crud
from app import perception
from app import schemas
from app.config import settings

# Реальные значения локаций из текущих чатов (см. Фазу 0)
REAL_LOCATIONS = [
    "Комната Марины",
    "Коридор",
    "гостиная",
    "кухня",
    "Аудитория Университета",
    "Квартира Ольги",
    "Улицы - Магазин",
]


def _world(adjacency=None, thread_deliveries=()):
    return perception.PerceptionWorldState(
        adjacency=adjacency or {},
        thread_deliveries=frozenset(thread_deliveries),
    )


# ---------------------------------------------------------------------------
# Golden #1: синонимичная локация → одинаковый present (по location_id)
# ---------------------------------------------------------------------------

def test_golden_1_synonymous_location_same_id_present(monkeypatch):
    """Разные строковые формы одной канонической локации → full/full."""
    monkeypatch.setattr(settings, "world_engine_locations_enabled", True)
    res = perception.perceive(
        world_state=_world(),
        event={
            "location": "Кухня (старое название)",
            "location_id": 5,
            "stimuli": [],
            "target_character_ids": [],
        },
        observer={"location": "кухня", "location_id": 5, "character_id": 1},
    )
    assert res.visual_level == "full"
    assert res.audio_level == "full"


def test_golden_1_same_id_case_insensitive(monkeypatch):
    monkeypatch.setattr(settings, "world_engine_locations_enabled", True)
    res = perception.perceive(
        world_state=_world(),
        event={"location": "КУХНЯ", "location_id": 7, "stimuli": [], "target_character_ids": []},
        observer={"location": "кухня", "location_id": 7, "character_id": 2},
    )
    assert (res.visual_level, res.audio_level) == ("full", "full")


def test_different_ids_not_same_location_even_if_strings_match(monkeypatch):
    """Разные id → разные локации, даже если строки совпадают (id — истина)."""
    monkeypatch.setattr(settings, "world_engine_locations_enabled", True)
    res = perception.perceive(
        world_state=_world(),
        event={"location": "Кухня", "location_id": 5, "stimuli": [], "target_character_ids": []},
        observer={"location": "Кухня", "location_id": 6, "character_id": 3},
    )
    assert res.visual_level == "none"
    assert res.audio_level == "none"


# ---------------------------------------------------------------------------
# Откат Фазы 1: флаг выключен → сравнение строк (legacy-bridge)
# ---------------------------------------------------------------------------

def test_flag_off_uses_string_comparison(monkeypatch):
    """Флаг off: id игнорируются, решение по нормализованным строкам."""
    monkeypatch.setattr(settings, "world_engine_locations_enabled", False)
    # id совпадают, строки различаются по-настоящему → не одна локация
    res = perception.perceive(
        world_state=_world(),
        event={
            "location": "Кухня (старое название)",
            "location_id": 5,
            "stimuli": [],
            "target_character_ids": [],
        },
        observer={"location": "кухня", "location_id": 5, "character_id": 1},
    )
    assert (res.visual_level, res.audio_level) == ("none", "none")
    # регистровый синоним по-прежнему совпадает по строкам
    res = perception.perceive(
        world_state=_world(),
        event={"location": "КУХНЯ", "location_id": 7, "stimuli": [], "target_character_ids": []},
        observer={"location": "кухня", "location_id": 8, "character_id": 2},
    )
    assert (res.visual_level, res.audio_level) == ("full", "full")


def test_flag_on_missing_id_falls_back_to_strings(monkeypatch):
    """Флаг on, но id с какой-то стороны нет → legacy-bridge по строкам."""
    monkeypatch.setattr(settings, "world_engine_locations_enabled", True)
    res = perception.perceive(
        world_state=_world(),
        event={"location": "Кухня", "stimuli": [], "target_character_ids": []},
        observer={"location": "кухня", "location_id": 5, "character_id": 1},
    )
    assert (res.visual_level, res.audio_level) == ("full", "full")


# ---------------------------------------------------------------------------
# Backfill characters.location_id
# ---------------------------------------------------------------------------

async def _create_locations(db, chat_id: int, names: list[str]) -> list:
    created = []
    for name in names:
        created.append(
            await crud.create_location(
                db, chat_id, schemas.LocationCreate(name=name)
            )
        )
    return created


_char_seq = {"n": 0}


async def _create_char(db, chat_id: int, name: str, location: str) -> object:
    _char_seq["n"] += 1
    return await crud.create_character(
        db,
        chat_id,
        schemas.CharacterCreate(
            name=name,
            personality="",
            location=location,
            order_index=_char_seq["n"],
        ),
    )


@pytest.mark.asyncio
async def test_backfill_sets_location_id(db_session, chat):
    locs = await _create_locations(db_session, chat.id, ["Кухня", "Гостиная", "Коридор"])
    await _create_char(db_session, chat.id, "Пётр", "кухня")  # регистр
    await _create_char(db_session, chat.id, "Маша", "  Гостиная  ")  # трим
    await _create_char(db_session, chat.id, "Иван", "Коридор")

    report = await crud.backfill_character_location_ids(db_session, chat.id)

    assert report.total == 3
    assert report.resolved == 3
    assert report.unresolved == []
    by_name = {c.name: c for c in await crud.get_characters_by_chat(db_session, chat.id, include_player=True)}
    assert by_name["Пётр"].location_id == locs[0].id
    assert by_name["Маша"].location_id == locs[1].id
    assert by_name["Иван"].location_id == locs[2].id


@pytest.mark.asyncio
async def test_backfill_shared_scene_stays_none(db_session, chat):
    await _create_locations(db_session, chat.id, ["Кухня"])
    char = await _create_char(db_session, chat.id, "Ольга", "")
    char2 = await _create_char(db_session, chat.id, "Тимур", "Общая сцена")

    report = await crud.backfill_character_location_ids(db_session, chat.id)

    assert report.shared_scene == 2
    assert report.resolved == 0
    chars = await crud.get_characters_by_chat(db_session, chat.id, include_player=True)
    for c in chars:
        assert c.location_id is None


@pytest.mark.asyncio
async def test_backfill_unresolved_goes_to_report(db_session, chat):
    await _create_locations(db_session, chat.id, ["Кухня"])
    good = await _create_char(db_session, chat.id, "Пётр", "кухня")
    bad = await _create_char(db_session, chat.id, "Аня", "Чердак")  # нет в таблице

    report = await crud.backfill_character_location_ids(db_session, chat.id)

    assert report.resolved == 1
    assert len(report.unresolved) == 1
    chat_id, char_id, name, location = report.unresolved[0]
    assert char_id == bad.id
    assert name == "Аня"
    assert location == "Чердак"
    assert bad.location_id is None
    assert good.location_id is not None
    # отчёт человекочитаем
    assert any("UNRESOLVED" in line for line in report.lines())


@pytest.mark.asyncio
async def test_backfill_includes_player(db_session, chat):
    await _create_locations(db_session, chat.id, ["Кухня", "Спальня"])
    player = await crud.create_player_character(db_session, chat.id, name="Игрок")
    player.location = "Спальня"
    await db_session.commit()

    report = await crud.backfill_character_location_ids(db_session, chat.id)

    assert report.resolved == 1
    await db_session.refresh(player)
    assert player.location_id is not None


@pytest.mark.asyncio
async def test_backfill_idempotent(db_session, chat):
    await _create_locations(db_session, chat.id, ["Кухня", "Гостиная"])
    await _create_char(db_session, chat.id, "Пётр", "кухня")
    await _create_char(db_session, chat.id, "Маша", "гостиная")

    r1 = await crud.backfill_character_location_ids(db_session, chat.id)
    ids1 = {
        c.name: c.location_id
        for c in await crud.get_characters_by_chat(db_session, chat.id, include_player=True)
    }
    r2 = await crud.backfill_character_location_ids(db_session, chat.id)
    ids2 = {
        c.name: c.location_id
        for c in await crud.get_characters_by_chat(db_session, chat.id, include_player=True)
    }

    assert r1.resolved == r2.resolved == 2
    assert ids1 == ids2


@pytest.mark.asyncio
async def test_backfill_after_location_rename(db_session, chat):
    locs = await _create_locations(db_session, chat.id, ["Кухня"])
    char = await _create_char(db_session, chat.id, "Пётр", "кухня")
    await crud.backfill_character_location_ids(db_session, chat.id)
    await db_session.refresh(char)
    assert char.location_id == locs[0].id

    # переименование локации обновляет строковые ссылки (Спринт 2 CRUD)
    await crud.update_location(
        db_session, locs[0].id, schemas.LocationUpdate(name="Кухня-студия")
    )
    await db_session.refresh(char)
    assert char.location == "Кухня-студия"

    # повторный backfill сохраняет тот же канонический id
    await crud.backfill_character_location_ids(db_session, chat.id)
    await db_session.refresh(char)
    assert char.location_id == locs[0].id

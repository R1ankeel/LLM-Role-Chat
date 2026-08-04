"""Sprint 0 — schema foundation + backfills (Plans/update20.md).

Покрывает:
- идемпотентность миграций на «копии прод-БД» (legacy-схема без новых колонок/
  таблиц → ensure_schema добавляет их без ошибок, повторный запуск безопасен);
- backfill ``chats.original_plot/story_prompt`` из ``general_prompt`` с отчётом;
- backfill ``world_events.location_id`` из строковой ``location`` (аналог
  ``backfill_character_location_ids``) с отчётом о нерезолвленных случаях.

Поведение не меняется: новые таблицы/колонки read-path не читает.
"""

from __future__ import annotations

import os
import tempfile

import pytest
from sqlalchemy import create_engine, inspect, text

import app.models  # noqa: F401  (регистрация моделей на Base.metadata)
from app import crud
from app import models
from app import schemas
from app.database import ensure_schema

NEW_TABLES = (
    "character_states",
    "beliefs",
    "story_states",
    "story_threads",
    "story_events",
    "event_links",
    "memory_anchors",
    "intents",
    "npc_plans",
    "consolidation_state",
)
NEW_CHAT_COLS = ("original_plot", "story_prompt", "story_enabled")
NEW_WE_COLS = ("location_id",)


@pytest.fixture
def legacy_db():
    """Копия «прод»-схемы ДО Sprint 0 (минимальные таблицы без новых полей)."""
    tmp = tempfile.mktemp(suffix=".db")
    engine = create_engine(f"sqlite:///{tmp}")
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE chats (id INTEGER PRIMARY KEY, name TEXT, "
                "general_prompt TEXT, created_at DATETIME)"
            )
        )
        conn.execute(
            text(
                "CREATE TABLE characters (id INTEGER PRIMARY KEY, chat_id INTEGER, "
                "name TEXT, personality TEXT, traits TEXT, order_index INTEGER, "
                "created_at DATETIME)"
            )
        )
        conn.execute(
            text(
                "CREATE TABLE messages (id INTEGER PRIMARY KEY, chat_id INTEGER, "
                "character_id INTEGER, role TEXT, content TEXT, timestamp DATETIME)"
            )
        )
        conn.execute(
            text(
                "CREATE TABLE memories (id INTEGER PRIMARY KEY, chat_id INTEGER, "
                "character_id INTEGER, content TEXT, created_at DATETIME)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO chats (id, name, general_prompt) "
                "VALUES (1, 'Old', 'Старый сюжет')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO characters (id, chat_id, name, order_index) "
                "VALUES (1, 1, 'Анна', 1)"
            )
        )
    try:
        yield engine
    finally:
        engine.dispose()
        try:
            os.unlink(tmp)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Миграции: идемпотентность
# ---------------------------------------------------------------------------

def test_migration_adds_new_columns_and_tables(legacy_db):
    ensure_schema(legacy_db)
    insp = inspect(legacy_db)
    chat_cols = {c["name"] for c in insp.get_columns("chats")}
    we_cols = {c["name"] for c in insp.get_columns("world_events")}
    tables = set(insp.get_table_names())
    assert set(NEW_CHAT_COLS) <= chat_cols
    assert set(NEW_WE_COLS) <= we_cols
    assert set(NEW_TABLES) <= tables


def test_migration_is_idempotent(legacy_db):
    ensure_schema(legacy_db)
    ensure_schema(legacy_db)  # повторный запуск не должен падать
    insp = inspect(legacy_db)
    chat_cols = {c["name"] for c in insp.get_columns("chats")}
    tables = set(insp.get_table_names())
    assert set(NEW_CHAT_COLS) <= chat_cols
    assert set(NEW_TABLES) <= tables


def test_world_events_location_id_is_nullable_fk(legacy_db):
    ensure_schema(legacy_db)
    cols = {c["name"]: c for c in inspect(legacy_db).get_columns("world_events")}
    assert cols["location_id"]["nullable"] is True


def test_new_tables_empty_after_migration(legacy_db):
    ensure_schema(legacy_db)
    insp = inspect(legacy_db)
    with legacy_db.connect() as conn:
        for table in NEW_TABLES:
            count = conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()
            assert count == 0, f"{table} должна быть пустой после Sprint 0"


# ---------------------------------------------------------------------------
# Backfill: chats.original_plot / story_prompt
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_backfill_plot_fields_from_general_prompt(db_session, chat):
    chat.general_prompt = "Общий сюжет"
    chat.original_plot = ""
    chat.story_prompt = ""
    chat.story_enabled = False
    await db_session.commit()

    report = await crud.backfill_plot_fields(db_session)
    await db_session.refresh(chat)

    assert chat.original_plot == "Общий сюжет"
    assert chat.story_prompt == "Общий сюжет"
    assert chat.story_enabled is False
    assert report.total == 1
    assert report.filled_original_plot == 1
    assert report.filled_story_prompt == 1
    assert report.story_enabled == 0


@pytest.mark.asyncio
async def test_backfill_plot_fields_idempotent(db_session, chat):
    chat.general_prompt = "Сюжет"
    await db_session.commit()

    first = await crud.backfill_plot_fields(db_session)
    second = await crud.backfill_plot_fields(db_session)
    await db_session.refresh(chat)

    assert first.filled_original_plot == 1
    assert second.filled_original_plot == 0
    assert second.filled_story_prompt == 0
    assert chat.original_plot == "Сюжет"
    assert chat.story_prompt == "Сюжет"


@pytest.mark.asyncio
async def test_backfill_plot_fields_does_not_overwrite_existing(db_session, chat):
    chat.general_prompt = "Сюжет"
    chat.original_plot = "Уже заданный замысел"
    chat.story_prompt = ""
    await db_session.commit()

    report = await crud.backfill_plot_fields(db_session)
    await db_session.refresh(chat)

    assert chat.original_plot == "Уже заданный замысел"
    assert chat.story_prompt == "Сюжет"
    assert report.filled_original_plot == 0
    assert report.filled_story_prompt == 1


@pytest.mark.asyncio
async def test_backfill_plot_fields_keeps_story_disabled(db_session, chat):
    chat.general_prompt = "Сюжет"
    chat.story_enabled = True  # защита: backfill сбрасывает случайно включённый флаг
    await db_session.commit()

    report = await crud.backfill_plot_fields(db_session)
    await db_session.refresh(chat)
    assert chat.story_enabled is False
    assert report.story_enabled == 1


# ---------------------------------------------------------------------------
# Backfill: world_events.location_id
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_backfill_event_location_ids_resolves(db_session, chat):
    loc = await crud.create_location(
        db_session, chat.id, schemas.LocationCreate(name="кухня")
    )
    we = models.WorldEvent(chat_id=chat.id, event_type="speech", location="Кухня")
    db_session.add(we)
    await db_session.commit()

    report = await crud.backfill_event_location_ids(db_session)
    await db_session.refresh(we)

    assert we.location_id == loc.id
    assert report.total == 1
    assert report.resolved == 1
    assert report.unresolved == []


@pytest.mark.asyncio
async def test_backfill_event_location_ids_idempotent(db_session, chat):
    await crud.create_location(db_session, chat.id, schemas.LocationCreate(name="кухня"))
    we = models.WorldEvent(chat_id=chat.id, event_type="speech", location="Кухня")
    db_session.add(we)
    await db_session.commit()

    first = await crud.backfill_event_location_ids(db_session)
    second = await crud.backfill_event_location_ids(db_session)
    await db_session.refresh(we)

    assert first.resolved == 1
    assert second.resolved == 1
    assert we.location_id is not None


@pytest.mark.asyncio
async def test_backfill_event_location_ids_unresolved_reported(db_session, chat):
    we = models.WorldEvent(
        chat_id=chat.id, event_type="move", location="Нет такой локации"
    )
    db_session.add(we)
    await db_session.commit()

    report = await crud.backfill_event_location_ids(db_session)
    await db_session.refresh(we)

    assert we.location_id is None
    assert len(report.unresolved) == 1
    assert report.unresolved[0][1] == we.id
    assert report.unresolved[0][3] == "Нет такой локации"


@pytest.mark.asyncio
async def test_backfill_event_location_ids_shared_scene(db_session, chat):
    we = models.WorldEvent(chat_id=chat.id, event_type="system", location="")
    db_session.add(we)
    await db_session.commit()

    report = await crud.backfill_event_location_ids(db_session)
    await db_session.refresh(we)

    assert we.location_id is None
    assert report.shared_scene == 1
    assert report.unresolved == []


@pytest.mark.asyncio
async def test_backfill_event_location_ids_respects_chat_filter(db_session, chat):
    await crud.create_location(db_session, chat.id, schemas.LocationCreate(name="кухня"))
    we1 = models.WorldEvent(chat_id=chat.id, event_type="speech", location="Кухня")
    we2 = models.WorldEvent(
        chat_id=chat.id, event_type="move", location="Нет такой локации"
    )
    db_session.add_all([we1, we2])
    await db_session.commit()

    report = await crud.backfill_event_location_ids(db_session, chat_id=chat.id)
    assert report.total == 2
    assert report.resolved == 1
    assert len(report.unresolved) == 1

"""Sprint 1 — schema migrations (Plans/update20.md §15).

Проверяет идемпотентность миграций на «копии прод-БД» ПОСЛЕ Sprint 0
(таблицы `world_events` и `relationship_events` уже существуют, но без новых
колонок Sprint 1):

- ``world_events``: +action, +importance, +story_salience, +emotional_salience
  (nullable / default), без дата-потерь;
- ``relationship_events``: +event_id (FK world_events, nullable);
- повторный ``ensure_schema`` безопасен;
- новые колонки read-path не читает — существующие данные не затронуты.
"""

from __future__ import annotations

import os
import tempfile

import pytest
from sqlalchemy import create_engine, inspect, text

import app.models  # noqa: F401
from app.database import ensure_schema

SPRINT1_WE_COLS = ("action", "importance", "story_salience", "emotional_salience")


@pytest.fixture
def post_sprint0_db():
    """«Прод»-схема после Sprint 0: таблицы есть, колонок Sprint 1 нет."""
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
                "name TEXT, personality TEXT, order_index INTEGER, created_at DATETIME)"
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
                "CREATE TABLE world_events ("
                "id INTEGER PRIMARY KEY, chat_id INTEGER, character_id INTEGER, "
                "message_id INTEGER, event_type TEXT, location TEXT DEFAULT '', "
                "location_from TEXT DEFAULT '', location_to TEXT DEFAULT '', "
                "round_id TEXT, target_character_ids TEXT DEFAULT '[]', "
                "created_at DATETIME)"
            )
        )
        conn.execute(
            text(
                "CREATE TABLE character_relationships ("
                "id INTEGER PRIMARY KEY, chat_id INTEGER, source_character_id INTEGER, "
                "target_character_id INTEGER, relationship_type TEXT)"
            )
        )
        conn.execute(
            text(
                "CREATE TABLE relationship_events ("
                "id INTEGER PRIMARY KEY, relationship_id INTEGER, description TEXT, "
                "source_round_id TEXT, timestamp DATETIME)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO world_events (id, chat_id, event_type, location, round_id) "
                "VALUES (1, 1, 'speech', 'Кухня', 'r1-m1')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO relationship_events (id, relationship_id, description) "
                "VALUES (1, 1, 'Старое событие')"
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


def test_sprint1_adds_world_events_columns(post_sprint0_db):
    ensure_schema(post_sprint0_db)
    cols = {c["name"]: c for c in inspect(post_sprint0_db).get_columns("world_events")}
    assert set(SPRINT1_WE_COLS) <= set(cols)
    # action NOT NULL с default — существующие строки получают '{}'
    assert cols["action"]["nullable"] is False
    assert cols["importance"]["nullable"] is True


def test_sprint1_adds_relationship_events_event_id(post_sprint0_db):
    ensure_schema(post_sprint0_db)
    cols = {
        c["name"]: c for c in inspect(post_sprint0_db).get_columns("relationship_events")
    }
    assert "event_id" in cols
    assert cols["event_id"]["nullable"] is True


def test_sprint1_migrations_idempotent(post_sprint0_db):
    ensure_schema(post_sprint0_db)
    ensure_schema(post_sprint0_db)  # повторный запуск безопасен
    cols = {c["name"] for c in inspect(post_sprint0_db).get_columns("world_events")}
    rel_cols = {
        c["name"] for c in inspect(post_sprint0_db).get_columns("relationship_events")
    }
    assert set(SPRINT1_WE_COLS) <= cols
    assert "event_id" in rel_cols


def test_sprint1_migration_preserves_existing_rows(post_sprint0_db):
    """Дата-потери нет: старые строки world_events сохраняются."""
    ensure_schema(post_sprint0_db)
    with post_sprint0_db.connect() as conn:
        row = conn.execute(
            text("SELECT id, event_type, location, round_id FROM world_events WHERE id = 1")
        ).fetchone()
        assert row is not None
        assert row.event_type == "speech"
        assert row.location == "Кухня"
        assert row.round_id == "r1-m1"
        rel = conn.execute(
            text("SELECT id, description FROM relationship_events WHERE id = 1")
        ).fetchone()
        assert rel is not None
        assert rel.description == "Старое событие"

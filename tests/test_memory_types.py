"""Sprint 2 — Memory Architecture v2: типы памяти (Plans/update20.md §7).

Проверяет:
- normalize_memory_type: валидные/пустые/неизвестные значения;
- ExtractedFact/MemoryCreate: тип сохраняется, невалидный → None;
- crud.create_memory: default 'semantic', сохранение валидного типа;
- memory_service.classify_memory_type: детерминированный fallback;
- validate_extracted_fact: fallback-тип проставляется при отсутствии LLM-типа;
- ollama_client.parse_extracted_facts: LLM может вернуть memory_type;
- миграция post-sprint0: memories получает memory_type/event_id/valence/intensity
  (идемпотентно, без дата-потерь).
"""

from __future__ import annotations

import os
import tempfile

import pytest
from sqlalchemy import create_engine, inspect, text

import app.models  # noqa: F401
from app import crud
from app import memory_service
from app import ollama_client
from app import schemas
from app.database import ensure_schema
from tests.conftest import create_characters

SPRINT2_MEM_COLS = ("memory_type", "event_id", "valence", "intensity")


# ------------------------- Schema normalization -------------------------
def test_normalize_memory_type_valid():
    assert schemas.normalize_memory_type("semantic") == "semantic"
    assert schemas.normalize_memory_type("EPISODIC") == "episodic"
    assert schemas.normalize_memory_type(" story ") == "story"
    assert schemas.normalize_memory_type("social") == "social"


def test_normalize_memory_type_empty_or_unknown():
    assert schemas.normalize_memory_type(None) is None
    assert schemas.normalize_memory_type("") is None
    assert schemas.normalize_memory_type("none") is None
    assert schemas.normalize_memory_type("global") is None
    assert schemas.normalize_memory_type(" семантический ") is None


def test_extracted_fact_memory_type_kept_or_nulled():
    fact = schemas.ExtractedFact(fact="x", memory_type="social")
    assert fact.memory_type == "social"
    fact_bad = schemas.ExtractedFact(fact="x", memory_type="неверный_тип")
    assert fact_bad.memory_type is None


def test_memory_create_memory_type_validated():
    create = schemas.MemoryCreate(
        chat_id=1,
        character_id=2,
        content="Факт",
        category="отношения",
        memory_type="social",
    )
    assert create.memory_type == "social"


# ----------------------------- Classifier ------------------------------
def test_classify_social_by_category():
    assert (
        memory_service.classify_memory_type(
            schemas.ExtractedFact(fact="Аня помирилась с Борей", category="отношения")
        )
        == "social"
    )


def test_classify_semantic_by_category():
    assert (
        memory_service.classify_memory_type(
            schemas.ExtractedFact(fact="Ключ лежит под камнем", category="локация")
        )
        == "semantic"
    )
    assert (
        memory_service.classify_memory_type(
            {"fact": "Меч хранится в сундуке", "category": "предмет"}
        )
        == "semantic"
    )


def test_classify_episodic_by_category():
    assert (
        memory_service.classify_memory_type(
            schemas.ExtractedFact(fact="Дракон сжёг восточную башню", category="событие")
        )
        == "episodic"
    )


def test_classify_story_by_text_markers():
    assert (
        memory_service.classify_memory_type(
            {"fact": "Отряд ищет пропавшего графа в лесу", "category": "другое"}
        )
        == "story"
    )
    assert (
        memory_service.classify_memory_type(
            {"fact": "Цель похода — найти артефакт", "category": ""}
        )
        == "story"
    )


def test_classify_default_semantic():
    assert (
        memory_service.classify_memory_type({"fact": "На улице идёт дождь", "category": ""})
        == "semantic"
    )


# ------------------------- validate_extracted_fact -------------------------
def test_validate_fills_fallback_memory_type():
    fact = schemas.ExtractedFact(
        fact="Игрок отдал Alice серебряный ключ от склада",
        category="предмет",
        importance=0.85,
        witnessed=True,
    )
    cleaned = memory_service.validate_extracted_fact(fact, "Alice")
    assert cleaned is not None
    assert cleaned.memory_type == "semantic"


def test_validate_keeps_llm_memory_type():
    fact = schemas.ExtractedFact(
        fact="Alice решила помогать игроку в поисках отца",
        category="событие",
        importance=0.7,
        witnessed=True,
        memory_type="story",
    )
    cleaned = memory_service.validate_extracted_fact(fact, "Alice")
    assert cleaned is not None
    assert cleaned.memory_type == "story"


# --------------------------- parse_extracted_facts ---------------------------
def test_parse_structured_facts_with_memory_type():
    raw = (
        '[{"fact": "Игрок представился как Алекс", "category": "отношения", '
        '"importance": 0.9, "witnessed": true, "memory_type": "social"}]'
    )
    facts = ollama_client.parse_extracted_facts(raw)
    assert len(facts) == 1
    assert facts[0].memory_type == "social"


def test_parse_legacy_facts_default_type_none():
    raw = '["Игрок отдал меч стражнику"]'
    facts = ollama_client.parse_extracted_facts(raw)
    assert len(facts) == 1
    assert facts[0].memory_type is None


# --------------------------- create_memory (CRUD) ---------------------------
@pytest.mark.asyncio
async def test_create_memory_defaults_to_semantic(db_session, chat):
    character = await create_characters(db_session, chat.id, 1)
    memory = await crud.create_memory(
        db_session,
        schemas.MemoryCreate(
            chat_id=chat.id,
            character_id=character[0].id,
            content="Факт без указанного типа",
            importance=0.5,
            category="событие",
        ),
    )
    assert memory is not None
    assert memory.memory_type == "semantic"


@pytest.mark.asyncio
async def test_create_memory_stores_explicit_type(db_session, chat):
    character = await create_characters(db_session, chat.id, 1)
    memory = await crud.create_memory(
        db_session,
        schemas.MemoryCreate(
            chat_id=chat.id,
            character_id=character[0].id,
            content="Аня доверяет Боре после долгого разговора",
            importance=0.8,
            category="отношения",
            memory_type="social",
        ),
    )
    assert memory is not None
    assert memory.memory_type == "social"


@pytest.mark.asyncio
async def test_update_memory_can_change_type(db_session, chat):
    character = await create_characters(db_session, chat.id, 1)
    memory = await crud.create_memory(
        db_session,
        schemas.MemoryCreate(
            chat_id=chat.id,
            character_id=character[0].id,
            content="Игрок купил зелье у торговца",
            importance=0.6,
            category="предмет",
        ),
    )
    updated = await crud.update_memory(
        db_session,
        memory.id,
        schemas.MemoryUpdate(memory_type="semantic", category="предмет"),
    )
    assert updated is not None
    assert updated.memory_type == "semantic"


# ------------------------------- Migration -------------------------------
@pytest.fixture
def post_sprint0_db():
    """«Прод»-схема после Sprint 0: memories без колонок Sprint 2."""
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
                "INSERT INTO memories (id, chat_id, character_id, content) "
                "VALUES (1, 1, 1, 'Старый факт без типа')"
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


def test_sprint2_adds_memory_columns(post_sprint0_db):
    ensure_schema(post_sprint0_db)
    cols = {c["name"]: c for c in inspect(post_sprint0_db).get_columns("memories")}
    assert set(SPRINT2_MEM_COLS) <= set(cols)
    assert cols["memory_type"]["nullable"] is False
    assert cols["event_id"]["nullable"] is True
    assert cols["valence"]["nullable"] is True
    assert cols["intensity"]["nullable"] is True


def test_sprint2_migrations_idempotent(post_sprint0_db):
    ensure_schema(post_sprint0_db)
    ensure_schema(post_sprint0_db)
    cols = {c["name"] for c in inspect(post_sprint0_db).get_columns("memories")}
    assert set(SPRINT2_MEM_COLS) <= cols


def test_sprint2_migration_backfills_semantic(post_sprint0_db):
    """Существующие строки получают memory_type='semantic' (без дата-потерь)."""
    ensure_schema(post_sprint0_db)
    with post_sprint0_db.connect() as conn:
        row = conn.execute(
            text("SELECT id, content, memory_type FROM memories WHERE id = 1")
        ).fetchone()
        assert row is not None
        assert row.content == "Старый факт без типа"
        assert row.memory_type == "semantic"


def test_sprint2_adds_anchor_indexes(post_sprint0_db):
    ensure_schema(post_sprint0_db)
    indexes = {
        idx["name"]: idx
        for idx in inspect(post_sprint0_db).get_indexes("memory_anchors")
    }
    assert "ix_anchors_rel" in indexes
    assert "ix_anchors_event" in indexes

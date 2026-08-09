"""Tests for memory_service summarization, extraction validation, and post-round."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest
from sqlalchemy.orm import sessionmaker

from app import crud
from app import memory_service
from app import ollama_client
from app import schemas
from app.config import settings
from tests.conftest import create_characters


@pytest.fixture
def mock_client():
    return httpx.AsyncClient(base_url="http://test")


def _seed_user_messages(db_session, chat_id: int, count: int) -> list:
    messages = []
    for index in range(count):
        messages.append(
            crud.create_message(
                db_session,
                schemas.MessageCreate(
                    chat_id=chat_id,
                    role="user",
                    content=f"User message {index}",
                ),
            )
        )
    return messages


@pytest.mark.asyncio
async def test_summary_not_triggered_below_threshold(
    db_session, chat, mock_client, db_engine
):
    create_characters(db_session, chat.id, 1)
    _seed_user_messages(db_session, chat.id, settings.summary_interval_messages - 1)

    summarize_calls: list[str] = []

    async def fake_summarize(client, model, character, dialogue_text, existing_summary=""):
        summarize_calls.append(character.name)
        return "Updated summary"

    test_session_factory = sessionmaker(bind=db_engine)
    character_snapshots = [
        schemas.CharacterRead.model_validate(c).model_dump(mode="python")
        for c in crud.get_characters_by_chat(db_session, chat.id)
    ]

    with patch(
        "app.memory_service.ollama_client.summarize_for_character",
        side_effect=fake_summarize,
    ), patch("app.memory.summaries.AsyncSessionLocal", test_session_factory):
        await memory_service._maybe_update_summaries(
            mock_client,
            chat.id,
            character_snapshots,
            chat.model_name,
        )

    assert summarize_calls == []


@pytest.mark.asyncio
async def test_summary_triggered_at_threshold(
    db_session, chat, mock_client, db_engine
):
    characters = create_characters(db_session, chat.id, 1)
    character = characters[0]
    messages = _seed_user_messages(db_session, chat.id, settings.summary_interval_messages)

    async def fake_summarize(client, model, character_obj, dialogue_text, existing_summary=""):
        return f"Summary for {character_obj.name} after {len(dialogue_text)} chars"

    test_session_factory = sessionmaker(bind=db_engine)
    character_snapshots = [
        schemas.CharacterRead.model_validate(character).model_dump(mode="python")
    ]

    with patch(
        "app.memory_service.ollama_client.summarize_for_character",
        side_effect=fake_summarize,
    ), patch("app.memory.summaries.AsyncSessionLocal", test_session_factory):
        await memory_service._maybe_update_summaries(
            mock_client,
            chat.id,
            character_snapshots,
            chat.model_name,
        )

    verify_session = test_session_factory()
    try:
        summary = crud.get_character_summary(verify_session, character.id)
        assert summary is not None
        assert "Summary for Character A" in summary.content
        assert summary.through_message_id == messages[-1].id
    finally:
        verify_session.close()


@pytest.mark.asyncio
async def test_summary_watermark_advances(
    db_session, chat, mock_client, db_engine
):
    character = create_characters(db_session, chat.id, 1)[0]
    first_batch = _seed_user_messages(db_session, chat.id, settings.summary_interval_messages)

    test_session_factory = sessionmaker(bind=db_engine)
    character_snapshots = [
        schemas.CharacterRead.model_validate(character).model_dump(mode="python")
    ]

    async def fake_summarize(client, model, character_obj, dialogue_text, existing_summary=""):
        if existing_summary:
            return existing_summary + "\nMore events."
        return "Initial summary."

    with patch(
        "app.memory_service.ollama_client.summarize_for_character",
        side_effect=fake_summarize,
    ), patch("app.memory.summaries.AsyncSessionLocal", test_session_factory):
        await memory_service._maybe_update_summaries(
            mock_client,
            chat.id,
            character_snapshots,
            chat.model_name,
        )

    second_batch = _seed_user_messages(
        db_session, chat.id, settings.summary_interval_messages
    )

    with patch(
        "app.memory_service.ollama_client.summarize_for_character",
        side_effect=fake_summarize,
    ), patch("app.memory.summaries.AsyncSessionLocal", test_session_factory):
        await memory_service._maybe_update_summaries(
            mock_client,
            chat.id,
            character_snapshots,
            chat.model_name,
        )

    verify_session = test_session_factory()
    try:
        summary = crud.get_character_summary(verify_session, character.id)
        assert summary.through_message_id == second_batch[-1].id
        assert summary.through_message_id > first_batch[-1].id
        assert "More events." in summary.content
    finally:
        verify_session.close()


@pytest.mark.asyncio
async def test_clear_messages_preserves_summaries(db_session, chat):
    characters = await create_characters(db_session, chat.id, 1)
    character = characters[0]
    await crud.upsert_character_summary(
        db_session,
        chat.id,
        character.id,
        "Old summary content",
        through_message_id=10,
    )

    summary = await crud.get_character_summary(db_session, character.id)
    assert summary is not None

    # clear_chat_messages only deletes messages, not summaries
    await crud.clear_chat_messages(db_session, chat.id)

    # Summary should still exist
    summary = await crud.get_character_summary(db_session, character.id)
    assert summary is not None


@pytest.mark.asyncio
async def test_clear_full_resets_summaries(db_session, chat):
    characters = await create_characters(db_session, chat.id, 1)
    character = characters[0]
    await crud.upsert_character_summary(
        db_session,
        chat.id,
        character.id,
        "Old summary content",
        through_message_id=10,
    )

    summary = await crud.get_character_summary(db_session, character.id)
    assert summary is not None

    await crud.reset_character_summaries_for_chat(db_session, chat.id)

    summary = await crud.get_character_summary(db_session, character.id)
    assert summary is None


# -------------------- Extraction validation (P1) --------------------


def test_parse_structured_facts():
    raw = """
    [
      {"fact": "Игрок представился как Алекс", "category": "отношения", "importance": 0.9, "witnessed": true},
      {"fact": "В таверне темно", "category": "локация", "importance": 0.4, "witnessed": true}
    ]
    """
    facts = ollama_client.parse_extracted_facts(raw)
    assert len(facts) == 2
    assert facts[0].fact == "Игрок представился как Алекс"
    assert facts[0].category == "отношения"
    assert facts[0].importance == pytest.approx(0.9)
    assert facts[1].category == "локация"


def test_parse_legacy_string_array():
    raw = '["Игрок отдал меч стражнику", "Дверь в подвал открыта"]'
    facts = ollama_client.parse_extracted_facts(raw)
    assert len(facts) == 2
    assert facts[0].fact == "Игрок отдал меч стражнику"
    assert facts[0].category == "событие"
    assert facts[0].importance == pytest.approx(0.5)


def test_parse_importance_scale_1_to_5():
    raw = '[{"fact": "Ключ лежит под камнем у ручья", "importance": 4, "category": "предмет"}]'
    facts = ollama_client.parse_extracted_facts(raw)
    assert len(facts) == 1
    assert facts[0].importance == pytest.approx(0.8)


def test_parse_markdown_fenced_json():
    raw = """```json
[{"fact": "Купец назвал цену в 50 золотых", "category": "событие", "importance": 0.6, "witnessed": true}]
```"""
    facts = ollama_client.parse_extracted_facts(raw)
    assert len(facts) == 1
    assert "50 золотых" in facts[0].fact


def test_validate_rejects_short_fact():
    fact = schemas.ExtractedFact(fact="Коротко", witnessed=True)
    assert memory_service.validate_extracted_fact(fact, "Alice") is None


def test_validate_rejects_generic_fact():
    fact = schemas.ExtractedFact(
        fact="Они поговорили о разных вещах",
        witnessed=True,
    )
    assert memory_service.validate_extracted_fact(fact, "Alice") is None


def test_validate_rejects_other_mind():
    fact = schemas.ExtractedFact(
        fact="Боб тайно думает предать отряд ночью",
        witnessed=True,
    )
    assert memory_service.validate_extracted_fact(fact, "Alice") is None


def test_validate_rejects_not_witnessed():
    fact = schemas.ExtractedFact(
        fact="В соседней комнате спрятан сундук с золотом",
        witnessed=False,
    )
    assert memory_service.validate_extracted_fact(fact, "Alice") is None


def test_validate_accepts_good_fact():
    fact = schemas.ExtractedFact(
        fact="Игрок отдал Alice серебряный ключ от склада",
        category="предмет",
        importance=0.85,
        witnessed=True,
    )
    cleaned = memory_service.validate_extracted_fact(fact, "Alice")
    assert cleaned is not None
    assert "серебряный ключ" in cleaned.fact
    assert cleaned.category == "предмет"
    assert cleaned.importance == pytest.approx(0.85)


def test_validate_allows_self_internal_state():
    fact = schemas.ExtractedFact(
        fact="Alice решила помочь игроку найти пропавший артефакт",
        category="событие",
        importance=0.7,
        witnessed=True,
    )
    cleaned = memory_service.validate_extracted_fact(fact, "Alice")
    assert cleaned is not None


def test_validate_near_dup_against_existing():
    fact = schemas.ExtractedFact(
        fact="Игрок представился как Алекс из северных земель",
        witnessed=True,
        importance=0.8,
    )
    existing = ["Игрок представился как Алекс из северных земель вчера"]
    assert (
        memory_service.validate_extracted_fact(
            fact, "Alice", existing_contents=existing
        )
        is None
    )


def test_validate_extracted_facts_keeps_top_by_importance():
    facts = [
        schemas.ExtractedFact(
            fact="Игрок купил зелье лечения у торговца",
            importance=0.4,
            category="предмет",
        ),
        schemas.ExtractedFact(
            fact="Дракон разрушил восточную башню крепости",
            importance=0.95,
            category="событие",
        ),
        schemas.ExtractedFact(
            fact="Стражник открыл ворота для отряда путников",
            importance=0.6,
            category="событие",
        ),
        schemas.ExtractedFact(
            fact="В подвале найден старый магический свиток",
            importance=0.7,
            category="предмет",
        ),
    ]
    result = memory_service.validate_extracted_facts(
        facts, "Alice", max_facts=2
    )
    assert len(result) == 2
    assert result[0].importance >= result[1].importance
    assert result[0].importance == pytest.approx(0.95)


@pytest.mark.asyncio
async def test_extract_and_save_stores_importance_category(
    db_session, chat, mock_client, db_engine
):
    character = create_characters(db_session, chat.id, 1)[0]
    user_msg = crud.create_message(
        db_session,
        schemas.MessageCreate(
            chat_id=chat.id,
            role="user",
            content="Hello, путник у входа в таверну",
        ),
    )
    char_msg = crud.create_message(
        db_session,
        schemas.MessageCreate(
            chat_id=chat.id,
            role="character",
            character_id=character.id,
            content="Привет, путник. Я Character A у входа в таверну.",
        ),
    )

    async def fake_extract(client, model, character_obj, round_text):
        return [
            schemas.ExtractedFact(
                fact="Игрок поприветствовал Character A у входа в таверну",
                category="событие",
                importance=0.75,
                witnessed=True,
            ),
            schemas.ExtractedFact(
                fact="они поговорили",  # should be filtered
                category="событие",
                importance=0.2,
                witnessed=True,
            ),
        ]

    test_session_factory = sessionmaker(bind=db_engine)
    character_snapshots = [
        schemas.CharacterRead.model_validate(character).model_dump(mode="python")
    ]
    round_snapshots = [
        schemas.MessageRead.model_validate(user_msg).model_dump(mode="json"),
        schemas.MessageRead.model_validate(char_msg).model_dump(mode="json"),
    ]

    with patch(
        "app.memory_service.ollama_client.extract_memories_for_character",
        side_effect=fake_extract,
    ), patch("app.memory.extraction.AsyncSessionLocal", test_session_factory):
        await memory_service._extract_and_save_memories(
            mock_client,
            chat.id,
            round_snapshots,
            character_snapshots,
            chat.model_name,
        )

    verify = test_session_factory()
    try:
        memories = crud.get_memories_by_character(verify, character.id)
        assert len(memories) == 1
        assert "таверну" in memories[0].content
        assert memories[0].importance == pytest.approx(0.75)
        assert memories[0].category == "событие"
    finally:
        verify.close()


def test_eviction_prefers_low_importance(db_session, chat):
    character = create_characters(db_session, chat.id, 1)[0]
    # Fill beyond limit: one high-importance + many low
    high = crud.create_memory(
        db_session,
        schemas.MemoryCreate(
            chat_id=chat.id,
            character_id=character.id,
            content="Критический факт: король объявил войну соседнему королевству",
            importance=0.99,
            category="событие",
        ),
    )
    assert high is not None
    for i in range(settings.max_memories_per_character):
        crud.create_memory(
            db_session,
            schemas.MemoryCreate(
                chat_id=chat.id,
                character_id=character.id,
                content=f"Малозначимый факт номер {i} о погоде на улице",
                importance=0.1,
                category="другое",
            ),
        )

    crud.ensure_memory_limit(db_session, character.id)
    remaining = crud.get_memories_by_character(db_session, character.id)
    assert len(remaining) == settings.max_memories_per_character
    contents = [m.content for m in remaining]
    assert any("король объявил войну" in c for c in contents)

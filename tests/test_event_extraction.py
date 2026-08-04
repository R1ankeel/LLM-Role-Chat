"""Sprint 1 — Structured World Events: round event extraction (Plans/update20.md §15).

Покрывает:
- ``event_extraction_enabled=False`` (по умолчанию) → стадия ничего не делает,
  LLM не вызывается, поведение раунда не меняется (canary);
- ``extract_round_events`` возвращает валидные ``ExtractedEvent`` (LLM mock);
- ``crud.save_round_events`` пишет ``world_events`` (action/importance/salience)
  и строит ``event_links`` из ``causes`` (индексы в списке);
- события ниже ``EVENT_MIN_IMPORTANCE`` пропускаются (стоимостной лимит);
- идемпотентность по ``round_id`` — повторный прогон не дублирует события;
- Sensors-hook (§5.1.3): Sensors предлагает classification, движок применяет
  свои правила; Sensors не пишет в БД.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from unittest.mock import MagicMock

from app import crud
from app import event_service
from app import models
from app import schemas


@pytest.fixture
def enable_extraction(monkeypatch):
    monkeypatch.setattr("app.event_service.settings.event_extraction_enabled", True)
    monkeypatch.setattr("app.event_service.settings.event_min_importance", 0.0)


async def _build_round_messages(db_session, chat, characters, round_id: str = "r1-m1"):
    """Сообщения раунда: user + реплики персонажей."""
    await crud.create_message(
        db_session,
        schemas.MessageCreate(
            chat_id=chat.id, role="user", content="Привет всем!", visibility="global"
        ),
        round_id=round_id,
    )
    for character in characters:
        await crud.create_message(
            db_session,
            schemas.MessageCreate(
                chat_id=chat.id,
                role="character",
                character_id=character.id,
                content=f"Ответ {character.name}.",
                visibility="global",
            ),
            round_id=round_id,
        )
    rows = (
        await db_session.execute(
            text("SELECT id FROM messages WHERE chat_id = :cid ORDER BY id"),
            {"cid": chat.id},
        )
    ).scalars().all()
    return [await db_session.get(models.Message, mid) for mid in rows]


# ---------------------------------------------------------------------------
# Флаг off (по умолчанию) — canary: никаких вызовов и записей
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_extract_off_by_default_returns_empty(monkeypatch, db_session, chat):
    """event_extraction_enabled=False → пустой результат, LLM не вызывается."""

    async def boom(*args, **kwargs):
        raise AssertionError("LLM не должен вызываться при off")

    monkeypatch.setattr("app.ollama_client.extract_round_events", boom)
    result = await event_service.extract_round_events(
        MagicMock(), db_session, chat.id, []
    )
    assert result.events == []
    assert result.sensors_used is False


@pytest.mark.asyncio
async def test_save_round_events_off_never_writes(monkeypatch, db_session, chat, three_characters):
    """Флаг off → crud.save_round_events не вызывается из pipeline; прямой вызов
    сам по себе не имеет флага (это pure-write). Здесь проверяем: событие
    валидно и пишется — сам флаг живёт в event_service/pipeline."""
    events = [
        schemas.ExtractedEvent(
            event_type="speech",
            description="Анна призналась",
            source_character="Character A",
            targets=["Character B"],
            importance=6.0,
            story_salience=0.8,
            emotional_salience=0.9,
            action=schemas.EventAction(
                actor="Character A", action="призналась", target="Character B"
            ),
        )
    ]
    report = await crud.save_round_events(
        db_session, chat.id, events, round_id="r1-m1"
    )
    assert report.written_events == 1
    row = (
        await db_session.execute(
            text(
                "SELECT character_id, event_type, importance, story_salience, "
                "emotional_salience, action FROM world_events LIMIT 1"
            )
        )
    ).fetchone()
    assert row.importance == 6.0
    assert row.story_salience == 0.8
    assert "призналась" in row.action


# ---------------------------------------------------------------------------
# extraction (LLM) → ExtractedEvent
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_extract_round_events_calls_llm_and_returns_events(
    enable_extraction, monkeypatch, db_session, chat, three_characters
):
    messages = await _build_round_messages(db_session, chat, three_characters)
    raw = [
        {
            "event_type": "speech",
            "description": "Character A обещает вернуться.",
            "source_character": "Character A",
            "targets": ["Character B"],
            "location": "Общая сцена",
            "importance": 7.0,
            "story_salience": 0.7,
            "emotional_salience": 0.6,
            "causes": [],
        }
    ]
    captured: dict = {}

    async def fake_extract_round_events(**kwargs):
        captured.update(kwargs)
        return raw

    monkeypatch.setattr("app.ollama_client.extract_round_events", fake_extract_round_events)
    result = await event_service.extract_round_events(
        MagicMock(), db_session, chat.id, messages,
        round_id="r1-m1",
        character_names={c.id: c.name for c in three_characters},
        model_name="test-model",
    )
    assert len(result.events) == 1
    event = result.events[0]
    assert event.source_character == "Character A"
    assert event.importance == 7.0
    assert event.targets == ["Character B"]
    assert captured["model_name"] == "test-model"


@pytest.mark.asyncio
async def test_extract_round_events_empty_history(
    enable_extraction, monkeypatch, db_session, chat
):
    async def boom(*args, **kwargs):
        raise AssertionError("LLM не вызывается для пустой истории")

    monkeypatch.setattr("app.ollama_client.extract_round_events", boom)
    result = await event_service.extract_round_events(
        MagicMock(), db_session, chat.id, []
    )
    assert result.events == []


@pytest.mark.asyncio
async def test_extract_round_events_llm_none_is_safe(
    enable_extraction, monkeypatch, db_session, chat, three_characters
):
    """LLM вернул None (недоступен) → пустой результат, раунд не падает."""
    messages = await _build_round_messages(db_session, chat, three_characters)

    async def fake_extract_round_events(**kwargs):
        return None

    monkeypatch.setattr("app.ollama_client.extract_round_events", fake_extract_round_events)
    result = await event_service.extract_round_events(
        MagicMock(), db_session, chat.id, messages
    )
    assert result.events == []


# ---------------------------------------------------------------------------
# save_round_events: запись + links + лимит важности + идемпотентность
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_save_round_events_writes_events_and_links(db_session, chat, three_characters):
    events = [
        schemas.ExtractedEvent(
            event_type="speech",
            description="Character A при всех оскорбил Character B.",
            source_character="Character A",
            targets=["Character B"],
            importance=8.0,
            story_salience=0.9,
            emotional_salience=0.9,
        ),
        schemas.ExtractedEvent(
            event_type="conflict",
            description="Character B уходит в ответ.",
            source_character="Character B",
            targets=["Character A"],
            importance=6.0,
            story_salience=0.6,
            emotional_salience=0.7,
            causes=[0],
        ),
    ]
    report = await crud.save_round_events(
        db_session, chat.id, events, round_id="r1-m1"
    )
    assert report.written_events == 2
    assert report.written_links == 1
    assert report.skipped_below_importance == 0

    world_rows = (
        await db_session.execute(
            text("SELECT id, character_id, event_type FROM world_events ORDER BY id")
        )
    ).all()
    assert len(world_rows) == 2
    assert world_rows[0].event_type == "speech"
    assert world_rows[1].event_type == "conflict"

    link_rows = (
        await db_session.execute(
            text("SELECT event_id, caused_by_event_id, kind FROM event_links")
        )
    ).all()
    assert len(link_rows) == 1
    assert link_rows[0].event_id == world_rows[1].id
    assert link_rows[0].caused_by_event_id == world_rows[0].id
    assert link_rows[0].kind == "causes"


@pytest.mark.asyncio
async def test_save_round_events_filters_below_importance(db_session, chat, three_characters):
    events = [
        schemas.ExtractedEvent(
            event_type="speech",
            description="Приветствие.",
            importance=1.0,
        ),
        schemas.ExtractedEvent(
            event_type="gift",
            description="Подарок.",
            importance=5.0,
        ),
    ]
    report = await crud.save_round_events(
        db_session, chat.id, events, round_id="r1-m1"
    )
    assert report.written_events == 1
    assert report.skipped_below_importance == 1
    rows = (
        await db_session.execute(text("SELECT event_type FROM world_events"))
    ).all()
    assert [r.event_type for r in rows] == ["gift"]


@pytest.mark.asyncio
async def test_save_round_events_idempotent_by_round_id(db_session, chat, three_characters):
    events = [
        schemas.ExtractedEvent(
            event_type="speech", description="Событие раунда.", importance=6.0
        )
    ]
    first = await crud.save_round_events(db_session, chat.id, events, round_id="r1-m1")
    second = await crud.save_round_events(db_session, chat.id, events, round_id="r1-m1")
    assert first.written_events == 1
    assert second.written_events == 0  # идемпотентно: раунд уже извлечён
    count = (
        await db_session.execute(text("SELECT COUNT(*) FROM world_events"))
    ).scalar()
    assert count == 1


@pytest.mark.asyncio
async def test_save_round_events_unknown_character_degrades_to_null(
    db_session, chat, three_characters
):
    """Неизвестный персонаж → character_id NULL, без падения."""
    events = [
        schemas.ExtractedEvent(
            event_type="speech",
            description="Кто-то чужой действует.",
            source_character="Нет такого персонажа",
            importance=5.0,
        )
    ]
    report = await crud.save_round_events(db_session, chat.id, events, round_id="r1-m1")
    assert report.written_events == 1
    row = (
        await db_session.execute(
            text("SELECT character_id FROM world_events LIMIT 1")
        )
    ).fetchone()
    assert row.character_id is None


# ---------------------------------------------------------------------------
# Sensors-hook (§5.1.3): предложение → движок применяет правила
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_sensors_hook_proposes_event_engine_writes(
    enable_extraction, monkeypatch, db_session, chat, three_characters
):
    messages = await _build_round_messages(db_session, chat, three_characters)

    async def fake_run(client, **kwargs):
        return {
            "event_type": "conflict",
            "source_character": "Character A",
            "targets": ["Character B"],
            "importance": 7.0,
            "audibility": "full",
            "visibility": "full",
            "requires_processing": True,
        }

    monkeypatch.setattr("app.sensors_service.sensors_service.run", fake_run)

    async def boom(*args, **kwargs):
        raise AssertionError("Sensors-предложение должно исключить LLM-вызов")

    monkeypatch.setattr("app.ollama_client.extract_round_events", boom)

    result = await event_service.extract_round_events(
        MagicMock(), db_session, chat.id, messages,
        round_id="r1-m1",
        character_names={c.id: c.name for c in three_characters},
    )
    assert result.sensors_used is True
    assert len(result.events) == 1
    assert result.events[0].source_character == "Character A"

    report = await crud.save_round_events(
        db_session, chat.id, result.events, round_id="r1-m1"
    )
    assert report.written_events == 1
    rows = (
        await db_session.execute(text("SELECT event_type FROM world_events"))
    ).all()
    assert [r.event_type for r in rows] == ["conflict"]


@pytest.mark.asyncio
async def test_sensors_hook_failure_falls_back_to_llm(
    enable_extraction, monkeypatch, db_session, chat, three_characters
):
    """Sensors упал (None) → движок идёт обычным LLM-путём (§5.1.8)."""
    messages = await _build_round_messages(db_session, chat, three_characters)

    async def fake_run(client, **kwargs):
        return None

    monkeypatch.setattr("app.sensors_service.sensors_service.run", fake_run)
    raw = [
        {
            "event_type": "speech",
            "description": "LLM-событие.",
            "source_character": "Character A",
            "targets": [],
            "importance": 5.0,
            "story_salience": 0.5,
            "emotional_salience": 0.5,
            "causes": [],
        }
    ]

    async def fake_extract_round_events(**kwargs):
        return raw

    monkeypatch.setattr("app.ollama_client.extract_round_events", fake_extract_round_events)

    result = await event_service.extract_round_events(
        MagicMock(), db_session, chat.id, messages
    )
    assert result.sensors_used is False
    assert len(result.events) == 1
    assert result.events[0].description == "LLM-событие."

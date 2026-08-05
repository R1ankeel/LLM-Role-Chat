"""Sprint 1 — Post-round pipeline orchestrator (Plans/update20.md §15).

Покрывает:
- порядок/полноту стадий (presence → event extraction → memory → relationships
  → story) и отчёт по каждой;
- изоляцию ошибок: падение одной стадии НЕ ломает раунд и остальные стадии
  (graceful degradation);
- event extraction стадию: пишет события+links под флагом, идемпотентность по
  round_id, no-op при флаге off;
- планирование memory/relationships как background-задач.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from unittest.mock import MagicMock

from app import crud
from app import schemas
from app.post_round_pipeline import run_post_round_pipeline


async def _round_ctx(db_session, chat, characters):
    """Раунд: user-сообщение + реплики, плюс все снапшоты для pipeline."""
    round_id = "r1-m1"
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
    from app import models

    ids = (
        await db_session.execute(
            text("SELECT id FROM messages WHERE chat_id = :cid ORDER BY id"),
            {"cid": chat.id},
        )
    ).scalars().all()
    messages = [await db_session.get(models.Message, mid) for mid in ids]
    character_ids = [c.id for c in characters]
    character_names = {c.id: c.name for c in characters}
    character_locations = {
        c.id: (getattr(c, "location", "") or "") for c in characters
    }
    round_snapshots = [
        {"id": m.id, "role": m.role, "content": m.content} for m in messages
    ]
    character_snapshots = [
        {"id": c.id, "name": c.name, "location": getattr(c, "location", "") or ""}
        for c in characters
    ]
    return (
        round_id,
        messages,
        character_ids,
        character_names,
        character_locations,
        round_snapshots,
        character_snapshots,
    )


def _pipeline_kwargs(ctx, db_session, chat, characters, client=None):
    (
        round_id,
        messages,
        character_ids,
        character_names,
        character_locations,
        round_snapshots,
        character_snapshots,
    ) = ctx
    return {
        "client": client or MagicMock(),
        "db": db_session,
        "chat_id": chat.id,
        "model_name": "test-model",
        "round_messages": messages,
        "character_ids": character_ids,
        "character_names": character_names,
        "characters": characters,
        "character_locations": character_locations,
        "round_id": round_id,
        "round_snapshots": round_snapshots,
        "character_snapshots": character_snapshots,
    }


@pytest.fixture
def enable_extraction(monkeypatch):
    monkeypatch.setattr("app.post_round_pipeline.settings.event_extraction_enabled", True)
    monkeypatch.setattr("app.event_service.settings.event_extraction_enabled", True)
    monkeypatch.setattr("app.event_service.settings.event_min_importance", 0.0)


# ---------------------------------------------------------------------------
# Полнота и порядок стадий (флаг off → event extraction no-op)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pipeline_runs_all_stages(db_session, chat, three_characters):
    ctx = await _round_ctx(db_session, chat, three_characters)
    kwargs = _pipeline_kwargs(ctx, db_session, chat, three_characters)
    report = await run_post_round_pipeline(**kwargs)

    assert set(report.keys()) == {
        "presence", "event_extraction", "memory", "relationships",
        "character_state", "beliefs", "story", "story_threads", "plans", "crisis",
    }
    assert report["presence"]["ok"] is True
    # флаг off по умолчанию → стадия извлечения событий — no-op
    assert report["event_extraction"]["ok"] is True
    assert report["event_extraction"].get("skipped") == "flag off"
    assert report["memory"]["ok"] is True
    assert report["memory"].get("skipped") == "no processor"
    assert report["relationships"]["ok"] is True
    assert report["relationships"].get("skipped") == "analyzer off"
    # character_state — флаг off по умолчанию → no-op
    assert report["character_state"]["ok"] is True
    assert report["character_state"].get("skipped") == "flag off"
    # beliefs — флаг off по умолчанию → no-op
    assert report["beliefs"]["ok"] is True
    assert report["beliefs"].get("skipped") == "flag off"
    assert report["story"]["ok"] is True
    # Sprint 10: story_threads/plans — флаг off по умолчанию → no-op
    assert report["story_threads"]["ok"] is True
    assert report["story_threads"].get("skipped") == "flag off"
    assert report["plans"]["ok"] is True
    assert report["plans"].get("skipped") == "flag off"


@pytest.mark.asyncio
async def test_pipeline_respects_stage_subset(db_session, chat, three_characters):
    """Можно запустить подмножество стадий (stages param)."""
    ctx = await _round_ctx(db_session, chat, three_characters)
    kwargs = _pipeline_kwargs(ctx, db_session, chat, three_characters)
    report = await run_post_round_pipeline(**kwargs, stages={"story"})
    assert set(report.keys()) == {"story"}
    assert report["story"]["ok"] is True


# ---------------------------------------------------------------------------
# Изоляция ошибок: падение одной стадии не ломает раунд
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pipeline_stage_failure_is_isolated(
    monkeypatch, db_session, chat, three_characters
):
    ctx = await _round_ctx(db_session, chat, three_characters)
    kwargs = _pipeline_kwargs(ctx, db_session, chat, three_characters)

    async def boom(*args, **kwargs):
        raise RuntimeError("presence сломался")

    monkeypatch.setattr("app.crud.compute_and_save_presence_for_round", boom)
    report = await run_post_round_pipeline(**kwargs)

    assert report["presence"]["ok"] is False
    # остальные стадии выполнены, pipeline не упал
    assert report["story"]["ok"] is True
    assert report["event_extraction"]["ok"] is True
    assert report["memory"]["ok"] is True


# ---------------------------------------------------------------------------
# Event extraction стадия: запись + идемпотентность
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pipeline_event_extraction_writes_events(
    enable_extraction, monkeypatch, db_session, chat, three_characters
):
    ctx = await _round_ctx(db_session, chat, three_characters)
    kwargs = _pipeline_kwargs(ctx, db_session, chat, three_characters)
    raw = [
        {
            "event_type": "speech",
            "description": "Character A говорит.",
            "source_character": "Character A",
            "targets": ["Character B"],
            "location": "",
            "importance": 7.0,
            "story_salience": 0.7,
            "emotional_salience": 0.6,
            "causes": [],
        }
    ]

    async def fake_extract_round_events(**kwargs):
        return raw

    monkeypatch.setattr("app.ollama_client.extract_round_events", fake_extract_round_events)

    report = await run_post_round_pipeline(**kwargs)
    assert report["event_extraction"]["written"] == 1
    count = (
        await db_session.execute(text("SELECT COUNT(*) FROM world_events"))
    ).scalar()
    assert count == 1


@pytest.mark.asyncio
async def test_pipeline_event_extraction_idempotent_second_run(
    enable_extraction, monkeypatch, db_session, chat, three_characters
):
    ctx = await _round_ctx(db_session, chat, three_characters)
    kwargs = _pipeline_kwargs(ctx, db_session, chat, three_characters)
    raw = [
        {
            "event_type": "speech",
            "description": "Событие раунда.",
            "source_character": "Character A",
            "targets": [],
            "location": "",
            "importance": 6.0,
            "story_salience": 0.5,
            "emotional_salience": 0.5,
            "causes": [],
        }
    ]

    async def fake_extract_round_events(**kwargs):
        return raw

    monkeypatch.setattr("app.ollama_client.extract_round_events", fake_extract_round_events)

    first = await run_post_round_pipeline(**kwargs)
    second = await run_post_round_pipeline(**kwargs)
    assert first["event_extraction"]["written"] == 1
    # повторный прогон того же раунда идемпотентен
    assert second["event_extraction"]["written"] == 0
    count = (
        await db_session.execute(text("SELECT COUNT(*) FROM world_events"))
    ).scalar()
    assert count == 1


# ---------------------------------------------------------------------------
# Memory / relationships планируются как background-задачи
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pipeline_memory_stage_schedules_background(
    db_session, chat, three_characters
):
    ctx = await _round_ctx(db_session, chat, three_characters)
    kwargs = _pipeline_kwargs(ctx, db_session, chat, three_characters)
    calls: list = []
    captured_coros: list = []

    async def fake_memory_processor(client, chat_id, round_snapshots, character_snapshots, model_name):
        calls.append((chat_id, model_name))

    def fake_create_task(coro):
        captured_coros.append(coro)
        coro.close()
        return MagicMock()

    from unittest.mock import patch

    with patch("app.post_round_pipeline.asyncio.create_task", fake_create_task):
        report = await run_post_round_pipeline(
            **kwargs, memory_processor=fake_memory_processor
        )
    assert report["memory"]["ok"] is True
    assert report["memory"].get("scheduled") is True
    assert len(captured_coros) == 1
    assert calls == []  # фоновая задача не выполнилась синхронно


@pytest.mark.asyncio
async def test_pipeline_relationships_stage_schedules_background(
    db_session, chat, three_characters, monkeypatch
):
    monkeypatch.setattr("app.post_round_pipeline.settings.relationship_analyzer_enabled", True)
    ctx = await _round_ctx(db_session, chat, three_characters)
    kwargs = _pipeline_kwargs(ctx, db_session, chat, three_characters)
    captured_locals: list = []

    async def fake_analyzer(client, chat_id, model_name, round_snapshots, character_snapshots, round_id=None):
        pass  # фоновая задача не выполняется синхронно

    def fake_create_task(coro):
        frame = coro.cr_frame
        captured_locals.append(dict(frame.f_locals) if frame is not None else {})
        coro.close()
        return MagicMock()

    from unittest.mock import patch

    with patch("app.post_round_pipeline.asyncio.create_task", fake_create_task):
        report = await run_post_round_pipeline(**kwargs, relationship_analyzer=fake_analyzer)
    assert report["relationships"]["ok"] is True
    assert report["relationships"].get("scheduled") is True
    assert len(captured_locals) == 1
    assert captured_locals[0]["chat_id"] == chat.id
    assert captured_locals[0]["model_name"] == "test-model"
    assert captured_locals[0]["round_id"] == "r1-m1"

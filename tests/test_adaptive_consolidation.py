"""Tests for Adaptive Consolidation (Sprint 12, Plans/update20.md §20).

Covers the score-based soft/hard/critical trigger that replaces the 24h timer:
- idle chats must NOT consolidate (score≈0 → skip);
- critical events trigger immediately regardless of score;
- soft/hard thresholds; critical dedup (≤ N per round);
- full adaptive set (memory+summary+relationship+anchors+story+index) for
  hard/critical and the reduced set for soft;
- canary flag ``adaptive_consolidation_enabled`` (off by default).
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from unittest.mock import AsyncMock, patch, MagicMock

from app import crud
from app import memory_service
from app import models
from app import schemas
from app.config import settings
from tests.conftest import create_characters


@pytest.fixture
def mock_client():
    return MagicMock()


async def _add_messages(db: AsyncSession, chat_id: int, count: int, character_id=None):
    for i in range(count):
        await crud.create_message(
            db,
            schemas.MessageCreate(
                chat_id=chat_id,
                role="character",
                content=f"Строка {i} длинного диалога персонажей",
                character_id=character_id,
            ),
        )


async def _add_world_event(
    db: AsyncSession,
    chat_id: int,
    *,
    importance=None,
    event_type="event",
    action="{}",
    round_id=None,
):
    event = models.WorldEvent(
        chat_id=chat_id,
        event_type=event_type,
        importance=importance,
        action=action,
        round_id=round_id,
    )
    db.add(event)
    await db.commit()
    return event


async def _add_relationship(db: AsyncSession, chat_id: int, source_id: int, target_id: int):
    rel = models.CharacterRelationship(
        chat_id=chat_id,
        source_character_id=source_id,
        target_character_id=target_id,
    )
    db.add(rel)
    await db.commit()
    await db.refresh(rel)
    return rel


# ---------------------------------------------------------------------------
# Pure score / critical helpers
# ---------------------------------------------------------------------------


def test_compute_consolidation_score_defaults():
    assert memory_service.compute_consolidation_score({}) == 0.0
    assert memory_service.compute_consolidation_score({"messages": 10}) == 10.0
    assert memory_service.compute_consolidation_score({"facts": 4}) == 12.0
    assert memory_service.compute_consolidation_score({"anchors": 2}) == 14.0
    counts = {"messages": 10, "events": 5, "facts": 3, "rel_events": 2, "story_events": 1, "anchors": 1}
    assert memory_service.compute_consolidation_score(counts) == (
        10 * 1 + 5 * 2 + 3 * 3 + 2 * 4 + 1 * 5 + 1 * 7
    )


def test_compute_consolidation_score_custom_weights():
    counts = {"messages": 2, "events": 3}
    assert memory_service.compute_consolidation_score(counts, weights=(1, 1, 1, 1, 1, 1)) == 5.0


def test_is_critical_event_by_importance():
    class Ev:
        importance = 9.0
        event_type = "speech"
        action = "{}"

    assert memory_service.is_critical_event(Ev()) is True

    class Ev2:
        importance = 3.0
        event_type = "move"
        action = "{}"

    assert memory_service.is_critical_event(Ev2()) is False


def test_is_critical_event_by_keyword():
    class Ev:
        importance = 2.0
        event_type = "event"
        action = {"actor": "Борис", "action": "предательство"}

    assert memory_service.is_critical_event(Ev()) is True


# ---------------------------------------------------------------------------
# evaluate_consolidation — deterministic decisions
# ---------------------------------------------------------------------------


async def test_evaluate_idle_chat_skips(db_session: AsyncSession):
    chat = await crud.create_chat(db_session, schemas.ChatCreate(name="Idle"))
    decision = await memory_service.evaluate_consolidation(db_session, chat.id)
    assert decision["level"] == "skip"
    assert decision["score_soft"] == 0.0
    assert decision["score_hard"] == 0.0


async def test_evaluate_soft_threshold(db_session: AsyncSession):
    chat = await crud.create_chat(db_session, schemas.ChatCreate(name="Soft"))
    await _add_messages(db_session, chat.id, 30)  # score 30 -> soft (< hard 50)
    decision = await memory_service.evaluate_consolidation(db_session, chat.id)
    assert decision["level"] == "soft"
    assert decision["score_soft"] == 30.0
    assert decision["score_hard"] == 30.0


async def test_evaluate_hard_threshold(db_session: AsyncSession):
    chat = await crud.create_chat(db_session, schemas.ChatCreate(name="Hard"))
    await _add_messages(db_session, chat.id, 60)  # score 60 >= hard 50
    decision = await memory_service.evaluate_consolidation(db_session, chat.id)
    assert decision["level"] == "hard"


async def test_evaluate_critical_immediate(db_session: AsyncSession):
    chat = await crud.create_chat(db_session, schemas.ChatCreate(name="Critical"))
    await _add_world_event(db_session, chat.id, importance=9.0, round_id="R1")
    # Tiny score — critical must still trigger immediately.
    decision = await memory_service.evaluate_consolidation(db_session, chat.id)
    assert decision["level"] == "critical"
    assert decision["critical"] is not None


async def test_evaluate_critical_dedup_cap(db_session: AsyncSession):
    chat = await crud.create_chat(db_session, schemas.ChatCreate(name="Dedup"))
    await _add_world_event(db_session, chat.id, importance=9.0, round_id="R1")
    await crud.upsert_consolidation_state(
        db_session,
        chat.id,
        counters={"critical_round": "R1", "critical_count": 2},
    )
    decision = await memory_service.evaluate_consolidation(db_session, chat.id, round_id="R1")
    # Cap reached for round R1 and score is below thresholds -> skip.
    assert decision["level"] == "skip"
    assert "dedup" in decision["reason"]


async def test_evaluate_critical_dedup_other_round(db_session: AsyncSession):
    chat = await crud.create_chat(db_session, schemas.ChatCreate(name="Dedup2"))
    await _add_world_event(db_session, chat.id, importance=9.0, round_id="R2")
    await crud.upsert_consolidation_state(
        db_session,
        chat.id,
        counters={"critical_round": "R1", "critical_count": 2},
    )
    decision = await memory_service.evaluate_consolidation(db_session, chat.id, round_id="R2")
    assert decision["level"] == "critical"


# ---------------------------------------------------------------------------
# schedule_adaptive_consolidation — trigger + dedup + enqueue
# ---------------------------------------------------------------------------


async def test_schedule_flag_off(db_session: AsyncSession):
    chat = await crud.create_chat(db_session, schemas.ChatCreate(name="Off"))
    await _add_messages(db_session, chat.id, 100)
    with patch(
        "app.memory_service.enqueue_consolidation_job", new_callable=AsyncMock
    ) as mock_enqueue:
        decision = await memory_service.schedule_adaptive_consolidation(
            db_session, chat_id=chat.id
        )
        assert decision["level"] == "skip"
        mock_enqueue.assert_not_called()


async def test_schedule_idle_skips_without_job(db_session: AsyncSession):
    original = settings.adaptive_consolidation_enabled
    settings.adaptive_consolidation_enabled = True
    try:
        chat = await crud.create_chat(db_session, schemas.ChatCreate(name="Idle2"))
        with patch(
            "app.memory_service.enqueue_consolidation_job", new_callable=AsyncMock
        ) as mock_enqueue:
            decision = await memory_service.schedule_adaptive_consolidation(
                db_session, chat_id=chat.id
            )
            assert decision["level"] == "skip"
            mock_enqueue.assert_not_called()
    finally:
        settings.adaptive_consolidation_enabled = original


async def test_schedule_soft_triggers_then_dedups(db_session: AsyncSession):
    original = settings.adaptive_consolidation_enabled
    settings.adaptive_consolidation_enabled = True
    try:
        chat = await crud.create_chat(db_session, schemas.ChatCreate(name="Soft2"))
        await _add_messages(db_session, chat.id, 30)
        with patch(
            "app.memory_service.enqueue_consolidation_job", new_callable=AsyncMock
        ) as mock_enqueue:
            mock_enqueue.return_value = MagicMock(id=7)
            first = await memory_service.schedule_adaptive_consolidation(
                db_session, chat_id=chat.id, model_name="test"
            )
            assert first["level"] == "soft"
            assert first["enqueued"] is True
            mock_enqueue.assert_awaited_once()

            # Baseline advanced -> the same events no longer trigger.
            second = await memory_service.schedule_adaptive_consolidation(
                db_session, chat_id=chat.id
            )
            assert second["level"] == "skip"
            assert mock_enqueue.await_count == 1
    finally:
        settings.adaptive_consolidation_enabled = original


async def test_schedule_critical_enqueues_hard(db_session: AsyncSession):
    original = settings.adaptive_consolidation_enabled
    settings.adaptive_consolidation_enabled = True
    try:
        chat = await crud.create_chat(db_session, schemas.ChatCreate(name="Crit2"))
        await _add_world_event(db_session, chat.id, importance=9.5, round_id="R9")
        with patch(
            "app.memory_service.enqueue_consolidation_job", new_callable=AsyncMock
        ) as mock_enqueue:
            mock_enqueue.return_value = MagicMock(id=9)
            decision = await memory_service.schedule_adaptive_consolidation(
                db_session, chat_id=chat.id, model_name="test", round_id="R9"
            )
            assert decision["level"] == "critical"
            assert decision["enqueued"] is True
            kwargs = mock_enqueue.await_args.kwargs
            assert kwargs["level"] == "critical"
            assert kwargs["chat_id"] == chat.id
            assert kwargs["model_name"] == "test"
            # The consolidation window (pre-trigger baseline) travels in payload.
            assert kwargs["since_soft"] is not None
            assert kwargs["since_hard"] is not None
            assert decision["since_soft"] is not None
    finally:
        settings.adaptive_consolidation_enabled = original


async def test_schedule_critical_dedup_within_round(db_session: AsyncSession):
    original = settings.adaptive_consolidation_enabled
    settings.adaptive_consolidation_enabled = True
    try:
        chat = await crud.create_chat(db_session, schemas.ChatCreate(name="Crit3"))
        await _add_world_event(db_session, chat.id, importance=9.5, round_id="R3")
        with patch(
            "app.memory_service.enqueue_consolidation_job", new_callable=AsyncMock
        ) as mock_enqueue:
            mock_enqueue.return_value = MagicMock(id=3)
            first = await memory_service.schedule_adaptive_consolidation(
                db_session, chat_id=chat.id, round_id="R3"
            )
            assert first["level"] == "critical"
            state = await crud.get_consolidation_state(db_session, chat.id)
            counters = memory_service._parse_consolidation_counters(state)
            assert counters["critical_round"] == "R3"
            assert counters["critical_count"] == 1
    finally:
        settings.adaptive_consolidation_enabled = original


# ---------------------------------------------------------------------------
# consolidate_chat_adaptive — full adaptive set
# ---------------------------------------------------------------------------


async def test_consolidate_adaptive_soft_reduced_set(db_session: AsyncSession, mock_client):
    chat = await crud.create_chat(db_session, schemas.ChatCreate(name="SoftRun"))
    report = await memory_service.consolidate_chat_adaptive(
        db_session, mock_client, chat_id=chat.id, model_name="test", level="soft"
    )
    assert report["level"] == "soft"
    assert "memory" in report
    assert "summary" in report
    # Reduced set: hard-only components are absent.
    assert "relationship" not in report
    assert "anchors" not in report
    assert "story" not in report
    assert "index" not in report


async def test_consolidate_adaptive_hard_full_set(db_session: AsyncSession, mock_client):
    chat = await crud.create_chat(db_session, schemas.ChatCreate(name="HardRun"))
    characters = await create_characters(db_session, chat.id, 2)
    await _add_messages(db_session, chat.id, 1, character_id=characters[0].id)
    rel = await _add_relationship(
        db_session, chat.id, characters[0].id, characters[1].id
    )
    # Two duplicate anchors for the same (relationship, event=None).
    db_session.add(
        models.MemoryAnchor(
            relationship_id=rel.id, emotion="trust", valence=0.5, intensity=0.6, importance=0.8
        )
    )
    db_session.add(
        models.MemoryAnchor(
            relationship_id=rel.id, emotion="trust", valence=0.4, intensity=0.5, importance=0.3
        )
    )
    await db_session.commit()

    original_embedding = settings.embedding_enabled
    settings.embedding_enabled = False
    try:
        with patch(
            "app.memory_service.ollama_client.summarize_for_character",
            new_callable=AsyncMock,
        ) as mock_summary:
            mock_summary.return_value = "Обновлённое резюме персонажа."
            report = await memory_service.consolidate_chat_adaptive(
                db_session, mock_client, chat_id=chat.id, model_name="test", level="hard"
            )
    finally:
        settings.embedding_enabled = original_embedding

    assert report["level"] == "hard"
    assert report["summary"]["updated"] == 1
    assert report["anchors"]["removed"] == 1
    assert report["relationship"]["relationships"] == 1
    assert report["story"]["skipped"] == "story consolidation flag off"
    assert report["index"]["skipped"] == "embedding disabled"

    summary = await crud.get_character_summary(db_session, characters[0].id)
    assert summary is not None
    assert summary.content == "Обновлённое резюме персонажа."


# ---------------------------------------------------------------------------
# consolidate_memories_job chat_id filter (legacy path stays intact)
# ---------------------------------------------------------------------------


async def test_consolidate_memories_job_chat_filter(db_session: AsyncSession, mock_client):
    chat_a = await crud.create_chat(db_session, schemas.ChatCreate(name="A"))
    chars_a = await create_characters(db_session, chat_a.id, 1)
    await crud.create_memory(
        db_session,
        schemas.MemoryCreate(
            chat_id=chat_a.id,
            character_id=chars_a[0].id,
            content="Факт из чата A",
            importance=0.5,
            category="событие",
        ),
    )
    chat_b = await crud.create_chat(db_session, schemas.ChatCreate(name="B"))
    chars_b = await create_characters(db_session, chat_b.id, 1)
    await crud.create_memory(
        db_session,
        schemas.MemoryCreate(
            chat_id=chat_b.id,
            character_id=chars_b[0].id,
            content="Факт из чата B",
            importance=0.5,
            category="событие",
        ),
    )

    result = await memory_service.consolidate_memories_job(
        db_session, mock_client, "test", chat_id=chat_a.id
    )
    assert result["status"] == "completed"
    assert result["chars_processed"] == 1  # only chat A


# ---------------------------------------------------------------------------
# post-round pipeline stage
# ---------------------------------------------------------------------------


async def test_post_round_stage_flag_off(db_session: AsyncSession):
    from app.post_round_pipeline import _stage_adaptive_consolidation

    original = settings.adaptive_consolidation_enabled
    settings.adaptive_consolidation_enabled = False
    try:
        chat = await crud.create_chat(db_session, schemas.ChatCreate(name="StageOff"))
        report = await _stage_adaptive_consolidation(
            db_session, chat_id=chat.id, model_name="test", round_id="R1"
        )
        assert report["ok"] is True
        assert report["skipped"] == "flag off"
    finally:
        settings.adaptive_consolidation_enabled = original


async def test_post_round_stage_idle_no_enqueue(db_session: AsyncSession):
    from app.post_round_pipeline import _stage_adaptive_consolidation

    original = settings.adaptive_consolidation_enabled
    settings.adaptive_consolidation_enabled = True
    try:
        chat = await crud.create_chat(db_session, schemas.ChatCreate(name="StageIdle"))
        report = await _stage_adaptive_consolidation(
            db_session, chat_id=chat.id, model_name="test", round_id="R1"
        )
        assert report["ok"] is True
        assert report["decision"]["level"] == "skip"
    finally:
        settings.adaptive_consolidation_enabled = original


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

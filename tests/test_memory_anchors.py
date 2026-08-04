"""Sprint 2 — Memory Architecture v2: эмоциональные якоря (Plans/update20.md §7).

Проверяет:
- crud.create_memory_anchor: клампинг valence/intensity/importance;
- anchor_activation_score: важность × свежесть;
- select_top_anchors: top-K активация и дедупликация по event_id;
- get_anchors_for_relationship: порядок по свежести;
- get_anchors_for_relationships: группировка одним запросом;
- anchor-запись из значимого RelationshipEvent (расширение
  ``_maybe_create_memory_from_event``), включение через ANCHORS_ENABLED.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app import crud
from app import relationship_service
from app.config import settings
from app.models import MemoryAnchor, RelationshipEvent
from tests.conftest import create_characters


@pytest.mark.asyncio
async def test_create_memory_anchor_clamps_values(db_session, chat):
    characters = await create_characters(db_session, chat.id, 2)
    rel = await relationship_service.get_or_create_relationship(
        db_session, chat.id, characters[0].id, characters[1].id
    )
    anchor = await crud.create_memory_anchor(
        db_session,
        relationship_id=rel.id,
        event_id=None,
        emotion="тепло",
        valence=5.0,
        intensity=-2.0,
        importance=7.0,
    )
    assert anchor.id is not None
    assert anchor.valence == pytest.approx(1.0)
    assert anchor.intensity == pytest.approx(0.0)
    assert anchor.importance == pytest.approx(1.0)
    assert anchor.emotion == "тепло"


def test_activation_score_prefers_recent_and_important():
    now = datetime(2026, 1, 10, 12, 0)
    fresh = MemoryAnchor(
        relationship_id=1, event_id=1, importance=0.5, timestamp=now
    )
    old_high = MemoryAnchor(
        relationship_id=1,
        event_id=2,
        importance=1.0,
        timestamp=now - timedelta(days=10),
    )
    # fresh: 0.5 * 1.0 = 0.5; old_high: 1.0 * (1/11) ≈ 0.091
    assert crud.anchor_activation_score(fresh, now) > crud.anchor_activation_score(
        old_high, now
    )


def test_select_top_anchors_respects_max_k():
    now = datetime(2026, 1, 10, 12, 0)
    anchors = [
        MemoryAnchor(
            relationship_id=1, event_id=i, importance=imp, timestamp=now
        )
        for i, imp in enumerate((0.9, 0.7, 0.5, 0.3))
    ]
    top = crud.select_top_anchors(anchors, max_k=2, now=now)
    assert len(top) == 2
    assert [a.event_id for a in top] == [0, 1]


def test_select_top_anchors_dedupes_by_event():
    """Один канонический event → не более одного якоря в top-K (уникальность)."""
    now = datetime(2026, 1, 10, 12, 0)
    anchors = [
        MemoryAnchor(relationship_id=1, event_id=1, importance=0.9, timestamp=now),
        MemoryAnchor(relationship_id=1, event_id=1, importance=0.4, timestamp=now),
        MemoryAnchor(relationship_id=1, event_id=2, importance=0.8, timestamp=now),
    ]
    top = crud.select_top_anchors(anchors, max_k=5, now=now)
    event_ids = [a.event_id for a in top]
    assert event_ids == [1, 2]
    assert len(event_ids) == len(set(event_ids))
    assert top[0].importance == pytest.approx(0.9)


def test_select_top_anchors_empty_or_zero():
    assert crud.select_top_anchors([], max_k=3) == []
    now = datetime(2026, 1, 10, 12, 0)
    anchors = [MemoryAnchor(relationship_id=1, event_id=1, importance=0.8, timestamp=now)]
    assert crud.select_top_anchors(anchors, max_k=0, now=now) == []


@pytest.mark.asyncio
async def test_get_anchors_for_relationship_ordered_by_recency(db_session, chat):
    characters = await create_characters(db_session, chat.id, 2)
    rel = await relationship_service.get_or_create_relationship(
        db_session, chat.id, characters[0].id, characters[1].id
    )
    base = datetime(2026, 1, 5, 12, 0)
    await crud.create_memory_anchor(
        db_session, relationship_id=rel.id, emotion="холод",
        valence=-0.5, intensity=0.4, importance=0.6, timestamp=base,
    )
    await crud.create_memory_anchor(
        db_session, relationship_id=rel.id, emotion="тепло",
        valence=0.8, intensity=0.9, importance=0.9, timestamp=base + timedelta(hours=5),
    )
    anchors = await crud.get_anchors_for_relationship(db_session, rel.id)
    assert len(anchors) == 2
    assert anchors[0].emotion == "тепло"  # свежее — первым
    assert anchors[1].emotion == "холод"


@pytest.mark.asyncio
async def test_get_anchors_for_relationships_groups(db_session, chat):
    characters = await create_characters(db_session, chat.id, 3)
    rel_ab = await relationship_service.get_or_create_relationship(
        db_session, chat.id, characters[0].id, characters[1].id
    )
    rel_ac = await relationship_service.get_or_create_relationship(
        db_session, chat.id, characters[0].id, characters[2].id
    )
    await crud.create_memory_anchor(db_session, relationship_id=rel_ab.id, emotion="a")
    await crud.create_memory_anchor(db_session, relationship_id=rel_ab.id, emotion="b")
    await crud.create_memory_anchor(db_session, relationship_id=rel_ac.id, emotion="c")
    grouped = await crud.get_anchors_for_relationships(db_session, [rel_ab.id, rel_ac.id])
    assert len(grouped[rel_ab.id]) == 2
    assert len(grouped[rel_ac.id]) == 1


@pytest.mark.asyncio
async def test_anchor_written_from_significant_event(db_session, chat, monkeypatch):
    """ANCHORS_ENABLED → значимый RelationshipEvent пишет якорь + social-память."""
    monkeypatch.setattr(settings, "anchors_enabled", True)
    monkeypatch.setattr(settings, "memory_types_enabled", True)
    monkeypatch.setattr(settings, "relationship_memory_enabled", True)

    characters = await create_characters(db_session, chat.id, 2)
    rel = await relationship_service.get_or_create_relationship(
        db_session, chat.id, characters[0].id, characters[1].id
    )
    event = RelationshipEvent(
        relationship_id=rel.id,
        kind="llm",
        description="Аня тепло обняла Борю после ссоры",
        reason="привязанность выросла",
        delta_affection=30,
        delta_trust=10,
        delta_attraction=0,
        delta_resentment=0,
        delta_jealousy=0,
        affection_after=80,
        trust_after=60,
        attraction_after=0,
        resentment_after=0,
        jealousy_after=0,
        importance=8,
        source_message_ids="[]",
    )
    db_session.add(event)
    await db_session.commit()

    created = await relationship_service._maybe_create_memory_from_event(
        db_session, rel, event, chat.id
    )
    assert created is not None
    assert created.memory_type == "social"  # memory_types_enabled=True

    anchors = await crud.get_anchors_for_relationship(db_session, rel.id)
    assert len(anchors) == 1
    assert anchors[0].emotion == "тепло"
    assert anchors[0].valence == pytest.approx(1.0)
    assert anchors[0].importance == pytest.approx(0.8)


@pytest.mark.asyncio
async def test_no_anchor_without_flag(db_session, chat, monkeypatch):
    """ANCHORS_ENABLED=False (по умолчанию) → якорь не пишется (legacy-поведение)."""
    monkeypatch.setattr(settings, "anchors_enabled", False)
    monkeypatch.setattr(settings, "relationship_memory_enabled", True)

    characters = await create_characters(db_session, chat.id, 2)
    rel = await relationship_service.get_or_create_relationship(
        db_session, chat.id, characters[0].id, characters[1].id
    )
    event = RelationshipEvent(
        relationship_id=rel.id,
        kind="llm",
        description="Аня и Боря поссорились",
        reason="обида выросла",
        delta_resentment=25,
        delta_affection=-15,
        delta_trust=0,
        delta_attraction=0,
        delta_jealousy=0,
        affection_after=40,
        trust_after=50,
        attraction_after=0,
        resentment_after=25,
        jealousy_after=0,
        importance=6,
        source_message_ids="[]",
    )
    db_session.add(event)
    await db_session.commit()

    created = await relationship_service._maybe_create_memory_from_event(
        db_session, rel, event, chat.id
    )
    assert created is not None  # память создаётся как раньше
    anchors = await crud.get_anchors_for_relationship(db_session, rel.id)
    assert anchors == []

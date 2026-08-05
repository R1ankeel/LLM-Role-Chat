"""Anchor activation in the relationship prompt block (Sprint 7, §7/§13).

When ``ANCHORS_ENABLED``, ``build_relationships_block`` loads the memory anchors
of each outgoing relationship (one query) and renders the top-K by
``importance × recency`` (``crud.select_top_anchors``, dedup by event_id) as
``якорь: {emotion} (важность {importance:.1f})`` lines. With the canary flag off
the block is the legacy text (no anchor lines, no anchor reads).
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime, timedelta

from app import crud, models
from app.config import settings
from app.relationship_service import (
    build_relationships_block,
    get_or_create_relationship,
)


@pytest.fixture(autouse=True)
def _anchors_off_by_default(monkeypatch):
    monkeypatch.setattr(settings, "anchors_enabled", False)


def _rel_ids_from_block(block: str, names: list[str]) -> list[int]:
    return [i for i, name in enumerate(names) if name in block]


class TestBuildRelationshipsBlockWithAnchors:
    async def test_anchors_rendered_when_enabled(
        self, db_session: AsyncSession, chat, three_characters, monkeypatch,
    ):
        a, b, _ = three_characters
        rel = await get_or_create_relationship(db_session, chat.id, a.id, b.id)
        await crud.create_memory_anchor(
            db_session, relationship_id=rel.id, emotion="тепло",
            importance=0.8,
        )
        await crud.create_memory_anchor(
            db_session, relationship_id=rel.id, emotion="настороженность",
            importance=0.6,
        )
        monkeypatch.setattr(settings, "anchors_enabled", True)
        monkeypatch.setattr(settings, "relationship_anchor_max", 3)

        block = await build_relationships_block(
            db_session,
            chat.id,
            a.id,
            "Character A",
            {b.id: "Character B"},
        )
        assert "якорь: тепло (важность 0.8)" in block
        assert "якорь: настороженность (важность 0.6)" in block

    async def test_no_anchors_when_disabled(
        self, db_session: AsyncSession, chat, three_characters,
    ):
        a, b, _ = three_characters
        rel = await get_or_create_relationship(db_session, chat.id, a.id, b.id)
        await crud.create_memory_anchor(
            db_session, relationship_id=rel.id, emotion="тепло", importance=0.8,
        )
        block = await build_relationships_block(
            db_session, chat.id, a.id, "Character A", {b.id: "Character B"}
        )
        assert "якорь" not in block

    async def test_top_k_limits_anchors(
        self, db_session: AsyncSession, chat, three_characters, monkeypatch,
    ):
        a, b, _ = three_characters
        rel = await get_or_create_relationship(db_session, chat.id, a.id, b.id)
        for emotion, importance in [("одно", 0.9), ("два", 0.8), ("три", 0.7), ("четыре", 0.6)]:
            await crud.create_memory_anchor(
                db_session, relationship_id=rel.id, emotion=emotion,
                importance=importance,
            )
        monkeypatch.setattr(settings, "anchors_enabled", True)
        monkeypatch.setattr(settings, "relationship_anchor_max", 2)

        block = await build_relationships_block(
            db_session, chat.id, a.id, "Character A", {b.id: "Character B"}
        )
        assert "якорь: одно" in block
        assert "якорь: два" in block
        assert "якорь: три" not in block
        assert "якорь: четыре" not in block

    async def test_dedup_by_event_id(
        self, db_session: AsyncSession, chat, three_characters, monkeypatch,
    ):
        a, b, _ = three_characters
        rel = await get_or_create_relationship(db_session, chat.id, a.id, b.id)
        # Two anchors from the SAME canonical event: only one wins top-K.
        world_event = models.WorldEvent(
            chat_id=chat.id,
            character_id=a.id,
            event_type="speech",
            round_id="r-dedup",
            target_character_ids="[]",
            action="{}",
        )
        db_session.add(world_event)
        await db_session.commit()
        await db_session.refresh(world_event)

        await crud.create_memory_anchor(
            db_session, relationship_id=rel.id, event_id=world_event.id,
            emotion="первый", importance=0.9,
        )
        await crud.create_memory_anchor(
            db_session, relationship_id=rel.id, event_id=world_event.id,
            emotion="дубль", importance=0.95,
        )
        monkeypatch.setattr(settings, "anchors_enabled", True)
        monkeypatch.setattr(settings, "relationship_anchor_max", 3)

        block = await build_relationships_block(
            db_session, chat.id, a.id, "Character A", {b.id: "Character B"}
        )
        # The higher-importance duplicate wins; the other is deduped away.
        assert "якорь: дубль" in block
        assert "якорь: первый" not in block
        assert block.count("якорь:") == 1

    async def test_empty_emotion_falls_back(
        self, db_session: AsyncSession, chat, three_characters, monkeypatch,
    ):
        a, b, _ = three_characters
        rel = await get_or_create_relationship(db_session, chat.id, a.id, b.id)
        await crud.create_memory_anchor(
            db_session, relationship_id=rel.id, emotion="   ", importance=0.7,
        )
        monkeypatch.setattr(settings, "anchors_enabled", True)
        block = await build_relationships_block(
            db_session, chat.id, a.id, "Character A", {b.id: "Character B"}
        )
        assert "якорь: нейтрально (важность 0.7)" in block

    async def test_error_loading_anchors_is_benign(
        self, db_session: AsyncSession, chat, three_characters, monkeypatch,
    ):
        a, b, _ = three_characters
        await get_or_create_relationship(db_session, chat.id, a.id, b.id)
        monkeypatch.setattr(settings, "anchors_enabled", True)

        async def _boom(*args, **kwargs):
            raise RuntimeError("db down")

        monkeypatch.setattr(
            "app.crud.get_anchors_for_relationships", _boom
        )
        block = await build_relationships_block(
            db_session, chat.id, a.id, "Character A", {b.id: "Character B"}
        )
        # Block still renders without anchors.
        assert "Character B" in block
        assert "якорь" not in block


class TestSelectTopAnchors:
    def test_max_k_zero_is_empty(self):
        from app.models import MemoryAnchor

        anchors = [MemoryAnchor(relationship_id=1, event_id=1, importance=0.8)]
        assert crud.select_top_anchors(anchors, max_k=0) == []

    def test_dedup_prefers_higher_importance(self):
        from app.models import MemoryAnchor

        now = datetime(2026, 1, 10, 12, 0)
        anchors = [
            MemoryAnchor(
                relationship_id=1, event_id=5, emotion="слабо",
                importance=0.5, timestamp=now,
            ),
            MemoryAnchor(
                relationship_id=1, event_id=5, emotion="сильно",
                importance=0.9, timestamp=now,
            ),
        ]
        selected = crud.select_top_anchors(anchors, max_k=1, now=now)
        assert len(selected) == 1
        assert selected[0].emotion == "сильно"

    def test_recency_breaks_importance_ties(self):
        from app.models import MemoryAnchor

        now = datetime(2026, 1, 10, 12, 0)
        anchors = [
            MemoryAnchor(
                relationship_id=1, event_id=1, emotion="старый",
                importance=0.8, timestamp=now - timedelta(days=5),
            ),
            MemoryAnchor(
                relationship_id=1, event_id=2, emotion="свежий",
                importance=0.8, timestamp=now,
            ),
        ]
        selected = crud.select_top_anchors(anchors, max_k=1, now=now)
        assert len(selected) == 1
        assert selected[0].emotion == "свежий"

"""Tests for single-transaction commit batching (Sprint 4 item 2).

The whole round (deltas, issues, decay, pruning) must be staged in one DB
session and committed with ONE flush+commit. ``apply_delta`` must not flush or
commit on its own — otherwise a mid-batch failure would leave a partial write.
"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import chat_engine
from app import relationship_service
from app import schemas
from app.models import CharacterRelationship, RelationshipEvent


async def _no_memory(*args, **kwargs):
    return None


def _snapshots(a, b):
    round_snapshots = [
        {
            "id": 1, "role": "user", "character_id": None,
            "content": "Вы в холле", "location": "hall",
            "visibility": "local", "channel": "direct",
            "target_character_ids": "[]",
        },
        {
            "id": 2, "role": "character", "character_id": a.id,
            "content": f"Привет, {b.name}!", "location": "hall",
            "visibility": "local", "channel": "direct",
            "target_character_ids": "[]",
        },
        {
            "id": 3, "role": "character", "character_id": b.id,
            "content": f"Привет, {a.name}!", "location": "hall",
            "visibility": "local", "channel": "direct",
            "target_character_ids": "[]",
        },
    ]
    character_snapshots = [
        {"id": a.id, "name": a.name, "location": "hall"},
        {"id": b.id, "name": b.name, "location": "hall"},
    ]
    return round_snapshots, character_snapshots


async def _event_count(session_factory, relationship_id: int) -> int:
    async with session_factory() as db:
        result = await db.execute(
            select(func.count()).select_from(RelationshipEvent).where(
                RelationshipEvent.relationship_id == relationship_id
            )
        )
        return result.scalar() or 0


class TestSingleTransaction:
    async def test_apply_delta_stages_without_commit(
        self, monkeypatch, db_engine, chat, three_characters
    ):
        """apply_delta must not persist anything before the caller commits."""
        monkeypatch.setattr(
            relationship_service, "_maybe_create_memory_from_event", _no_memory
        )
        a, b, _ = three_characters
        session_factory = async_sessionmaker(
            db_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
        )

        async with session_factory() as db:
            rel = await chat_engine.relationship_service.get_or_create_relationship(
                db, chat.id, a.id, b.id
            )
            delta = schemas.RelationshipDelta(
                source_character_id=a.id,
                target_character_id=b.id,
                delta_affection=10,
                importance=5,
            )
            await chat_engine.relationship_service.apply_delta(
                db, delta, chat.id, round_id="r1-m1"
            )
            # A separate session must not see the staged event yet.
            assert await _event_count(session_factory, rel.id) == 0
            await db.commit()

        assert await _event_count(session_factory, rel.id) == 1

    async def test_rollback_discards_staged_deltas(
        self, monkeypatch, db_engine, chat, three_characters
    ):
        """If the batch never commits, nothing is persisted."""
        monkeypatch.setattr(
            relationship_service, "_maybe_create_memory_from_event", _no_memory
        )
        a, b, _ = three_characters
        session_factory = async_sessionmaker(
            db_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
        )

        async with session_factory() as db:
            rel = await chat_engine.relationship_service.get_or_create_relationship(
                db, chat.id, a.id, b.id
            )
            rel_id = rel.id
            delta = schemas.RelationshipDelta(
                source_character_id=a.id,
                target_character_id=b.id,
                delta_affection=10,
                importance=5,
            )
            await chat_engine.relationship_service.apply_delta(
                db, delta, chat.id, round_id="r1-m1"
            )
            await db.rollback()

        # Rel created by get_or_create_relationship still exists (it commits),
        # but the delta event must be gone.
        async with session_factory() as db:
            rel = await db.get(CharacterRelationship, rel_id)
            assert rel is not None
            assert rel.affection == 50
        assert await _event_count(session_factory, rel_id) == 0

    async def test_batch_applies_all_deltas_in_one_transaction(
        self, monkeypatch, db_engine, chat, three_characters
    ):
        """Full batch path: one LLM call, all deltas, single flush+commit."""
        a, b, _ = three_characters
        session_factory = async_sessionmaker(
            db_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
        )
        monkeypatch.setattr(
            chat_engine.relationship_service, "_maybe_create_memory_from_event", _no_memory
        )
        monkeypatch.setattr(chat_engine, "AsyncSessionLocal", session_factory)

        async def fake_batch(client, model_name, scene_text, pairs, known_pairs):
            deltas = [
                schemas.RelationshipDelta(
                    source_character_id=p["source_id"],
                    target_character_id=p["target_id"],
                    delta_affection=10,
                    importance=5,
                )
                for p in pairs
            ]
            return deltas, []

        monkeypatch.setattr(
            chat_engine.relationship_analyzer,
            "analyze_batch_relationships",
            fake_batch,
        )

        round_snapshots, character_snapshots = _snapshots(a, b)
        summary = await chat_engine._analyze_and_update_relationships(
            object(), chat.id, "model-x", round_snapshots, character_snapshots,
            round_id=f"r{chat.id}-m1",
        )

        assert summary["analyzed_pairs"] == 2
        assert summary["applied_deltas"] == 2
        assert summary["created_events"] == 2
        assert summary["decay_events"] == 0
        assert summary["pruned_events"] == 0

        async with session_factory() as db:
            events = (
                await db.execute(
                    select(RelationshipEvent).where(
                        RelationshipEvent.round_id == f"r{chat.id}-m1"
                    )
                )
            ).scalars().all()
            assert len(events) == 2
            rel_ab = await chat_engine.relationship_service.get_relationship(
                db, a.id, b.id
            )
            # delta 10 -> 4 by growth resistance at current=50 (§27.1).
            assert rel_ab.affection == 54

"""Tests for relationship event pruning/archiving (Sprint 4 item 3).

Events above ``RELATIONSHIP_EVENTS_MAX_PER_PAIR`` are folded into ONE archive
row that aggregates counts per kind and snapshots the current edge values.
"""

from sqlalchemy import select

from app.models import CharacterRelationship, RelationshipEvent
from app.relationship_service import (
    get_or_create_relationship,
    prune_relationship_events,
)


async def _add_event(
    db, relationship_id: int, *, kind: str = "llm", round_id: str, delta: int = 0
):
    event = RelationshipEvent(
        relationship_id=relationship_id,
        kind=kind,
        description=f"event {kind}",
        reason="",
        delta_affection=delta,
        delta_trust=0,
        delta_attraction=0,
        delta_resentment=0,
        delta_jealousy=0,
        affection_after=50,
        trust_after=50,
        attraction_after=0,
        resentment_after=0,
        jealousy_after=0,
        importance=1,
        source_message_ids="[]",
        round_id=round_id,
        source_round_id=round_id,
    )
    db.add(event)
    return event


async def _all_events(db, relationship_id: int) -> list[RelationshipEvent]:
    result = await db.execute(
        select(RelationshipEvent)
        .where(RelationshipEvent.relationship_id == relationship_id)
        .order_by(RelationshipEvent.timestamp, RelationshipEvent.id)
    )
    return list(result.scalars().all())


class TestPruneRelationshipEvents:
    async def test_no_prune_when_within_limit(self, db_session, chat, three_characters):
        a, b, _ = three_characters
        rel = await get_or_create_relationship(db_session, chat.id, a.id, b.id)
        for i in range(50):
            await _add_event(db_session, rel.id, round_id=f"r{i}")
        await db_session.commit()

        archive = await prune_relationship_events(db_session, rel.id, max_events=100)
        assert archive is None
        await db_session.commit()
        assert len(await _all_events(db_session, rel.id)) == 50

    async def test_prunes_into_single_archive(self, db_session, chat, three_characters):
        a, b, _ = three_characters
        rel = await get_or_create_relationship(db_session, chat.id, a.id, b.id)
        # 60 llm + 40 decay + 20 manual = 120 events; oldest 20 are llm.
        for i in range(60):
            await _add_event(db_session, rel.id, kind="llm", round_id=f"r{i}", delta=5)
        for i in range(40):
            await _add_event(db_session, rel.id, kind="decay", round_id=f"r{i}")
        for i in range(20):
            await _add_event(db_session, rel.id, kind="manual", round_id=f"r{i}")

        rel.affection = 80
        rel.trust = 70
        rel.updated_at = __import__("datetime").datetime.utcnow()

        archive = await prune_relationship_events(db_session, rel.id, max_events=100)
        await db_session.commit()

        assert archive is not None
        assert archive.kind == "archive"
        # Archive never changes live state.
        assert archive.delta_affection == 0
        assert archive.delta_trust == 0
        # Snapshot reflects the edge's CURRENT values at archive time.
        assert archive.affection_after == 80
        assert archive.trust_after == 70
        assert archive.importance == 0
        # Aggregates over the folded events (oldest 20 are llm).
        assert "llm=20" in archive.description
        assert "decay=0" in archive.description
        assert "manual=0" in archive.description

        remaining = await _all_events(db_session, rel.id)
        # 100 newest raw events + 1 archive row.
        assert len(remaining) == 101
        kinds = [e.kind for e in remaining]
        assert kinds.count("archive") == 1
        assert kinds.count("llm") == 40
        assert kinds.count("decay") == 40
        assert kinds.count("manual") == 20

    async def test_prune_keeps_edge_state_unchanged(self, db_session, chat, three_characters):
        """Archive rows must never alter the live relationship metrics."""
        a, b, _ = three_characters
        rel = await get_or_create_relationship(db_session, chat.id, a.id, b.id)
        for i in range(150):
            await _add_event(db_session, rel.id, kind="llm", round_id=f"r{i}", delta=3)
        rel.affection = 90
        rel.trust = 60

        await prune_relationship_events(db_session, rel.id, max_events=100)
        await db_session.commit()

        fresh = await db_session.get(CharacterRelationship, rel.id)
        assert fresh.affection == 90
        assert fresh.trust == 60

"""Tests for directed-edge reciprocity WITHOUT synchronization (docs/relations.md §10, Sprint 2 item 11).

Directed edges are preserved and automatic mirroring is forbidden: A->B and B->A
are independent rows, so `A->B affection=90`, `B->A affection=20` is valid and
nothing ever syncs one edge into the reverse.
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.relationship_service import (
    apply_delta,
    get_or_create_relationship,
    get_relationship,
    get_recent_events,
    update_relationship_fields,
)
from app.schemas import RelationshipDelta


class TestReciprocityNoSync:
    async def test_opposite_edges_hold_different_values(
        self, db_session: AsyncSession, chat, three_characters
    ):
        a, b, _ = three_characters
        rel_ab = await get_or_create_relationship(db_session, chat.id, a.id, b.id)
        rel_ba = await get_or_create_relationship(db_session, chat.id, b.id, a.id)
        await update_relationship_fields(db_session, rel_ab, affection=90)
        await update_relationship_fields(db_session, rel_ba, affection=20)

        ab = await get_relationship(db_session, a.id, b.id)
        ba = await get_relationship(db_session, b.id, a.id)
        assert ab is not None and ba is not None
        assert ab.affection == 90
        assert ba.affection == 20
        assert ab.affection != ba.affection

    async def test_delta_on_one_edge_does_not_sync_reverse(
        self, db_session: AsyncSession, chat, three_characters
    ):
        a, b, _ = three_characters
        await get_or_create_relationship(db_session, chat.id, a.id, b.id)
        await get_or_create_relationship(db_session, chat.id, b.id, a.id)

        delta = RelationshipDelta(
            source_character_id=a.id,
            target_character_id=b.id,
            delta_affection=40,
            delta_trust=-10,
            reason="A helped B",
            importance=8,
        )
        await apply_delta(db_session, delta, chat.id)

        ab = await get_relationship(db_session, a.id, b.id)
        ba = await get_relationship(db_session, b.id, a.id)
        # 50 + 20 (schema clamps |delta| <= 20) -> growth resistance at
        # current=50 (§27.1): 20 * ((100-50)/100)**1.5 ≈ 7 -> 57.
        assert ab.affection == 57
        assert ab.trust == 40      # 50 - 10
        # Reverse edge untouched (still defaults)
        assert ba.affection == 50
        assert ba.trust == 50

        # And the reverse direction: a delta on B->A must not touch A->B
        delta_ba = RelationshipDelta(
            source_character_id=b.id,
            target_character_id=a.id,
            delta_resentment=20,
            reason="B resents A",
            importance=8,
        )
        await apply_delta(db_session, delta_ba, chat.id)
        ab_after = await get_relationship(db_session, a.id, b.id)
        ba_after = await get_relationship(db_session, b.id, a.id)
        assert ba_after.resentment == 20
        assert ab_after.affection == 57
        assert ab_after.trust == 40
        assert ab_after.resentment == 0

    async def test_update_fields_does_not_sync_reverse(
        self, db_session: AsyncSession, chat, three_characters
    ):
        a, b, _ = three_characters
        rel_ab = await get_or_create_relationship(db_session, chat.id, a.id, b.id)
        await get_or_create_relationship(db_session, chat.id, b.id, a.id)

        await update_relationship_fields(
            db_session, rel_ab, relationship_type="друг", trust=70,
        )

        ab = await get_relationship(db_session, a.id, b.id)
        ba = await get_relationship(db_session, b.id, a.id)
        assert ab.relationship_type == "друг"
        assert ab.trust == 70
        assert ba.relationship_type == "нейтральное"
        assert ba.trust == 50

    async def test_type_transition_not_mirrored(
        self, db_session: AsyncSession, chat, three_characters
    ):
        a, b, _ = three_characters
        rel_ab = await get_or_create_relationship(db_session, chat.id, a.id, b.id)
        await get_or_create_relationship(db_session, chat.id, b.id, a.id)

        delta = RelationshipDelta(
            source_character_id=a.id,
            target_character_id=b.id,
            relationship_type="друг",
            reason="A befriended B",
            importance=9,
        )
        await apply_delta(db_session, delta, chat.id)

        ab = await get_relationship(db_session, a.id, b.id)
        ba = await get_relationship(db_session, b.id, a.id)
        assert ab.relationship_type == "друг"
        assert ba.relationship_type == "нейтральное"

    async def test_create_does_not_create_reverse(
        self, db_session: AsyncSession, chat, three_characters
    ):
        a, b, _ = three_characters
        await get_or_create_relationship(db_session, chat.id, a.id, b.id)
        reverse = await get_relationship(db_session, b.id, a.id)
        assert reverse is None

    async def test_event_log_written_only_for_changed_edge(
        self, db_session: AsyncSession, chat, three_characters
    ):
        a, b, _ = three_characters
        rel_ab = await get_or_create_relationship(db_session, chat.id, a.id, b.id)
        rel_ba = await get_or_create_relationship(db_session, chat.id, b.id, a.id)

        delta = RelationshipDelta(
            source_character_id=a.id,
            target_character_id=b.id,
            delta_affection=10,
            reason="a nice gesture",
            importance=8,
        )
        await apply_delta(db_session, delta, chat.id)

        events_ab = await get_recent_events(db_session, rel_ab)
        events_ba = await get_recent_events(db_session, rel_ba)
        assert len(events_ab) == 1
        assert events_ab[0].delta_affection == 10
        assert len(events_ba) == 0

    async def test_self_loop_rejected(
        self, db_session: AsyncSession, chat, three_characters
    ):
        a, _, _ = three_characters
        with pytest.raises(ValueError):
            await get_or_create_relationship(db_session, chat.id, a.id, a.id)

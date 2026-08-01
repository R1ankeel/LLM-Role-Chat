"""Tests for hearsay reliability (Sprint 2 item 12, docs/relations.md §12).

The deterministic cap formula: hearsay is always weaker than direct/observed;
the LLM never grades a rumor's reliability. Base cap halves when the source's
trust in the teller is low and is further multiplied by 0.7 when the
teller->target valence is hostile (a gossip).
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat_engine import (
    _compute_hearsay_effective_cap,
    _hearsay_effective_cap,
)
from app.config import settings
from app.relationship_service import (
    get_or_create_relationship,
    update_relationship_fields,
)


class TestHearsayEffectiveCap:
    """Pure formula, no DB."""

    def test_base_cap_when_neutral(self):
        assert _hearsay_effective_cap(
            trust=50, hostility_high=False, base_cap=3,
        ) == 3

    def test_high_trust_keeps_base_cap(self):
        assert _hearsay_effective_cap(
            trust=90, hostility_high=False, base_cap=3,
        ) == 3

    def test_low_trust_halves_cap(self):
        assert _hearsay_effective_cap(
            trust=20, hostility_high=False, base_cap=3,
        ) == 1  # int(3/2) == 1

    def test_missing_trust_edge_is_neutral(self):
        assert _hearsay_effective_cap(
            trust=None, hostility_high=False, base_cap=3,
        ) == 3

    def test_hostile_teller_gossip_cuts_cap(self):
        assert _hearsay_effective_cap(
            trust=50, hostility_high=True, base_cap=3,
        ) == 2  # int(3 * 0.7) == 2

    def test_combined_low_trust_and_gossip(self):
        assert _hearsay_effective_cap(
            trust=20, hostility_high=True, base_cap=3,
        ) == 1  # int(int(3/2) * 0.7) == 0 -> floored to 1

    def test_floor_is_one(self):
        assert _hearsay_effective_cap(
            trust=10, hostility_high=True, base_cap=1,
        ) == 1

    def test_deterministic(self):
        kwargs = dict(trust=45, hostility_high=False, base_cap=3)
        assert _hearsay_effective_cap(**kwargs) == _hearsay_effective_cap(**kwargs)


class TestComputeHearsayEffectiveCap:
    """DB-backed resolution from stored edges."""

    async def test_missing_edges_use_base_cap(
        self, db_session: AsyncSession, chat, three_characters,
    ):
        a, b, c = three_characters
        cap = await _compute_hearsay_effective_cap(db_session, a.id, c.id, b.id)
        assert cap == settings.relationship_hearsay_cap

    async def test_low_trust_in_teller_halves_cap(
        self, db_session: AsyncSession, chat, three_characters,
    ):
        a, b, c = three_characters
        rel = await get_or_create_relationship(db_session, chat.id, a.id, c.id)
        await update_relationship_fields(db_session, rel, trust=20)
        cap = await _compute_hearsay_effective_cap(db_session, a.id, c.id, b.id)
        assert cap == max(1, int(settings.relationship_hearsay_cap / 2))

    async def test_hostile_teller_target_valence_cuts_cap(
        self, db_session: AsyncSession, chat, three_characters,
    ):
        a, b, c = three_characters
        rel = await get_or_create_relationship(db_session, chat.id, c.id, b.id)
        await update_relationship_fields(db_session, rel, resentment=60)
        cap = await _compute_hearsay_effective_cap(db_session, a.id, c.id, b.id)
        assert cap == max(1, int(settings.relationship_hearsay_cap * 0.7))

    async def test_neutral_teller_target_valence_keeps_base(
        self, db_session: AsyncSession, chat, three_characters,
    ):
        a, b, c = three_characters
        await get_or_create_relationship(db_session, chat.id, c.id, b.id)
        cap = await _compute_hearsay_effective_cap(db_session, a.id, c.id, b.id)
        assert cap == settings.relationship_hearsay_cap

"""Tests for the relationship service layer."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CharacterRelationship, RelationshipEvent
from app.relationship_service import (
    apply_delta,
    apply_saturation_guard,
    build_behavior_drivers_block,
    build_epistemic_mask_block,
    build_relationships_block,
    create_issue,
    get_or_create_relationship,
    get_relationship,
    get_recent_events,
    is_family_type,
    list_received_relationships,
    list_relationships_for_character,
    recent_gain,
    scale_delta_by_resistance,
    trajectory_metric_gain,
    update_relationship_fields,
    validate_transition,
)
from app.schemas import RelationshipDelta


# ---------------------------------------------------------------------------
# Transition validation
# ---------------------------------------------------------------------------
class TestValidateTransition:
    def test_same_type_is_allowed(self):
        assert validate_transition("нейтральное", "нейтральное") is True

    def test_valid_transition(self):
        assert validate_transition("нейтральное", "друг") is True
        assert validate_transition("друг", "близкий_друг") is True
        assert validate_transition("враг", "нейтральное") is True

    def test_invalid_transition(self):
        assert validate_transition("нейтральное", "заклятый_враг") is False
        assert validate_transition("незнакомец", "возлюбленные") is False

    def test_unknown_type_reverts_to_false(self):
        assert validate_transition("нейтральное", "nonexistent_type") is False


class TestIsFamilyType:
    def test_family_types(self):
        assert is_family_type("семья") is True
        assert is_family_type("родитель") is True
        assert is_family_type("брат_сестра") is True

    def test_non_family_types(self):
        assert is_family_type("друг") is False
        assert is_family_type("нейтральное") is False
        assert is_family_type("враг") is False


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------
class TestGetOrCreateRelationship:
    async def test_creates_new(self, db_session: AsyncSession, chat, three_characters):
        a, b, _ = three_characters
        rel = await get_or_create_relationship(db_session, chat.id, a.id, b.id)
        assert rel.source_character_id == a.id
        assert rel.target_character_id == b.id
        assert rel.relationship_type == "нейтральное"
        assert rel.affection == 50

    async def test_returns_existing(self, db_session: AsyncSession, chat, three_characters):
        a, b, _ = three_characters
        rel1 = await get_or_create_relationship(db_session, chat.id, a.id, b.id)
        rel2 = await get_or_create_relationship(db_session, chat.id, a.id, b.id)
        assert rel1.id == rel2.id

    async def test_directionality(self, db_session: AsyncSession, chat, three_characters):
        a, b, _ = three_characters
        ab = await get_or_create_relationship(db_session, chat.id, a.id, b.id)
        ba = await get_or_create_relationship(db_session, chat.id, b.id, a.id)
        assert ab.id != ba.id
        assert ab.source_character_id == a.id
        assert ba.source_character_id == b.id


class TestGetRelationship:
    async def test_returns_none_if_missing(self, db_session: AsyncSession, chat, three_characters):
        a, b, _ = three_characters
        rel = await get_relationship(db_session, a.id, b.id)
        assert rel is None

    async def test_finds_existing(self, db_session: AsyncSession, chat, three_characters):
        a, b, _ = three_characters
        await get_or_create_relationship(db_session, chat.id, a.id, b.id)
        rel = await get_relationship(db_session, a.id, b.id)
        assert rel is not None
        assert rel.source_character_id == a.id


class TestListRelationships:
    async def test_lists_outgoing(self, db_session: AsyncSession, chat, three_characters):
        a, b, c = three_characters
        await get_or_create_relationship(db_session, chat.id, a.id, b.id)
        await get_or_create_relationship(db_session, chat.id, a.id, c.id)
        rels = await list_relationships_for_character(db_session, a.id)
        assert len(rels) == 2

    async def test_lists_received(self, db_session: AsyncSession, chat, three_characters):
        a, b, c = three_characters
        await get_or_create_relationship(db_session, chat.id, b.id, a.id)
        await get_or_create_relationship(db_session, chat.id, c.id, a.id)
        rels = await list_received_relationships(db_session, a.id)
        assert len(rels) == 2


# ---------------------------------------------------------------------------
# Update fields
# ---------------------------------------------------------------------------
class TestUpdateFields:
    async def test_clamps_values(self, db_session: AsyncSession, chat, three_characters):
        a, b, _ = three_characters
        rel = await get_or_create_relationship(db_session, chat.id, a.id, b.id)
        updated = await update_relationship_fields(
            db_session, rel, affection=150, trust=-10,
        )
        assert updated.affection == 100
        assert updated.trust == 0

    async def test_partial_update(self, db_session: AsyncSession, chat, three_characters):
        a, b, _ = three_characters
        rel = await get_or_create_relationship(db_session, chat.id, a.id, b.id)
        updated = await update_relationship_fields(
            db_session, rel, relationship_type="друг",
        )
        assert updated.relationship_type == "друг"
        assert updated.affection == 50  # unchanged


# ---------------------------------------------------------------------------
# Apply delta
# ---------------------------------------------------------------------------
class TestApplyDelta:
    async def test_apply_simple_delta(self, db_session: AsyncSession, chat, three_characters):
        a, b, _ = three_characters
        delta = RelationshipDelta(
            source_character_id=a.id,
            target_character_id=b.id,
            delta_affection=10,
            delta_trust=5,
            description="They had a good talk",
            reason="friendly conversation",
            importance=5,
        )
        rel = await apply_delta(db_session, delta, chat.id)
        # Growth resistance (§27.1): at current=50 the factor is
        # ((100-50)/100)**1.5 ≈ 0.354, so 10 → 4 and 5 → 2.
        assert rel.affection == 54
        assert rel.trust == 52

    async def test_apply_negative_delta_not_dampened(self, db_session: AsyncSession, chat, three_characters):
        a, b, _ = three_characters
        delta = RelationshipDelta(
            source_character_id=a.id,
            target_character_id=b.id,
            delta_affection=-10,
            importance=5,
        )
        rel = await apply_delta(db_session, delta, chat.id)
        assert rel.affection == 40  # negative deltas pass through unchanged

    async def test_apply_resistance_vanishes_near_zero(self, db_session: AsyncSession, chat, three_characters):
        a, b, _ = three_characters
        rel = await get_or_create_relationship(db_session, chat.id, a.id, b.id)
        await update_relationship_fields(db_session, rel, affection=10)
        delta = RelationshipDelta(
            source_character_id=a.id,
            target_character_id=b.id,
            delta_affection=10,
            importance=5,
        )
        rel = await apply_delta(db_session, delta, chat.id)
        # ((100-10)/100)**1.5 ≈ 0.854 → 10 * 0.854 ≈ 9.
        assert rel.affection == 19

    async def test_apply_clamps_delta(self, db_session: AsyncSession, chat, three_characters):
        a, b, _ = three_characters
        delta = RelationshipDelta(
            source_character_id=a.id,
            target_character_id=b.id,
            delta_affection=100,
        )
        rel = await apply_delta(db_session, delta, chat.id)
        assert rel.affection <= 100  # clamped by schema

    async def test_invalid_transition_rejected(self, db_session: AsyncSession, chat, three_characters):
        a, b, _ = three_characters
        rel = await get_or_create_relationship(db_session, chat.id, a.id, b.id)
        assert rel.relationship_type == "нейтральное"
        delta = RelationshipDelta(
            source_character_id=a.id,
            target_character_id=b.id,
            delta_affection=0,
            relationship_type="заклятый_враг",
        )
        rel = await apply_delta(db_session, delta, chat.id)
        assert rel.relationship_type == "нейтральное"  # rejected, stays neutral

    async def test_family_type_cannot_be_removed_by_engine(
        self, db_session: AsyncSession, chat, three_characters
    ):
        a, b, _ = three_characters
        rel = await get_or_create_relationship(db_session, chat.id, a.id, b.id)
        rel = await update_relationship_fields(
            db_session, rel, relationship_type="родитель",
        )
        assert rel.relationship_type == "родитель"

        delta = RelationshipDelta(
            source_character_id=a.id,
            target_character_id=b.id,
            delta_affection=5,
            relationship_type="друг",
            importance=7,
        )
        rel = await apply_delta(db_session, delta, chat.id)
        assert rel.relationship_type == "родитель"  # blocked, stays family
        assert rel.affection == 52  # 5 → 2 by growth resistance at current=50

    async def test_family_type_cannot_be_set_by_engine(
        self, db_session: AsyncSession, chat, three_characters
    ):
        a, b, _ = three_characters
        rel = await get_or_create_relationship(db_session, chat.id, a.id, b.id)
        assert rel.relationship_type == "нейтральное"

        delta = RelationshipDelta(
            source_character_id=a.id,
            target_character_id=b.id,
            delta_affection=5,
            relationship_type="семья",
            importance=7,
        )
        rel = await apply_delta(db_session, delta, chat.id)
        assert rel.relationship_type == "нейтральное"  # blocked, no family via engine

    async def test_family_type_blocked_even_with_valid_transition(
        self, db_session: AsyncSession, chat, three_characters
    ):
        a, b, _ = three_characters
        rel = await get_or_create_relationship(db_session, chat.id, a.id, b.id)
        rel = await update_relationship_fields(
            db_session, rel, relationship_type="семья",
        )
        delta = RelationshipDelta(
            source_character_id=a.id,
            target_character_id=b.id,
            delta_affection=0,
            relationship_type="близкий_друг",  # valid per transition graph
            importance=7,
        )
        rel = await apply_delta(db_session, delta, chat.id)
        assert rel.relationship_type == "семья"

    async def test_description_updates_only_when_flag_set(self, db_session: AsyncSession, chat, three_characters):
        a, b, _ = three_characters
        delta = RelationshipDelta(
            source_character_id=a.id,
            target_character_id=b.id,
            delta_affection=0,
            description="New description",
            update_description=False,
        )
        rel = await apply_delta(db_session, delta, chat.id)
        assert rel.description == ""  # not updated

        delta2 = RelationshipDelta(
            source_character_id=a.id,
            target_character_id=b.id,
            delta_affection=0,
            description="New description",
            update_description=True,
        )
        rel2 = await apply_delta(db_session, delta2, chat.id)
        assert rel2.description == "New description"

    async def test_creates_event_log(self, db_session: AsyncSession, chat, three_characters):
        a, b, _ = three_characters
        delta = RelationshipDelta(
            source_character_id=a.id,
            target_character_id=b.id,
            delta_affection=10,
            reason="test reason",
            importance=8,
        )
        await apply_delta(db_session, delta, chat.id)
        rel = await get_relationship(db_session, a.id, b.id)
        events = await get_recent_events(db_session, rel)
        assert len(events) == 1
        assert events[0].reason == "test reason"
        assert events[0].delta_affection == 10

    async def test_skips_low_importance(self, db_session: AsyncSession, chat, three_characters):
        a, b, _ = three_characters
        delta = RelationshipDelta(
            source_character_id=a.id,
            target_character_id=b.id,
            delta_affection=10,
            importance=1,
        )
        rel = await apply_delta(db_session, delta, chat.id)
        assert rel.affection == 50
        events = await get_recent_events(db_session, rel)
        assert len(events) == 0

    async def test_no_event_when_nothing_changed(self, db_session: AsyncSession, chat, three_characters):
        a, b, _ = three_characters
        delta = RelationshipDelta(
            source_character_id=a.id,
            target_character_id=b.id,
            delta_affection=0,
            relationship_type="нейтральное",
            importance=5,
        )
        rel = await apply_delta(db_session, delta, chat.id)
        assert rel.affection == 50
        assert rel.relationship_type == "нейтральное"
        events = await get_recent_events(db_session, rel)
        assert len(events) == 0


# ---------------------------------------------------------------------------
# Anti-inflation (§27.1): growth resistance
# ---------------------------------------------------------------------------
class TestScaleDeltaByResistance:
    def test_negative_delta_unchanged(self):
        assert scale_delta_by_resistance(50, -10) == -10
        assert scale_delta_by_resistance(50, 0) == 0

    def test_low_value_almost_no_dampening(self):
        # ((100-10)/100)**1.5 ≈ 0.854 → 10 * 0.854 ≈ 9
        assert scale_delta_by_resistance(10, 10) == 9

    def test_mid_value_half_dampening(self):
        # ((100-50)/100)**1.5 ≈ 0.354 → 10 * 0.354 ≈ 4
        assert scale_delta_by_resistance(50, 10) == 4

    def test_high_value_strong_dampening(self):
        # ((100-90)/100)**1.5 = 0.1**1.5 ≈ 0.032 → 20 * 0.032 ≈ 1
        assert scale_delta_by_resistance(90, 20) == 1

    def test_at_ceiling_returns_zero(self):
        assert scale_delta_by_resistance(100, 20) == 0

    def test_explicit_exponent(self):
        # exponent=1: (0.5)**1 = 0.5 → 10 * 0.5 = 5
        assert scale_delta_by_resistance(50, 10, exponent=1) == 5


# ---------------------------------------------------------------------------
# Anti-inflation (§27.3): saturation guard
# ---------------------------------------------------------------------------
class TestApplySaturationGuard:
    def test_below_threshold_unchanged(self):
        assert apply_saturation_guard(10, 20, threshold=25) == 10

    def test_above_threshold_scaled(self):
        # 10 * 0.3 = 3
        assert apply_saturation_guard(10, 30, threshold=25, factor=0.3) == 3

    def test_floor_is_one(self):
        assert apply_saturation_guard(2, 30, threshold=25, factor=0.3) == 1

    def test_negative_delta_unchanged(self):
        assert apply_saturation_guard(-10, 100, threshold=25) == -10

    def test_zero_delta_unchanged(self):
        assert apply_saturation_guard(0, 100, threshold=25) == 0


# ---------------------------------------------------------------------------
# Anti-inflation (§27.3): trajectory gain
# ---------------------------------------------------------------------------
class TestTrajectoryMetricGain:
    @staticmethod
    def _event(affection_after: int) -> RelationshipEvent:
        return RelationshipEvent(
            relationship_id=1,
            description="",
            reason="",
            affection_after=affection_after,
            importance=5,
        )

    def test_sums_positive_diffs_only(self):
        events = [
            self._event(55), self._event(58), self._event(52),
        ]
        # diffs: +3, -6 -> only +3 counts
        assert trajectory_metric_gain(events, "affection") == 3

    def test_flat_window_is_zero(self):
        events = [self._event(55), self._event(55), self._event(55)]
        assert trajectory_metric_gain(events, "affection") == 0

    def test_single_event_is_zero(self):
        assert trajectory_metric_gain([self._event(55)], "affection") == 0

    def test_unknown_metric_is_zero(self):
        events = [self._event(55), self._event(58)]
        assert trajectory_metric_gain(events, "nonsense") == 0

    def test_uses_only_after_snapshots(self):
        events = [self._event(55), self._event(60)]
        assert trajectory_metric_gain(events, "trust") == 0  # trust_after defaults 0


class TestRecentGain:
    async def test_recent_gain_from_db(self, db_session: AsyncSession, chat, three_characters):
        a, b, _ = three_characters
        rel = await get_or_create_relationship(db_session, chat.id, a.id, b.id)
        events = [
            RelationshipEvent(
                relationship_id=rel.id, kind="llm", description="", reason="",
                delta_affection=5, affection_after=55, importance=5, round_id="r1",
            ),
            RelationshipEvent(
                relationship_id=rel.id, kind="llm", description="", reason="",
                delta_affection=3, affection_after=58, importance=5, round_id="r2",
            ),
            RelationshipEvent(
                relationship_id=rel.id, kind="llm", description="", reason="",
                delta_affection=-3, affection_after=55, importance=5, round_id="r3",
            ),
            RelationshipEvent(
                relationship_id=rel.id, kind="decay", description="", reason="",
                delta_affection=-2, affection_after=53, importance=1, round_id="r4",
            ),
        ]
        for event in events:
            db_session.add(event)
        await db_session.flush()
        # diffs over llm events: +3, -3 (decay event excluded by kind filter)
        assert await recent_gain(db_session, rel.id, "affection") == 3

    async def test_recent_gain_respects_window(self, db_session: AsyncSession, chat, three_characters):
        a, b, _ = three_characters
        rel = await get_or_create_relationship(db_session, chat.id, a.id, b.id)
        for idx in range(1, 6):
            db_session.add(
                RelationshipEvent(
                    relationship_id=rel.id, kind="llm", description="", reason="",
                    delta_affection=1, affection_after=50 + idx, importance=5,
                    round_id=f"r{idx}",
                )
            )
        await db_session.flush()
        # window=2 -> last 2 events: 54 -> 55 → gain 1
        assert await recent_gain(db_session, rel.id, "affection", window=2) == 1
        # window=4 -> 52 -> 53 -> 54 -> 55 → gain 3
        assert await recent_gain(db_session, rel.id, "affection", window=4) == 3


# ---------------------------------------------------------------------------
# Build relationships block
# ---------------------------------------------------------------------------
class TestBuildRelationshipsBlock:
    async def test_empty_if_no_relationships(self, db_session: AsyncSession, chat, three_characters):
        a, _, _ = three_characters
        block = await build_relationships_block(
            db_session, chat.id, a.id, "Character A",
            {c.id: c.name for c in three_characters},
        )
        assert block == ""

    async def test_contains_target_names(self, db_session: AsyncSession, chat, three_characters):
        a, b, c = three_characters
        await get_or_create_relationship(db_session, chat.id, a.id, b.id)
        await get_or_create_relationship(db_session, chat.id, a.id, c.id)
        block = await build_relationships_block(
            db_session, chat.id, a.id, "Character A",
            {c.id: c.name for c in three_characters},
        )
        assert "Character B" in block
        assert "Character C" in block
        assert "нейтральное" in block

    async def test_interpretation_instead_of_numbers(self, db_session: AsyncSession, chat, three_characters):
        a, b, _ = three_characters
        await update_relationship_fields(
            db_session,
            await get_or_create_relationship(db_session, chat.id, a.id, b.id),
            affection=80,
            trust=20,
        )
        block = await build_relationships_block(
            db_session, chat.id, a.id, "Character A",
            {c.id: c.name for c in three_characters},
        )
        assert "Character B" in block
        assert "привязан" in block
        assert "не доверяешь" in block
        assert "affection=" not in block
        assert "привязанность=" not in block
        assert "=80" not in block


# ---------------------------------------------------------------------------
# Build behavior drivers block
# ---------------------------------------------------------------------------
class TestBuildBehaviorDriversBlock:
    def _names(self, chars) -> dict:
        return {c.id: c.name for c in chars}

    async def test_empty_if_no_relationships(self, db_session: AsyncSession, chat, three_characters):
        a, _, _ = three_characters
        block = await build_behavior_drivers_block(
            db_session, chat.id, a.id, "Character A", self._names(three_characters),
        )
        assert block == ""

    async def test_neutral_relationship_produces_empty_block(self, db_session: AsyncSession, chat, three_characters):
        a, b, _ = three_characters
        await get_or_create_relationship(db_session, chat.id, a.id, b.id)
        block = await build_behavior_drivers_block(
            db_session, chat.id, a.id, "Character A", self._names(three_characters),
        )
        assert block == ""

    async def test_strong_state_yields_drivers_block(self, db_session: AsyncSession, chat, three_characters):
        a, b, _ = three_characters
        await update_relationship_fields(
            db_session,
            await get_or_create_relationship(db_session, chat.id, a.id, b.id),
            affection=80,
            trust=20,
        )
        block = await build_behavior_drivers_block(
            db_session, chat.id, a.id, "Character A", self._names(three_characters),
        )
        assert block.startswith("<behavior_drivers>")
        assert block.endswith("</behavior_drivers>")
        assert "эмоционально привязан" in block
        assert "не доверяешь" in block
        assert "должен" not in block
        assert "обязан" not in block

    async def test_capped_by_relationship_drivers_max(self, db_session: AsyncSession, chat, three_characters):
        a, b, c = three_characters
        await update_relationship_fields(
            db_session,
            await get_or_create_relationship(db_session, chat.id, a.id, b.id),
            affection=80,
            trust=20,
            resentment=60,
            jealousy=70,
        )
        await update_relationship_fields(
            db_session,
            await get_or_create_relationship(db_session, chat.id, a.id, c.id),
            affection=80,
            trust=20,
        )
        from app.config import settings
        block = await build_behavior_drivers_block(
            db_session, chat.id, a.id, "Character A", self._names(three_characters),
        )
        driver_lines = [ln for ln in block.splitlines() if ln.startswith("- ")]
        assert len(driver_lines) <= settings.relationship_drivers_max

    async def test_explicit_max_drivers(self, db_session: AsyncSession, chat, three_characters):
        a, b, _ = three_characters
        await update_relationship_fields(
            db_session,
            await get_or_create_relationship(db_session, chat.id, a.id, b.id),
            affection=80,
            trust=20,
            resentment=60,
            jealousy=70,
        )
        block = await build_behavior_drivers_block(
            db_session, chat.id, a.id, "Character A", self._names(three_characters),
            max_drivers=2,
        )
        driver_lines = [ln for ln in block.splitlines() if ln.startswith("- ")]
        assert len(driver_lines) == 2

    async def test_open_issue_feeds_drivers(self, db_session: AsyncSession, chat, three_characters):
        a, b, _ = three_characters
        rel = await get_or_create_relationship(db_session, chat.id, a.id, b.id)
        await create_issue(
            db_session, rel, issue_type="lie",
            text="Character B солгал Character A", importance=7,
        )
        block = await build_behavior_drivers_block(
            db_session, chat.id, a.id, "Character A", self._names(three_characters),
        )
        assert "нерешённом вопросе" in block


# ---------------------------------------------------------------------------
# MVP epistemic mask (Sprint 2 item 10, docs/relations.md §10)
# ---------------------------------------------------------------------------
class TestBuildEpistemicMaskBlock:
    def _names(self, chars) -> dict:
        return {c.id: c.name for c in chars}

    async def test_empty_if_no_incoming_relationships(self, db_session: AsyncSession, chat, three_characters):
        a, _, _ = three_characters
        block = await build_epistemic_mask_block(
            db_session, chat.id, a.id, "Character A", self._names(three_characters),
        )
        assert block == ""

    async def test_no_evidence_marks_unknown(self, db_session: AsyncSession, chat, three_characters):
        a, b, _ = three_characters
        await get_or_create_relationship(db_session, chat.id, b.id, a.id)
        block = await build_epistemic_mask_block(
            db_session, chat.id, a.id, "Character A", self._names(three_characters),
        )
        assert block.startswith("<epistemic_mask>")
        assert block.endswith("</epistemic_mask>")
        assert "Тебе неизвестно, как Character B относится к тебе" in block
        assert "Известное тебе отношение" not in block

    async def test_evidence_shows_interpretation_without_numbers(self, db_session: AsyncSession, chat, three_characters):
        a, b, _ = three_characters
        await update_relationship_fields(
            db_session,
            await get_or_create_relationship(db_session, chat.id, b.id, a.id),
            affection=80,
            trust=60,
        )
        block = await build_epistemic_mask_block(
            db_session, chat.id, a.id, "Character A", self._names(three_characters),
            evidenced_target_ids=[b.id],
        )
        assert "Известное тебе отношение Character B к тебе" in block
        assert "привязан к тебе" in block
        assert "Тебе неизвестно" not in block
        assert "=" not in block
        assert "80" not in block
        assert "affection" not in block

    async def test_mixed_evidence(self, db_session: AsyncSession, chat, three_characters):
        a, b, c = three_characters
        await get_or_create_relationship(db_session, chat.id, b.id, a.id)
        await get_or_create_relationship(db_session, chat.id, c.id, a.id)
        block = await build_epistemic_mask_block(
            db_session, chat.id, a.id, "Character A", self._names(three_characters),
            evidenced_target_ids=[b.id],
        )
        assert "Character B" in block
        assert "Character C" in block

    async def test_max_edges_cap(self, db_session: AsyncSession, chat, three_characters):
        a, b, c = three_characters
        await get_or_create_relationship(db_session, chat.id, b.id, a.id)
        await get_or_create_relationship(db_session, chat.id, c.id, a.id)
        block = await build_epistemic_mask_block(
            db_session, chat.id, a.id, "Character A", self._names(three_characters),
            evidenced_target_ids=[b.id],
            max_edges=1,
        )
        lines = [ln for ln in block.splitlines() if ln.startswith("- ")]
        assert len(lines) == 1

    async def test_disabled_returns_empty(self, db_session: AsyncSession, chat, three_characters):
        from unittest.mock import patch
        a, b, _ = three_characters
        await get_or_create_relationship(db_session, chat.id, b.id, a.id)
        with patch("app.relationship_service.settings.relationship_epistemic_mask_enabled", False):
            block = await build_epistemic_mask_block(
                db_session, chat.id, a.id, "Character A", self._names(three_characters),
            )
        assert block == ""

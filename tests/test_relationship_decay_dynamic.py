"""Dynamic decay (Sprint 7, docs/relations.md §18, Plans/update20.md §18).

``apply_decay`` with ``DYNAMIC_DECAY_ENABLED`` scales the base per-round decay
rates by a character factor derived from ``character_state.stress``: a stressed
character holds onto resentment/jealousy longer (slower decay, factor < 1), a
calm one lets go faster (factor > 1). Neutral stress (0.5) → factor 1.0.

Legacy invariant preserved: affection/trust/attraction never decay, and a decay
``RelationshipEvent(kind="decay")`` is created only when a metric crosses a
multiple of 10.
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import app.relationship_service as svc
from app.relationship_service import (
    _dynamic_decay_factor,
    apply_decay,
    get_or_create_relationship,
    get_recent_events,
    update_relationship_fields,
)
from app.crud import get_or_create_character_state, update_character_state


class TestDynamicDecayFactor:
    def test_neutral_stress_is_one(self):
        class _State:
            stress = 0.5

        assert _dynamic_decay_factor(_State()) == 1.0

    def test_high_stress_slows_decay(self):
        class _State:
            stress = 1.0

        assert _dynamic_decay_factor(_State()) < 1.0

    def test_low_stress_speeds_decay(self):
        class _State:
            stress = 0.0

        assert _dynamic_decay_factor(_State()) > 1.0

    def test_missing_state_is_neutral(self):
        assert _dynamic_decay_factor(None) == 1.0

    def test_unknown_stress_is_neutral(self):
        class _State:
            stress = None

        assert _dynamic_decay_factor(_State()) == 1.0

    def test_clamped_to_bounds(self):
        class _State:
            stress = 0.0

        original = svc.settings.dynamic_decay_stress_sensitivity
        svc.settings.dynamic_decay_stress_sensitivity = 10.0
        try:
            factor = _dynamic_decay_factor(_State())
            assert factor <= svc.settings.dynamic_decay_factor_max
            assert factor >= svc.settings.dynamic_decay_factor_min
        finally:
            svc.settings.dynamic_decay_stress_sensitivity = original

    def test_deterministic(self):
        class _State:
            stress = 0.7

        assert _dynamic_decay_factor(_State()) == _dynamic_decay_factor(_State())


class TestApplyDecayDynamic:
    async def test_legacy_rates_when_disabled(
        self, db_session: AsyncSession, chat, three_characters, monkeypatch,
    ):
        a, b, _ = three_characters
        rel = await get_or_create_relationship(db_session, chat.id, a.id, b.id)
        await update_relationship_fields(db_session, rel, jealousy=22, resentment=12)
        monkeypatch.setattr(
            "app.relationship_service.settings.dynamic_decay_enabled", False
        )

        await apply_decay(db_session, chat.id, "r1")
        rel = await get_or_create_relationship(db_session, chat.id, a.id, b.id)
        # Legacy jealousy 22-3=19 (crosses 20 boundary), resentment 12-1=11.
        assert rel.jealousy == 19
        assert rel.resentment == 11
        kinds = {e.kind for e in await get_recent_events(db_session, rel, limit=10)}
        assert "decay" in kinds

    async def test_dynamic_slower_decay_at_high_stress(
        self, db_session: AsyncSession, chat, three_characters, monkeypatch,
    ):
        a, b, _ = three_characters
        rel = await get_or_create_relationship(db_session, chat.id, a.id, b.id)
        await update_relationship_fields(db_session, rel, jealousy=60, resentment=60)

        state = await get_or_create_character_state(db_session, chat.id, a.id)
        await update_character_state(db_session, a.id, stress=1.0, updated_round_id="r1")

        monkeypatch.setattr(
            "app.relationship_service.settings.dynamic_decay_enabled", True
        )
        await apply_decay(db_session, chat.id, "r1")

        rel = await get_or_create_relationship(db_session, chat.id, a.id, b.id)
        factor = _dynamic_decay_factor(state)
        expected_jealousy = max(
            0, 60 - max(0, round(svc.settings.dynamic_decay_jealousy_base_rate * factor))
        )
        expected_resentment = max(
            0, 60 - max(0, round(svc.settings.dynamic_decay_resentment_base_rate * factor))
        )
        assert factor < 1.0
        assert rel.jealousy == expected_jealousy
        assert rel.resentment == expected_resentment
        # Slower than legacy: remaining jealousy must be > 57 (legacy 60-3).
        assert rel.jealousy > 57
        assert 0 <= rel.jealousy <= 100
        assert 0 <= rel.resentment <= 100

    async def test_dynamic_faster_decay_at_low_stress(
        self, db_session: AsyncSession, chat, three_characters, monkeypatch,
    ):
        a, b, _ = three_characters
        rel = await get_or_create_relationship(db_session, chat.id, a.id, b.id)
        await update_relationship_fields(db_session, rel, jealousy=60, resentment=60)

        state = await get_or_create_character_state(db_session, chat.id, a.id)
        await update_character_state(db_session, a.id, stress=0.0, updated_round_id="r1")

        monkeypatch.setattr(
            "app.relationship_service.settings.dynamic_decay_enabled", True
        )
        await apply_decay(db_session, chat.id, "r1")

        rel = await get_or_create_relationship(db_session, chat.id, a.id, b.id)
        factor = _dynamic_decay_factor(state)
        assert factor > 1.0
        # Faster than legacy: remaining jealousy must be < 57.
        # (resentment base 1 × factor ~1.25 still rounds to 1 — unchanged.)
        assert rel.jealousy < 57
        assert rel.resentment <= 59

    async def test_dynamic_missing_state_is_legacy(
        self, db_session: AsyncSession, chat, three_characters, monkeypatch,
    ):
        a, b, _ = three_characters
        rel = await get_or_create_relationship(db_session, chat.id, a.id, b.id)
        await update_relationship_fields(db_session, rel, jealousy=22, resentment=12)
        monkeypatch.setattr(
            "app.relationship_service.settings.dynamic_decay_enabled", True
        )

        await apply_decay(db_session, chat.id, "r1")
        rel = await get_or_create_relationship(db_session, chat.id, a.id, b.id)
        # No character_state row → neutral factor 1.0 → same as legacy.
        assert rel.jealousy == 19
        assert rel.resentment == 11

    async def test_affection_trust_attraction_never_decay(
        self, db_session: AsyncSession, chat, three_characters, monkeypatch,
    ):
        a, b, _ = three_characters
        rel = await get_or_create_relationship(db_session, chat.id, a.id, b.id)
        await update_relationship_fields(
            db_session, rel, affection=80, trust=70, attraction=40,
            jealousy=60, resentment=60,
        )
        monkeypatch.setattr(
            "app.relationship_service.settings.dynamic_decay_enabled", True
        )
        await apply_decay(db_session, chat.id, "r1")

        rel = await get_or_create_relationship(db_session, chat.id, a.id, b.id)
        assert rel.affection == 80
        assert rel.trust == 70
        assert rel.attraction == 40

    async def test_decay_event_created_at_boundary(
        self, db_session: AsyncSession, chat, three_characters, monkeypatch,
    ):
        a, b, _ = three_characters
        rel = await get_or_create_relationship(db_session, chat.id, a.id, b.id)
        await update_relationship_fields(db_session, rel, jealousy=22, resentment=12)
        monkeypatch.setattr(
            "app.relationship_service.settings.dynamic_decay_enabled", False
        )
        await apply_decay(db_session, chat.id, "r1")

        rel = await get_or_create_relationship(db_session, chat.id, a.id, b.id)
        recent = await get_recent_events(db_session, rel, limit=10)
        decay = [e for e in recent if e.kind == "decay"]
        assert decay, "a decay event should be created at the boundary"
        assert all(e.delta_jealousy <= 0 and e.delta_resentment <= 0 for e in decay)

    async def test_no_crossing_no_event_and_no_write(
        self, db_session: AsyncSession, chat, three_characters, monkeypatch,
    ):
        a, b, _ = three_characters
        rel = await get_or_create_relationship(db_session, chat.id, a.id, b.id)
        # jealousy 5-3=2 (no 10-crossing: 0→0), resentment 15-1=14 (1→1).
        await update_relationship_fields(db_session, rel, jealousy=5, resentment=15)
        monkeypatch.setattr(
            "app.relationship_service.settings.dynamic_decay_enabled", False
        )
        await apply_decay(db_session, chat.id, "r1")

        rel = await get_or_create_relationship(db_session, chat.id, a.id, b.id)
        # No crossing → no write, values stay.
        assert rel.jealousy == 5
        assert rel.resentment == 15
        recent = await get_recent_events(db_session, rel, limit=10)
        assert not [e for e in recent if e.kind == "decay"]

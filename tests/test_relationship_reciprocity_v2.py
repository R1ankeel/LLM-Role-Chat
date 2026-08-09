"""Reciprocity pipeline with belief-confidence dampening (Sprint 7, §10).

The reciprocity loop behavior → perception → interpretation → update is driven
by what the *source* believes about the target. ``compute_reciprocity_belief_multiplier``
converts the strongest belief confidence into a deterministic cap multiplier:

    multiplier = clamp(1 - dampening * max_confidence, min, 1.0)

``_constrain_pair_delta`` applies the multiplier (stashed on ``pair_ctx``) to the
observed/hearsay cap, so a strong belief narrows how far a single round can move
the relationship. Deliberately gated by canary flags: with ``reciprocity_enabled``
off (the default) the multiplier is always 1.0 and the pipeline is inert.
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from types import SimpleNamespace

from app import crud
from app.config import settings
from app.pipeline.relations import _constrain_pair_delta
from app.relationship_service import compute_reciprocity_belief_multiplier
from app.schemas import RelationshipDelta


class TestBeliefMultiplier:
    async def test_returns_one_when_disabled(
        self, db_session: AsyncSession, three_characters, monkeypatch,
    ):
        a, b, _ = three_characters
        await crud.upsert_belief(
            db_session, b.chat_id, a.id,
            subject=b.name, predicate="считает", object="врагом",
            confidence=0.9, type="fact",
        )
        monkeypatch.setattr(
            "app.relationship_service.settings.reciprocity_enabled", False
        )
        monkeypatch.setattr(
            "app.relationship_service.settings.beliefs_enabled", True
        )
        assert (
            await compute_reciprocity_belief_multiplier(db_session, a.id, b.name)
            == 1.0
        )

    async def test_returns_one_when_beliefs_disabled(
        self, db_session: AsyncSession, three_characters, monkeypatch,
    ):
        a, b, _ = three_characters
        await crud.upsert_belief(
            db_session, b.chat_id, a.id,
            subject=b.name, predicate="считает", object="врагом",
            confidence=0.9, type="fact",
        )
        monkeypatch.setattr(
            "app.relationship_service.settings.reciprocity_enabled", True
        )
        monkeypatch.setattr(
            "app.relationship_service.settings.beliefs_enabled", False
        )
        assert (
            await compute_reciprocity_belief_multiplier(db_session, a.id, b.name)
            == 1.0
        )

    async def test_strong_confidence_dampens(
        self, db_session: AsyncSession, three_characters, monkeypatch,
    ):
        a, b, _ = three_characters
        await crud.upsert_belief(
            db_session, b.chat_id, a.id,
            subject=b.name, predicate="считает", object="врагом",
            confidence=1.0, type="fact",
        )
        monkeypatch.setattr(
            "app.relationship_service.settings.reciprocity_enabled", True
        )
        monkeypatch.setattr(
            "app.relationship_service.settings.beliefs_enabled", True
        )
        monkeypatch.setattr(
            "app.relationship_service.settings.reciprocity_belief_dampening", 0.5
        )
        monkeypatch.setattr(
            "app.relationship_service.settings.reciprocity_belief_multiplier_min", 0.5
        )
        mult = await compute_reciprocity_belief_multiplier(db_session, a.id, b.name)
        assert mult == pytest.approx(0.5)
        assert settings.reciprocity_belief_multiplier_min <= mult <= 1.0

    async def test_partial_confidence_partial_dampening(
        self, db_session: AsyncSession, three_characters, monkeypatch,
    ):
        a, b, _ = three_characters
        await crud.upsert_belief(
            db_session, b.chat_id, a.id,
            subject=b.name, predicate="выглядит", object="спокойным",
            confidence=0.4, type="belief",
        )
        monkeypatch.setattr(
            "app.relationship_service.settings.reciprocity_enabled", True
        )
        monkeypatch.setattr(
            "app.relationship_service.settings.beliefs_enabled", True
        )
        monkeypatch.setattr(
            "app.relationship_service.settings.reciprocity_belief_dampening", 0.5
        )
        mult = await compute_reciprocity_belief_multiplier(db_session, a.id, b.name)
        # 1 - 0.5 * 0.4 = 0.8
        assert mult == pytest.approx(0.8)

    async def test_uses_strongest_confidence(
        self, db_session: AsyncSession, three_characters, monkeypatch,
    ):
        a, b, _ = three_characters
        await crud.upsert_belief(
            db_session, b.chat_id, a.id,
            subject=b.name, predicate="выглядит", object="спокойным",
            confidence=0.3, type="belief",
        )
        await crud.upsert_belief(
            db_session, b.chat_id, a.id,
            subject=b.name, predicate="считает", object="врагом",
            confidence=0.8, type="fact",
        )
        monkeypatch.setattr(
            "app.relationship_service.settings.reciprocity_enabled", True
        )
        monkeypatch.setattr(
            "app.relationship_service.settings.beliefs_enabled", True
        )
        monkeypatch.setattr(
            "app.relationship_service.settings.reciprocity_belief_dampening", 0.5
        )
        mult = await compute_reciprocity_belief_multiplier(db_session, a.id, b.name)
        # 1 - 0.5 * 0.8 = 0.6
        assert mult == pytest.approx(0.6)

    async def test_no_matching_belief_is_neutral(
        self, db_session: AsyncSession, three_characters, monkeypatch,
    ):
        a, b, _ = three_characters
        await crud.upsert_belief(
            db_session, b.chat_id, a.id,
            subject="Другой", predicate="считает", object="союзником",
            confidence=0.9, type="fact",
        )
        monkeypatch.setattr(
            "app.relationship_service.settings.reciprocity_enabled", True
        )
        monkeypatch.setattr(
            "app.relationship_service.settings.beliefs_enabled", True
        )
        assert (
            await compute_reciprocity_belief_multiplier(db_session, a.id, b.name)
            == 1.0
        )

    async def test_ignores_case_whitespace(
        self, db_session: AsyncSession, three_characters, monkeypatch,
    ):
        a, b, _ = three_characters
        await crud.upsert_belief(
            db_session, b.chat_id, a.id,
            subject=f"  {b.name.lower()}  ", predicate="считает", object="врагом",
            confidence=1.0, type="fact",
        )
        monkeypatch.setattr(
            "app.relationship_service.settings.reciprocity_enabled", True
        )
        monkeypatch.setattr(
            "app.relationship_service.settings.beliefs_enabled", True
        )
        monkeypatch.setattr(
            "app.relationship_service.settings.reciprocity_belief_dampening", 0.5
        )
        mult = await compute_reciprocity_belief_multiplier(db_session, a.id, b.name)
        assert mult < 1.0

    async def test_error_is_benign(
        self, db_session: AsyncSession, three_characters, monkeypatch,
    ):
        a, b, _ = three_characters
        monkeypatch.setattr(
            "app.relationship_service.settings.reciprocity_enabled", True
        )
        monkeypatch.setattr(
            "app.relationship_service.settings.beliefs_enabled", True
        )

        async def _boom(*args, **kwargs):
            raise RuntimeError("db down")

        monkeypatch.setattr(
            "app.crud.get_beliefs_for_character", _boom
        )
        assert (
            await compute_reciprocity_belief_multiplier(db_session, a.id, b.name)
            == 1.0
        )


class TestConstrainPairDeltaWithMultiplier:
    """The multiplier narrows the observed/hearsay cap; direct mode is untouched."""

    def _delta(self, **kwargs) -> RelationshipDelta:
        return RelationshipDelta(
            source_character_id=1,
            target_character_id=2,
            delta_affection=20,
            delta_attraction=15,
            relationship_type="возлюбленные",
            importance=5,
            **kwargs,
        )

    def test_observed_cap_scaled_by_multiplier(self, monkeypatch):
        monkeypatch.setattr(
            "app.chat_engine.settings.relationship_reflection_delta_cap", 5
        )
        rel = SimpleNamespace(relationship_type="нейтральное")
        out = _constrain_pair_delta(
            self._delta(),
            rel,
            {
                "direct_interaction": False,
                "observed_target": True,
                "reciprocity_belief_multiplier": 0.6,
            },
        )
        assert out is not None
        assert out.delta_affection == 3  # int(5 * 0.6)
        assert out.relationship_type == "нейтральное"

    def test_hearsay_cap_scaled_by_multiplier(self, monkeypatch):
        monkeypatch.setattr(
            "app.chat_engine.settings.relationship_hearsay_cap", 4
        )
        rel = SimpleNamespace(relationship_type="нейтральное")
        out = _constrain_pair_delta(
            self._delta(),
            rel,
            {
                "hearsay": True,
                "direct_interaction": False,
                "observed_target": False,
                "reciprocity_belief_multiplier": 0.5,
            },
        )
        assert out is not None
        assert out.delta_affection == 2  # int(4 * 0.5)
        assert out.relationship_type == "нейтральное"

    def test_no_multiplier_is_legacy(self, monkeypatch):
        monkeypatch.setattr(
            "app.chat_engine.settings.relationship_reflection_delta_cap", 5
        )
        rel = SimpleNamespace(relationship_type="нейтральное")
        out = _constrain_pair_delta(
            self._delta(),
            rel,
            {"direct_interaction": False, "observed_target": True},
        )
        assert out is not None
        assert out.delta_affection == 5

    def test_direct_mode_ignores_multiplier(self, monkeypatch):
        monkeypatch.setattr(
            "app.chat_engine.settings.relationship_reflection_delta_cap", 5
        )
        rel = SimpleNamespace(relationship_type="нейтральное")
        out = _constrain_pair_delta(
            self._delta(),
            rel,
            {
                "direct_interaction": True,
                "reciprocity_belief_multiplier": 0.3,
            },
        )
        assert out is not None
        # The belief multiplier narrows only observed/hearsay caps; direct is
        # narrowed by the importance cap instead (§27.2): importance=5 → 10.
        assert out.delta_affection == 10

    def test_cap_floor_is_one(self, monkeypatch):
        monkeypatch.setattr(
            "app.chat_engine.settings.relationship_reflection_delta_cap", 2
        )
        rel = SimpleNamespace(relationship_type="нейтральное")
        out = _constrain_pair_delta(
            self._delta(),
            rel,
            {
                "direct_interaction": False,
                "observed_target": True,
                "reciprocity_belief_multiplier": 0.2,
            },
        )
        assert out is not None
        assert out.delta_affection == 1  # int(2 * 0.2) == 0 -> floored to 1

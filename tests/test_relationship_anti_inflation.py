"""Anti-inflation regression (docs/relations.md §27).

Three warm compliments in a row must not inflate affection to the ceiling.
Before the anti-inflation mechanisms a calibrated model could add ~20-27
points per compliment (~60-80 total); with the importance cap (§27.2), growth
resistance (§27.1) and saturation guard (§27.3) the total stays well under 15.
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app import relationship_service
from app.pipeline.relations import _constrain_pair_delta
from app.schemas import RelationshipDelta

METRICS = ("affection", "trust", "attraction", "resentment", "jealousy")


def _pair_ctx_with_recent_gains(db_session, rel, direct: bool = True) -> dict:
    """Mimic the caller in ``_analyze_and_update_relationships``: recent gains
    are computed from the pair's trajectory events and stashed on pair_ctx."""
    return {
        "direct_interaction": direct,
        "recent_gains": {
            metric: relationship_service.trajectory_metric_gain(
                [], metric,
            )
            for metric in METRICS
        },
    }


class TestThreeComplimentsRegression:
    async def test_three_compliments_stay_below_ceiling(
        self, db_session: AsyncSession, chat, three_characters
    ):
        a, b, _ = three_characters
        rel = await relationship_service.get_or_create_relationship(
            db_session, chat.id, a.id, b.id,
        )
        start = rel.affection
        pair_ctx = _pair_ctx_with_recent_gains(db_session, rel)

        for round_i in range(3):
            # A generous compliment: the model proposes a large delta, but the
            # importance is calibrated to 2 (бытовое) and the engine caps it.
            delta = RelationshipDelta(
                source_character_id=a.id,
                target_character_id=b.id,
                delta_affection=20,
                relationship_type="нейтральное",
                importance=2,
            )
            gated = _constrain_pair_delta(delta, rel, pair_ctx)
            assert gated is not None
            assert gated.delta_affection == 3  # cap_by_importance[2]
            rel = await relationship_service.apply_delta(
                db_session, gated, chat.id, round_id=f"compliment-{round_i}",
            )
            # Recompute recent gains from the events staged by apply_delta.
            trajectory_events = await relationship_service.get_trajectory_events(
                db_session, rel.id,
                window=relationship_service.settings.relationship_saturation_window,
            )
            pair_ctx["recent_gains"] = {
                metric: relationship_service.trajectory_metric_gain(
                    trajectory_events, metric,
                )
                for metric in METRICS
            }

        total_gain = rel.affection - start
        assert total_gain <= 15
        assert rel.affection < 70  # far from the ceiling

    async def test_saturation_guard_bounds_repeated_big_events(
        self, db_session: AsyncSession, chat, three_characters
    ):
        """After a big event saturates the window, the next big delta is halved
        to ~factor (0.3), even with high importance."""
        a, b, _ = three_characters
        rel = await relationship_service.get_or_create_relationship(
            db_session, chat.id, a.id, b.id,
        )
        # Saturate the trajectory window with two +30 affection events.
        for idx, after in enumerate((70, 100)):
            db_session.add(
                relationship_service.RelationshipEvent(
                    relationship_id=rel.id, kind="llm", description="", reason="",
                    delta_affection=20, affection_after=after,
                    importance=8, round_id=f"big-{idx}",
                )
            )
        await db_session.flush()

        pair_ctx = _pair_ctx_with_recent_gains(db_session, rel)
        # Refresh recent gains from the staged big events.
        trajectory_events = await relationship_service.get_trajectory_events(
            db_session, rel.id,
            window=relationship_service.settings.relationship_saturation_window,
        )
        pair_ctx["recent_gains"] = {
            metric: relationship_service.trajectory_metric_gain(
                trajectory_events, metric,
            )
            for metric in METRICS
        }
        assert pair_ctx["recent_gains"]["affection"] >= 25

        delta = RelationshipDelta(
            source_character_id=a.id,
            target_character_id=b.id,
            delta_affection=20,
            relationship_type="нейтральное",
            importance=10,
        )
        gated = _constrain_pair_delta(delta, rel, pair_ctx)
        assert gated is not None
        # 20 * 0.3 = 6 (below the importance-10 cap of 30).
        assert gated.delta_affection == 6

"""Траектория метрик отношения (Milestone 6B, decomposition.md §4.4).

Вынесено из ``app/relationship_service.py`` без изменения поведения (тела
функций перенесены 1:1). Чтение LLM-событий, расчёт gain для saturation
guard (через ``deltas.trajectory_metric_gain``) и формат блока для промпта.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..models import RelationshipEvent
from .deltas import trajectory_metric_gain


# ---------------------------------------------------------------------------
# Trajectory (docs/relations.md §11, §17)
# ---------------------------------------------------------------------------
async def get_trajectory_events(
    db: AsyncSession,
    relationship_id: int,
    window: int = 4,
) -> list[RelationshipEvent]:
    """Get LLM events for trajectory (kind='llm' only, reverse chronological)."""
    stmt = (
        select(RelationshipEvent)
        .where(
            RelationshipEvent.relationship_id == relationship_id,
            RelationshipEvent.kind == "llm",
        )
        .order_by(RelationshipEvent.id.desc())
        .limit(window)
    )
    result = await db.execute(stmt)
    return list(reversed(result.scalars().all()))


async def recent_gain(
    db: AsyncSession,
    relationship_id: int,
    metric: str,
    window: int | None = None,
) -> int:
    """Snapshot-based gain of ``metric`` over the recent window (§27.3).

    Sums positive per-event changes of the ``*_after`` snapshots across the
    last ``window`` LLM events (default ``RELATIONSHIP_SATURATION_WINDOW``).
    Used by the saturation guard in ``_constrain_pair_delta``.
    """
    if window is None:
        window = settings.relationship_saturation_window
    events = await get_trajectory_events(db, relationship_id, window=window)
    return trajectory_metric_gain(events, metric)


def build_trajectory_block(
    events: list[RelationshipEvent],
    source_name: str,
    target_name: str,
) -> str:
    """Format trajectory as a compact table for batch prompt (§11).

    Columns: affection, trust, attraction, resentment, jealousy
    Shows *_after values from each LLM event, oldest first.
    """
    if not events:
        return ""

    lines = [f"Последние {len(events)} раунда ({source_name} → {target_name}):"]
    metrics = [
        ("привязанность", "affection_after"),
        ("доверие", "trust_after"),
        ("влечение", "attraction_after"),
        ("обида", "resentment_after"),
        ("ревность", "jealousy_after"),
    ]
    for metric_name, attr in metrics:
        values = [str(getattr(e, attr, 0)) for e in events]
        lines.append(f"  {metric_name:<12}: {' → '.join(values)}")
    return "\n".join(lines)

"""Применение дельт отношений и anti-inflation (Milestone 6B, §4.4).

Вынесено из ``app/relationship_service.py`` без изменения поведения (тела
функций перенесены 1:1). Единственная точка записи движка (``apply_delta``);
issue-обновления делегируются ``issues.py``, память — ``memory_feed.py``.
"""

import json
import logging
from datetime import datetime
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..models import CharacterRelationship, RelationshipEvent
from ..schemas import RelationshipDelta
from .crud import get_or_create_relationship
from .issues import apply_issue_deltas
from .memory_feed import _maybe_create_memory_from_event
from .validation import clamp_metric, is_family_type, validate_transition

logger = logging.getLogger(__name__)

MAX_DELTA = settings.relationship_max_delta


def _log_relationship_event(event: RelationshipEvent) -> None:
    """Emit one structured JSON log line per created event (Stage 4, Sprint 4).

    The ``extra`` payload is rendered top-level by the root ``JSONFormatter``
    configured in ``main.py`` — usable for analytics/debugging without parsing
    free-text messages.
    """
    try:
        source_ids = json.loads(event.source_message_ids or "[]")
    except (json.JSONDecodeError, TypeError):
        source_ids = []
    logger.info(
        "relationship_event",
        extra={
            "relationship_id": event.relationship_id,
            "event_kind": event.kind,
            "delta_affection": event.delta_affection,
            "delta_trust": event.delta_trust,
            "delta_attraction": event.delta_attraction,
            "delta_resentment": event.delta_resentment,
            "delta_jealousy": event.delta_jealousy,
            "affection_after": event.affection_after,
            "trust_after": event.trust_after,
            "attraction_after": event.attraction_after,
            "resentment_after": event.resentment_after,
            "jealousy_after": event.jealousy_after,
            "importance": event.importance,
            "round_id": event.round_id,
            "source_message_ids": source_ids,
        },
    )


# ---------------------------------------------------------------------------
# Anti-inflation (docs/relations.md §27): deterministic growth dampening
# ---------------------------------------------------------------------------
def scale_delta_by_resistance(
    current: int,
    delta: int,
    exponent: float | None = None,
) -> int:
    """Scale a positive delta down as the metric approaches 100 (§27.1).

    ``factor = ((100 - current) / 100) ** exponent`` — at low values the
    factor is close to 1 (growth is barely dampened), near 100 it decays to 0
    (the metric approaches its ceiling asymptotically). Negative and zero
    deltas are returned unchanged; decay (``kind="decay"``) never goes through
    this function.
    """
    if delta <= 0:
        return delta
    exp = (
        exponent
        if exponent is not None
        else settings.relationship_growth_resistance_exponent
    )
    current = max(0, min(100, current))
    factor = ((100 - current) / 100) ** exp
    return int(round(delta * factor))


def apply_saturation_guard(
    delta: int,
    recent: int,
    threshold: int,
    factor: float = 0.3,
) -> int:
    """Dampen a positive delta when the metric already grew a lot recently (§27.3).

    ``recent`` is the snapshot-based gain of the metric over the trajectory
    window (``recent_gain``). When it is at least ``threshold``, positive
    deltas are scaled by ``factor`` (floor 1 — growth stays alive but small).
    Negative deltas and values below the threshold are returned unchanged.
    """
    if delta <= 0 or recent < threshold:
        return delta
    return max(1, int(round(delta * factor)))


def trajectory_metric_gain(
    events: list[RelationshipEvent],
    metric: str,
) -> int:
    """Sum of positive per-event changes of ``metric`` across a trajectory.

    Events are expected in chronological order (oldest first, as returned by
    ``get_trajectory_events``). Only positive diffs of the ``*_after``
    snapshots count — dips do not cancel growth, so repeated warm rounds keep
    the saturation signal.
    """
    attr = f"{metric}_after"
    values = [int(getattr(e, attr, None) or 0) for e in events]
    if len(values) < 2:
        return 0
    gain = 0
    previous = values[0]
    for value in values[1:]:
        diff = value - previous
        if diff > 0:
            gain += diff
        previous = value
    return gain


# ---------------------------------------------------------------------------
# Apply delta from LLM analysis
# ---------------------------------------------------------------------------
async def apply_delta(
    db: AsyncSession,
    delta: RelationshipDelta,
    chat_id: int,
    round_id: Optional[str] = None,
) -> CharacterRelationship:
    """Apply a delta to the single directed edge source -> target.

    Reciprocity invariant: only ``delta.source_character_id ->
    delta.target_character_id`` is modified. The reverse edge is never touched,
    mirroring is forbidden (docs/relations.md §10, §22) — unrequited feelings
    (A->B affection 90, B->A affection 20) are valid and preserved.
    """
    rel = await get_or_create_relationship(
        db, chat_id, delta.source_character_id, delta.target_character_id,
    )

    # Issues are applied regardless of the metric delta importance — each
    # issue carries its own importance gate (see create_issue).
    issue_results = await apply_issue_deltas(
        db, delta.issues, rel=rel, round_id=round_id,
    )

    if delta.importance < settings.relationship_min_importance:
        logger.debug(
            "Skipping relationship delta for %d->%d: importance %d < %d",
            delta.source_character_id, delta.target_character_id,
            delta.importance, settings.relationship_min_importance,
        )
        # No commit here: the batch caller owns the single flush+commit.
        return rel

    old_type = rel.relationship_type
    new_type = delta.relationship_type or old_type

    if new_type != old_type:
        if is_family_type(old_type) or is_family_type(new_type):
            logger.warning(
                "Family relation type change %s -> %s blocked for rel %d->%d (UI-only)",
                old_type, new_type, delta.source_character_id,
                delta.target_character_id,
            )
            new_type = old_type
        elif not validate_transition(old_type, new_type):
            logger.warning(
                "Invalid transition %s -> %s for rel %d->%d; keeping %s",
                old_type, new_type, delta.source_character_id,
                delta.target_character_id, old_type,
            )
            new_type = old_type

    old_values = (
        rel.affection, rel.trust, rel.attraction, rel.resentment, rel.jealousy,
        rel.relationship_type, rel.description,
    )

    # Apply clamped deltas with growth resistance (§27.1): positive deltas are
    # scaled by ((100 - current) / 100) ** exponent so metrics approach their
    # ceiling asymptotically instead of jumping to 100; negative/zero deltas
    # pass through unchanged (damage is never dampened).
    rel.affection = clamp_metric(
        rel.affection
        + scale_delta_by_resistance(rel.affection, _clamp_delta(delta.delta_affection))
    )
    rel.trust = clamp_metric(
        rel.trust
        + scale_delta_by_resistance(rel.trust, _clamp_delta(delta.delta_trust))
    )
    rel.attraction = clamp_metric(
        rel.attraction
        + scale_delta_by_resistance(rel.attraction, _clamp_delta(delta.delta_attraction))
    )
    rel.resentment = clamp_metric(
        rel.resentment
        + scale_delta_by_resistance(rel.resentment, _clamp_delta(delta.delta_resentment))
    )
    rel.jealousy = clamp_metric(
        rel.jealousy
        + scale_delta_by_resistance(rel.jealousy, _clamp_delta(delta.delta_jealousy))
    )
    rel.relationship_type = new_type

    if delta.update_description and delta.description:
        rel.description = delta.description

    new_values = (
        rel.affection, rel.trust, rel.attraction, rel.resentment, rel.jealousy,
        rel.relationship_type, rel.description,
    )

    if old_values == new_values:
        logger.debug(
            "No actual change for rel %d->%d; skipping event",
            delta.source_character_id, delta.target_character_id,
        )
        # Values are identical to what is already persisted, so there is
        # nothing to write. Do not rollback (it would expire the ORM object).
        # No commit here: the batch caller owns the single flush+commit.
        return rel

    rel.updated_at = datetime.utcnow()

    # Create event log with kind + snapshot after (docs/relations.md §11, §17).
    # No flush/refresh: the batch caller applies one flush+commit per round and
    # ``rel`` already holds the new values in-memory. ``event`` gets its DB id
    # at that final flush.
    event = RelationshipEvent(
        relationship_id=rel.id,
        kind="llm",
        description=delta.description or "",
        reason=delta.reason or "",
        delta_affection=_clamp_delta(delta.delta_affection),
        delta_trust=_clamp_delta(delta.delta_trust),
        delta_attraction=_clamp_delta(delta.delta_attraction),
        delta_resentment=_clamp_delta(delta.delta_resentment),
        delta_jealousy=_clamp_delta(delta.delta_jealousy),
        affection_after=rel.affection,
        trust_after=rel.trust,
        attraction_after=rel.attraction,
        resentment_after=rel.resentment,
        jealousy_after=rel.jealousy,
        importance=delta.importance,
        source_message_ids=json.dumps(delta.source_message_ids or []),
        round_id=round_id,
        source_round_id=round_id,
    )
    db.add(event)
    _log_relationship_event(event)

    # Create memory for significant relationship events (Sprint 3 item 19)
    try:
        await _maybe_create_memory_from_event(db, rel, event, chat_id)
    except Exception as exc:
        logger.warning(
            "Failed to create memory from relationship event: %s", exc
        )

    return rel


def _clamp_delta(value: int) -> int:
    return max(-MAX_DELTA, min(MAX_DELTA, value))

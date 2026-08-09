"""Decay и архивирование событий (Milestone 6B, decomposition.md §4.4).

Вынесено из ``app/relationship_service.py`` без изменения поведения (тела
функций перенесены 1:1). ``apply_decay`` — посуточное затухание
ревности/обиды (динамический фактор по stress); ``prune_relationship_events``
— сворачивание старых событий в одну archive-запись.
"""

import logging
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import crud
from ..config import settings
from ..models import CharacterRelationship, RelationshipEvent

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Decay (Sprint 3 item 16, docs/relations.md §18)
# ---------------------------------------------------------------------------
def _dynamic_decay_factor(state) -> float:
    """Character factor for dynamic decay (Sprint 7, §18).

    Derives from ``character_state.stress`` (0..1): a stressed character holds
    onto resentment/jealousy longer, so decay is slower (factor < 1); a calm
    character lets go faster (factor > 1). Neutral stress (0.5) → 1.0.

    Returns the clamped factor in [dynamic_decay_factor_min, max]; 1.0 when no
    state row or stress is unknown.
    """
    if state is None:
        return 1.0
    stress = getattr(state, "stress", None)
    if stress is None:
        return 1.0
    try:
        stress = max(0.0, min(1.0, float(stress)))
    except (TypeError, ValueError):
        return 1.0
    sensitivity = settings.dynamic_decay_stress_sensitivity
    factor = 1.0 + sensitivity * (0.5 - stress)
    return max(
        settings.dynamic_decay_factor_min,
        min(settings.dynamic_decay_factor_max, factor),
    )


async def apply_decay(
    db: AsyncSession,
    chat_id: int,
    round_id: str,
) -> list[RelationshipEvent]:
    """Apply per-round decay to jealousy and resentment for all relationships in chat.

    - jealousy: -RELATIONSHIP_DECAY_JEALOUSY_PER_ROUND per round (default 3)
    - resentment: -RELATIONSHIP_DECAY_RESENTMENT_PER_ROUND per round (default 1)
    - affection/trust/attraction: no decay
    - dynamic (Sprint 7, ``dynamic_decay_enabled``): base_rate × character_factor
      where the factor comes from the source character's ``character_state.stress``
      (high stress → slower decay). ``dynamic_decay_jealousy_base_rate`` /
      ``dynamic_decay_resentment_base_rate`` default to the legacy rates.

    Creates RelationshipEvent(kind="decay") ONLY when value crosses a multiple of 10:
    20→19 (event), 10→9 (event), 0→0 (no event if already 0).

    Returns list of created decay events.
    """
    from sqlalchemy import select

    jealousy_decay = settings.relationship_decay_jealousy_per_round
    resentment_decay = settings.relationship_decay_resentment_per_round

    # Dynamic profile: load one character_state per source once, then map.
    state_by_character: dict[int, Any] = {}
    if settings.dynamic_decay_enabled:
        try:
            states = await crud.get_character_states_for_chat(db, chat_id)
            state_by_character = {s.character_id: s for s in states}
        except Exception as exc:  # noqa: BLE001 — decay никогда не роняет раунд
            logger.warning(
                "[chat_id=%d] Failed to load character states for dynamic decay: %s",
                chat_id, exc,
            )
            state_by_character = {}

    stmt = select(CharacterRelationship).where(CharacterRelationship.chat_id == chat_id)
    result = await db.execute(stmt)
    relationships = list(result.scalars().all())

    created_events: list[RelationshipEvent] = []

    for rel in relationships:
        old_jealousy = rel.jealousy
        old_resentment = rel.resentment

        if settings.dynamic_decay_enabled:
            factor = _dynamic_decay_factor(state_by_character.get(rel.source_character_id))
            rel_jealousy_decay = max(
                0, round(settings.dynamic_decay_jealousy_base_rate * factor)
            )
            rel_resentment_decay = max(
                0, round(settings.dynamic_decay_resentment_base_rate * factor)
            )
        else:
            rel_jealousy_decay = jealousy_decay
            rel_resentment_decay = resentment_decay

        new_jealousy = max(0, old_jealousy - rel_jealousy_decay)
        new_resentment = max(0, old_resentment - rel_resentment_decay)

        # Check if either crossed a multiple-of-10 boundary
        jealousy_crossed = (old_jealousy // 10) != (new_jealousy // 10) and old_jealousy > 0
        resentment_crossed = (old_resentment // 10) != (new_resentment // 10) and old_resentment > 0

        if not jealousy_crossed and not resentment_crossed:
            continue

        # Apply changes
        rel.jealousy = new_jealousy
        rel.resentment = new_resentment
        rel.updated_at = datetime.utcnow()

        # Create decay event with snapshot
        event = RelationshipEvent(
            relationship_id=rel.id,
            kind="decay",
            description="Естественное затухание эмоций",
            reason="",
            delta_affection=0,
            delta_trust=0,
            delta_attraction=0,
            delta_resentment=new_resentment - old_resentment,
            delta_jealousy=new_jealousy - old_jealousy,
            affection_after=rel.affection,
            trust_after=rel.trust,
            attraction_after=rel.attraction,
            resentment_after=rel.resentment,
            jealousy_after=rel.jealousy,
            importance=1,
            source_message_ids="[]",
            round_id=round_id,
            source_round_id=round_id,
        )
        db.add(event)
        created_events.append(event)

    return created_events


# ---------------------------------------------------------------------------
# Event pruning / archiving (Sprint 4 item 3, docs/relations.md §20)
# ---------------------------------------------------------------------------
async def prune_relationship_events(
    db: AsyncSession,
    relationship_id: int,
    max_events: int | None = None,
) -> Optional[RelationshipEvent]:
    """Fold old events of one relationship into a single archive entry.

    Keeps the newest ``max_events`` (``RELATIONSHIP_EVENTS_MAX_PER_PAIR``,
    default 100) raw events and replaces every older event with ONE aggregate
    ``kind="archive"`` row. The archive row:

    - carries ``delta_* = 0`` so it never changes the live relationship state;
    - snapshots the *current* ``*_after`` values of the edge;
    - aggregates counts per original kind (llm / decay / manual);
    - stores the folded period (``from_ts``–``to_ts``) in the description;
    - ``importance = 0`` so it never shows in trajectory/prompt blocks.

    Called from the batch commit in ``chat_engine`` and after manual field
    updates in the API — always inside the caller's transaction.
    """
    if max_events is None:
        max_events = settings.relationship_events_max_per_pair
    max_events = max(1, int(max_events))

    stmt = (
        select(RelationshipEvent)
        .where(RelationshipEvent.relationship_id == relationship_id)
        .order_by(RelationshipEvent.timestamp, RelationshipEvent.id)
    )
    result = await db.execute(stmt)
    events = list(result.scalars().all())

    if len(events) <= max_events:
        return None

    archive_prefix = events[: len(events) - max_events]
    archive_ids = [e.id for e in archive_prefix]

    llm_count = sum(1 for e in archive_prefix if e.kind == "llm")
    decay_count = sum(1 for e in archive_prefix if e.kind == "decay")
    manual_count = sum(1 for e in archive_prefix if e.kind == "manual")

    from_ts = archive_prefix[0].timestamp
    to_ts = archive_prefix[-1].timestamp

    rel = await db.get(CharacterRelationship, relationship_id)
    if rel is None:
        return None

    description = (
        f"Архив {len(archive_prefix)} событий "
        f"({from_ts:%Y-%m-%d %H:%M}–{to_ts:%Y-%m-%d %H:%M}): "
        f"llm={llm_count}, decay={decay_count}, manual={manual_count}"
    )

    if archive_ids:
        await db.execute(
            delete(RelationshipEvent).where(RelationshipEvent.id.in_(archive_ids))
        )

    archive_event = RelationshipEvent(
        relationship_id=relationship_id,
        kind="archive",
        description=description,
        reason="",
        delta_affection=0,
        delta_trust=0,
        delta_attraction=0,
        delta_resentment=0,
        delta_jealousy=0,
        affection_after=rel.affection,
        trust_after=rel.trust,
        attraction_after=rel.attraction,
        resentment_after=rel.resentment,
        jealousy_after=rel.jealousy,
        importance=0,
        source_message_ids="[]",
        round_id=archive_prefix[-1].round_id,
        source_round_id=archive_prefix[-1].round_id,
    )
    db.add(archive_event)
    return archive_event

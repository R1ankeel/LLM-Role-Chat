"""Память: witness-фильтр, rerank-буст, anchors, consolidation state (Sprint 4)."""



from __future__ import annotations



import json

import logging

from datetime import datetime

from typing import Any, Literal, Tuple

from sqlalchemy import delete, func, select

from sqlalchemy.ext.asyncio import AsyncSession

from .. import models

from .. import schemas

from ..config import settings

from ..database import memory_content_hash



logger = logging.getLogger(__name__)

# ----------------------------- Memory ------------------------------
async def _memory_exists(db: AsyncSession, character_id: int, content_hash: str) -> bool:
    stmt = select(models.Memory.id).where(
        models.Memory.character_id == character_id,
        models.Memory.content_hash == content_hash,
    )
    result = await db.execute(stmt)
    return result.scalars().first() is not None

async def create_memory(db: AsyncSession, memory: schemas.MemoryCreate, source_message_ids: list[int] | None = None) -> models.Memory | None:
    content_hash = memory_content_hash(memory.content)
    if await _memory_exists(db, memory.character_id, content_hash):
        return None
    import json
    data = memory.model_dump()
    # Sprint 2 (§7): пустое memory_type → default 'semantic' (риск миграции —
    # существующие строки получают 'semantic', type НЕ входит в content_hash).
    data["memory_type"] = data.get("memory_type") or "semantic"
    db_memory = models.Memory(
        **data,
        content_hash=content_hash,
        source_message_ids=json.dumps(source_message_ids or []),
    )
    db.add(db_memory)
    await db.commit()
    await db.refresh(db_memory)
    return db_memory

async def _count_memories_for_character(db: AsyncSession, character_id: int) -> int:
    stmt = (
        select(func.count())
        .select_from(models.Memory)
        .where(models.Memory.character_id == character_id)
    )
    result = await db.execute(stmt)
    return result.scalar() or 0

async def _delete_lowest_value_memories(
    db: AsyncSession, character_id: int, keep_count: int
) -> None:
    """Drop lowest-importance memories first, then oldest (P1 extraction quality)."""
    stmt = select(models.Memory).where(models.Memory.character_id == character_id)
    result = await db.execute(stmt)
    memories = list(result.scalars().all())
    if len(memories) <= keep_count:
        return

    def sort_key(mem: models.Memory):
        importance = mem.importance if mem.importance is not None else 0.5
        created = mem.created_at or datetime.min
        return (importance, created, mem.id)

    memories.sort(key=sort_key)
    to_delete = memories[: len(memories) - keep_count]
    delete_ids = [m.id for m in to_delete]
    if not delete_ids:
        return
    await db.execute(delete(models.Memory).where(models.Memory.id.in_(delete_ids)))
    await db.commit()

async def _delete_oldest_memories(db: AsyncSession, character_id: int, keep_count: int) -> None:
    """Backward-compatible alias → importance-aware eviction."""
    await _delete_lowest_value_memories(db, character_id, keep_count)

async def ensure_memory_limit(db: AsyncSession, character_id: int) -> None:
    count = await _count_memories_for_character(db, character_id)
    if count > settings.max_memories_per_character:
        await _delete_lowest_value_memories(db, character_id, settings.max_memories_per_character)

async def get_memories_by_character(
    db: AsyncSession, character_id: int, limit: int | None = None
) -> list[models.Memory]:
    stmt = (
        select(models.Memory)
        .where(models.Memory.character_id == character_id)
        .order_by(models.Memory.created_at.desc(), models.Memory.id.desc())
    )
    if limit is not None:
        stmt = stmt.limit(limit)
    result = await db.execute(stmt)
    memories = list(result.scalars().all())
    memories.reverse()
    if memories:
        await _touch_memory_access(db, [m.id for m in memories])
    return memories

async def filter_memories_by_witness(
    db: AsyncSession,
    memories: list[models.Memory],
    viewer_character_id: int,
) -> Tuple[list[models.Memory], dict[int, WitnessQuality]]:
    """Filter memories so the character only keeps those they actually witnessed.

    Checks each memory's *source_message_ids* against the MessagePresence table.
    A memory is kept if at least one of its source messages has presence
    ``present`` or ``told`` for *viewer_character_id*.

    Returns ``(filtered_memories, quality_map)`` where *quality_map* maps
    memory id → ``"direct"`` (at least one source with ``present``) or
    ``"hearsay"`` (all sources are ``told`` only).
    """
    if not memories:
        return [], {}

    # Collect all unique source message IDs across all memories
    import json
    all_source_ids: set[int] = set()
    mem_to_sources: dict[int, list[int]] = {}
    for mem in memories:
        try:
            ids = json.loads(mem.source_message_ids or "[]")
        except (json.JSONDecodeError, TypeError):
            ids = []
        mem_to_sources[mem.id] = ids
        all_source_ids.update(ids)

    if not all_source_ids:
        # No source IDs means legacy memory — keep it (assume direct)
        return memories, {mem.id: "direct" for mem in memories}

    # Batch-query presence for all source messages
    stmt = select(models.MessagePresence).where(
        models.MessagePresence.character_id == viewer_character_id,
        models.MessagePresence.message_id.in_(all_source_ids),
    )
    result = await db.execute(stmt)
    presence_rows = list(result.scalars().all())
    msg_presence: dict[int, str] = {row.message_id: row.presence for row in presence_rows}

    # Sprint 1 (§7.1): константа инлайнится из witness_model.MEMORY_OBSERVABLE_PRESENCES
    # (crud не импортирует WPE-модули). {"present", "told"}
    OBSERVABLE = frozenset({"present", "told"})

    filtered: list[models.Memory] = []
    quality_map: dict[int, str] = {}
    for mem in memories:
        source_ids = mem_to_sources.get(mem.id, [])
        if not source_ids:
            filtered.append(mem)
            quality_map[mem.id] = "direct"
            continue
        source_presences = [msg_presence.get(sid) for sid in source_ids]
        if any(p == "present" for p in source_presences):
            filtered.append(mem)
            quality_map[mem.id] = "direct"
        elif any(p in OBSERVABLE for p in source_presences):
            filtered.append(mem)
            quality_map[mem.id] = "hearsay"
        # If all absent, memory is excluded

    return filtered, quality_map

def _apply_witness_boost(
    selected: list[models.Memory],
    quality_map: dict[int, WitnessQuality],
    boost: float = 1.5,
) -> list[models.Memory]:
    """Re-rank so directly witnessed facts appear before hearsay at same score.

    Sorts stable so that within equal positions, direct facts come first.
    This is a gentle nudge, not a hard cutoff.
    """
    if not selected or not quality_map:
        return selected
    return sorted(
        selected,
        key=lambda m: (
            0 if quality_map.get(m.id) == "direct" else 1,
        ),
    )

async def decay_memory_importance(db: AsyncSession) -> None:
    """Periodic decay of importance for memories not accessed recently.

    Reduces ``importance`` by ``settings.memory_importance_decay_factor`` for
    every full ``settings.memory_importance_decay_days`` since last access.
    """
    import math
    from datetime import datetime, timedelta

    decay_days = settings.memory_importance_decay_days
    decay_factor = settings.memory_importance_decay_factor
    if decay_days <= 0 or decay_factor <= 0:
        return

    cutoff = datetime.utcnow() - timedelta(days=decay_days)
    stmt = (
        select(models.Memory)
        .where(models.Memory.last_accessed_at < cutoff)
        .where(models.Memory.importance > 0.1)
    )
    result = await db.execute(stmt)
    stale = list(result.scalars().all())
    if not stale:
        return

    for mem in stale:
        days_unused = (datetime.utcnow() - (mem.last_accessed_at or mem.created_at)).days
        periods = max(1, days_unused // decay_days)
        decayed = mem.importance * (decay_factor ** periods)
        mem.importance = max(0.05, round(decayed, 2))

    await db.commit()
    logger.info("Decayed importance for %d stale memories", len(stale))

async def _touch_memory_access(db: AsyncSession, memory_ids: list[int]) -> None:
    """Update last_accessed_at for retrieved memories."""
    if not memory_ids:
        return
    from datetime import datetime
    await db.execute(
        models.Memory.__table__.update()
        .where(models.Memory.id.in_(memory_ids))
        .values(last_accessed_at=datetime.utcnow())
    )
    await db.commit()

async def get_memories_for_characters(
    db: AsyncSession, character_ids: list[int], limit: int | None = None
) -> dict[int, list[models.Memory]]:
    """Load memories for multiple characters in one query."""
    if not character_ids:
        return {}
    stmt = (
        select(models.Memory)
        .where(models.Memory.character_id.in_(character_ids))
        .order_by(
            models.Memory.character_id,
            models.Memory.created_at.desc(),
            models.Memory.id.desc(),
        )
    )
    result = await db.execute(stmt)
    all_memories = list(result.scalars().all())
    grouped: dict[int, list[models.Memory]] = {cid: [] for cid in character_ids}
    for mem in all_memories:
        bucket = grouped[mem.character_id]
        if limit is None or len(bucket) < limit:
            bucket.append(mem)
    for cid in grouped:
        grouped[cid].reverse()
    return grouped

async def delete_memory(db: AsyncSession, memory_id: int) -> bool:
    db_memory = await db.get(models.Memory, memory_id)
    if db_memory is None:
        return False
    await db.delete(db_memory)
    await db.commit()
    return True

async def update_memory(
    db: AsyncSession, memory_id: int, memory_update: schemas.MemoryUpdate
) -> models.Memory | None:
    """Update memory content/importance/category (+ Sprint 2 поля типа/эмоции)."""
    db_memory = await db.get(models.Memory, memory_id)
    if db_memory is None:
        return None
    update_data = memory_update.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(db_memory, field, value)
    if db_memory.memory_type is None:
        db_memory.memory_type = "semantic"
    await db.commit()
    await db.refresh(db_memory)
    return db_memory

# --------------------- Memory Anchors (Sprint 2, §7) ---------------------
def anchor_activation_score(anchor: models.MemoryAnchor, now=None) -> float:
    """Детерминированный score активации якоря: importance × recency (§7).

    recency = 1 / (1 + возраст_в_днях) — свежие якоря получают больший вес;
    score в диапазоне (0..1], монотонен по importance и свежести.
    """
    if now is None:
        now = datetime.utcnow()
    timestamp = anchor.timestamp or now
    age_days = max(0.0, (now - timestamp).total_seconds() / 86400.0)
    recency = 1.0 / (1.0 + age_days)
    return float(anchor.importance or 0.5) * recency

def select_top_anchors(
    anchors: list[models.MemoryAnchor],
    max_k: int,
    now=None,
) -> list[models.MemoryAnchor]:
    """Top-K якорей по ``importance × recency`` (активация в контекст, §7/§12.3).

    Дедупликация по ``event_id``: один канонический источник порождает не более
    одного якоря в top-K (уникальность в контексте). ``max_k`` — cap из
    ``RELATIONSHIP_ANCHOR_MAX`` (по умолчанию 3).
    """
    if not anchors or max_k <= 0:
        return []
    scored = [(anchor_activation_score(a, now), a) for a in anchors]
    scored.sort(key=lambda pair: pair[0], reverse=True)
    seen_events: set[int] = set()
    selected: list[models.MemoryAnchor] = []
    for score, anchor in scored:
        event_id = anchor.event_id
        if event_id is not None and event_id in seen_events:
            continue
        if event_id is not None:
            seen_events.add(event_id)
        selected.append(anchor)
        if len(selected) >= max_k:
            break
    return selected

async def create_memory_anchor(
    db: AsyncSession,
    *,
    relationship_id: int,
    event_id: int | None = None,
    emotion: str = "",
    valence: float = 0.0,
    intensity: float = 0.0,
    importance: float = 0.5,
    timestamp=None,
) -> models.MemoryAnchor:
    """Записать эмоциональный якорь направленного отношения (§7).

    Якорь пишется движком из значимых RelationshipEvent (расширение
    ``_maybe_create_memory_from_event``); Sensors якоря НЕ предлагает.
    """
    anchor = models.MemoryAnchor(
        relationship_id=relationship_id,
        event_id=event_id,
        emotion=emotion or "",
        valence=max(-1.0, min(1.0, float(valence or 0.0))),
        intensity=max(0.0, min(1.0, float(intensity or 0.0))),
        importance=max(0.0, min(1.0, float(importance or 0.5))),
        timestamp=timestamp or datetime.utcnow(),
    )
    db.add(anchor)
    await db.commit()
    await db.refresh(anchor)
    return anchor

async def get_anchors_for_relationship(
    db: AsyncSession, relationship_id: int, limit: int | None = None
) -> list[models.MemoryAnchor]:
    """Все якоря отношения source→target (по убыванию свежести)."""
    stmt = (
        select(models.MemoryAnchor)
        .where(models.MemoryAnchor.relationship_id == relationship_id)
        .order_by(models.MemoryAnchor.timestamp.desc(), models.MemoryAnchor.id.desc())
    )
    if limit is not None:
        stmt = stmt.limit(limit)
    result = await db.execute(stmt)
    return list(result.scalars().all())

async def get_anchors_for_relationships(
    db: AsyncSession, relationship_ids: list[int], limit: int | None = None
) -> dict[int, list[models.MemoryAnchor]]:
    """Загрузить якоря для нескольких отношений одним запросом (для контекста)."""
    if not relationship_ids:
        return {}
    stmt = (
        select(models.MemoryAnchor)
        .where(models.MemoryAnchor.relationship_id.in_(relationship_ids))
        .order_by(models.MemoryAnchor.timestamp.desc(), models.MemoryAnchor.id.desc())
    )
    result = await db.execute(stmt)
    anchors = list(result.scalars().all())
    grouped: dict[int, list[models.MemoryAnchor]] = {rid: [] for rid in relationship_ids}
    for anchor in anchors:
        bucket = grouped.get(anchor.relationship_id)
        if bucket is None:
            continue
        if limit is None or len(bucket) < limit:
            bucket.append(anchor)
    return grouped

# ------------------------ Consolidation State (Sprint 12) ----------------------
async def get_consolidation_state(
    db: AsyncSession, chat_id: int
) -> models.ConsolidationState | None:
    """Read `consolidation_state` row for a chat (§20, Sprint 12)."""
    stmt = select(models.ConsolidationState).where(
        models.ConsolidationState.chat_id == chat_id
    )
    result = await db.execute(stmt)
    return result.scalars().first()

async def upsert_consolidation_state(
    db: AsyncSession,
    chat_id: int,
    *,
    last_soft_at: datetime | None = None,
    last_hard_at: datetime | None = None,
    counters: dict | None = None,
) -> models.ConsolidationState:
    """Create-or-update `consolidation_state` row (Sprint 12).

    ``counters`` is the JSON snapshot of new-input counts since the last
    consolidation plus dedup metadata (``critical_round``/``critical_count``).
    """
    state = await get_consolidation_state(db, chat_id)
    now = datetime.utcnow()
    if state is None:
        state = models.ConsolidationState(
            chat_id=chat_id,
            last_soft_at=last_soft_at,
            last_hard_at=last_hard_at,
            counters=json.dumps(counters or {}, ensure_ascii=False),
            updated_at=now,
        )
        db.add(state)
    else:
        if last_soft_at is not None:
            state.last_soft_at = last_soft_at
        if last_hard_at is not None:
            state.last_hard_at = last_hard_at
        if counters is not None:
            state.counters = json.dumps(counters, ensure_ascii=False)
        state.updated_at = now
    await db.commit()
    await db.refresh(state)
    return state

async def reset_consolidation_state(db: AsyncSession, chat_id: int) -> None:
    """Delete the `consolidation_state` row for a chat (Sprint 12)."""
    state = await get_consolidation_state(db, chat_id)
    if state is not None:
        await db.delete(state)
        await db.commit()

async def count_consolidation_inputs(
    db: AsyncSession, chat_id: int, since: datetime | None
) -> dict[str, int]:
    """Count new rows per consolidation input since ``since`` (§20).

    Returns ``{"messages", "events", "facts", "rel_events", "story_events",
    "anchors"}`` — new rows across the six consolidation tables created after
    ``since`` (or all rows when ``since`` is None). Cheap indexed counts.
    """
    if since is None:
        since = datetime.min
    keys = ("messages", "events", "facts", "rel_events", "story_events", "anchors")
    counts: dict[str, int] = {}
    for (model, ts_col, join_model), key in zip(_CONSOLIDATION_INPUTS, keys):
        ts = getattr(model, ts_col)
        stmt = select(func.count()).select_from(model)
        if join_model is not None:
            # relationship_events / memory_anchors -> join through relationships
            stmt = stmt.join(
                join_model,
                join_model.id == model.relationship_id,
            ).where(join_model.chat_id == chat_id, ts > since)
        else:
            stmt = stmt.where(model.chat_id == chat_id, ts > since)
        result = await db.execute(stmt)
        counts[key] = result.scalar() or 0
    return counts

WitnessQuality = Literal["direct", "hearsay"]

# (table, timestamp column, extra join predicate) triples for the consolidation
# score inputs (§20). `relationship_events`/`memory_anchors` carry no chat_id —
# they are scoped through `character_relationships`.
_CONSOLIDATION_INPUTS: tuple[tuple[Any, str, Any], ...] = (
    (models.Message, "timestamp", None),
    (models.WorldEvent, "created_at", None),
    (models.Memory, "created_at", None),
    (models.RelationshipEvent, "timestamp", models.CharacterRelationship),
    (models.StoryEvent, "created_at", None),
    (models.MemoryAnchor, "timestamp", models.CharacterRelationship),
)

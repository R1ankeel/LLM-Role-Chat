"""Память: адаптивная консолидация Sprint 12 (Sprint 6C).

Sprint 6C (§4.5 decomposition.md): перенос из ``memory_service.py`` адаптивного
блока (§20 update20.md): скоринг, триггеры, ``consolidate_chat_adaptive``.
Направление: memory/ → crud, embedding_service, ollama_client, task_queue
(без обратных импортов). Локальные импорты против цикла сохраняются.
"""

import json
from datetime import datetime
from typing import Any

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import crud
from .. import embedding_service
from .. import models
from .. import ollama_client
from ..config import settings
from .consolidation import _consolidate_character_memories

logger = structlog.get_logger(__name__)


# ============================================================
# Adaptive Consolidation (Sprint 12, Plans/update20.md §20)
# ============================================================

# Input keys in the same order as the score weights.
CONSOLIDATION_COUNT_KEYS = (
    "messages",
    "events",
    "facts",
    "rel_events",
    "story_events",
    "anchors",
)

# Deterministic critical-event whitelist (§20): смерть, предательство, признание,
# свадьба, важное раскрытие, сюжетный milestone, завершение главной цели и т.п.
CRITICAL_ACTION_KEYWORDS = (
    "смерть",
    "умирает",
    "погиб",
    "погибает",
    "предател",
    "признание",
    "признаётся",
    "признается",
    "свадьб",
    "бракосочетан",
    "раскрыти",
    "разоблач",
    "milestone",
    "сюжетн",
    "главная цель",
    "завершени",
    "завершен",
    "завершён",
    "начиная отношений",
    "конец отношений",
)


def _consolidation_weights() -> tuple[float, ...]:
    """Score weights from config (§20)."""
    return (
        settings.consolidation_weight_messages,
        settings.consolidation_weight_events,
        settings.consolidation_weight_facts,
        settings.consolidation_weight_rel_events,
        settings.consolidation_weight_story_events,
        settings.consolidation_weight_anchors,
    )


def compute_consolidation_score(
    counts: dict[str, int], weights: tuple[float, ...] | None = None
) -> float:
    """Weighted score of new inputs since the last consolidation (§20).

    ``counts`` has the ``CONSOLIDATION_COUNT_KEYS`` shape. Deterministic.
    """
    if weights is None:
        weights = _consolidation_weights()
    return sum(
        float(counts.get(key, 0)) * weight
        for key, weight in zip(CONSOLIDATION_COUNT_KEYS, weights)
    )


def is_critical_event(event: Any, *, critical_importance: float | None = None) -> bool:
    """Deterministic critical-event detection (§20).

    True when ``importance >= critical_importance`` OR the event's action/type/
    description matches the whitelist keywords. No LLM involved.
    """
    if critical_importance is None:
        critical_importance = float(settings.consolidation_critical_importance or 8.0)

    importance = 0.0
    try:
        importance = float(getattr(event, "importance", 0) or 0)
    except (TypeError, ValueError):
        importance = 0.0
    if importance >= critical_importance:
        return True

    parts: list[str] = []
    for attr in ("action", "event_type", "description", "event", "kind", "content"):
        value = getattr(event, attr, None)
        if value is None:
            continue
        if isinstance(value, dict):
            value = json.dumps(value, ensure_ascii=False)
        parts.append(str(value))
    haystack = " ".join(parts).lower()
    return any(keyword in haystack for keyword in CRITICAL_ACTION_KEYWORDS)


async def _latest_critical_event(
    db: AsyncSession, chat_id: int, since: datetime
) -> dict | None:
    """Most recent critical world event in the chat since ``since`` (§20)."""
    stmt = (
        select(models.WorldEvent)
        .where(
            models.WorldEvent.chat_id == chat_id,
            models.WorldEvent.created_at > since,
        )
        .order_by(models.WorldEvent.created_at.desc(), models.WorldEvent.id.desc())
        .limit(200)
    )
    result = await db.execute(stmt)
    for event in result.scalars().all():
        if is_critical_event(event):
            return {
                "id": event.id,
                "round_id": event.round_id,
                "importance": event.importance,
                "event_type": event.event_type,
                "action": event.action,
            }
    return None


def _parse_consolidation_counters(state) -> dict:
    if state is None or not state.counters:
        return {}
    try:
        parsed = json.loads(state.counters)
        return parsed if isinstance(parsed, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _parse_payload_dt(value: Any) -> datetime | None:
    """Parse an ISO datetime from a job payload (Sprint 12)."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


async def evaluate_consolidation(
    db: AsyncSession,
    chat_id: int,
    *,
    round_id: str | None = None,
) -> dict:
    """Deterministic adaptive-consolidation decision (§20).

    Computes soft/hard scores since the last consolidation and detects critical
    events. Returns ``level`` in {critical, hard, soft, skip} plus the counts
    and scores. No DB writes; the scheduler/hook uses this to decide whether to
    enqueue a consolidation job.
    """
    chat = await crud.get_chat(db, chat_id)
    if chat is None:
        return {"level": "skip", "reason": "no chat"}
    if not settings.consolidation_enabled:
        return {"level": "skip", "reason": "consolidation disabled"}

    state = await crud.get_consolidation_state(db, chat_id)
    base = getattr(chat, "created_at", None) or datetime.min
    since_soft = state.last_soft_at if state is not None and state.last_soft_at else base
    since_hard = state.last_hard_at if state is not None and state.last_hard_at else base

    counts_soft = await crud.count_consolidation_inputs(db, chat_id, since_soft)
    counts_hard = await crud.count_consolidation_inputs(db, chat_id, since_hard)
    score_soft = compute_consolidation_score(counts_soft)
    score_hard = compute_consolidation_score(counts_hard)

    soft_threshold = float(settings.consolidation_soft_threshold or 25.0)
    hard_threshold = float(settings.consolidation_hard_threshold or 50.0)
    max_per_round = int(settings.consolidation_critical_max_per_round or 2)

    result = {
        "chat_id": chat_id,
        "score_soft": score_soft,
        "score_hard": score_hard,
        "counts": counts_hard,
        "critical": None,
        "round_id": round_id,
        "since_soft": since_soft,
        "since_hard": since_hard,
    }

    critical = await _latest_critical_event(db, chat_id, since_hard)
    if critical is not None:
        result["critical"] = critical
        counters = _parse_consolidation_counters(state)
        current_round = critical.get("round_id") or round_id
        already = counters.get("critical_round") == current_round
        if already and int(counters.get("critical_count", 0)) >= max_per_round:
            # Dedup: no more critical consolidations for this round; fall back
            # to score-based tiers.
            if score_hard >= hard_threshold:
                result["level"] = "hard"
                result["reason"] = "critical dedup -> hard score"
            elif score_soft >= soft_threshold:
                result["level"] = "soft"
                result["reason"] = "critical dedup -> soft score"
            else:
                result["level"] = "skip"
                result["reason"] = "critical dedup cap reached"
            return result
        result["level"] = "critical"
        result["reason"] = "critical event"
        return result

    if score_hard >= hard_threshold:
        result["level"] = "hard"
        result["reason"] = "hard threshold"
    elif score_soft >= soft_threshold:
        result["level"] = "soft"
        result["reason"] = "soft threshold"
    else:
        result["level"] = "skip"
        result["reason"] = "idle (score below thresholds)"
    return result


async def schedule_adaptive_consolidation(
    db: AsyncSession,
    *,
    chat_id: int,
    model_name: str | None = None,
    round_id: str | None = None,
) -> dict:
    """Score-based trigger (§20): evaluate and, when triggered, mark the
    ``consolidation_state`` baseline and enqueue a background job.

    Idle chats (score≈0, no critical) are skipped. Critical events trigger an
    immediate hard consolidation (deduplicated to N per round). Enqueuing
    advances the baseline, so repeated polls for the same events do not
    re-trigger.
    """
    if not settings.adaptive_consolidation_enabled:
        return {"level": "skip", "reason": "adaptive disabled"}

    decision = await evaluate_consolidation(db, chat_id, round_id=round_id)
    level = decision["level"]
    if level == "skip":
        return decision

    state = await crud.get_consolidation_state(db, chat_id)
    counters = _parse_consolidation_counters(state)
    for key in CONSOLIDATION_COUNT_KEYS:
        counters[key] = int(decision["counts"].get(key, 0))

    now = datetime.utcnow()
    last_soft: datetime | None = now
    last_hard: datetime | None = None

    if level == "critical":
        last_hard = now
        current_round = (decision.get("critical") or {}).get("round_id") or round_id
        if counters.get("critical_round") != current_round:
            counters["critical_round"] = current_round
            counters["critical_count"] = 1
        else:
            counters["critical_count"] = int(counters.get("critical_count", 0)) + 1
    elif level == "hard":
        last_hard = now

    await crud.upsert_consolidation_state(
        db,
        chat_id,
        last_soft_at=last_soft,
        last_hard_at=last_hard,
        counters=counters,
    )

    from .jobs import enqueue_consolidation_job  # локальный импорт против цикла

    job = await enqueue_consolidation_job(
        chat_id=chat_id,
        model_name=model_name,
        level=level,
        since_soft=decision.get("since_soft"),
        since_hard=decision.get("since_hard"),
    )
    decision["enqueued"] = True
    decision["job_id"] = job.id
    decision["job"] = job
    return decision


async def _consolidate_chat_memories(
    db: AsyncSession,
    client: httpx.AsyncClient,
    *,
    chat_id: int,
    model_name: str,
) -> dict:
    """Component: memory clustering + merge for all characters of a chat."""
    stmt = (
        select(models.Character.id, models.Character.name, models.Character.chat_id)
        .join(models.Memory, models.Memory.character_id == models.Character.id)
        .where(models.Character.chat_id == chat_id)
        .distinct()
    )
    result = await db.execute(stmt)
    characters = result.all()

    total_merged = 0
    total_deleted = 0
    chars_processed = 0
    for char_id, char_name, _cid in characters:
        merged, deleted = await _consolidate_character_memories(
            db,
            client,
            model_name,
            char_id,
            char_name,
            settings.consolidation_similarity_threshold,
            settings.consolidation_min_cluster_size,
            settings.consolidation_max_memories_per_char,
        )
        total_merged += merged
        total_deleted += deleted
        chars_processed += 1
    return {"chars": chars_processed, "merged": total_merged, "deleted": total_deleted}


async def _consolidate_chat_summary(
    db: AsyncSession,
    client: httpx.AsyncClient,
    *,
    chat_id: int,
    model_name: str,
    since: datetime,
) -> dict:
    """Component: refresh per-character summaries from dialogue since ``since``.

    Summary is ONE of the consolidation outputs (§20) — the pipeline stays
    separate from the ~20-message summarizer in ``chat_engine``.
    """
    if since is None:
        since = datetime.min
    characters = await crud.get_characters_by_chat(db, chat_id)
    updated = 0
    for character in characters:
        dialogue = await crud.get_messages_since_ts(
            db,
            chat_id,
            since,
            role="character",
            character_id=character.id,
            limit=120,
        )
        if not dialogue:
            continue
        dialogue_text = "\n".join(msg.content for msg in dialogue)
        existing = await crud.get_character_summary(db, character.id)
        existing_content = existing.content if existing else ""
        try:
            updated_summary = await ollama_client.summarize_for_character(
                client,
                model_name,
                character,
                dialogue_text,
                existing_summary=existing_content,
            )
        except Exception:
            logger.warning(
                "[Consolidation][chat_id=%d] summary refresh failed for %s",
                chat_id,
                character.name,
            )
            continue
        if not (updated_summary or "").strip():
            continue
        through_message_id = max(msg.id for msg in dialogue)
        await crud.upsert_character_summary(
            db, chat_id, character.id, updated_summary.strip(), through_message_id
        )
        updated += 1
    return {"updated": updated}


async def _consolidate_chat_relationships(db: AsyncSession, *, chat_id: int) -> dict:
    """Component: fold old relationship events into archive rows (hard).

    Reuses the deterministic ``prune_relationship_events`` fold for every
    relationship of the chat — relationship evidence stays bounded.
    """
    stmt = select(models.CharacterRelationship).where(
        models.CharacterRelationship.chat_id == chat_id
    )
    result = await db.execute(stmt)
    relationships = list(result.scalars().all())

    from .. import relationship_service  # локальный импорт против цикла

    folded = 0
    for rel in relationships:
        archived = await relationship_service.prune_relationship_events(db, rel.id)
        if archived is not None:
            folded += 1
    if folded:
        await db.commit()
    return {"relationships": len(relationships), "folded": folded}


async def _consolidate_chat_anchors(db: AsyncSession, *, chat_id: int) -> dict:
    """Component: dedupe emotional anchors by (relationship, event) — keep the
    most important, drop weaker duplicates (hard).
    """
    stmt = (
        select(models.MemoryAnchor)
        .join(
            models.CharacterRelationship,
            models.CharacterRelationship.id == models.MemoryAnchor.relationship_id,
        )
        .where(models.CharacterRelationship.chat_id == chat_id)
        .order_by(
            models.MemoryAnchor.importance.desc(),
            models.MemoryAnchor.timestamp.desc(),
            models.MemoryAnchor.id.desc(),
        )
    )
    result = await db.execute(stmt)
    anchors = list(result.scalars().all())

    seen: dict[tuple, models.MemoryAnchor] = {}
    removed = 0
    for anchor in anchors:
        key = (anchor.relationship_id, anchor.event_id)
        existing = seen.get(key)
        if existing is None:
            seen[key] = anchor
            continue
        # Ordering already puts the highest-importance first; drop the later one.
        await db.delete(anchor)
        removed += 1
    if removed:
        await db.commit()
    return {"anchors": len(anchors), "removed": removed}


async def _consolidate_chat_story(
    db: AsyncSession,
    client: httpx.AsyncClient,
    *,
    chat_id: int,
    model_name: str,
    level: str,
) -> dict:
    """Component: story state update (hard). Delegates to the existing Sprint 9
    hook, which is a canary gated by ``story_consolidation_enabled``.
    """
    if not settings.story_consolidation_enabled:
        return {"skipped": "story consolidation flag off"}
    from ..plot import story_consolidation

    try:
        return await story_consolidation.maybe_consolidate_story(
            db,
            client,
            chat_id=chat_id,
            round_id=None,
            model_name=model_name,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[Consolidation][chat_id=%d] story component failed: %s", chat_id, exc
        )
        return {"error": str(exc)}


async def _consolidate_chat_index(db: AsyncSession, *, chat_id: int) -> dict:
    """Component: embedding/index refresh for the chat's memories (hard).

    Deterministic fallback to legacy behaviour when ``embedding_enabled`` is
    off — no forced embedding pipeline.
    """
    if not settings.embedding_enabled:
        return {"skipped": "embedding disabled"}
    try:
        emb_service = embedding_service.get_embedding_service()
        stmt = (
            select(models.Memory)
            .where(
                models.Memory.chat_id == chat_id,
                models.Memory.embedding.is_(None),
            )
            .order_by(models.Memory.created_at.desc())
            .limit(200)
        )
        result = await db.execute(stmt)
        memories = list(result.scalars().all())
        processed = 0
        for mem in memories:
            embedding = await emb_service.embed_single(mem.content)
            if embedding:
                mem.embedding = emb_service.pack_embedding(embedding)
                processed += 1
        if processed:
            await db.commit()
        return {"processed": processed, "missing": len(memories)}
    except Exception as exc:  # noqa: BLE001
        logger.warning("[Consolidation][chat_id=%d] index refresh failed: %s", chat_id, exc)
        return {"error": str(exc)}


async def consolidate_chat_adaptive(
    db: AsyncSession,
    client: httpx.AsyncClient,
    *,
    chat_id: int,
    model_name: str,
    level: str,
    since_soft: datetime | None = None,
    since_hard: datetime | None = None,
) -> dict:
    """Full adaptive consolidation set for one chat (§20, Sprint 12).

    - soft: memory merge + summary refresh;
    - hard/critical: + relationship evidence fold + anchors dedupe + story
      update + embedding/index refresh.

    ``since_soft``/``since_hard`` pin the consolidation window (the baselines
    at trigger time, passed through the job payload); when absent they fall back
    to the ``consolidation_state`` markers (or chat creation). Runs only the
    enabled components; every component is isolated so a failure does not break
    the rest. Returns a per-component report.
    """
    report: dict = {"chat_id": chat_id, "level": level}

    report["memory"] = await _consolidate_chat_memories(
        db, client, chat_id=chat_id, model_name=model_name
    )

    if since_soft is None:
        state = await crud.get_consolidation_state(db, chat_id)
        since_soft = (
            state.last_soft_at
            if state is not None and state.last_soft_at
            else datetime.min
        )
    report["summary"] = await _consolidate_chat_summary(
        db, client, chat_id=chat_id, model_name=model_name, since=since_soft
    )

    if level in ("hard", "critical"):
        report["relationship"] = await _consolidate_chat_relationships(
            db, chat_id=chat_id
        )
        report["anchors"] = await _consolidate_chat_anchors(db, chat_id=chat_id)
        report["story"] = await _consolidate_chat_story(
            db, client, chat_id=chat_id, model_name=model_name, level=level
        )
        report["index"] = await _consolidate_chat_index(db, chat_id=chat_id)

    logger.info(
        "[Consolidation][chat_id=%d] adaptive %s complete: %s",
        chat_id,
        level,
        {k: v for k, v in report.items() if k != "chat_id"},
    )
    return report

"""Память: legacy-консолидация P3 — кластеризация и слияние (Sprint 6C).

Sprint 6C (§4.5 decomposition.md): перенос из ``memory_service.py``
`_cluster_memories_by_similarity` … `consolidate_memories_job`. Направление:
memory/ → crud, ollama_client (без обратных импортов).
"""

from datetime import datetime

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import models
from .. import ollama_client
from ..config import settings
from .validation import jaccard_similarity

logger = structlog.get_logger(__name__)


# ============================================================
# Memory Consolidation (P3)
# ============================================================

async def _cluster_memories_by_similarity(
    memories: list[models.Memory], threshold: float
) -> list[list[models.Memory]]:
    """Group memories into clusters by Jaccard similarity (greedy agglomerative)."""
    if not memories:
        return []

    # Sort by importance desc (highest first) - these become cluster centers
    sorted_memories = sorted(
        memories, key=lambda m: m.importance if m.importance is not None else 0.5, reverse=True
    )

    clusters: list[list[models.Memory]] = []
    used: set[int] = set()

    for mem in sorted_memories:
        if mem.id in used:
            continue

        cluster = [mem]
        used.add(mem.id)

        for other in sorted_memories:
            if other.id in used:
                continue

            sim = jaccard_similarity(mem.content, other.content)
            if sim >= threshold:
                cluster.append(other)
                used.add(other.id)

        clusters.append(cluster)

    return clusters


async def _merge_memory_cluster_llm(
    client: httpx.AsyncClient,
    model_name: str,
    cluster: list[models.Memory],
    character_name: str,
) -> str | None:
    """Use LLM to merge similar facts into one concise fact."""
    if len(cluster) == 1:
        return cluster[0].content

    facts_text = "\n".join(f"- {m.content}" for m in cluster)

    # Use extraction model or default
    consolidation_model = settings.consolidation_llm_model or model_name

    prompt = (
        f"Персонаж: {character_name}\n"
        f"Схожие факты для объединения:\n{facts_text}\n\n"
        "Объедини эти факты в ОДИН точный и краткий факт. "
        "Сохрани важные детали: имена, места, даты, отношения. Убери повторы. "
        "Результат — только объединённый факт, без лишних слов."
    )

    try:
        async with ollama_client.llm_request(consolidation_model, "/api/generate"):
            resp = await client.post(
                "/api/generate",
                json={
                    "model": consolidation_model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {"temperature": 0.3, "num_predict": 100},
                },
                timeout=settings.ollama_timeout,
            )
        resp.raise_for_status()
        data = resp.json()
        merged = data.get("response", "").strip()
        if merged:
            logger.debug(
                "[Consolidation] Merged %d facts into: %s",
                len(cluster),
                merged[:120],
            )
            return merged
    except Exception:
        logger.warning("[Consolidation] LLM merge failed for cluster of %d", len(cluster))

    # Fallback: keep longest fact
    return max(cluster, key=lambda m: len(m.content)).content


async def _consolidate_character_memories(
    db: AsyncSession,
    client: httpx.AsyncClient,
    model_name: str,
    character_id: int,
    character_name: str,
    threshold: float,
    min_cluster_size: int,
    max_memories: int,
) -> tuple[int, int]:
    """Consolidate memories for a single character. Returns (merged_count, deleted_count)."""
    # Load memories - most recent/important first
    stmt = (
        select(models.Memory)
        .where(models.Memory.character_id == character_id)
        .order_by(models.Memory.importance.desc(), models.Memory.created_at.desc())
        .limit(max_memories)
    )
    result = await db.execute(stmt)
    memories = list(result.scalars().all())

    if len(memories) < min_cluster_size:
        return 0, 0

    clusters = await _cluster_memories_by_similarity(memories, threshold)

    merged_count = 0
    deleted_count = 0

    for cluster in clusters:
        if len(cluster) < min_cluster_size:
            continue

        # Sort cluster by importance desc - primary is first
        cluster.sort(key=lambda m: m.importance if m.importance is not None else 0.5, reverse=True)
        primary = cluster[0]
        to_merge = cluster[1:]

        merged_content = await _merge_memory_cluster_llm(
            client, model_name, cluster, character_name
        )

        if merged_content and merged_content != primary.content:
            primary.content = merged_content
            primary.last_accessed_at = datetime.utcnow()
            primary.importance = min(1.0, primary.importance + 0.1)  # slight boost
            merged_count += 1

        # Delete merged memories
        for mem in to_merge:
            await db.delete(mem)
            deleted_count += 1

    if merged_count > 0 or deleted_count > 0:
        await db.commit()

    return merged_count, deleted_count


async def consolidate_memories_job(
    db: AsyncSession,
    client: httpx.AsyncClient,
    model_name: str,
    chat_id: int = 0,
) -> dict:
    """Main consolidation job - processes all characters (optionally of one chat).

    Legacy P3 job: memory clustering + merge only. Sprint 12 replaces the
    trigger (score-based) and extends the set via ``consolidate_chat_adaptive``;
    ``chat_id`` filters to a single chat (0 = all chats, legacy behavior).
    """
    if not settings.consolidation_enabled:
        return {"status": "disabled", "chars_processed": 0, "merged": 0, "deleted": 0}

    # Get characters with memories (optionally restricted to a chat)
    stmt = (
        select(models.Character.id, models.Character.name, models.Character.chat_id)
        .join(models.Memory, models.Memory.character_id == models.Character.id)
        .distinct()
    )
    if chat_id:
        stmt = stmt.where(models.Character.chat_id == chat_id)
    result = await db.execute(stmt)
    characters = result.all()

    total_merged = 0
    total_deleted = 0
    chars_processed = 0

    for char_id, char_name, char_chat_id in characters:
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

    logger.info(
        "[Consolidation] Complete: chars=%d merged=%d deleted=%d",
        chars_processed,
        total_merged,
        total_deleted,
    )

    return {
        "status": "completed",
        "chars_processed": chars_processed,
        "merged": total_merged,
        "deleted": total_deleted,
    }

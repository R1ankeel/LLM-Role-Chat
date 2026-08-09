"""Память: джобы — обработчики, enqueue-функции, регистрация (Sprint 6C).

Sprint 6C (§4.5 decomposition.md): перенос из ``memory_service.py`` джобовой
части (``process_post_round``, обработчики task_queue, embedding-джобы).
Направление: memory/ → crud, embedding_service, task_queue (без обратных
импортов).
"""

import asyncio
from datetime import datetime

import httpx
import structlog
from sqlalchemy import select

from .. import embedding_service
from .. import models
from .. import task_queue
from ..config import settings
from ..database import AsyncSessionLocal
from .adaptive import _parse_payload_dt, consolidate_chat_adaptive
from .consolidation import consolidate_memories_job
from .extraction import _extract_and_save_memories
from .summaries import _maybe_update_summaries

logger = structlog.get_logger(__name__)


async def _process_post_round_job(payload: dict) -> dict:
    """Job handler for post-round memory processing."""
    # Recreate httpx client from settings
    client = httpx.AsyncClient(
        base_url=settings.ollama_base_url,
        timeout=settings.ollama_timeout,
    )
    try:
        await _extract_and_save_memories(
            client,
            payload["chat_id"],
            payload["round_snapshots"],
            payload["character_snapshots"],
            payload["model_name"],
        )
        await _maybe_update_summaries(
            client,
            payload["chat_id"],
            payload["character_snapshots"],
            payload["model_name"],
        )
        return {"status": "completed", "chat_id": payload["chat_id"]}
    finally:
        await client.aclose()


async def process_post_round(
    client: httpx.AsyncClient,
    chat_id: int,
    round_snapshots: list[dict],
    character_snapshots: list[dict],
    model_name: str,
) -> None:
    """Enqueue memory processing job instead of fire-and-forget."""
    if not settings.task_queue_enabled:
        # Fallback: direct execution (for testing/simple deployments)
        try:
            await _extract_and_save_memories(
                client,
                chat_id,
                round_snapshots,
                character_snapshots,
                model_name,
            )
            await _maybe_update_summaries(
                client,
                chat_id,
                character_snapshots,
                model_name,
            )
        except Exception:
            logger.exception("post_round_failed", chat_id=chat_id)
        return

    def _serialize_datetime(obj):
        """JSON serializer for datetime objects."""
        if isinstance(obj, datetime):
            return obj.isoformat()
        raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")

    payload = {
        "chat_id": chat_id,
        "round_snapshots": round_snapshots,
        "character_snapshots": character_snapshots,
        "model_name": model_name,
    }

    job = await task_queue.memory_job_queue.enqueue(
        job_type="post_round",
        chat_id=chat_id,
        payload=payload,
    )

    # Fire-and-forget the actual processing
    # run_job will dispatch to _process_post_round_job via _dispatch_job based on job_type
    asyncio.create_task(
        task_queue.memory_job_queue.run_job(job)
    )


async def _process_consolidation_job(payload: dict) -> dict:
    """Job handler for consolidation - compatible with task queue.

    Sprint 12: payload with ``level`` (soft/hard/critical) runs the full
    adaptive set for a single chat; legacy payload (no ``level``) keeps the
    old all-chats memory clustering behaviour.
    """
    client = httpx.AsyncClient(
        base_url=settings.ollama_base_url,
        timeout=settings.ollama_timeout,
    )
    try:
        async with AsyncSessionLocal() as db:
            model_name = payload.get("model_name", settings.default_model)
            chat_id = int(payload.get("chat_id") or 0)
            level = payload.get("level")
            if level:
                return await consolidate_chat_adaptive(
                    db,
                    client,
                    chat_id=chat_id,
                    model_name=model_name,
                    level=level,
                    since_soft=_parse_payload_dt(payload.get("since_soft")),
                    since_hard=_parse_payload_dt(payload.get("since_hard")),
                )
            return await consolidate_memories_job(
                db, client, model_name, chat_id=chat_id
            )
    finally:
        await client.aclose()


async def enqueue_consolidation_job(
    chat_id: int = 0,
    model_name: str | None = None,
    level: str | None = None,
    since_soft: datetime | None = None,
    since_hard: datetime | None = None,
) -> models.MemoryJob:
    """Enqueue a consolidation job.

    ``level`` = soft/hard/critical (Sprint 12). ``since_soft``/``since_hard``
    pin the consolidation window (the pre-trigger baselines) so the job
    refreshes summaries from the dialogue that actually triggered it.
    """
    payload: dict = {
        "chat_id": chat_id,
        "model_name": model_name or settings.default_model,
    }
    if level:
        payload["level"] = level
    if since_soft is not None:
        payload["since_soft"] = since_soft.isoformat()
    if since_hard is not None:
        payload["since_hard"] = since_hard.isoformat()
    return await task_queue.memory_job_queue.enqueue(
        job_type="consolidation",
        chat_id=chat_id,
        payload=payload,
    )


# ============================================================
# Embedding Generation (P3)
# ============================================================

async def _process_embed_memory_job(payload: dict) -> dict:
    """Job handler for embedding a single memory."""
    memory_id = payload["memory_id"]
    content = payload["content"]
    
    if not settings.embedding_enabled:
        return {"status": "disabled", "memory_id": memory_id}
    
    emb_service = embedding_service.get_embedding_service()
    try:
        embedding = await emb_service.embed_single(content)
        if embedding:
            async with AsyncSessionLocal() as db:
                db_memory = await db.get(models.Memory, memory_id)
                if db_memory:
                    db_memory.embedding = emb_service.pack_embedding(embedding)
                    await db.commit()
                    logger.debug("[Embedding] Generated for memory %d", memory_id)
                    return {"status": "completed", "memory_id": memory_id}
        return {"status": "failed", "memory_id": memory_id, "reason": "no_embedding"}
    except Exception as exc:
        logger.exception("[Embedding] Failed for memory %d: %s", memory_id, exc)
        return {"status": "failed", "memory_id": memory_id, "reason": str(exc)}


async def _process_backfill_embeddings_job(payload: dict) -> dict:
    """Job handler for backfilling embeddings for existing memories."""
    if not settings.embedding_enabled:
        return {"status": "disabled"}
    
    chat_id = payload.get("chat_id", 0)
    batch_size = payload.get("batch_size", 100)
    limit = payload.get("limit", 0)  # 0 = no limit
    
    emb_service = embedding_service.get_embedding_service()
    processed = 0
    failed = 0
    
    try:
        async with AsyncSessionLocal() as db:
            stmt = select(models.Memory).where(models.Memory.embedding.is_(None))
            if chat_id:
                stmt = stmt.where(models.Memory.chat_id == chat_id)
            stmt = stmt.order_by(models.Memory.created_at.desc())
            if limit:
                stmt = stmt.limit(limit)
            
            result = await db.execute(stmt)
            memories = list(result.scalars().all())
            
            logger.info("[Backfill] Found %d memories without embeddings", len(memories))
            
            for i in range(0, len(memories), batch_size):
                batch = memories[i : i + batch_size]
                contents = [m.content for m in batch]
                embeddings = await emb_service.embed_batch(contents)
                
                for mem, emb in zip(batch, embeddings):
                    if emb:
                        mem.embedding = emb_service.pack_embedding(emb)
                        processed += 1
                    else:
                        failed += 1
                
                await db.commit()
                logger.debug("[Backfill] Processed batch %d-%d", i, min(i + batch_size, len(memories)))
        
        logger.info("[Backfill] Complete: processed=%d failed=%d", processed, failed)
        return {"status": "completed", "processed": processed, "failed": failed}
    except Exception as exc:
        logger.exception("[Backfill] Failed: %s", exc)
        return {"status": "failed", "reason": str(exc)}


async def enqueue_embed_memory_job(memory_id: int, content: str) -> models.MemoryJob:
    """Enqueue an embedding generation job for a memory."""
    payload = {"memory_id": memory_id, "content": content}
    return await task_queue.memory_job_queue.enqueue(
        job_type="embed_memory",
        chat_id=0,
        payload=payload,
    )


async def enqueue_backfill_embeddings_job(
    chat_id: int = 0, batch_size: int = 100, limit: int = 0
) -> models.MemoryJob:
    """Enqueue a backfill job for memories missing embeddings."""
    payload = {"chat_id": chat_id, "batch_size": batch_size, "limit": limit}
    return await task_queue.memory_job_queue.enqueue(
        job_type="backfill_embeddings",
        chat_id=chat_id,
        payload=payload,
    )


# ---------------------------------------------------------------------------
# Handler-registry (Sprint 1, §7.1 decomposition.md)
# ---------------------------------------------------------------------------
# Обработчики регистрируются в task_queue при импорте: диспетчер джобов не
# знает о memory_service напрямую (цикл ``task_queue ↔ memory_service`` разорван).
task_queue.register_handler("post_round", _process_post_round_job)
task_queue.register_handler("consolidation", _process_consolidation_job)
task_queue.register_handler("embed_memory", _process_embed_memory_job)
task_queue.register_handler("backfill_embeddings", _process_backfill_embeddings_job)

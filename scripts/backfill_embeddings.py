#!/usr/bin/env python3
"""Backfill embeddings for existing memories.

Usage:
    python scripts/backfill_embeddings.py [--chat-id CHAT_ID] [--batch-size 100] [--limit 0]
"""

import argparse
import asyncio
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app import embedding_service, models, task_queue
from app.config import settings
from app.database import AsyncSessionLocal, init_db
from sqlalchemy import select


async def main():
    parser = argparse.ArgumentParser(description="Backfill embeddings for existing memories")
    parser.add_argument("--chat-id", type=int, default=0, help="Chat ID to limit backfill (0 = all chats)")
    parser.add_argument("--batch-size", type=int, default=100, help="Batch size for embedding generation")
    parser.add_argument("--limit", type=int, default=0, help="Max memories to process (0 = no limit)")
    parser.add_argument("--enqueue-only", action="store_true", help="Only enqueue job, don't process")
    args = parser.parse_args()

    # Initialize DB
    await init_db()

    if args.enqueue_only:
        # Just enqueue the backfill job
        job = await task_queue.memory_job_queue.enqueue(
            job_type="backfill_embeddings",
            chat_id=args.chat_id,
            payload={
                "chat_id": args.chat_id,
                "batch_size": args.batch_size,
                "limit": args.limit,
            },
        )
        print(f"Enqueued backfill job: {job.id}")
        return

    # Direct processing
    print(f"Starting backfill: chat_id={args.chat_id or 'all'}, batch_size={args.batch_size}, limit={args.limit or 'unlimited'}")

    emb_svc = embedding_service.get_embedding_service()

    try:
        async with AsyncSessionLocal() as db:
            stmt = select(models.Memory).where(models.Memory.embedding.is_(None))
            if args.chat_id:
                stmt = stmt.where(models.Memory.chat_id == args.chat_id)
            stmt = stmt.order_by(models.Memory.created_at.desc())
            if args.limit:
                stmt = stmt.limit(args.limit)

            result = await db.execute(stmt)
            memories = list(result.scalars().all())

            print(f"Found {len(memories)} memories without embeddings")

            if not memories:
                print("Nothing to do.")
                return

            processed = 0
            failed = 0

            for i in range(0, len(memories), args.batch_size):
                batch = memories[i : i + args.batch_size]
                contents = [m.content for m in batch]

                print(f"Processing batch {i // args.batch_size + 1}/{(len(memories) + args.batch_size - 1) // args.batch_size} ({len(batch)} memories)...")

                embeddings = await emb_svc.embed_batch(contents)

                for mem, emb in zip(batch, embeddings):
                    if emb:
                        mem.embedding = emb_svc.pack_embedding(emb)
                        processed += 1
                    else:
                        failed += 1

                await db.commit()

                # Small delay to avoid overwhelming Ollama
                await asyncio.sleep(0.1)

            print(f"\nBackfill complete: processed={processed}, failed={failed}")

    finally:
        await embedding_service.close_embedding_service()


if __name__ == "__main__":
    asyncio.run(main())
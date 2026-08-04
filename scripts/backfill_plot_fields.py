#!/usr/bin/env python3
"""Backfill story fields of chats (Plans/update20.md §16.1, Sprint 0).

Заполняет ``chats.original_plot`` / ``chats.story_prompt`` из ``general_prompt``
(copy, не move). ``story_enabled`` остаётся False — динамический сюжет включается
только в Sprint 8. Идемпотентно: заполняются только пустые поля.

Usage:
    python scripts/backfill_plot_fields.py [--chat-id CHAT_ID]
"""

import argparse
import asyncio
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app import crud
from app.database import AsyncSessionLocal, init_db


async def main():
    parser = argparse.ArgumentParser(
        description="Backfill chats.original_plot/story_prompt from general_prompt (Sprint 0)"
    )
    parser.add_argument(
        "--chat-id",
        type=int,
        default=0,
        help="Chat ID to limit backfill (0 = all chats)",
    )
    args = parser.parse_args()

    await init_db()

    async with AsyncSessionLocal() as db:
        report = await crud.backfill_plot_fields(db, chat_id=args.chat_id or None)

    print(f"Backfill complete ({args.chat_id or 'all chats'}):")
    for line in report.lines():
        print(line)

    if report.story_enabled:
        print("\nВНИМАНИЕ: обнаружены чаты с включённым story_enabled — флаг сброшен в false.")
        raise SystemExit(1)
    print("\nOK: story fields backfilled; story_enabled=false (сюжет выключен до Sprint 8).")


if __name__ == "__main__":
    asyncio.run(main())

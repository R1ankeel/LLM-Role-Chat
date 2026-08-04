#!/usr/bin/env python3
"""Backfill `world_events.location_id` from string locations (Plans/update20.md, Sprint 0).

Заполняет каноническую ``location_id`` для каждого world-event из строковой
``world_events.location`` (аналог ``scripts/backfill_location_ids.py`` для
персонажей). Нерезолвленные случаи (имя локации отсутствует в таблице
``locations`` чата и не является общей сценой) НЕ проставляются — они выводятся
в отчёт на ручной разбор.

Usage:
    python scripts/backfill_event_location_ids.py [--chat-id CHAT_ID]
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
        description="Backfill world_events.location_id from string locations (Sprint 0)"
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
        report = await crud.backfill_event_location_ids(
            db, chat_id=args.chat_id or None
        )

    print(f"Backfill complete ({args.chat_id or 'all chats'}):")
    for line in report.lines():
        print(line)

    if report.unresolved:
        print(
            "\nВНИМАНИЕ: неоднозначные случаи требуют ручного разбора — "
            "location_id для них не проставлен."
        )
        raise SystemExit(1)
    print("\nOK: неоднозначных случаев нет.")


if __name__ == "__main__":
    asyncio.run(main())

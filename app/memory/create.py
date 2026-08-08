"""Публичный интерфейс создания памяти (Sprint 1, §7.1 decomposition.md).

Направление: сервис → ``memory/`` → ``crud``. Сервисы (relationship_service)
создают память через этот интерфейс, а не импортируют ``crud`` внутри функций.
Полная резка ``memory/*`` — спринт 6C.
"""

from __future__ import annotations

from typing import Any

from .. import crud
from ..schemas import MemoryCreate

__all__ = ["create_memory"]


async def create_memory(
    db: Any,
    memory: MemoryCreate,
    *,
    source_message_ids: list[int] | None = None,
) -> Any:
    """Создать память персонажа (обёртка над ``crud.create_memory``)."""
    return await crud.create_memory(db, memory, source_message_ids=source_message_ids)

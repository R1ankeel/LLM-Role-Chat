"""WPE 3.0 Phase 7 — Event Bus / Interrupts (Plans/WPE.md §7, Ул.5, И17).

Цикл раунда физически вынесен сюда из ``app/chat_engine.py`` (правило §9):
``run_round`` — единственная оркестрирующая функция, владеет очередью
приоритетов (``EventBus``) и делегирует генерацию каждого NPC переданному
шагу. Логически шина живёт на уровне ``chat_engine`` (Ул.5): он владеет
состоянием раунда и вызывает ``run_round``.

Правила буждения (§7):
- приоритет: разбуженные NPC идут впереди плановых; внутри приоритета —
  FIFO пробуждения (риски §12, защита от зацикливания/инверсии);
- плановый порядок — исходный ``order_index`` (детерминизм);
- один NPC генерирует максимум один ответ за раунд; повторные ``wake``
  игнорируются (уже ответил / уже разбужен / не NPC).

Откат: ``run_round_fixed`` — исходный фиксированный порядок без изменения
поведения (флаг ``WORLD_ENGINE_EVENT_BUS_ENABLED`` off).
"""

from __future__ import annotations

import logging
from collections import deque
from collections.abc import AsyncIterator, Iterable
from typing import Any, Callable

logger = logging.getLogger(__name__)


class EventBus:
    """Очередь генерации раунда с приоритетом разбуженных NPC (Ул.5, И17)."""

    def __init__(self, characters: Iterable[Any]):
        self._planned_ids = [
            c.id for c in sorted(characters, key=lambda c: (c.order_index, c.id))
        ]
        self._pending = list(self._planned_ids)
        self._woken: deque[int] = deque()
        self._woken_set: set[int] = set()
        self._generated: set[int] = set()

    def seed(self, target_ids: Iterable[int]) -> None:
        """Буждение игроком (адресация из user-сообщения) — первый ход раунда."""
        for target_id in target_ids or ():
            self.wake(target_id)

    def wake(self, character_id: int) -> None:
        """Пометить NPC разбуженным для внеочередной генерации.

        Повторные буждения игнорируются: NPC, уже ответивший или уже
        разбуженный в этом раунде, не попадает в очередь повторно.
        """
        if character_id in self._generated:
            return
        if character_id in self._woken_set:
            return
        self._woken_set.add(character_id)
        self._woken.append(character_id)

    def pop_next(self) -> int | None:
        """Следующий NPC: сначала разбуженные (FIFO), затем плановый порядок."""
        if self._woken:
            cid = self._woken.popleft()
            self._woken_set.discard(cid)
            self._generated.add(cid)
            self._discard_pending(cid)
            return cid
        while self._pending:
            cid = self._pending.pop(0)
            if cid in self._generated:
                continue
            self._generated.add(cid)
            return cid
        return None

    def _discard_pending(self, character_id: int) -> None:
        try:
            self._pending.remove(character_id)
        except ValueError:
            pass

    def generated(self) -> set[int]:
        """Множество NPC, уже сгенерировавших ответ в этом раунде."""
        return set(self._generated)


async def run_round(
    characters: list[Any],
    step: Callable[[Any, EventBus | None], AsyncIterator[dict]],
    *,
    seed_target_ids: list[int] | None = None,
) -> AsyncIterator[dict]:
    """Оркестрация раунда: очередь приоритетов + буждение при addressed=true.

    ``step(character, bus)`` — асинхронный генератор, генерирующий одного NPC
    и отдающий события протокола (``{"type": "message"|"token"|...}``). Шаг сам
    вызывает ``bus.wake`` для NPC-адресатов своей реплики (NPC↔NPC, И17) —
    до завершения шага очередь уже пополнена, поэтому разбуженный NPC
    генерирует следующим (даже если позже по ``order_index``).
    """
    npc_ids = {c.id for c in characters}
    bus = EventBus(characters)
    if seed_target_ids:
        bus.seed(tid for tid in seed_target_ids if tid in npc_ids)
    while True:
        cid = bus.pop_next()
        if cid is None:
            return
        character = next(c for c in characters if c.id == cid)
        async for event in step(character, bus):
            yield event


async def run_round_fixed(
    characters: list[Any],
    step: Callable[[Any, EventBus | None], AsyncIterator[dict]],
) -> AsyncIterator[dict]:
    """Откат (флаг off): исходный фиксированный порядок без изменения поведения."""
    for character in characters:
        async for event in step(character, None):
            yield event

"""Global LLM request serialization (Sprint 5A, §4.3 decomposition.md).

Ollama (один сервер, одна GPU) обрабатывает запросы по одному; параллельные
вызовы одной модели заставляют её держать несколько KV-окон (срывая reuse из
context_budget_manager) или ротировать модели в VRAM. Поэтому каждый запрос к
Ollama берёт общий lock и держит его весь обмен (включая полный стрим и ретраи):
следующий вызов отправляется только когда предыдущий полностью завершён.

Lock хранится по одному на event loop: приложение живёт в одном loop, а
pytest-asyncio создаёт свой loop на каждый тест — модульный asyncio.Lock падал
бы с "bound to a different event loop" (Python 3.10+).
"""

from weakref import WeakKeyDictionary
from typing import Any

import asyncio

_llm_locks: "WeakKeyDictionary[Any, asyncio.Lock]" = WeakKeyDictionary()


def _llm_lock_for() -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    lock = _llm_locks.get(loop)
    if lock is None:
        lock = asyncio.Lock()
        _llm_locks[loop] = lock
    return lock

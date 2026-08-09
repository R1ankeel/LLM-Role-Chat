"""Пакет памяти (Milestone 6C, decomposition.md §4.5).

``__init__`` намеренно пуст (без реэкспорта): по правилу Sprint 1
``app.crud`` ↔ ``app.memory.retrieval`` — взаимная верхнеуровневая
зависимость (``crud/__init__.py:13`` импортирует ``memory.retrieval``,
``memory/retrieval.py`` — ``from .. import crud`` с отложенным доступом к
атрибутам). Реэкспорт через ``__init__`` тянет ``jobs.py``, который на
верхнем уровне регистрирует handler'ы в ``task_queue``, и ломает этот цикл
(partial-init ``task_queue``). Поэтому публичный API ``memory_service``
зафиксирован фасадом ``app/memory_service.py`` (явный список реэкспорта) и
прямыми импортами из подмодулей (``memory.retrieval``/``memory.create`` —
Sprint 1, потребители ``crud``, роутеры). Снятие фасада и фиксация API в
``__init__`` — спринт 10 (decomposition.md §9 этап 21).
"""

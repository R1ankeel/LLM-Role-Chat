"""Тонкий реэкспорт-фасад БД (Sprint 3, decomposition-sprints.md §4).

Реальная реализация вынесена в пакет ``app/db/``:

- ``db/schema.py`` — вся DDL из ``ensure_schema`` (без изменений SQL);
- ``db/engine.py`` — engine, pragma, ``init_db``, фабрики сессий.

Этот модуль сохранён, чтобы прежние импорты ``from app.database import ...``
(``Base``, ``engine``, ``SessionLocal``, ``init_db``, ``get_async_db`` и т.д.)
продолжали работать. Фасад удаляется на этапе 19 (§11 decomposition.md).
"""

from .db.engine import (
    ASYNC_SQLALCHEMY_DATABASE_URL,
    SQLALCHEMY_DATABASE_URL,
    AsyncSessionLocal,
    Base,
    SessionLocal,
    async_engine,
    engine,
    get_async_db,
    get_async_session_factory,
    get_db,
    get_session_factory,
    init_db,
)
from .db.schema import INDEXES, ensure_schema, memory_content_hash

__all__ = [
    "ASYNC_SQLALCHEMY_DATABASE_URL",
    "SQLALCHEMY_DATABASE_URL",
    "AsyncSessionLocal",
    "Base",
    "INDEXES",
    "SessionLocal",
    "async_engine",
    "engine",
    "ensure_schema",
    "get_async_db",
    "get_async_session_factory",
    "get_db",
    "get_session_factory",
    "init_db",
    "memory_content_hash",
]

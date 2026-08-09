"""Engine, pragma, сессии и ``init_db`` (Sprint 3, decomposition-sprints.md §4).

Вынесено из ``app/database.py``: движки SQLite (sync + async), PRAGMA-листенеры,
фабрики сессий и ``init_db``. DDL-миграция ``ensure_schema`` живёт в
``db/schema.py``.
"""

import logging
from typing import AsyncGenerator

from sqlalchemy import create_engine, event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .schema import ensure_schema

logger = logging.getLogger(__name__)

SQLALCHEMY_DATABASE_URL = "sqlite:///./ai_chat.db"
ASYNC_SQLALCHEMY_DATABASE_URL = "sqlite+aiosqlite:///./ai_chat.db"

# Sync engine for migrations/background tasks
engine = create_engine(
    SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
)

# Async engine for request handlers
async_engine = create_async_engine(
    ASYNC_SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
)


# Включаем поддержку внешних ключей в SQLite (нужно для ON DELETE CASCADE)
@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()


@event.listens_for(async_engine.sync_engine, "connect")
def _set_sqlite_pragma_async(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

AsyncSessionLocal = async_sessionmaker(
    async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


class Base(DeclarativeBase):
    """Базовый класс для всех ORM-моделей."""

    pass


async def init_db() -> None:
    """Initialize database: create tables and run migrations."""
    # Run sync migrations first (uses sync engine directly)
    ensure_schema(engine)
    # Then create tables via async engine
    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_async_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency: yields async DB session and closes after request."""
    async with AsyncSessionLocal() as session:
        yield session


def get_db():
    """Sync DB dependency (for background tasks if needed)."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_session_factory():
    """Return the sync session factory for testing patching."""
    return SessionLocal


def get_async_session_factory():
    """Return the async session factory for testing patching."""
    return AsyncSessionLocal

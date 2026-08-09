"""Shared pytest fixtures (async)."""

import sys
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy import event

# Force UTF-8 encoding for Windows (reconfigure in place, don't replace the
# stream object: pytest's capture keeps a reference to the original sys.stdout).
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError, io.UnsupportedOperation):
        pass
    try:
        sys.stderr.reconfigure(encoding="utf-8")
    except (AttributeError, OSError, io.UnsupportedOperation):
        pass

from app import models  # noqa: F401
from app.database import Base
from app import crud
from app import schemas


@pytest_asyncio.fixture
async def db_engine():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:?cache=shared",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine) -> AsyncSession:
    async_session = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        yield session


@pytest_asyncio.fixture
async def chat(db_session):
    return await crud.create_chat(
        db_session,
        schemas.ChatCreate(name="Test Chat", general_prompt="A test scene"),
    )


async def create_characters(
    db_session, chat_id: int, count: int, start_index: int = 1
) -> list:
    """Create `count` characters named Character A, Character B, ...
    starting at ``order_index=start_index`` (default 1)."""
    characters = []
    for index in range(count):
        label = chr(ord("A") + index)
        characters.append(
            await crud.create_character(
                db_session,
                chat_id,
                schemas.CharacterCreate(
                    name=f"Character {label}",
                    personality=f"Personality of {label}",
                    traits=f"Traits of {label}",
                    order_index=start_index + index,
                ),
            )
        )
    return characters


@pytest_asyncio.fixture
async def three_characters(db_session, chat):
    return await create_characters(db_session, chat.id, 3)
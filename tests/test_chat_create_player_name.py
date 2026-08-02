"""API tests for POST /api/chats + player_name (named player character).

Verifies that a chat created with ``player_name`` gets a player character with
that name and ``is_player=true``, and that the default is still «Игрок».
"""

from __future__ import annotations

from httpx import ASGITransport, AsyncClient
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database import get_async_db
from app.routers.chats import router as chats_router


async def _make_client(session_factory) -> AsyncClient:
    app = FastAPI()
    app.include_router(chats_router)

    async def override_db():
        async with session_factory() as db:
            yield db

    app.dependency_overrides[get_async_db] = override_db
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


async def _fetch_characters(db, chat_id: int) -> list:
    from app import crud

    return await crud.get_characters_by_chat(db, chat_id, include_player=True)


class TestChatCreatePlayerName:
    async def test_default_player_name(self, db_engine):
        session_factory = async_sessionmaker(
            db_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
        )
        async with await _make_client(session_factory) as client:
            resp = await client.post("/chats", json={"name": "Test", "general_prompt": "scene"})
            assert resp.status_code == 201
            chat_id = resp.json()["id"]

        async with session_factory() as db:
            characters = await _fetch_characters(db, chat_id)
            players = [c for c in characters if c.is_player]
            assert len(players) == 1
            assert players[0].name == "Игрок"

    async def test_custom_player_name(self, db_engine):
        session_factory = async_sessionmaker(
            db_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
        )
        async with await _make_client(session_factory) as client:
            resp = await client.post(
                "/chats", json={"name": "Test", "general_prompt": "scene", "player_name": "Алиса"}
            )
            assert resp.status_code == 201
            chat_id = resp.json()["id"]
            # player_name не протекает в ответ ChatRead
            assert "player_name" not in resp.json()

        async with session_factory() as db:
            characters = await _fetch_characters(db, chat_id)
            players = [c for c in characters if c.is_player]
            assert len(players) == 1
            assert players[0].name == "Алиса"
            assert players[0].is_player is True

    async def test_only_one_player_character(self, db_engine):
        session_factory = async_sessionmaker(
            db_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
        )
        async with await _make_client(session_factory) as client:
            resp = await client.post(
                "/chats", json={"name": "Test", "player_name": "Первый"}
            )
            assert resp.status_code == 201
            chat_id = resp.json()["id"]

        async with session_factory() as db:
            characters = await _fetch_characters(db, chat_id)
            players = [c for c in characters if c.is_player]
            assert len(players) == 1
            assert players[0].name == "Первый"

"""API tests for character profile fields (Этап A, docs/Profile.docx).

Covers the model/schema/migration stage: `appearance` and `avatar_url` fields,
`temperature` range validation (0–2), and that `avatar_url` cannot be set at
creation (files are loaded only through the upload endpoint — Этап B).
"""

from __future__ import annotations

from httpx import ASGITransport, AsyncClient
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import crud, schemas
from app.database import get_async_db
from app.routers.characters import router as characters_router


async def _make_client(session_factory) -> AsyncClient:
    app = FastAPI()
    app.include_router(characters_router)

    async def override_db():
        async with session_factory() as db:
            yield db

    app.dependency_overrides[get_async_db] = override_db
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


async def _chat_and_character(db_engine) -> tuple[int, int]:
    session_factory = async_sessionmaker(
        db_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
    )
    async with session_factory() as db:
        chat = await crud.create_chat(
            db, schemas.ChatCreate(name="Test", general_prompt="scene")
        )
        char = await crud.create_character(
            db, chat.id, schemas.CharacterCreate(name="Alice", order_index=1)
        )
        return chat.id, char.id


class TestCharacterProfileFields:
    async def test_create_character_has_appearance_and_empty_avatar(self, db_engine):
        session_factory = async_sessionmaker(
            db_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
        )
        async with session_factory() as db:
            chat = await crud.create_chat(
                db, schemas.ChatCreate(name="Test", general_prompt="scene")
            )

        async with await _make_client(session_factory) as client:
            resp = await client.post(
                f"/chats/{chat.id}/characters",
                json={
                    "name": "Alice",
                    "appearance": "Tall, red hair",
                    "avatar_url": "/static/avatars/evil.png",
                },
            )
            assert resp.status_code == 201
            data = resp.json()
            assert data["appearance"] == "Tall, red hair"
            # avatar_url при создании не задаётся (только через upload endpoint)
            assert data["avatar_url"] == ""

    async def test_update_appearance_persists(self, db_engine):
        session_factory = async_sessionmaker(
            db_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
        )
        chat_id, char_id = await _chat_and_character(db_engine)

        async with await _make_client(session_factory) as client:
            resp = await client.put(
                f"/characters/{char_id}",
                json={"appearance": "Silver hair, green eyes"},
            )
            assert resp.status_code == 200
            assert resp.json()["appearance"] == "Silver hair, green eyes"

        async with session_factory() as db:
            fresh = await crud.get_character(db, char_id)
            assert fresh.appearance == "Silver hair, green eyes"

    async def test_update_avatar_url_persists(self, db_engine):
        session_factory = async_sessionmaker(
            db_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
        )
        chat_id, char_id = await _chat_and_character(db_engine)

        async with await _make_client(session_factory) as client:
            resp = await client.put(
                f"/characters/{char_id}",
                json={"avatar_url": "/static/avatars/1-123.png"},
            )
            assert resp.status_code == 200
            assert resp.json()["avatar_url"] == "/static/avatars/1-123.png"

        async with session_factory() as db:
            fresh = await crud.get_character(db, char_id)
            assert fresh.avatar_url == "/static/avatars/1-123.png"

    async def test_temperature_out_of_range_rejected(self, db_engine):
        session_factory = async_sessionmaker(
            db_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
        )
        chat_id, char_id = await _chat_and_character(db_engine)

        async with await _make_client(session_factory) as client:
            for bad_temp in (2.5, -0.1):
                resp = await client.put(
                    f"/characters/{char_id}", json={"temperature": bad_temp}
                )
                assert resp.status_code == 422, f"temperature={bad_temp}"

    async def test_temperature_within_range_accepted(self, db_engine):
        session_factory = async_sessionmaker(
            db_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
        )
        chat_id, char_id = await _chat_and_character(db_engine)

        async with await _make_client(session_factory) as client:
            resp = await client.put(
                f"/characters/{char_id}", json={"temperature": 1.35}
            )
            assert resp.status_code == 200
            assert resp.json()["temperature"] == 1.35

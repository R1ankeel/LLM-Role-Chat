"""API tests for Locations CRUD (Локации 2.0, Спринт 2, §22 п.10).

Covers: create, list, duplicate name → 409 (case-insensitive), rename with
string-reference sync (characters/messages/scene_states/chats.locations),
delete with referencing characters → 409, delete unreferenced → 204.
"""

import json

from httpx import ASGITransport, AsyncClient
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import crud, models, schemas
from app.database import get_async_db
from app.routers.locations import router as locations_router


async def _make_client(session_factory) -> AsyncClient:
    app = FastAPI()
    app.include_router(locations_router)

    async def override_db():
        async with session_factory() as db:
            yield db

    app.dependency_overrides[get_async_db] = override_db
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _seed(db_engine) -> tuple[int, int]:
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


class TestLocationsCRUD:
    async def test_create_and_list_locations(self, db_engine):
        chat_id, _ = await _seed(db_engine)
        session_factory = async_sessionmaker(
            db_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
        )
        async with await _make_client(session_factory) as client:
            resp = await client.post(
                f"/chats/{chat_id}/locations",
                json={"name": "Гостиная", "description": "Большая светлая комната"},
            )
            assert resp.status_code == 201
            body = resp.json()
            assert body["name"] == "Гостиная"
            assert body["description"] == "Большая светлая комната"
            assert body["chat_id"] == chat_id
            assert body["id"] > 0

            lst = await client.get(f"/chats/{chat_id}/locations")
            assert lst.status_code == 200
            names = [l["name"] for l in lst.json()]
            assert "Гостиная" in names

        # `chats.locations` cache synced
        async with session_factory() as db:
            chat = await crud.get_chat(db, chat_id)
            assert json.loads(chat.locations) == ["Гостиная"]

    async def test_create_duplicate_name_409(self, db_engine):
        chat_id, _ = await _seed(db_engine)
        session_factory = async_sessionmaker(
            db_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
        )
        async with await _make_client(session_factory) as client:
            r1 = await client.post(
                f"/chats/{chat_id}/locations", json={"name": "Кухня"}
            )
            assert r1.status_code == 201
            # exact duplicate
            r2 = await client.post(
                f"/chats/{chat_id}/locations", json={"name": "Кухня"}
            )
            assert r2.status_code == 409
            # case-insensitive duplicate (normalize_locations casefolds)
            r3 = await client.post(
                f"/chats/{chat_id}/locations", json={"name": "кухня"}
            )
            assert r3.status_code == 409

    async def test_create_empty_name_422(self, db_engine):
        chat_id, _ = await _seed(db_engine)
        session_factory = async_sessionmaker(
            db_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
        )
        async with await _make_client(session_factory) as client:
            resp = await client.post(
                f"/chats/{chat_id}/locations", json={"name": "   "}
            )
            assert resp.status_code == 422

    async def test_rename_syncs_references(self, db_engine):
        chat_id, char_id = await _seed(db_engine)
        session_factory = async_sessionmaker(
            db_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
        )
        async with session_factory() as db:
            loc = await crud.create_location(
                db, chat_id, schemas.LocationCreate(name="Гостиная")
            )
            # references: character, message, scene state
            await crud.update_character(
                db, char_id, schemas.CharacterUpdate(location="Гостиная")
            )
            await crud.create_message(
                db,
                schemas.MessageCreate(
                    chat_id=chat_id, role="user", content="привет", location="Гостиная"
                ),
            )
            await crud.upsert_scene_state(
                db, chat_id, schemas.SceneStateUpdate(
                    character_locations={str(char_id): "Гостиная"}
                )
            )
            loc_id = loc.id

        async with await _make_client(session_factory) as client:
            resp = await client.put(
                f"/chats/{chat_id}/locations/{loc_id}",
                json={"name": "Гостиная комната", "description": "Уютная"},
            )
            assert resp.status_code == 200
            assert resp.json()["name"] == "Гостиная комната"

        async with session_factory() as db:
            chat = await crud.get_chat(db, chat_id)
            assert json.loads(chat.locations) == ["Гостиная комната"]
            char = await crud.get_character(db, char_id)
            assert char.location == "Гостиная комната"
            msgs = await crud.get_messages_by_chat(db, chat_id)
            assert all(m.location == "Гостиная комната" for m in msgs)
            scene = await crud.get_scene_state(db, chat_id)
            assert json.loads(scene.character_locations) == {
                str(char_id): "Гостиная комната"
            }

    async def test_delete_referenced_409_with_characters(self, db_engine):
        chat_id, char_id = await _seed(db_engine)
        session_factory = async_sessionmaker(
            db_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
        )
        async with session_factory() as db:
            loc = await crud.create_location(
                db, chat_id, schemas.LocationCreate(name="Гостиная")
            )
            await crud.update_character(
                db, char_id, schemas.CharacterUpdate(location="Гостиная")
            )
            loc_id = loc.id

        async with await _make_client(session_factory) as client:
            resp = await client.delete(f"/chats/{chat_id}/locations/{loc_id}")
            assert resp.status_code == 409
            detail = resp.json()["detail"]
            assert detail["message"]
            assert "Alice" in detail["characters"]

        # location still present
        async with session_factory() as db:
            assert await crud.get_location(db, loc_id) is not None

    async def test_delete_unreferenced_204(self, db_engine):
        chat_id, _ = await _seed(db_engine)
        session_factory = async_sessionmaker(
            db_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
        )
        async with session_factory() as db:
            loc = await crud.create_location(
                db, chat_id, schemas.LocationCreate(name="Сад")
            )
            loc_id = loc.id

        async with await _make_client(session_factory) as client:
            resp = await client.delete(f"/chats/{chat_id}/locations/{loc_id}")
            assert resp.status_code == 204
            lst = await client.get(f"/chats/{chat_id}/locations")
            assert lst.json() == []

        async with session_factory() as db:
            chat = await crud.get_chat(db, chat_id)
            assert json.loads(chat.locations) == []

    async def test_not_found(self, db_engine):
        session_factory = async_sessionmaker(
            db_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
        )
        async with await _make_client(session_factory) as client:
            assert (await client.get("/chats/999/locations")).status_code == 404
            resp = await client.put("/chats/999/locations/1", json={"name": "X"})
            assert resp.status_code == 404
            assert (await client.delete("/chats/999/locations/1")).status_code == 404

    async def test_wrong_chat_mismatch_404(self, db_engine):
        chat_id, _ = await _seed(db_engine)
        session_factory = async_sessionmaker(
            db_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
        )
        async with session_factory() as db:
            loc = await crud.create_location(
                db, chat_id, schemas.LocationCreate(name="Гостиная")
            )
            other = await crud.create_chat(db, schemas.ChatCreate(name="Other"))
            loc_id = loc.id
            other_id = other.id

        async with await _make_client(session_factory) as client:
            resp = await client.put(
                f"/chats/{other_id}/locations/{loc_id}", json={"name": "X"}
            )
            assert resp.status_code == 404
            assert (await client.delete(f"/chats/{other_id}/locations/{loc_id}")).status_code == 404

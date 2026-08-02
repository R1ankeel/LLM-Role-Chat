"""Tests for the relationship graph endpoint (Sprint 4 п.24).

Validates that the full chat graph is returned: character nodes (NPCs +
player), all directed edges, and the per-edge count of open issues.
"""

from httpx import ASGITransport, AsyncClient
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import crud
from app import schemas
from app.database import get_async_db
from app.relationship_service import create_issue, get_or_create_relationship
from app.routers.relationships import router as relationships_router

GRAPH = "/chats/{chat_id}/relationships/graph"


async def _make_client(session_factory) -> AsyncClient:
    app = FastAPI()
    app.include_router(relationships_router)

    async def override_db():
        async with session_factory() as db:
            yield db

    app.dependency_overrides[get_async_db] = override_db
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


class TestRelationshipGraph:
    async def test_returns_all_nodes_and_edges(self, db_engine, chat, three_characters):
        a, b, c = three_characters
        session_factory = async_sessionmaker(
            db_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
        )
        async with session_factory() as db:
            await get_or_create_relationship(db, chat.id, a.id, b.id)
            await get_or_create_relationship(db, chat.id, a.id, c.id)
            await get_or_create_relationship(db, chat.id, b.id, c.id)

        async with await _make_client(session_factory) as client:
            resp = await client.get(GRAPH.format(chat_id=chat.id))
            assert resp.status_code == 200
            data = resp.json()
            assert {ch["id"] for ch in data["characters"]} == {a.id, b.id, c.id}
            assert len(data["edges"]) == 3
            edge_keys = {
                (e["source_character_id"], e["target_character_id"])
                for e in data["edges"]
            }
            assert edge_keys == {(a.id, b.id), (a.id, c.id), (b.id, c.id)}
            edge = next(e for e in data["edges"] if e["source_character_id"] == a.id and e["target_character_id"] == b.id)
            assert edge["relationship_type"] == "нейтральное"
            assert edge["open_issue_count"] == 0
            assert "affection" in edge and "trust" in edge

    async def test_open_issue_count_per_edge(self, db_engine, chat, three_characters):
        a, b, c = three_characters
        session_factory = async_sessionmaker(
            db_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
        )
        async with session_factory() as db:
            rel_ab = await get_or_create_relationship(db, chat.id, a.id, b.id)
            await get_or_create_relationship(db, chat.id, a.id, c.id)
            await create_issue(
                db, rel_ab,
                issue_type="broken_promise",
                text="Борис не выполнил обещание",
                importance=7,
                round_id="r1-m1",
            )
            await db.commit()

        async with await _make_client(session_factory) as client:
            resp = await client.get(GRAPH.format(chat_id=chat.id))
            assert resp.status_code == 200
            data = resp.json()
            counts = {
                (e["source_character_id"], e["target_character_id"]): e["open_issue_count"]
                for e in data["edges"]
            }
            assert counts[(a.id, b.id)] == 1
            assert counts[(a.id, c.id)] == 0

    async def test_player_node_included(self, db_engine, chat):
        session_factory = async_sessionmaker(
            db_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
        )
        async with session_factory() as db:
            await crud.create_player_character(db, chat.id)
        async with await _make_client(session_factory) as client:
            resp = await client.get(GRAPH.format(chat_id=chat.id))
            assert resp.status_code == 200
            data = resp.json()
            players = [ch for ch in data["characters"] if ch["is_player"]]
            assert len(players) == 1

    async def test_404_for_unknown_chat(self, db_engine, chat):
        session_factory = async_sessionmaker(
            db_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
        )
        async with await _make_client(session_factory) as client:
            assert (await client.get(GRAPH.format(chat_id=99999))).status_code == 404

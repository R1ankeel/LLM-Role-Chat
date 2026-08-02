"""Tests for the relationship timeline endpoint (Sprint 4 item 4.3).

Validates pagination (limit/offset), Query clamping (422 out of range), and the
join of source messages referenced by events.
"""

from httpx import ASGITransport, AsyncClient
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import crud
from app import schemas
from app.database import get_async_db
from app.models import RelationshipEvent
from app.relationship_service import get_or_create_relationship
from app.routers.relationships import router as relationships_router

TIMELINE = "/chats/{chat_id}/relationships/{source_id}/{target_id}/timeline"


async def _make_client(session_factory) -> AsyncClient:
    app = FastAPI()
    app.include_router(relationships_router)

    async def override_db():
        async with session_factory() as db:
            yield db

    app.dependency_overrides[get_async_db] = override_db
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


async def _seed_events(db, relationship_id: int, count: int) -> None:
    for i in range(count):
        db.add(
            RelationshipEvent(
                relationship_id=relationship_id,
                kind="llm" if i % 3 else "decay",
                description=f"event {i}",
                reason="",
                delta_affection=1,
                delta_trust=0,
                delta_attraction=0,
                delta_resentment=0,
                delta_jealousy=0,
                affection_after=51,
                trust_after=50,
                attraction_after=0,
                resentment_after=0,
                jealousy_after=0,
                importance=5,
                source_message_ids="[]",
                round_id=None,
            )
        )
    await db.commit()


class TestTimelinePagination:
    async def test_paginated_events(self, db_engine, chat, three_characters):
        a, b, _ = three_characters
        session_factory = async_sessionmaker(
            db_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
        )
        async with session_factory() as db:
            rel = await get_or_create_relationship(db, chat.id, a.id, b.id)
            await _seed_events(db, rel.id, 150)

        async with await _make_client(session_factory) as client:
            url = TIMELINE.format(chat_id=chat.id, source_id=a.id, target_id=b.id)

            resp = await client.get(url, params={"limit": 50, "offset": 0})
            assert resp.status_code == 200
            data = resp.json()
            assert len(data["events"]) == 50
            assert data["pagination"]["total_events"] == 150
            assert data["pagination"]["total"] == 150
            assert "kind" in data["events"][0]
            assert data["events"][0]["delta_affection"] == 1

            resp = await client.get(url, params={"limit": 50, "offset": 100})
            data = resp.json()
            assert len(data["events"]) == 50

            resp = await client.get(url, params={"limit": 50, "offset": 130})
            data = resp.json()
            assert len(data["events"]) == 20

    async def test_query_clamps_out_of_range(self, db_engine, chat, three_characters):
        a, b, _ = three_characters
        session_factory = async_sessionmaker(
            db_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
        )
        async with session_factory() as db:
            rel = await get_or_create_relationship(db, chat.id, a.id, b.id)
            await _seed_events(db, rel.id, 3)

        async with await _make_client(session_factory) as client:
            url = TIMELINE.format(chat_id=chat.id, source_id=a.id, target_id=b.id)
            assert (await client.get(url, params={"limit": 501})).status_code == 422
            assert (await client.get(url, params={"limit": 0})).status_code == 422
            assert (await client.get(url, params={"offset": -1})).status_code == 422
            assert (await client.get(url, params={"limit": 500})).status_code == 200

    async def test_404_for_unknown_pair(self, db_engine, chat, three_characters):
        a, b, _ = three_characters
        session_factory = async_sessionmaker(
            db_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
        )
        async with await _make_client(session_factory) as client:
            url = TIMELINE.format(chat_id=chat.id, source_id=a.id, target_id=b.id)
            assert (await client.get(url)).status_code == 404

    async def test_source_messages_joined(self, db_engine, chat, three_characters):
        a, b, _ = three_characters
        session_factory = async_sessionmaker(
            db_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
        )
        async with session_factory() as db:
            rel = await get_or_create_relationship(db, chat.id, a.id, b.id)
            user_msg = await crud.create_message(
                db,
                schemas.MessageCreate(chat_id=chat.id, role="user", content="Сцена"),
            )
            db.add(
                RelationshipEvent(
                    relationship_id=rel.id,
                    kind="llm",
                    description="значимое событие",
                    reason="",
                    delta_affection=5,
                    delta_trust=0,
                    delta_attraction=0,
                    delta_resentment=0,
                    delta_jealousy=0,
                    affection_after=55,
                    trust_after=50,
                    attraction_after=0,
                    resentment_after=0,
                    jealousy_after=0,
                    importance=6,
                    source_message_ids=f"[{user_msg.id}]",
                    round_id=f"r{chat.id}-m{user_msg.id}",
                )
            )
            await db.commit()

        async with await _make_client(session_factory) as client:
            url = TIMELINE.format(chat_id=chat.id, source_id=a.id, target_id=b.id)
            resp = await client.get(url)
            assert resp.status_code == 200
            data = resp.json()
            assert len(data["events"]) == 1
            assert data["events"][0]["source_messages"][0]["content"] == "Сцена"
            assert any(m["id"] == user_msg.id for m in data["messages"])

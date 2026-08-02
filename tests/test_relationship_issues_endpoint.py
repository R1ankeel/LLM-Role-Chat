"""Tests for the chat-wide open issues endpoint (Sprint 4 п.26).

Validates the ``state`` filter and that each issue carries source/target names
so the UI can group them by pair.
"""

from httpx import ASGITransport, AsyncClient
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database import get_async_db
from app.relationship_service import (
    create_issue,
    get_or_create_relationship,
    resolve_issue,
)
from app.routers.relationships import router as relationships_router

ISSUES = "/chats/{chat_id}/relationships/issues"


async def _make_client(session_factory) -> AsyncClient:
    app = FastAPI()
    app.include_router(relationships_router)

    async def override_db():
        async with session_factory() as db:
            yield db

    app.dependency_overrides[get_async_db] = override_db
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


async def _seed(db, chat_id, three_characters):
    a, b, c = three_characters
    rel_ab = await get_or_create_relationship(db, chat_id, a.id, b.id)
    rel_ac = await get_or_create_relationship(db, chat_id, a.id, c.id)
    issue1 = await create_issue(
        db, rel_ab,
        issue_type="broken_promise",
        text="Борис не выполнил обещание",
        importance=8,
        round_id="r1-m1",
    )
    issue2 = await create_issue(
        db, rel_ac,
        issue_type="suspicion",
        text="Аня подозревает Катю",
        importance=6,
        round_id="r1-m1",
    )
    await db.commit()
    return a, b, c, rel_ab, rel_ac, issue1, issue2


class TestChatRelationshipIssues:
    async def test_open_issues_with_names(self, db_engine, chat, three_characters):
        session_factory = async_sessionmaker(
            db_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
        )
        async with session_factory() as db:
            a, b, c, *_ = await _seed(db, chat.id, three_characters)

        async with await _make_client(session_factory) as client:
            resp = await client.get(ISSUES.format(chat_id=chat.id), params={"state": "open"})
            assert resp.status_code == 200
            data = resp.json()
            assert len(data) == 2
            issue = next(i for i in data if i["target_name"] == "Character B")
            assert issue["source_name"] == "Character A"
            assert issue["issue_type"] == "broken_promise"
            assert issue["importance"] == 8
            assert issue["state"] == "open"

    async def test_state_filter(self, db_engine, chat, three_characters):
        session_factory = async_sessionmaker(
            db_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
        )
        async with session_factory() as db:
            a, b, c, rel_ab, rel_ac, issue1, issue2 = await _seed(db, chat.id, three_characters)
            await resolve_issue(db, rel_ab, issue1.id, reason="извинился")
            await db.commit()

        async with await _make_client(session_factory) as client:
            url = ISSUES.format(chat_id=chat.id)
            resolved = (await client.get(url, params={"state": "resolved"})).json()
            assert len(resolved) == 1
            assert resolved[0]["state"] == "resolved"

            all_issues = (await client.get(url, params={"state": "all"})).json()
            assert len(all_issues) == 2
            assert {i["state"] for i in all_issues} == {"open", "resolved"}

    async def test_404_for_unknown_chat(self, db_engine, chat):
        session_factory = async_sessionmaker(
            db_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
        )
        async with await _make_client(session_factory) as client:
            assert (await client.get(ISSUES.format(chat_id=99999))).status_code == 404

"""Tests for the read-only debug observability contour (§29.1, Sprint 13).

Validates: 404 when ``debug_enabled`` is off, state summary composition,
per-endpoint gating, and the in-memory pipeline report store.
"""

import pytest
from httpx import ASGITransport, AsyncClient
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import settings
from app.database import get_async_db
from app.routers.debug import remember_pipeline_report, router as debug_router


async def _make_client(session_factory, debug_enabled: bool = True) -> AsyncClient:
    app = FastAPI()
    app.include_router(debug_router)

    async def override_db():
        async with session_factory() as db:
            yield db

    app.dependency_overrides[get_async_db] = override_db
    old = settings.debug_enabled
    settings.debug_enabled = debug_enabled
    transport = ASGITransport(app=app)
    client = AsyncClient(transport=transport, base_url="http://test")

    def _restore():
        settings.debug_enabled = old

    client._debug_restore = _restore
    return client


class TestDebugStateEndpoint:
    async def test_off_flag_returns_404(self, db_engine, chat):
        session_factory = async_sessionmaker(
            db_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
        )
        client = await _make_client(session_factory, debug_enabled=False)
        try:
            resp = await client.get(f"/chats/{chat.id}/debug/state")
            assert resp.status_code == 404
        finally:
            client._debug_restore()
            await client.aclose()

    async def test_state_summary_shape(self, db_engine, chat):
        session_factory = async_sessionmaker(
            db_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
        )
        client = await _make_client(session_factory, debug_enabled=True)
        try:
            resp = await client.get(f"/chats/{chat.id}/debug/state")
            assert resp.status_code == 200
            data = resp.json()
            assert data["chat_id"] == chat.id
            for key in (
                "story_state",
                "character_states",
                "beliefs",
                "intents",
                "active_story_threads",
            ):
                assert key in data
            assert data["character_states"] == []
            assert data["beliefs"] == []
            assert data["intents"] == []
            assert data["active_story_threads"] == []
        finally:
            client._debug_restore()
            await client.aclose()

    async def test_missing_chat_returns_404(self, db_engine):
        session_factory = async_sessionmaker(
            db_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
        )
        client = await _make_client(session_factory, debug_enabled=True)
        try:
            resp = await client.get("/chats/999999/debug/state")
            assert resp.status_code == 404
        finally:
            client._debug_restore()
            await client.aclose()


class TestDebugViews:
    async def test_views_respond(self, db_engine, chat):
        session_factory = async_sessionmaker(
            db_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
        )
        client = await _make_client(session_factory, debug_enabled=True)
        try:
            for view in ("beliefs", "threads", "events", "anchors", "pipeline"):
                resp = await client.get(f"/chats/{chat.id}/debug/{view}")
                assert resp.status_code == 200, view
                assert "chat_id" in resp.json()
        finally:
            client._debug_restore()
            await client.aclose()


class TestDebugPipelineStore:
    async def test_remember_and_read(self, db_engine, chat):
        session_factory = async_sessionmaker(
            db_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
        )
        client = await _make_client(session_factory, debug_enabled=True)
        try:
            resp = await client.get(f"/chats/{chat.id}/debug/pipeline")
            assert resp.json()["last_report"] is None

            remember_pipeline_report(
                chat.id, {"presence": {"ok": True}, "memory": {"ok": True}}
            )
            resp = await client.get(f"/chats/{chat.id}/debug/pipeline")
            report = resp.json()["last_report"]
            assert report["presence"] == {"ok": True}
            assert report["memory"] == {"ok": True}
        finally:
            client._debug_restore()
            await client.aclose()

"""Tests for the one-time user intervention ("Вмешательство").

Covers the DB-backed store, the HTTP endpoints, the prompt block builder,
and the chat-engine lifecycle (consumed after a successful round, preserved
on failures, applied-but-not-consumed on regeneration, never persisted to
message history). Recipient filtering is covered in test_intervention_recipients.py.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import chat_engine
from app import crud
from app import pending_intervention
from app import prompt_builder
from app import schemas
from app.database import get_async_db
from app.routers.chat_engine import router as chat_router
from tests.conftest import create_characters


@pytest_asyncio.fixture(autouse=True)
async def _clean_interventions(db_session):
    await pending_intervention.clear_all(db_session)
    yield
    await pending_intervention.clear_all(db_session)


async def _run_in_current_thread(func, /, *args, **kwargs):
    return func(*args, **kwargs)


# ---------------------------------------------------------------------------
# DB-backed store
# ---------------------------------------------------------------------------


class TestPendingInterventionStore:
    async def test_set_get_remove_consume(self, db_session, chat):
        entry = await pending_intervention.set_intervention(db_session, chat.id, "Смените тему")
        assert entry.id is not None
        assert await pending_intervention.get_chat_wide_intervention(db_session, chat.id) == entry

        assert await pending_intervention.remove_chat_wide_intervention(db_session, chat.id) is True
        assert await pending_intervention.get_chat_wide_intervention(db_session, chat.id) is None
        assert await pending_intervention.remove_chat_wide_intervention(db_session, chat.id) is False

        entry = await pending_intervention.set_intervention(db_session, chat.id, "Опиши закат")
        assert await pending_intervention.consume_intervention(db_session, entry.id) is True
        assert await pending_intervention.get_chat_wide_intervention(db_session, chat.id) is None

    async def test_set_replaces_existing(self, db_session, chat):
        await pending_intervention.set_intervention(db_session, chat.id, "Первая")
        await pending_intervention.set_intervention(db_session, chat.id, "Вторая")
        current = await pending_intervention.get_chat_wide_intervention(db_session, chat.id)
        assert current is not None
        assert current.instruction == "Вторая"

    async def test_consume_guarded_by_identity(self, db_session, chat):
        first = await pending_intervention.set_intervention(db_session, chat.id, "Первая")
        second = await pending_intervention.set_intervention(db_session, chat.id, "Вторая")

        # Replacing deletes the old row, so the old id can no longer be consumed.
        assert await pending_intervention.consume_intervention(db_session, first.id) is False
        assert (await pending_intervention.get_chat_wide_intervention(db_session, chat.id)) == second

        assert await pending_intervention.consume_intervention(db_session, second.id) is True
        assert await pending_intervention.get_chat_wide_intervention(db_session, chat.id) is None

    async def test_list_interventions(self, db_session, chat):
        (character,) = await create_characters(db_session, chat.id, 1)
        first = await pending_intervention.set_intervention(db_session, chat.id, "Первая")
        second = await pending_intervention.set_intervention(
            db_session, chat.id, "Вторая", character_id=character.id
        )
        listed = await pending_intervention.list_interventions(db_session, chat.id)
        assert {inv.id for inv in listed} == {first.id, second.id}


# ---------------------------------------------------------------------------
# HTTP endpoints
# ---------------------------------------------------------------------------


async def _make_client(session_factory) -> AsyncClient:
    app = FastAPI()
    app.include_router(chat_router)

    async def override_db():
        async with session_factory() as db:
            yield db

    app.dependency_overrides[get_async_db] = override_db
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


class TestInterventionEndpoints:
    async def test_put_get_delete(self, db_engine, chat):
        session_factory = async_sessionmaker(
            db_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
        )
        async with await _make_client(session_factory) as client:
            resp = await client.put(
                f"/chats/{chat.id}/intervention", json={"instruction": "Смените тему"}
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["chat_id"] == chat.id
            assert data["character_id"] is None
            assert data["instruction"] == "Смените тему"
            assert data["recipient_character_ids"] == []

            resp = await client.get(f"/chats/{chat.id}/intervention")
            assert resp.status_code == 200
            assert resp.json()["instruction"] == "Смените тему"

            resp = await client.put(
                f"/chats/{chat.id}/intervention", json={"instruction": "Новая тема"}
            )
            assert resp.status_code == 200
            assert resp.json()["instruction"] == "Новая тема"

            resp = await client.delete(f"/chats/{chat.id}/intervention")
            assert resp.status_code == 204

            resp = await client.get(f"/chats/{chat.id}/intervention")
            assert resp.status_code == 200
            assert resp.json() is None

    async def test_put_empty_or_whitespace_rejected(self, db_engine, chat):
        session_factory = async_sessionmaker(
            db_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
        )
        async with await _make_client(session_factory) as client:
            resp = await client.put(
                f"/chats/{chat.id}/intervention", json={"instruction": ""}
            )
            assert resp.status_code == 422

            resp = await client.put(
                f"/chats/{chat.id}/intervention", json={"instruction": "   "}
            )
            assert resp.status_code == 422

            resp = await client.put(
                f"/chats/{chat.id}/intervention", json={"instruction": "  тест  "}
            )
            assert resp.status_code == 200
            assert resp.json()["instruction"] == "тест"

    async def test_missing_chat_404(self, db_engine):
        session_factory = async_sessionmaker(
            db_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
        )
        async with await _make_client(session_factory) as client:
            assert (
                await client.put(
                    "/chats/999/intervention", json={"instruction": "текст"}
                )
            ).status_code == 404
            assert (
                await client.get("/chats/999/intervention")
            ).status_code == 404
            assert (
                await client.delete("/chats/999/intervention")
            ).status_code == 404


# ---------------------------------------------------------------------------
# Prompt block builder
# ---------------------------------------------------------------------------


class TestBuildInterventionBlock:
    def test_builds_block(self):
        block = prompt_builder.build_intervention_block("Смените тему")
        assert "<intervention>" in block
        assert "</intervention>" in block
        assert "Смените тему" in block

    def test_empty_text_yields_empty_block(self):
        assert prompt_builder.build_intervention_block("") == ""
        assert prompt_builder.build_intervention_block("   ") == ""


# ---------------------------------------------------------------------------
# Chat engine lifecycle
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_client():
    import httpx

    return httpx.AsyncClient(base_url="http://test")


class TestInterventionLifecycle:
    @pytest.mark.asyncio
    async def test_directive_passed_and_consumed_after_success(
        self, db_session, chat, mock_client
    ):
        (character,) = await create_characters(db_session, chat.id, 1)
        await pending_intervention.set_intervention(
            db_session, chat.id, "Пусть герои уйдут на кухню",
            recipient_ids=[character.id],
        )
        call_log: list[dict] = []

        async def fake_generate(**kwargs):
            call_log.append({"directive": kwargs.get("directive")})
            yield {
                "type": "response",
                "text": "Character A moves to the kitchen and speaks at length.",
            }

        with patch(
            "app.chat_engine.ollama_client.generate", side_effect=fake_generate
        ), patch("app.chat_engine.asyncio.create_task"), patch(
            "app.chat_engine.asyncio.to_thread", side_effect=_run_in_current_thread
        ):
            async for _ in chat_engine.process_user_message_streaming(
                mock_client, db_session, chat.id, "Go"
            ):
                pass

        assert call_log and all(
            entry["directive"] == "Пусть герои уйдут на кухню" for entry in call_log
        )
        assert await pending_intervention.get_chat_wide_intervention(db_session, chat.id) is None

    @pytest.mark.asyncio
    async def test_directive_persisted_as_discreet_memory_after_success(
        self, db_session, chat, mock_client
    ):
        (character,) = await create_characters(db_session, chat.id, 1)
        await pending_intervention.set_intervention(
            db_session, chat.id, "Пусть герои уйдут на кухню",
            recipient_ids=[character.id],
        )

        async def fake_generate(**kwargs):
            yield {
                "type": "response",
                "text": "Character A moves to the kitchen and speaks at length.",
            }

        with patch(
            "app.chat_engine.ollama_client.generate", side_effect=fake_generate
        ), patch("app.chat_engine.asyncio.create_task"), patch(
            "app.chat_engine.asyncio.to_thread", side_effect=_run_in_current_thread
        ):
            async for _ in chat_engine.process_user_message_streaming(
                mock_client, db_session, chat.id, "Go"
            ):
                pass

        memories = await crud.get_memories_by_character(db_session, character.id)
        facts = [m.content for m in memories]
        assert "Игрок попросил: Пусть герои уйдут на кухню" in facts
        assert not any("вмешательство" in (f or "").lower() for f in facts)
        assert await pending_intervention.get_chat_wide_intervention(db_session, chat.id) is None

    @pytest.mark.asyncio
    async def test_directive_not_persisted_when_round_fails(
        self, db_session, chat, mock_client
    ):
        (character,) = await create_characters(db_session, chat.id, 1)
        await pending_intervention.set_intervention(
            db_session, chat.id, "Смените тему", recipient_ids=[character.id],
        )

        async def fake_generate(**kwargs):
            raise RuntimeError("ollama down")
            yield  # pragma: no cover

        with patch(
            "app.chat_engine.ollama_client.generate", side_effect=fake_generate
        ), patch("app.chat_engine.asyncio.create_task"), patch(
            "app.chat_engine.asyncio.to_thread", side_effect=_run_in_current_thread
        ):
            async for _ in chat_engine.process_user_message_streaming(
                mock_client, db_session, chat.id, "Go"
            ):
                pass

        memories = await crud.get_memories_by_character(db_session, character.id)
        assert not any("Игрок попросил" in (m.content or "") for m in memories)
        assert await pending_intervention.get_chat_wide_intervention(db_session, chat.id) is not None

    @pytest.mark.asyncio
    async def test_directive_not_leaked_to_history(self, db_session, chat, mock_client):
        (character,) = await create_characters(db_session, chat.id, 1)
        await pending_intervention.set_intervention(
            db_session, chat.id, "СЕКРЕТ_ВМЕШАТЕЛЬСТВА_123",
            recipient_ids=[character.id],
        )

        async def fake_generate(**kwargs):
            yield {
                "type": "response",
                "text": "Character A answers the player.",
            }

        with patch(
            "app.chat_engine.ollama_client.generate", side_effect=fake_generate
        ), patch("app.chat_engine.asyncio.create_task"), patch(
            "app.chat_engine.asyncio.to_thread", side_effect=_run_in_current_thread
        ):
            async for _ in chat_engine.process_user_message_streaming(
                mock_client, db_session, chat.id, "Hello"
            ):
                pass

        saved = await crud.get_messages_by_chat(db_session, chat.id)
        assert not any("СЕКРЕТ_ВМЕШАТЕЛЬСТВА_123" in (m.content or "") for m in saved)

    @pytest.mark.asyncio
    async def test_directive_preserved_on_generation_runtime_error(
        self, db_session, chat, mock_client
    ):
        (character,) = await create_characters(db_session, chat.id, 1)
        await pending_intervention.set_intervention(
            db_session, chat.id, "Смените тему", recipient_ids=[character.id],
        )

        async def fake_generate(**kwargs):
            raise RuntimeError("ollama down")
            yield  # pragma: no cover

        with patch(
            "app.chat_engine.ollama_client.generate", side_effect=fake_generate
        ), patch("app.chat_engine.asyncio.create_task"), patch(
            "app.chat_engine.asyncio.to_thread", side_effect=_run_in_current_thread
        ):
            async for _ in chat_engine.process_user_message_streaming(
                mock_client, db_session, chat.id, "Go"
            ):
                pass

        # A per-character fallback means the round failed: keep the directive.
        assert await pending_intervention.get_chat_wide_intervention(db_session, chat.id) is not None

    @pytest.mark.asyncio
    async def test_directive_preserved_when_round_aborts(
        self, db_session, chat, mock_client
    ):
        (character,) = await create_characters(db_session, chat.id, 1)
        await pending_intervention.set_intervention(
            db_session, chat.id, "Смените тему", recipient_ids=[character.id],
        )

        async def fake_generate(**kwargs):
            raise ValueError("network exploded")
            yield  # pragma: no cover

        with pytest.raises(ValueError), patch(
            "app.chat_engine.ollama_client.generate", side_effect=fake_generate
        ), patch("app.chat_engine.asyncio.create_task"), patch(
            "app.chat_engine.asyncio.to_thread", side_effect=_run_in_current_thread
        ):
            async for _ in chat_engine.process_user_message_streaming(
                mock_client, db_session, chat.id, "Go"
            ):
                pass

        assert await pending_intervention.get_chat_wide_intervention(db_session, chat.id) is not None

    @pytest.mark.asyncio
    async def test_regenerate_applies_but_does_not_consume(
        self, db_session, chat, mock_client
    ):
        character = (await create_characters(db_session, chat.id, 1))[0]
        await crud.create_message(
            db_session,
            schemas.MessageCreate(chat_id=chat.id, role="user", content="Hello"),
        )
        char_msg = await crud.create_message(
            db_session,
            schemas.MessageCreate(
                chat_id=chat.id,
                role="character",
                character_id=character.id,
                content="Old reply text.",
            ),
        )
        await pending_intervention.set_intervention(
            db_session, chat.id, "Опиши закат", recipient_ids=[character.id],
        )
        call_log: list[dict] = []

        async def fake_generate(**kwargs):
            call_log.append({"directive": kwargs.get("directive")})
            yield {
                "type": "response",
                "text": "A brand new reply that is long enough to pass validation.",
            }

        with patch(
            "app.chat_engine.ollama_client.generate", side_effect=fake_generate
        ), patch("app.chat_engine.asyncio.create_task"), patch(
            "app.chat_engine.asyncio.to_thread", side_effect=_run_in_current_thread
        ):
            async for _ in chat_engine.regenerate_message_streaming(
                mock_client, db_session, chat.id, char_msg.id
            ):
                pass

        assert call_log and call_log[0]["directive"] == "Опиши закат"
        assert await pending_intervention.get_chat_wide_intervention(db_session, chat.id) is not None

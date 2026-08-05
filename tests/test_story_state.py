"""Sprint 8 — Dynamic Story State (Plans/update20.md §16).

Покрывает:
- `story_events.write_story_events_from_round` — запись story_events из
  extraction world_events раунда (порог importance, идемпотентность, actor-
  имена в event-тексте);
- `story_state.update_story_state_from_round` — current_story (summary,
  активные story_threads, progress), дедупликация потоков, рост importance,
  привязка story_event→thread;
- `build_story_block` — рендер STORY блока (фаза + top-K потоков + прогресс);
- контекст: story_text в BuiltContext, self-build по флагам;
- сюжетный текст сцены: `story_prompt` при включённом story,
  `general_prompt` не меняется (критерий Sprint 8);
- изоляцию: story_enabled=false / chats.story_enabled=false → не пишет и
  не рендерит (canary);
- API story state GET/PATCH (original_plot — user-only).
"""

from __future__ import annotations

import json

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import crud
from app import models
from app import schemas
from app.config import settings
from app.context_builder import ContextBuilder
from app.plot import story_events as plot_events
from app.plot import story_state as plot_state
from app.chat_engine import _chat_plot_text
from app.routers.chats import router as chats_router
from app.database import get_async_db


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def enable_story(monkeypatch):
    """Master canary включён; перчатовый тумблер ставит каждый тест сам."""
    monkeypatch.setattr(settings, "story_enabled", True)


async def _enable_chat_story(db_session, chat) -> None:
    chat.story_enabled = True
    chat.story_prompt = chat.general_prompt or ""
    await db_session.commit()


async def _create_world_event(
    db_session, chat, character, *, round_id="r1", importance=6.0,
    action=None, event_type="event", target_ids=None,
):
    from app import models as m

    event = m.WorldEvent(
        chat_id=chat.id,
        character_id=character.id,
        event_type=event_type,
        location="таверна",
        round_id=round_id,
        target_character_ids=json.dumps(target_ids or [], ensure_ascii=False),
        action=json.dumps(
            action or {"actor": character.name, "action": "находит", "object": "письмо"},
            ensure_ascii=False,
        ),
        importance=importance,
        story_salience=0.7,
        emotional_salience=0.6,
    )
    db_session.add(event)
    await db_session.commit()
    return event


async def _make_client(session_factory) -> AsyncClient:
    app = FastAPI()
    app.include_router(chats_router)

    async def override_db():
        async with session_factory() as db:
            yield db

    app.dependency_overrides[get_async_db] = override_db
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


# ---------------------------------------------------------------------------
# story_events: запись из world_events раунда
# ---------------------------------------------------------------------------

class TestStoryEventsWrite:
    @pytest.mark.asyncio
    async def test_writes_events_above_importance_threshold(
        self, enable_story, db_session, chat, three_characters
    ):
        a, b, _ = three_characters
        await _enable_chat_story(db_session, chat)
        await _create_world_event(
            db_session, chat, a, importance=8.0,
            action={"actor": a.name, "action": "находит", "object": "письмо"},
        )
        await _create_world_event(
            db_session, chat, b, importance=5.0,
            action={"actor": b.name, "action": "уходит"},
        )
        # ниже порога STORY_EVENT_MIN_IMPORTANCE (4.0) — не пишется
        await _create_world_event(
            db_session, chat, b, importance=2.0,
            action={"actor": b.name, "action": "зевает"},
        )

        report = await plot_events.write_story_events_from_round(
            db_session, chat.id, "r1", {a.id: a.name, b.id: b.name}
        )

        assert report["ok"] is True
        assert report["written"] == 2
        events = await crud.get_story_events_for_chat(db_session, chat.id)
        assert len(events) == 2
        texts = {e.event for e in events}
        assert any("находит письмо" in t and a.name in t for t in texts)
        assert any("уходит" in t and b.name in t for t in texts)

    @pytest.mark.asyncio
    async def test_write_is_idempotent(
        self, enable_story, db_session, chat, three_characters
    ):
        a, _, _ = three_characters
        await _enable_chat_story(db_session, chat)
        await _create_world_event(db_session, chat, a, importance=7.0)

        first = await plot_events.write_story_events_from_round(
            db_session, chat.id, "r1", {a.id: a.name}
        )
        second = await plot_events.write_story_events_from_round(
            db_session, chat.id, "r1", {a.id: a.name}
        )

        assert first["written"] == 1
        assert second["written"] == 0
        events = await crud.get_story_events_for_chat(db_session, chat.id)
        assert len(events) == 1

    @pytest.mark.asyncio
    async def test_skip_when_story_disabled(
        self, db_session, chat, three_characters
    ):
        """story_enabled=false (canary) → ничего не пишется."""
        a, _, _ = three_characters
        await _create_world_event(db_session, chat, a, importance=7.0)
        report = await plot_events.write_story_events_from_round(
            db_session, chat.id, "r1", {a.id: a.name}
        )
        assert report["skipped"] == "flag off"
        assert await crud.count_story_events(db_session, chat.id) == 0


# ---------------------------------------------------------------------------
# story_state: current_story / threads / progress
# ---------------------------------------------------------------------------

class TestStoryStateUpdate:
    @pytest.mark.asyncio
    async def test_updates_state_with_summary_threads_progress(
        self, enable_story, db_session, chat, three_characters
    ):
        a, b, _ = three_characters
        await _enable_chat_story(db_session, chat)
        await _create_world_event(
            db_session, chat, a, importance=8.0,
            action={"actor": a.name, "action": "находит", "object": "письмо"},
        )
        await _create_world_event(
            db_session, chat, b, importance=5.0,
            action={"actor": b.name, "action": "уходит"},
        )
        await plot_events.write_story_events_from_round(
            db_session, chat.id, "r1", {a.id: a.name, b.id: b.name}
        )

        report = await plot_state.update_story_state_from_round(
            db_session, chat.id, "r1", characters=[a, b]
        )

        assert report["ok"] is True
        assert report["events"] == 2
        state = await crud.get_story_state(db_session, chat.id)
        current = json.loads(state.current_story)
        # summary содержит обе строки событий
        assert any("письмо" in s for s in current["summary"])
        assert any("уходит" in s for s in current["summary"])
        # thread создаётся только для события importance >= 6
        threads = await crud.get_active_story_threads(db_session, chat.id)
        assert len(threads) == 1
        assert "письмо" in threads[0].name
        assert current["active_threads"] == [threads[0].name]
        assert current["progress"]["story_events"] == 2
        assert current["progress"]["active_threads"] == 1
        assert current["progress"]["last_round"] == "r1"

    @pytest.mark.asyncio
    async def test_thread_dedupe_and_importance_growth(
        self, enable_story, db_session, chat, three_characters
    ):
        a, _, _ = three_characters
        await _enable_chat_story(db_session, chat)
        for imp in (6.0, 9.0):
            await _create_world_event(
                db_session, chat, a, round_id=f"r{int(imp)}",
                importance=imp,
                action={"actor": a.name, "action": "преследует", "object": "апостола"},
            )
            await plot_events.write_story_events_from_round(
                db_session, chat.id, f"r{int(imp)}", {a.id: a.name}
            )
            await plot_state.update_story_state_from_round(
                db_session, chat.id, f"r{int(imp)}", characters=[a]
            )

        threads = await crud.get_active_story_threads(db_session, chat.id)
        assert len(threads) == 1
        assert threads[0].importance == 9

    @pytest.mark.asyncio
    async def test_phase_preserved_by_engine(
        self, enable_story, db_session, chat, three_characters
    ):
        """story_phase движок не трогает (задаётся пользователем)."""
        a, _, _ = three_characters
        await _enable_chat_story(db_session, chat)
        await crud.get_or_create_story_state(db_session, chat.id)
        await crud.update_story_state(
            db_session, chat.id, story_phase="охота на апостолов"
        )
        await _create_world_event(db_session, chat, a, importance=7.0)
        await plot_events.write_story_events_from_round(
            db_session, chat.id, "r1", {a.id: a.name}
        )
        await plot_state.update_story_state_from_round(
            db_session, chat.id, "r1", characters=[a]
        )
        state = await crud.get_story_state(db_session, chat.id)
        assert state.story_phase == "охота на апостолов"

    @pytest.mark.asyncio
    async def test_skip_when_chat_story_disabled(
        self, enable_story, db_session, chat, three_characters
    ):
        """chats.story_enabled=false → стадия story — no-op."""
        a, _, _ = three_characters
        await _create_world_event(db_session, chat, a, importance=7.0)
        from app.post_round_pipeline import _stage_story

        report = await _stage_story(
            None, db_session, chat_id=chat.id, round_id="r1",
            character_names={a.id: a.name},
        )
        assert report["ok"] is True
        assert report["skipped"] == "chat story disabled"
        assert await crud.count_story_events(db_session, chat.id) == 0
        assert await crud.get_story_state(db_session, chat.id) is None


# ---------------------------------------------------------------------------
# STORY block (read-path)
# ---------------------------------------------------------------------------

class TestStoryBlock:
    @pytest.mark.asyncio
    async def test_build_story_block_renders_phase_threads(
        self, enable_story, db_session, chat, three_characters
    ):
        a, _, _ = three_characters
        await _enable_chat_story(db_session, chat)
        await _create_world_event(
            db_session, chat, a, importance=8.0,
            action={"actor": a.name, "action": "находит", "object": "письмо"},
        )
        await plot_events.write_story_events_from_round(
            db_session, chat.id, "r1", {a.id: a.name}
        )
        await plot_state.update_story_state_from_round(
            db_session, chat.id, "r1", characters=[a]
        )
        await crud.update_story_state(db_session, chat.id, story_phase="формирование группы")

        block = await plot_state.build_story_block(db_session, chat.id)
        assert "<story>" in block
        assert "Фаза: формирование группы" in block
        assert "письмо" in block

    @pytest.mark.asyncio
    async def test_empty_without_story_state(self, enable_story, db_session, chat):
        assert await plot_state.build_story_block(db_session, chat.id) == ""

    @pytest.mark.asyncio
    async def test_top_k_threads_cap(self, enable_story, monkeypatch, db_session, chat, three_characters):
        monkeypatch.setattr(settings, "story_threads_max", 2)
        a, _, _ = three_characters
        await _enable_chat_story(db_session, chat)
        await crud.get_or_create_story_state(db_session, chat.id)
        for i in range(4):
            thread = await crud.create_story_thread(
                db_session, chat_id=chat.id, name=f"линия {i}",
                actors=[a.name], importance=7 + i,
            )
        block = await plot_state.build_story_block(db_session, chat.id)
        # top-K=2 → только 2 линии, самые важные
        assert block.count("линия ") >= 2
        assert "линия 3" in block and "линия 2" in block
        assert "линия 0" not in block


# ---------------------------------------------------------------------------
# Контекст: story_text + сюжетный текст сцены (general_prompt не меняется)
# ---------------------------------------------------------------------------

class TestStoryInContext:
    @pytest.mark.asyncio
    async def test_story_text_in_built_context(
        self, enable_story, db_session, chat, three_characters
    ):
        a, _, _ = three_characters
        await _enable_chat_story(db_session, chat)
        await _create_world_event(
            db_session, chat, a, importance=8.0,
            action={"actor": a.name, "action": "находит", "object": "письмо"},
        )
        await plot_events.write_story_events_from_round(
            db_session, chat.id, "r1", {a.id: a.name}
        )
        await plot_state.update_story_state_from_round(
            db_session, chat.id, "r1", characters=[a]
        )
        block = await plot_state.build_story_block(db_session, chat.id)

        built = await ContextBuilder().build(
            db=db_session,
            chat_id=chat.id,
            character=a,
            user_message="тест",
            general_prompt=chat.general_prompt,
            messages_window=[],
            round_messages=[],
            character_names={a.id: a.name},
            character_locations={a.id: ""},
            story_block=block,
        )
        assert built.story_text == block
        assert "<story>" in built.story_text
        assert built.component_tokens.get("story", 0) > 0

    @pytest.mark.asyncio
    async def test_story_block_empty_when_disabled(
        self, db_session, chat, three_characters
    ):
        a, _, _ = three_characters
        built = await ContextBuilder().build(
            db=db_session,
            chat_id=chat.id,
            character=a,
            user_message="тест",
            general_prompt=chat.general_prompt,
            messages_window=[],
            round_messages=[],
            character_names={a.id: a.name},
            character_locations={a.id: ""},
        )
        assert built.story_text == ""

    @pytest.mark.asyncio
    async def test_plot_text_uses_story_prompt_when_enabled(
        self, enable_story, db_session, chat
    ):
        chat.story_enabled = True
        chat.story_prompt = "СТОРИ ПРОМПТ"
        chat.general_prompt = "СТАРЫЙ ПРОМПТ"
        await db_session.commit()

        assert _chat_plot_text(chat) == "СТОРИ ПРОМПТ"
        # критерий Sprint 8: исходное general_prompt не меняется
        assert chat.general_prompt == "СТАРЫЙ ПРОМПТ"

    @pytest.mark.asyncio
    async def test_plot_text_falls_back_to_general_prompt(
        self, db_session, chat
    ):
        chat.story_enabled = True
        chat.story_prompt = "СТОРИ"
        chat.general_prompt = "СТАРЫЙ"
        await db_session.commit()
        assert _chat_plot_text(chat) == "СТАРЫЙ"  # settings.story_enabled=false

    def test_plot_text_empty_story_prompt_falls_back(self):
        from types import SimpleNamespace

        chat = SimpleNamespace(
            story_enabled=True, story_prompt="   ", general_prompt="БАЗА"
        )
        old = settings.story_enabled
        settings.story_enabled = True
        try:
            assert _chat_plot_text(chat) == "БАЗА"
        finally:
            settings.story_enabled = old


# ---------------------------------------------------------------------------
# API story state GET/PATCH (original_plot — user-only)
# ---------------------------------------------------------------------------

class TestStoryApi:
    async def test_get_creates_state_and_returns_chat_flags(
        self, db_engine, chat
    ):
        session_factory = async_sessionmaker(
            db_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
        )
        async with session_factory() as db:
            chat_row = await crud.get_chat(db, chat.id)
            chat_row.original_plot = "ПЛОТ"
            await db.commit()

        async with await _make_client(session_factory) as client:
            resp = await client.get(f"/chats/{chat.id}/story")
            assert resp.status_code == 200
            data = resp.json()
            assert data["original_plot"] == "ПЛОТ"
            assert data["story_enabled"] is False
            assert data["active_threads"] == []

    async def test_patch_updates_plot_phase_and_enables_story(
        self, db_engine, chat
    ):
        session_factory = async_sessionmaker(
            db_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
        )
        async with await _make_client(session_factory) as client:
            resp = await client.patch(
                f"/chats/{chat.id}/story",
                json={
                    "story_enabled": True,
                    "original_plot": "НОВЫЙ ПЛОТ",
                    "story_phase": "охота на апостолов",
                },
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["story_enabled"] is True
            assert data["original_plot"] == "НОВЫЙ ПЛОТ"
            assert data["story_phase"] == "охота на апостолов"

        # original_plot записан и в чат, и в story_state; story_prompt посеян
        async with session_factory() as db:
            chat = await crud.get_chat(db, chat.id)
            assert chat.original_plot == "НОВЫЙ ПЛОТ"
            assert chat.story_enabled is True
            assert chat.story_prompt == chat.general_prompt

    async def test_patch_merges_current_story_partial(self, db_engine, chat):
        session_factory = async_sessionmaker(
            db_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
        )
        async with await _make_client(session_factory) as client:
            r1 = await client.patch(
                f"/chats/{chat.id}/story",
                json={"current_story": {"active_goal": "найти письмо"}},
            )
            assert r1.status_code == 200
            r2 = await client.patch(
                f"/chats/{chat.id}/story",
                json={"current_story": {"tension": 0.7}},
            )
            current = r2.json()["current_story"]
            assert current["active_goal"] == "найти письмо"
            assert current["tension"] == 0.7

    async def test_404_for_unknown_chat(self, db_engine, chat):
        session_factory = async_sessionmaker(
            db_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
        )
        async with await _make_client(session_factory) as client:
            assert (
                await client.get("/chats/999999/story")
            ).status_code == 404
            assert (
                await client.patch("/chats/999999/story", json={"story_phase": "x"})
            ).status_code == 404

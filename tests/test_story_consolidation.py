"""Sprint 9 — Story Consolidation (Plans/update20.md §17).

Покрывает:
- trigger (§17.1): интервал в раундах, критическое событие (раньше срока),
  интервал не достигнут → skip без LLM-вызова;
- вход → LLM → выход (§17.2): контракт, grounded new/updated/archived_threads,
  completed_goals уходят из active, progress сохраняется и клампится;
- защита (§17.3): original plot diff (фаза только из original_plot),
  hallucination guard (не grounded не применяется), confidence < порога,
  rollback при невалидном JSON / ошибке LLM (версия не растёт, состояние
  не меняется);
- canary: выключенный флаг / выключенный story → skip без LLM.
"""

from __future__ import annotations

import json

import pytest
import pytest_asyncio

from app import crud
from app import models as m
from app.config import settings
from app.plot import story_consolidation as sc
from app.plot import story_events as plot_events
from app.plot import story_state as plot_state


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def enable_story(monkeypatch):
    monkeypatch.setattr(settings, "story_enabled", True)


@pytest.fixture
def enable_consolidation(monkeypatch):
    monkeypatch.setattr(settings, "story_consolidation_enabled", True)
    monkeypatch.setattr(settings, "story_consolidation_interval_rounds", 15)
    monkeypatch.setattr(settings, "story_consolidation_min_confidence", 0.5)


@pytest.fixture
def enable_consolidation_now(monkeypatch):
    """Консолидация срабатывает с первого же раунда (interval=1)."""
    monkeypatch.setattr(settings, "story_consolidation_enabled", True)
    monkeypatch.setattr(settings, "story_consolidation_interval_rounds", 1)
    monkeypatch.setattr(settings, "story_consolidation_min_confidence", 0.5)


async def _enable_chat_story(db_session, chat) -> None:
    chat.story_enabled = True
    chat.story_prompt = chat.general_prompt or ""
    await db_session.commit()


async def _create_world_event(
    db_session, chat, character, *, round_id="r1", importance=6.0,
    action=None, event_type="event",
):
    event = m.WorldEvent(
        chat_id=chat.id,
        character_id=character.id,
        event_type=event_type,
        location="таверна",
        round_id=round_id,
        target_character_ids="[]",
        action=json.dumps(
            action
            or {"actor": character.name, "action": "находит", "object": "письмо"},
            ensure_ascii=False,
        ),
        importance=importance,
        story_salience=0.7,
        emotional_salience=0.6,
    )
    db_session.add(event)
    await db_session.commit()
    return event


async def _run_round(
    db_session, chat, character, *, round_id, importance=6.0, action=None
):
    """Полный write-path раунда (world_event → story_events → story_state)."""
    await _create_world_event(
        db_session, chat, character, round_id=round_id,
        importance=importance, action=action,
    )
    await plot_events.write_story_events_from_round(
        db_session, chat.id, round_id, {character.id: character.name}
    )
    await plot_state.update_story_state_from_round(
        db_session, chat.id, round_id, characters=[character]
    )


def _make_invoke(payload):
    """Тестовая инъекция LLM-вызова: messages → JSON-строка."""
    async def _invoke(messages):
        return json.dumps(payload, ensure_ascii=False)
    return _invoke


async def _state_dict(db_session, chat_id) -> dict:
    state = await crud.get_story_state(db_session, chat_id)
    return json.loads(state.current_story) if state else {}


# ---------------------------------------------------------------------------
# trigger / canary
# ---------------------------------------------------------------------------

class TestTrigger:
    @pytest.mark.asyncio
    async def test_disabled_flag_skips_without_invoke(
        self, enable_story, db_session, chat, three_characters
    ):
        a, _, _ = three_characters
        await _enable_chat_story(db_session, chat)
        await _run_round(db_session, chat, a, round_id="r1", importance=6.0)

        invoked = []
        report = await sc.maybe_consolidate_story(
            db_session, None, chat_id=chat.id, round_id="r1",
            invoke=lambda messages: invoked.append(messages) or "{}",
        )
        assert report["skipped"] == "flag off"
        assert invoked == []

    @pytest.mark.asyncio
    async def test_skip_when_story_disabled(
        self, enable_consolidation, db_session, chat, three_characters
    ):
        a, _, _ = three_characters
        await _enable_chat_story(db_session, chat)
        await _run_round(db_session, chat, a, round_id="r1", importance=6.0)
        # settings.story_enabled=false → story данные не пишутся
        report = await sc.maybe_consolidate_story(
            db_session, None, chat_id=chat.id, round_id="r1",
            invoke=_make_invoke({}),
        )
        assert report["skipped"] == "story disabled"

    @pytest.mark.asyncio
    async def test_skip_when_chat_story_disabled(
        self,         enable_story, enable_consolidation_now, db_session, chat, three_characters
    ):
        a, _, _ = three_characters
        await _run_round(db_session, chat, a, round_id="r1", importance=6.0)
        # chats.story_enabled=false → перчатовый тумблер
        report = await sc.maybe_consolidate_story(
            db_session, None, chat_id=chat.id, round_id="r1",
            invoke=_make_invoke({}),
        )
        assert report["skipped"] == "story disabled"

    @pytest.mark.asyncio
    async def test_interval_not_reached_skips(
        self,         enable_story, enable_consolidation_now, monkeypatch,
        db_session, chat, three_characters,
    ):
        monkeypatch.setattr(settings, "story_consolidation_interval_rounds", 10)
        a, _, _ = three_characters
        await _enable_chat_story(db_session, chat)
        for rid in ("r1", "r2"):
            await _run_round(db_session, chat, a, round_id=rid, importance=6.0)

        invoked = []
        report = await sc.maybe_consolidate_story(
            db_session, None, chat_id=chat.id, round_id="r2",
            invoke=lambda messages: invoked.append(messages) or "{}",
        )
        assert report["skipped"] == "interval not reached"
        assert invoked == []

    @pytest.mark.asyncio
    async def test_interval_reached_triggers(
        self,         enable_story, enable_consolidation_now, monkeypatch,
        db_session, chat, three_characters,
    ):
        monkeypatch.setattr(settings, "story_consolidation_interval_rounds", 2)
        a, _, _ = three_characters
        await _enable_chat_story(db_session, chat)
        for rid in ("r1", "r2", "r3"):
            await _run_round(db_session, chat, a, round_id=rid, importance=6.0)

        report = await sc.maybe_consolidate_story(
            db_session, None, chat_id=chat.id, round_id="r3",
            invoke=_make_invoke({}),
        )
        assert report["ok"] is True
        assert report["trigger"] == "interval"
        assert report["rounds"] == 3

    @pytest.mark.asyncio
    async def test_critical_event_triggers_before_interval(
        self,         enable_story, enable_consolidation_now, monkeypatch,
        db_session, chat, three_characters,
    ):
        monkeypatch.setattr(settings, "story_consolidation_interval_rounds", 100)
        monkeypatch.setattr(settings, "story_consolidation_critical_importance", 8.0)
        a, _, _ = three_characters
        await _enable_chat_story(db_session, chat)
        await _run_round(
            db_session, chat, a, round_id="r1", importance=9.0,
            action={"actor": a.name, "action": "предаёт", "object": "группу"},
        )

        report = await sc.maybe_consolidate_story(
            db_session, None, chat_id=chat.id, round_id="r1",
            invoke=_make_invoke({}),
        )
        assert report["ok"] is True
        assert report["trigger"] == "critical"


# ---------------------------------------------------------------------------
# валидация и применение
# ---------------------------------------------------------------------------

class TestApply:
    @pytest.mark.asyncio
    async def test_completed_goals_leave_active(
        self,         enable_story, enable_consolidation_now,
        db_session, chat, three_characters,
    ):
        a, _, _ = three_characters
        await _enable_chat_story(db_session, chat)
        await _run_round(
            db_session, chat, a, round_id="r1", importance=7.0,
            action={"actor": a.name, "action": "находит", "object": "письмо"},
        )
        threads = await crud.get_active_story_threads(db_session, chat.id)
        assert len(threads) == 1
        thread_name = threads[0].name

        report = await sc.maybe_consolidate_story(
            db_session, None, chat_id=chat.id, round_id="r1",
            invoke=_make_invoke({
                "completed_goals": [{"name": thread_name, "confidence": 0.9}],
            }),
        )
        assert report["ok"] is True
        assert report["applied"]["completed_goals"] == 1

        threads = await crud.get_active_story_threads(db_session, chat.id)
        assert threads == []
        current = await _state_dict(db_session, chat.id)
        assert thread_name in current["completed_goals"]
        assert thread_name not in current["active_threads"]

    @pytest.mark.asyncio
    async def test_new_threads_grounded_only(
        self,         enable_story, enable_consolidation_now,
        db_session, chat, three_characters,
    ):
        a, _, _ = three_characters
        await _enable_chat_story(db_session, chat)
        await _run_round(
            db_session, chat, a, round_id="r1", importance=7.0,
            action={"actor": a.name, "action": "находит", "object": "письмо"},
        )

        report = await sc.maybe_consolidate_story(
            db_session, None, chat_id=chat.id, round_id="r1",
            invoke=_make_invoke({
                "new_threads": [
                    {"name": "тайное письмо", "actors": [a.name],
                     "importance": 7, "confidence": 0.9},
                    {"name": "охота на драконов", "actors": ["Дракон"],
                     "importance": 8, "confidence": 0.9},
                ],
            }),
        )
        assert report["ok"] is True
        assert report["applied"]["new_threads"] == 1

        threads = await crud.get_active_story_threads(db_session, chat.id)
        names = [t.name for t in threads]
        assert any("письмо" in n for n in names)
        assert not any("дракон" in n for n in names)

    @pytest.mark.asyncio
    async def test_progress_preserved_and_clamped(
        self,         enable_story, enable_consolidation_now,
        db_session, chat, three_characters,
    ):
        a, _, _ = three_characters
        await _enable_chat_story(db_session, chat)
        await _run_round(
            db_session, chat, a, round_id="r1", importance=7.0,
            action={"actor": a.name, "action": "находит", "object": "письмо"},
        )
        threads = await crud.get_active_story_threads(db_session, chat.id)
        thread_name = threads[0].name

        report = await sc.maybe_consolidate_story(
            db_session, None, chat_id=chat.id, round_id="r1",
            invoke=_make_invoke({
                "updated_threads": [
                    {"name": thread_name, "progress": 1.5,
                     "importance": 20, "confidence": 0.9},
                ],
                "progress": {"overall": 0.7, "confidence": 0.8},
            }),
        )
        assert report["ok"] is True
        assert report["progress_applied"] is True

        current = await _state_dict(db_session, chat.id)
        assert current["thread_progress"][thread_name] == 1.0  # кламп в 0..1
        assert current["progress"]["overall"] == 0.7
        thread = await crud.find_story_thread_by_name(db_session, chat.id, thread_name)
        assert thread.importance == 10  # importance клампится в 1..10

    @pytest.mark.asyncio
    async def test_phase_change_validated_against_original_plot(
        self,         enable_story, enable_consolidation_now,
        db_session, chat, three_characters,
    ):
        a, _, _ = three_characters
        await _enable_chat_story(db_session, chat)
        await crud.get_or_create_story_state(db_session, chat.id)
        await crud.update_story_state(
            db_session, chat.id,
            original_plot="Охота на апостолов. Сбор группы в таверне.",
        )
        await _run_round(
            db_session, chat, a, round_id="r1", importance=7.0,
            action={"actor": a.name, "action": "находит", "object": "письмо"},
        )

        report = await sc.maybe_consolidate_story(
            db_session, None, chat_id=chat.id, round_id="r1",
            invoke=_make_invoke({
                "phase_change": {"phase": "охота на апостолов", "confidence": 0.9},
            }),
        )
        assert report["ok"] is True
        state = await crud.get_story_state(db_session, chat.id)
        assert state.story_phase == "охота на апостолов"

    @pytest.mark.asyncio
    async def test_unknown_phase_not_applied(
        self,         enable_story, enable_consolidation_now,
        db_session, chat, three_characters,
    ):
        a, _, _ = three_characters
        await _enable_chat_story(db_session, chat)
        await _run_round(
            db_session, chat, a, round_id="r1", importance=7.0,
            action={"actor": a.name, "action": "находит", "object": "письмо"},
        )
        state = await crud.get_story_state(db_session, chat.id)
        assert state.original_plot == ""  # фаза нигде не зарегистрирована

        report = await sc.maybe_consolidate_story(
            db_session, None, chat_id=chat.id, round_id="r1",
            invoke=_make_invoke({
                "phase_change": {"phase": "совсем новая фаза", "confidence": 0.9},
            }),
        )
        assert report["ok"] is True
        state = await crud.get_story_state(db_session, chat.id)
        assert state.story_phase == ""

    @pytest.mark.asyncio
    async def test_original_plot_untouched(
        self,         enable_story, enable_consolidation_now,
        db_session, chat, three_characters,
    ):
        a, _, _ = three_characters
        await _enable_chat_story(db_session, chat)
        await _run_round(
            db_session, chat, a, round_id="r1", importance=7.0,
            action={"actor": a.name, "action": "находит", "object": "письмо"},
        )
        await crud.update_story_state(
            db_session, chat.id, original_plot="ПЛОТ НЕПРИКОСНОВЕНЕН"
        )

        report = await sc.maybe_consolidate_story(
            db_session, None, chat_id=chat.id, round_id="r1",
            invoke=_make_invoke({"summary": {"text": "сюжет продвигается",
                                             "confidence": 0.9}}),
        )
        assert report["ok"] is True
        state = await crud.get_story_state(db_session, chat.id)
        assert state.original_plot == "ПЛОТ НЕПРИКОСНОВЕНЕН"
        chat_row = await crud.get_chat(db_session, chat.id)
        assert chat_row.original_plot == ""

    @pytest.mark.asyncio
    async def test_low_confidence_not_applied(
        self,         enable_story, enable_consolidation_now,
        db_session, chat, three_characters,
    ):
        a, _, _ = three_characters
        await _enable_chat_story(db_session, chat)
        await _run_round(
            db_session, chat, a, round_id="r1", importance=7.0,
            action={"actor": a.name, "action": "находит", "object": "письмо"},
        )
        before = await _state_dict(db_session, chat.id)

        report = await sc.maybe_consolidate_story(
            db_session, None, chat_id=chat.id, round_id="r1",
            invoke=_make_invoke({
                "new_threads": [
                    {"name": "тайное письмо", "actors": [a.name],
                     "importance": 7, "confidence": 0.1},
                ],
            }),
        )
        assert report["ok"] is True
        assert report["applied"]["new_threads"] == 0
        # low-confidence кандидат не создан (остаётся только линия самого раунда)
        threads = await crud.get_active_story_threads(db_session, chat.id)
        assert len(threads) == 1
        assert not any("тайное письмо" == t.name for t in threads)
        assert await _state_dict(db_session, chat.id) == before


# ---------------------------------------------------------------------------
# rollback
# ---------------------------------------------------------------------------

class TestRollback:
    @pytest.mark.asyncio
    async def test_invalid_json_rollback(
        self,         enable_story, enable_consolidation_now,
        db_session, chat, three_characters,
    ):
        a, _, _ = three_characters
        await _enable_chat_story(db_session, chat)
        await _run_round(
            db_session, chat, a, round_id="r1", importance=7.0,
            action={"actor": a.name, "action": "находит", "object": "письмо"},
        )
        state = await crud.get_story_state(db_session, chat.id)
        before_version = state.version
        before_story = state.current_story

        async def _bad_invoke(messages):
            return "совсем не JSON"

        report = await sc.maybe_consolidate_story(
            db_session, None, chat_id=chat.id, round_id="r1",
            invoke=_bad_invoke,
        )
        assert report["ok"] is False
        assert report["error"] == "invalid JSON"
        assert report["rolled_back"] is True

        state = await crud.get_story_state(db_session, chat.id)
        assert state.version == before_version
        assert state.current_story == before_story
        assert state.last_consolidation_rounds is None

    @pytest.mark.asyncio
    async def test_invoke_exception_rollback(
        self,         enable_story, enable_consolidation_now,
        db_session, chat, three_characters,
    ):
        a, _, _ = three_characters
        await _enable_chat_story(db_session, chat)
        await _run_round(
            db_session, chat, a, round_id="r1", importance=7.0,
            action={"actor": a.name, "action": "находит", "object": "письмо"},
        )
        state = await crud.get_story_state(db_session, chat.id)
        before_version = state.version
        before_story = state.current_story

        async def _boom(messages):
            raise RuntimeError("llm down")

        report = await sc.maybe_consolidate_story(
            db_session, None, chat_id=chat.id, round_id="r1", invoke=_boom,
        )
        assert report["ok"] is False
        assert report["rolled_back"] is True
        state = await crud.get_story_state(db_session, chat.id)
        assert state.version == before_version
        assert state.current_story == before_story

    @pytest.mark.asyncio
    async def test_no_changes_keeps_version(
        self,         enable_story, enable_consolidation_now,
        db_session, chat, three_characters,
    ):
        a, _, _ = three_characters
        await _enable_chat_story(db_session, chat)
        await _run_round(
            db_session, chat, a, round_id="r1", importance=7.0,
            action={"actor": a.name, "action": "находит", "object": "письмо"},
        )
        state = await crud.get_story_state(db_session, chat.id)
        before_version = state.version

        report = await sc.maybe_consolidate_story(
            db_session, None, chat_id=chat.id, round_id="r1",
            invoke=_make_invoke({"completed_goals": []}),
        )
        assert report["ok"] is True
        assert report["applied"]["completed_goals"] == 0
        state = await crud.get_story_state(db_session, chat.id)
        assert state.version == before_version
        assert state.last_consolidation_rounds == 1  # консолидация состоялась

    @pytest.mark.asyncio
    async def test_version_bumps_on_apply(
        self,         enable_story, enable_consolidation_now,
        db_session, chat, three_characters,
    ):
        a, _, _ = three_characters
        await _enable_chat_story(db_session, chat)
        await _run_round(
            db_session, chat, a, round_id="r1", importance=7.0,
            action={"actor": a.name, "action": "находит", "object": "письмо"},
        )
        state = await crud.get_story_state(db_session, chat.id)
        before_version = state.version

        report = await sc.maybe_consolidate_story(
            db_session, None, chat_id=chat.id, round_id="r1",
            invoke=_make_invoke({
                "summary": {"text": "письмо найдено", "confidence": 0.9},
            }),
        )
        assert report["ok"] is True
        assert report["summary_applied"] is True
        state = await crud.get_story_state(db_session, chat.id)
        assert state.version == before_version + 1

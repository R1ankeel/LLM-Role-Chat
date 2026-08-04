"""WPE 3.0 (Plans/WPE.md) Фаза 7 — Event Bus / Interrupts (Ул.5, §7, И17).

Покрывает:
- Golden #21 («звонок/обращение будит NPC, один ответ за раунд, без
  зацикливания»): очередь приоритетов ``round_engine.EventBus`` — разбуженные
  NPC идут впереди плановых, внутри приоритета FIFO, плановый порядок —
  исходный ``order_index`` (детерминизм);
- игрок→NPC (``seed`` из ``target_character_ids``) — адресат отвечает первым;
- NPC→NPC (``target_character_ids`` реплики) — буждение вне очереди, даже если
  разбуженный стоит позже по расписанию;
- один ответ на NPC за раунд; повторные буждения и буждения уже ответивших
  игнорируются (И17);
- ``run_round`` — единственная оркестрирующая функция (§9); ``run_round_fixed``
  — откат: исходный фиксированный порядок (флаг off, без изменения поведения).

Флаги по умолчанию выключены (инвариант Фазы 0) — каждый тест включает
нужные отдельным ``monkeypatch``.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest

import app.ollama_client as ollama_client
from app import chat_engine
from app import round_engine
from app.config import settings

# Защита от известной утечки мока (test_stream_disconnect) — как в Фазе 2–6.
_REAL_GENERATE = ollama_client.generate


@pytest.fixture(autouse=True)
def _reset_wpe_state(monkeypatch):
    monkeypatch.setattr(settings, "world_engine_events_enabled", False)
    monkeypatch.setattr(settings, "world_engine_perception_enabled", False)
    monkeypatch.setattr(settings, "world_engine_recency_tail_enabled", False)
    monkeypatch.setattr(settings, "world_engine_threads_enabled", False)
    monkeypatch.setattr(settings, "world_engine_partial_perception_enabled", False)
    monkeypatch.setattr(settings, "world_engine_actions_enabled", False)
    monkeypatch.setattr(settings, "world_engine_event_bus_enabled", False)
    ollama_client.generate = _REAL_GENERATE
    yield


def _npc(cid, order, name=None):
    return SimpleNamespace(id=cid, name=name or f"N{order}", order_index=order)


# ---------------------------------------------------------------------------
# EventBus — очередь приоритетов (unit, без БД/LLM)
# ---------------------------------------------------------------------------

def test_event_bus_planned_order():
    bus = round_engine.EventBus([_npc(1, 1, "A"), _npc(2, 2, "B"), _npc(3, 3, "C")])
    assert [bus.pop_next() for _ in range(3)] == [1, 2, 3]
    assert bus.pop_next() is None


def test_event_bus_wake_out_of_order():
    """Звонок/обращение будит NPC вне очереди (Golden #21)."""
    bus = round_engine.EventBus([_npc(1, 1, "A"), _npc(2, 2, "B"), _npc(3, 3, "C")])
    bus.wake(3)
    assert bus.pop_next() == 3
    assert [bus.pop_next() for _ in range(2)] == [1, 2]
    assert bus.pop_next() is None


def test_event_bus_wake_fifo():
    """Внутри приоритета разбуженных — FIFO (риски §12, защита от инверсии)."""
    bus = round_engine.EventBus([_npc(1, 1, "A"), _npc(2, 2, "B"), _npc(3, 3, "C")])
    bus.wake(3)
    bus.wake(2)
    assert [bus.pop_next() for _ in range(3)] == [3, 2, 1]
    assert bus.pop_next() is None


def test_event_bus_one_response_per_npc():
    """Один ответ на NPC за раунд; повторные буждения игнорируются (И17)."""
    bus = round_engine.EventBus([_npc(1, 1, "A"), _npc(2, 2, "B"), _npc(3, 3, "C")])
    assert bus.pop_next() == 1  # A ответил
    bus.wake(1)  # уже ответил → игнор
    bus.wake(2)
    bus.wake(2)  # уже разбужен → игнор
    assert bus.pop_next() == 2
    assert bus.pop_next() == 3
    assert bus.pop_next() is None
    assert bus.generated() == {1, 2, 3}


def test_event_bus_seed_first_move():
    """Игрок→NPC: адресат user-сообщения будится первым ходом раунда."""
    bus = round_engine.EventBus([_npc(1, 1, "A"), _npc(2, 2, "B"), _npc(3, 3, "C")])
    bus.seed([2])
    assert bus.pop_next() == 2
    assert [bus.pop_next() for _ in range(2)] == [1, 3]
    assert bus.pop_next() is None


# ---------------------------------------------------------------------------
# run_round / run_round_fixed — оркестрация (без БД, шаг — заглушка)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_round_call_wakes_npc_out_of_order():
    """NPC A «звонит» C → C генерирует раньше планового B."""
    a, b, c = _npc(1, 1, "A"), _npc(2, 2, "B"), _npc(3, 3, "C")
    generated = []

    async def step(character, bus):
        generated.append(character.name)
        if character.id == a.id:
            bus.wake(c.id)
        yield {"type": "mark", "name": character.name}

    events = [e async for e in round_engine.run_round([a, b, c], step)]
    assert generated == ["A", "C", "B"]
    assert [e["name"] for e in events] == ["A", "C", "B"]


@pytest.mark.asyncio
async def test_run_round_player_target_first():
    """seed_target_ids=[B] → B отвечает первым (игрок обратился к B)."""
    a, b, c = _npc(1, 1, "A"), _npc(2, 2, "B"), _npc(3, 3, "C")
    generated = []

    async def step(character, bus):
        generated.append(character.name)
        yield {"type": "mark", "name": character.name}

    events = [e async for e in
              round_engine.run_round([a, b, c], step, seed_target_ids=[b.id])]
    assert generated == ["B", "A", "C"]
    assert len(events) == 3


@pytest.mark.asyncio
async def test_run_round_no_regeneration_no_loop():
    """Самоссылки и повторные буждения не дают второго ответа и не зацикливают."""
    a, b, c = _npc(1, 1, "A"), _npc(2, 2, "B"), _npc(3, 3, "C")
    generated = []

    async def step(character, bus):
        generated.append(character.name)
        if character.id == a.id:
            bus.wake(a.id)  # самоссылка после ответа → игнор
            bus.wake(b.id)
            bus.wake(b.id)  # повторное буждение → игнор
        yield {}

    events = [e async for e in round_engine.run_round([a, b, c], step)]
    assert len(generated) == 3
    assert sorted(generated) == ["A", "B", "C"]
    assert len(events) == 3


@pytest.mark.asyncio
async def test_run_round_fixed_ignores_wakes():
    """Откат: run_round_fixed игнорирует буждения — исходный фиксированный порядок."""
    a, b, c = _npc(1, 1, "A"), _npc(2, 2, "B"), _npc(3, 3, "C")
    generated = []

    async def step(character, bus):
        generated.append(character.name)
        if bus is not None and character.id == a.id:
            bus.wake(c.id)
        yield {}

    events = [e async for e in round_engine.run_round_fixed([a, b, c], step)]
    assert generated == ["A", "B", "C"]
    assert len(events) == 3


# ---------------------------------------------------------------------------
# End-to-end: process_user_message_streaming с мокнутым generate
# ---------------------------------------------------------------------------

async def _run_in_current_thread(func, /, *args, **kwargs):
    return func(*args, **kwargs)


async def _stream(db_session, chat, *, targets=None, fake_generate):
    with (
        patch("app.chat_engine.ollama_client.generate", side_effect=fake_generate),
        patch("app.chat_engine.asyncio.create_task"),
        patch("app.chat_engine.asyncio.to_thread", side_effect=_run_in_current_thread),
    ):
        async for _ in chat_engine.process_user_message_streaming(
            httpx.AsyncClient(base_url="http://test"),
            db_session,
            chat.id,
            "Hello everyone",
            target_character_ids=targets,
        ):
            pass


@pytest.mark.asyncio
async def test_streaming_call_wakes_npc_out_of_order(
    db_session, chat, three_characters, monkeypatch
):
    """Golden #21: NPC A «звонит» C → C отвечает раньше планового B."""
    monkeypatch.setattr(settings, "world_engine_event_bus_enabled", True)
    a, b, c = three_characters
    call_log = []

    async def fake_generate(**kwargs):
        character = kwargs["character"]
        call_log.append(character.name)
        text = (
            f"Позвоню {c.name}."
            if character.id == a.id
            else f"Reply from {character.name} with enough text for validation."
        )
        yield {"type": "response", "text": text}

    await _stream(db_session, chat, fake_generate=fake_generate)
    assert call_log == ["Character A", "Character C", "Character B"]


@pytest.mark.asyncio
async def test_streaming_player_target_first(
    db_session, chat, three_characters, monkeypatch
):
    """Игрок обратился к конкретному NPC → он отвечает первым (Golden #2 → Ул.5)."""
    monkeypatch.setattr(settings, "world_engine_event_bus_enabled", True)
    a, b, c = three_characters
    call_log = []

    async def fake_generate(**kwargs):
        character = kwargs["character"]
        call_log.append(character.name)
        yield {
            "type": "response",
            "text": f"Reply from {character.name} with enough text for validation.",
        }

    await _stream(db_session, chat, targets=[b.id], fake_generate=fake_generate)
    assert call_log == ["Character B", "Character A", "Character C"]


@pytest.mark.asyncio
async def test_streaming_repeated_addressing_ignored(
    db_session, chat, three_characters, monkeypatch
):
    """Повторные буждения к уже ответившему игнорируются — один ответ на NPC."""
    monkeypatch.setattr(settings, "world_engine_event_bus_enabled", True)
    a, b, c = three_characters
    call_log = []

    async def fake_generate(**kwargs):
        character = kwargs["character"]
        call_log.append(character.name)
        if character.id == a.id:
            text = f"Позвоню {c.name}."
        elif character.id == c.id:
            text = f"Позвоню {a.name} и {b.name}."  # A уже ответил, B ещё нет
        else:
            text = f"Reply from {character.name} with enough text for validation."
        yield {"type": "response", "text": text}

    await _stream(db_session, chat, fake_generate=fake_generate)
    assert call_log == ["Character A", "Character C", "Character B"]
    assert len(call_log) == 3
    assert len(set(call_log)) == 3  # ни один NPC не сгенерировал дважды


@pytest.mark.asyncio
async def test_streaming_off_fixed_order(db_session, chat, three_characters):
    """Откат: флаг off → адресация в тексте не меняет порядок (фиксированный)."""
    a, b, c = three_characters
    call_log = []

    async def fake_generate(**kwargs):
        character = kwargs["character"]
        call_log.append(character.name)
        text = (
            f"Позвоню {c.name}."
            if character.id == a.id
            else f"Reply from {character.name} with enough text for validation."
        )
        yield {"type": "response", "text": text}

    await _stream(db_session, chat, fake_generate=fake_generate)
    assert call_log == ["Character A", "Character B", "Character C"]

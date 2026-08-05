"""Tests for Context Builder v2 (Plans/update20.md §23, Sprint 13).

Покрывает:
1. бюджет v2 — per-block подбюджеты (WORLD/PERCEIVE/RELATIONSHIP/GOAL/STORY/
   KNOWLEDGE/RELEVANT MEMORY) при включённом ``context_v2_enabled``;
2. приоритеты — P0-блоки (WORLD, WHAT YOU PERCEIVE) не усекаются; P1/P2
   усекаются до своих подбюджетов;
3. усечение RELEVANT MEMORY по ``relevant_memory_budget``;
4. откат — при ``context_v2_enabled=false`` всё legacy-поведение не меняется
   (scene_text, relationships в system, `<character_memories>`), v2-поля пусты;
5. golden-снэпшоты блоков (WORLD/PERCEIVE/RELATIONSHIP/RELEVANT MEMORY) — только
   при включённом флаге.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
import pytest_asyncio

from app import crud
from app import schemas
from app.config import settings
from app.context_budget import build_budget
from app.context_builder import ContextBuilder
from app.prompt_builder import (
    build_perceive_block,
    build_relationship_block,
    build_relevant_memory_block,
    build_world_block,
)


async def _add_message(db, chat_id, content, *, role="user", character_id=None, location=""):
    return await crud.create_message(
        db,
        schemas.MessageCreate(
            chat_id=chat_id,
            role=role,
            content=content,
            character_id=character_id,
            location=location,
            visibility="local",
        ),
    )


async def _load_window(db, chat_id, limit=None):
    return await crud.get_messages_by_chat(db, chat_id, limit)


def _names(characters):
    return {c.id: c.name for c in characters}


def _locations(characters, **overrides):
    locs = {c.id: "" for c in characters}
    locs.update(overrides)
    return locs


async def _build(db, chat, character, characters, messages_window, *, user_message="Тест", **kwargs):
    builder = ContextBuilder()
    return await builder.build(
        db=db,
        chat_id=chat.id,
        character=character,
        user_message=user_message,
        general_prompt=chat.general_prompt,
        messages_window=messages_window,
        round_messages=kwargs.pop("round_messages", []),
        character_names=kwargs.pop("character_names", _names(characters)),
        character_locations=kwargs.pop("character_locations", _locations(characters)),
        **kwargs,
    )


@pytest_asyncio.fixture
async def v2_enabled(monkeypatch):
    monkeypatch.setattr(settings, "context_v2_enabled", True)
    yield True


# ---------------------------------------------------------------------------
# Бюджет v2
# ---------------------------------------------------------------------------


def test_v2_budget_sub_budgets(v2_enabled):
    """Per-block sub-budgets присутствуют при включённом флаге."""
    budget = build_budget(60000)
    assert budget.world_budget > 0
    assert budget.perceive_budget > 0
    assert budget.relationship_budget > 0
    assert budget.goal_budget > 0
    assert budget.story_budget > 0
    assert budget.knowledge_budget > 0
    assert budget.relevant_memory_budget > 0
    assert budget.memory_budget == budget.relevant_memory_budget
    # Сумма не превышает total − reserve.
    allocated = (
        budget.state_budget
        + budget.world_budget
        + budget.perceive_budget
        + budget.goal_budget
        + budget.relationship_budget
        + budget.story_budget
        + budget.summary_budget
        + budget.relevant_memory_budget
        + budget.knowledge_budget
        + budget.retrieved_history_budget
        + budget.recent_history_max_tokens
    )
    assert allocated <= budget.total_tokens - budget.reserve_tokens + 1


def test_legacy_budget_no_v2_fields():
    """При off подбюджеты v2 обнулены (legacy-поведение)."""
    budget = build_budget(60000)
    assert budget.world_budget == 0
    assert budget.perceive_budget == 0
    assert budget.relationship_budget == 0
    assert budget.relevant_memory_budget == 0


# ---------------------------------------------------------------------------
# Сборка блоков v2
# ---------------------------------------------------------------------------


async def test_v2_build_populates_blocks(
    db_session, chat, three_characters, v2_enabled
):
    a = three_characters[0]
    await _add_message(
        db_session, chat.id, "Привет всем!", role="user"
    )
    await _add_message(
        db_session,
        chat.id,
        "Здравствуй, путник.",
        role="character",
        character_id=a.id,
    )
    window = await _load_window(db_session, chat.id)

    built = await _build(
        db_session,
        chat,
        a,
        three_characters,
        window,
        round_messages=window,
        relationships_block="Character B: друг",
        character_state=None,
    )

    # P0 WORLD заменяет legacy `<scene>`.
    assert built.world_text
    assert "<world>" in built.world_text
    assert "<scene>" not in built.scene_text
    assert built.scene_text == ""
    # WHAT YOU PERCEIVE — perception-строки раунда.
    assert "<what_you_perceive>" in built.perceive_text
    # RELATIONSHIP — отдельный user-блок.
    assert "<relationship>" in built.relationship_text
    assert "Character B" in built.relationship_text
    # RELEVANT MEMORY — пустой при отсутствии memories.
    assert built.relevant_memory_text == ""
    # Component-tokens содержат новые оси.
    assert "world" in built.component_tokens
    assert "perceive" in built.component_tokens
    assert "relationship" in built.component_tokens
    # Budget v2.
    assert built.budget.world_budget > 0


async def test_v2_build_relevant_memory(
    db_session, chat, three_characters, v2_enabled
):
    a = three_characters[0]
    m = await crud.create_memory(
        db_session,
        schemas.MemoryCreate(
            chat_id=chat.id,
            character_id=a.id,
            content="Игрок подарил Алисе серебряный ключ",
            importance=0.9,
            category="событие",
        ),
    )
    await db_session.commit()
    await _add_message(db_session, chat.id, "Привет", role="user")
    window = await _load_window(db_session, chat.id)

    built = await _build(
        db_session,
        chat,
        a,
        three_characters,
        window,
        round_messages=window,
        memories=[m],
        max_tokens=60000,
    )
    assert "<relevant_memory>" in built.relevant_memory_text
    assert "серебряный ключ" in built.relevant_memory_text
    assert "<character_memories>" not in built.relevant_memory_text


async def test_v2_perceive_block_only_round_lines(
    db_session, chat, three_characters, v2_enabled
):
    """WHAT YOU PERCEIVE — только строки текущего раунда (не вся история)."""
    a = three_characters[0]
    await _add_message(db_session, chat.id, "Старое сообщение из истории", role="user")
    await _add_message(db_session, chat.id, "Свежее сообщение раунда", role="user")
    window = await _load_window(db_session, chat.id)

    built = await _build(
        db_session,
        chat,
        a,
        three_characters,
        window,
        round_messages=[window[-1]],
    )
    assert "<what_you_perceive>" in built.perceive_text
    assert "Свежее сообщение раунда" in built.perceive_text
    assert "Старое сообщение из истории" not in built.perceive_text


# ---------------------------------------------------------------------------
# Откат: флаг off = legacy-поведение
# ---------------------------------------------------------------------------


async def test_legacy_build_no_v2_blocks(db_session, chat, three_characters):
    a = three_characters[0]
    await _add_message(db_session, chat.id, "Привет", role="user")
    window = await _load_window(db_session, chat.id)

    built = await _build(
        db_session,
        chat,
        a,
        three_characters,
        window,
        round_messages=window,
        relationships_block="Character B: друг",
    )
    assert built.scene_text
    assert "<scene>" in built.scene_text
    assert built.world_text == ""
    assert built.perceive_text == ""
    assert built.relationship_text == ""
    assert built.relevant_memory_text == ""
    # В системе relationships остаются в system (не дублируются в user-блоке).
    assert built.component_tokens["relationships"] > 0


async def test_v2_budget_did_not_change_legacy(db_session, chat, three_characters):
    """При off бюджет идентичен legacy-расчёту (scene_block в scene_text)."""
    a = three_characters[0]
    await _add_message(db_session, chat.id, "Привет", role="user")
    window = await _load_window(db_session, chat.id)

    built = await _build(db_session, chat, a, three_characters, window)
    assert built.budget.world_budget == 0
    assert built.budget.perceive_budget == 0
    assert built.budget.relationship_budget == 0


# ---------------------------------------------------------------------------
# Усечение P2 (RELEVANT MEMORY)
# ---------------------------------------------------------------------------


async def test_v2_relevant_memory_truncated_to_budget(
    db_session, chat, three_characters, v2_enabled, monkeypatch
):
    a = three_characters[0]
    monkeypatch.setattr(settings, "context_v2_memory_budget", 20)
    monkeypatch.setattr(settings, "context_recent_max_tokens", 500)
    monkeypatch.setattr(settings, "max_context_tokens", 4000)
    monkeypatch.setattr(settings, "context_reserve_tokens", 200)

    memories = []
    for i in range(5):
        m = await crud.create_memory(
            db_session,
            schemas.MemoryCreate(
                chat_id=chat.id,
                character_id=a.id,
                content=f"Длинное воспоминание номер {i}: " + "текст " * 30,
                importance=0.9,
                category="событие",
            ),
        )
        memories.append(m)
    await db_session.commit()
    await _add_message(db_session, chat.id, "Привет", role="user")
    window = await _load_window(db_session, chat.id)

    built = await _build(
        db_session,
        chat,
        a,
        three_characters,
        window,
        round_messages=window,
        memories=memories,
        max_tokens=4000,
    )
    # RELEVANT MEMORY усечён до подбюджета (не весь список в блоке).
    assert any(
        d.component == "relevant_memory" and d.reason == "budget"
        for d in built.dropped_items
    ) or len(built.memories) < len(memories)


# ---------------------------------------------------------------------------
# Golden-блоки (только при флаге)
# ---------------------------------------------------------------------------


def test_world_block_golden(v2_enabled):
    scene = SimpleNamespace(
        time_of_day="вечер",
        custom_state='{"weather": "дождь"}',
        character_locations={"Character A": "Таверна", "Character B": "Таверна"},
    )
    block = build_world_block(
        "Плот: таверна у дороги",
        scene,
        None,
        current_character_name="Character A",
        character_locations={"Character A": "Таверна", "Character B": "Таверна"},
    )
    assert "<world>" in block
    assert "вечер" in block
    assert "дождь" in block
    assert "Таверна" in block
    assert "<scene>" not in block
    assert "мировая истина" not in block


def test_perceive_block_golden(v2_enabled):
    block = build_perceive_block(
        ["Character B: Здравствуй, путник.", "Character C стоит в дверях."]
    )
    assert "<what_you_perceive>" in block
    assert "Character B" in block
    assert "Character C" in block


def test_relationship_block_golden(v2_enabled):
    block = build_relationship_block("Character B: друг\n  описание: давние друзья")
    assert "<relationship>" in block
    assert "Character B" in block
    assert "<relationships>" not in block


def test_relevant_memory_block_golden(v2_enabled):
    block = build_relevant_memory_block(
        [SimpleNamespace(content="Память о ключе", importance=0.9)]
    )
    assert "<relevant_memory>" in block
    assert "Память о ключе" in block
    assert "<character_memories>" not in block


def test_v2_blocks_empty_when_flag_off():
    """При off golden-блоки v2 пустые (legacy-рендер не меняется)."""
    assert build_world_block("", None) == ""
    assert build_perceive_block([]) == ""
    assert build_relationship_block("") == ""
    assert build_relevant_memory_block([]) == ""

"""Tests for the token-aware ContextBuilder (TZ §27 scenarios)."""

import pytest
import pytest_asyncio

from app import crud
from app import models
from app import schemas
from app.context_builder import ContextBuilder


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
async def chat_with_messages(db_session, chat, three_characters):
    for index in range(40):
        await _add_message(
            db_session,
            chat.id,
            f"Сообщение номер {index}: обычный диалоговый текст без специальных слов.",
            role="character" if index % 2 else "user",
            character_id=three_characters[index % 3].id,
        )
    window = await _load_window(db_session, chat.id)
    return chat, three_characters, window


async def test_short_history_is_not_compressed(db_session, chat, three_characters):
    a = three_characters[0]
    m1 = await _add_message(db_session, chat.id, "Привет, как дела?", role="user")
    m2 = await _add_message(
        db_session, chat.id, "Отлично! Гулял по парку.", role="character", character_id=a.id
    )
    window = await _load_window(db_session, chat.id)

    built = await _build(db_session, chat, a, three_characters, window)

    assert "Привет, как дела?" in built.recent_text
    assert "Гулял по парку" in built.recent_text
    assert built.diagnostics.newest_included_message_id == m2.id
    assert built.total_tokens > 0
    assert len(built.dropped_items) == 0


@pytest.mark.parametrize("max_tokens", [16384, 32768, 60000])
async def test_token_budget_never_exceeded(db_session, chat, three_characters, max_tokens):
    a = three_characters[0]
    for index in range(30):
        await _add_message(
            db_session,
            chat.id,
            f"Длинное сообщение {index} " + "текст " * 40,
            role="user",
        )
    window = await _load_window(db_session, chat.id)

    built = await _build(db_session, chat, a, three_characters, window, max_tokens=max_tokens)

    assert built.budget.total_tokens == max_tokens
    assert built.total_tokens <= max_tokens
    assert built.total_tokens > 0


async def test_summary_frontier_excludes_old_messages(db_session, chat, three_characters):
    a = three_characters[0]
    for index in range(30):
        await _add_message(
            db_session,
            chat.id,
            f"Событие {index}: ничем не примечательная деталь.",
            role="user",
        )
    window = await _load_window(db_session, chat.id)
    frontier = window[14].id

    built = await _build(
        db_session,
        chat,
        a,
        three_characters,
        window,
        summary="Краткое содержание произошедшего.",
        summary_through_message_id=frontier,
    )

    included = built.diagnostics.recent_message_ids
    assert included
    assert all(mid > frontier for mid in included)
    assert built.summary_text
    assert "Краткое содержание" in built.summary_text


async def test_retrieval_brings_relevant_event_beyond_frontier(db_session, chat, three_characters):
    a = three_characters[0]
    for index in range(25):
        await _add_message(
            db_session,
            chat.id,
            f"Пустое событие {index}: ничего важного не произошло.",
            role="user",
        )
    old_event = await _add_message(
        db_session,
        chat.id,
        "В тот вечер Максим спрятал магический кристалл в подвале замка.",
        role="character",
        character_id=three_characters[1].id,
    )
    for index in range(8):
        await _add_message(
            db_session,
            chat.id,
            f"Ещё одно событие {index}: рутинный разговор ни о чём.",
            role="user",
        )
    window = await _load_window(db_session, chat.id)
    frontier = window[-1].id

    built = await _build(
        db_session,
        chat,
        a,
        three_characters,
        window,
        user_message="Где Максим спрятал кристалл?",
        summary_through_message_id=frontier,
    )

    assert old_event.id in built.diagnostics.retrieved_message_ids
    assert "кристалл" in built.retrieved_text


async def test_witness_isolation_excludes_absent_events(db_session, chat, three_characters):
    viewer, other = three_characters[0], three_characters[1]
    secret = await _add_message(
        db_session,
        chat.id,
        "СЕКРЕТНЫЙ разговор за закрытой дверью.",
        role="character",
        character_id=other.id,
        location="cellar",
    )
    open_msg = await _add_message(
        db_session,
        chat.id,
        "Открытая беседа в зале.",
        role="user",
        location="hall",
    )
    db_session.add(
        models.MessagePresence(
            message_id=secret.id, character_id=viewer.id, presence="absent"
        )
    )
    await db_session.commit()
    window = await _load_window(db_session, chat.id)

    built = await _build(
        db_session,
        chat,
        viewer,
        three_characters,
        window,
        character_locations={viewer.id: "hall", other.id: "cellar"},
        viewer_location="hall",
    )

    assert "СЕКРЕТНЫЙ" not in built.dialogue_text
    assert open_msg.id in built.diagnostics.recent_message_ids


async def test_multi_character_receives_different_contexts(db_session, chat, three_characters):
    a, b = three_characters[0], three_characters[1]
    await _add_message(
        db_session,
        chat.id,
        "Личная заметка персонажа A о своих планах.",
        role="character",
        character_id=a.id,
        location="kitchen",
    )
    await _add_message(
        db_session,
        chat.id,
        "Общая тема разговора в зале.",
        role="user",
        location="hall",
    )
    window = await _load_window(db_session, chat.id)

    built_a = await _build(
        db_session,
        chat,
        a,
        three_characters,
        window,
        character_locations={a.id: "kitchen", b.id: "garden"},
        viewer_location="kitchen",
    )
    built_b = await _build(
        db_session,
        chat,
        b,
        three_characters,
        window,
        character_locations={a.id: "kitchen", b.id: "garden"},
        viewer_location="garden",
    )

    assert "Личная заметка персонажа A" in built_a.dialogue_text
    assert "Личная заметка персонажа A" not in built_b.dialogue_text
    assert built_a.dialogue_text != built_b.dialogue_text


async def test_token_aware_retrieval_large_memories_do_not_overflow(
    db_session, chat, three_characters
):
    a = three_characters[0]
    memories = [
        models.Memory(
            chat_id=chat.id,
            character_id=a.id,
            content="Длинное воспоминание " + "много важных деталей " * 120,
        )
        for _ in range(10)
    ]
    await _add_message(db_session, chat.id, "Текущий вопрос.", role="user")
    window = await _load_window(db_session, chat.id)

    built = await _build(
        db_session, chat, a, three_characters, window, memories=memories, max_tokens=16384
    )

    assert built.memories
    assert len(built.memories) < len(memories)
    assert any(item.component == "memories" for item in built.dropped_items)
    assert built.total_tokens <= built.budget.total_tokens


async def test_empty_retrieval_and_no_memories(db_session, chat, three_characters):
    a = three_characters[0]
    await _add_message(db_session, chat.id, "Только текущее сообщение.", role="user")
    window = await _load_window(db_session, chat.id)

    built = await _build(db_session, chat, a, three_characters, window)

    assert built.retrieved_text == ""
    assert not built.memories
    assert built.total_tokens > 0
    assert "Только текущее сообщение." in built.dialogue_text


async def test_scene_state_absent_and_summary_absent(db_session, chat, three_characters):
    a = three_characters[0]
    await _add_message(db_session, chat.id, "Вопрос без сцены.", role="user")
    window = await _load_window(db_session, chat.id)

    built = await _build(db_session, chat, a, three_characters, window)

    assert built.summary_text is None
    assert "Вопрос без сцены." in built.dialogue_text
    assert built.total_tokens > 0


async def test_bm25_fallback_works_without_embeddings(db_session, chat, three_characters):
    a = three_characters[0]
    needle = await _add_message(
        db_session,
        chat.id,
        "Спрятанное сокровище лежит под старым дубом у реки.",
        role="character",
        character_id=three_characters[1].id,
    )
    for index in range(12):
        await _add_message(
            db_session,
            chat.id,
            f"Фоновая активность {index}: обычный шум.",
            role="user",
        )
    await _add_message(db_session, chat.id, "Текущий вопрос.", role="user")
    window = await _load_window(db_session, chat.id)

    built = await _build(
        db_session,
        chat,
        a,
        three_characters,
        window,
        user_message="Где спрятано сокровище?",
        max_tokens=8000,
    )

    assert needle.id in built.diagnostics.retrieved_message_ids
    assert built.token_count_mode in ("estimated", "exact")


async def test_newest_round_message_always_included(db_session, chat, three_characters):
    a = three_characters[0]
    await _add_message(db_session, chat.id, "История диалога.", role="user")
    window = await _load_window(db_session, chat.id)
    draft = schemas.MessageCreate(
        chat_id=chat.id,
        role="character",
        character_id=a.id,
        content="Текущий ответ персонажа прямо сейчас.",
    )

    built = await _build(
        db_session,
        chat,
        a,
        three_characters,
        window,
        round_messages=[draft],
    )

    assert "Текущий ответ персонажа" in built.recent_text

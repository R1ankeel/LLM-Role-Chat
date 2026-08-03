"""Cross-location memory extraction, perspective, and isolation tests."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import sessionmaker

from app import crud
from app import memory_service
from app import prompt_builder
from app import schemas
from app import witness_model
from tests.conftest import create_characters


def _snap_char(character) -> dict:
    return schemas.CharacterRead.model_validate(character).model_dump(mode="python")


def _snap_msg(message) -> dict:
    return schemas.MessageRead.model_validate(message).model_dump(mode="json")


def _msg(
    mid: int,
    role: str,
    content: str,
    *,
    character_id: int | None = None,
    location: str = "",
    visibility: str = "local",
) -> SimpleNamespace:
    return SimpleNamespace(
        id=mid,
        role=role,
        content=content,
        character_id=character_id,
        location=location,
        visibility=visibility,
        target_character_ids="[]",
    )


def test_memory_filter_excludes_mentioned_includes_present():
    names = {1: "Максим", 2: "Алина"}
    messages = [
        _msg(1, "character", "Алина, где ты?", character_id=1, location="street"),
        _msg(2, "character", "Я на улице с Катей.", character_id=1, location="street"),
    ]
    # Alina is remote — both lines are absent (address without reachability is
    # NOT mentioned per ТЗ §14); nothing becomes a hard memory.
    ctx = witness_model.filter_history_for_memory_extraction(
        messages,
        viewer_character_id=2,
        character_names=names,
        viewer_location="home",
        character_locations={1: "street", 2: "home"},
    )
    assert not ctx.has_observable_events
    assert "улице" not in ctx.text
    assert all(s["reason"] == "not_visible" for s in ctx.skipped)

    # Maxim sees his own lines
    ctx_m = witness_model.filter_history_for_memory_extraction(
        messages,
        viewer_character_id=1,
        character_names=names,
        viewer_location="street",
        character_locations={1: "street", 2: "home"},
    )
    assert ctx_m.has_observable_events
    assert "Я на улице с Катей" in ctx_m.text


def test_memory_filter_same_location_all_present():
    names = {1: "Олег", 2: "Алина", 3: "Максим"}
    messages = [
        _msg(10, "character", "Я поцеловал Катю.", character_id=3, location="hall"),
    ]
    for cid in (1, 2, 3):
        ctx = witness_model.filter_history_for_memory_extraction(
            messages,
            viewer_character_id=cid,
            character_names=names,
            viewer_location="hall",
            character_locations={1: "hall", 2: "hall", 3: "hall"},
        )
        assert ctx.has_observable_events
        assert "поцеловал" in ctx.text


def test_false_me_patient_rejected_without_name_in_context():
    fact = schemas.ExtractedFact(
        fact="Максим поцеловал меня на улице вечером",
        witnessed=True,
        importance=0.9,
        category="событие",
    )
    # Context has the kiss but not Alina's name — she must not become "me"
    context = "Максим: Я поцеловал Катю.\nКатя: *краснеет*"
    assert (
        memory_service.validate_extracted_fact(
            fact, "Алина", observable_context=context
        )
        is None
    )


def test_false_me_patient_allowed_when_name_in_context():
    fact = schemas.ExtractedFact(
        fact="Максим поцеловал меня на улице вечером",
        witnessed=True,
        importance=0.9,
        category="событие",
    )
    context = "Максим: Я поцеловал Катю.\nКатя: *краснеет от поцелуя*"
    cleaned = memory_service.validate_extracted_fact(
        fact, "Катя", observable_context=context
    )
    assert cleaned is not None
    assert "поцеловал" in cleaned.fact


def test_ungrounded_fact_rejected():
    fact = schemas.ExtractedFact(
        fact="Дракон разрушил восточную башню крепости ночью",
        witnessed=True,
        importance=0.95,
        category="событие",
    )
    context = "Максим: Я поцеловал Катю на улице."
    assert (
        memory_service.validate_extracted_fact(
            fact, "Максим", observable_context=context
        )
        is None
    )


def test_grounded_actor_fact_accepted():
    fact = schemas.ExtractedFact(
        fact="Я поцеловал Катю на улице возле фонаря",
        witnessed=True,
        importance=0.9,
        category="событие",
    )
    context = "Максим: Я поцеловал Катю на улице возле фонаря."
    cleaned = memory_service.validate_extracted_fact(
        fact, "Максим", observable_context=context
    )
    assert cleaned is not None


@pytest.mark.asyncio
async def test_cross_location_memory_extraction_skip(
    db_session, chat, db_engine
):
    """Street kiss must not create memories for home characters."""
    oleg = crud.create_character(
        db_session,
        chat.id,
        schemas.CharacterCreate(name="Олег", location="home", order_index=1),
    )
    alina = crud.create_character(
        db_session,
        chat.id,
        schemas.CharacterCreate(name="Алина", location="home", order_index=2),
    )
    maxim = crud.create_character(
        db_session,
        chat.id,
        schemas.CharacterCreate(name="Максим", location="street", order_index=3),
    )
    katya = crud.create_character(
        db_session,
        chat.id,
        schemas.CharacterCreate(name="Катя", location="street", order_index=4),
    )
    characters = [oleg, alina, maxim, katya]
    names = {c.id: c.name for c in characters}

    user_msg = crud.create_message(
        db_session,
        schemas.MessageCreate(
            chat_id=chat.id,
            role="user",
            content="Что происходит?",
            location="street",
            visibility="local",
        ),
    )
    maxim_msg = crud.create_message(
        db_session,
        schemas.MessageCreate(
            chat_id=chat.id,
            role="character",
            character_id=maxim.id,
            content="Я поцеловал Катю.",
            location="street",
            visibility="local",
        ),
    )
    katya_msg = crud.create_message(
        db_session,
        schemas.MessageCreate(
            chat_id=chat.id,
            role="character",
            character_id=katya.id,
            content="*смущённо улыбается после поцелуя*",
            location="street",
            visibility="local",
        ),
    )
    # Home characters still "reply" but their messages stay at home
    oleg_msg = crud.create_message(
        db_session,
        schemas.MessageCreate(
            chat_id=chat.id,
            role="character",
            character_id=oleg.id,
            content="*читает книгу дома*",
            location="home",
            visibility="local",
        ),
    )
    alina_msg = crud.create_message(
        db_session,
        schemas.MessageCreate(
            chat_id=chat.id,
            role="character",
            character_id=alina.id,
            content="*наливает чай на кухне*",
            location="home",
            visibility="local",
        ),
    )

    round_messages = [user_msg, maxim_msg, katya_msg, oleg_msg, alina_msg]
    crud.compute_and_save_presence_for_round(
        db_session,
        round_messages,
        [c.id for c in characters],
        names,
        characters=characters,
    )

    captured: dict[str, str] = {}

    async def fake_extract(client, model, character, round_text):
        captured[character.name] = round_text
        if character.name == "Максим":
            return [
                schemas.ExtractedFact(
                    fact="Я поцеловал Катю на улице",
                    witnessed=True,
                    importance=0.9,
                    category="событие",
                )
            ]
        if character.name == "Катя":
            return [
                schemas.ExtractedFact(
                    fact="Максим поцеловал меня на улице",
                    witnessed=True,
                    importance=0.9,
                    category="событие",
                )
            ]
        # If home chars are wrongly called, try to inject leak
        return [
            schemas.ExtractedFact(
                fact="Максим поцеловал меня на улице",
                witnessed=True,
                importance=0.9,
                category="событие",
            )
        ]

    test_factory = sessionmaker(bind=db_engine)
    with patch(
        "app.memory_service.ollama_client.extract_memories_for_character",
        side_effect=fake_extract,
    ), patch("app.memory_service.SessionLocal", test_factory):
        await memory_service._extract_and_save_memories(
            httpx.AsyncClient(base_url="http://test"),
            chat.id,
            [_snap_msg(m) for m in round_messages],
            [_snap_char(c) for c in characters],
            chat.model_name,
        )

    # Extraction only for who perceived street events (and own home lines)
    assert "Максим" in captured
    assert "Катя" in captured
    assert "поцеловал" in captured["Максим"]
    assert "поцеловал" in captured["Катя"]
    # Home pair must not see the kiss in extraction context
    assert "поцеловал" not in captured.get("Олег", "")
    assert "поцеловал" not in captured.get("Алина", "")

    verify = test_factory()
    try:
        mem_m = crud.get_memories_by_character(verify, maxim.id)
        mem_k = crud.get_memories_by_character(verify, katya.id)
        mem_o = crud.get_memories_by_character(verify, oleg.id)
        mem_a = crud.get_memories_by_character(verify, alina.id)
        assert any("поцелов" in m.content.lower() for m in mem_m)
        assert any("поцелов" in m.content.lower() for m in mem_k)
        assert not any("поцелов" in m.content.lower() for m in mem_o)
        assert not any("поцелов" in m.content.lower() for m in mem_a)
    finally:
        verify.close()


@pytest.mark.asyncio
async def test_first_person_pronoun_no_leak_to_remote(
    db_session, chat, db_engine
):
    """«Я её поцеловал» must not become memory for remote Alina."""
    alina = crud.create_character(
        db_session,
        chat.id,
        schemas.CharacterCreate(name="Алина", location="home", order_index=1),
    )
    maxim = crud.create_character(
        db_session,
        chat.id,
        schemas.CharacterCreate(name="Максим", location="street", order_index=2),
    )
    katya = crud.create_character(
        db_session,
        chat.id,
        schemas.CharacterCreate(name="Катя", location="street", order_index=3),
    )
    characters = [alina, maxim, katya]
    names = {c.id: c.name for c in characters}

    maxim_msg = crud.create_message(
        db_session,
        schemas.MessageCreate(
            chat_id=chat.id,
            role="character",
            character_id=maxim.id,
            content="Я её поцеловал.",
            location="street",
        ),
    )
    katya_msg = crud.create_message(
        db_session,
        schemas.MessageCreate(
            chat_id=chat.id,
            role="character",
            character_id=katya.id,
            content="*молчит, глядя на Максима*",
            location="street",
        ),
    )
    round_messages = [maxim_msg, katya_msg]
    crud.compute_and_save_presence_for_round(
        db_session,
        round_messages,
        [c.id for c in characters],
        names,
        characters=characters,
    )

    called_for: list[str] = []

    async def fake_extract(client, model, character, round_text):
        called_for.append(character.name)
        return [
            schemas.ExtractedFact(
                fact="Максим поцеловал меня после прогулки",
                witnessed=True,
                importance=0.85,
                category="событие",
            )
        ]

    test_factory = sessionmaker(bind=db_engine)
    with patch(
        "app.memory_service.ollama_client.extract_memories_for_character",
        side_effect=fake_extract,
    ), patch("app.memory_service.SessionLocal", test_factory):
        await memory_service._extract_and_save_memories(
            httpx.AsyncClient(base_url="http://test"),
            chat.id,
            [_snap_msg(m) for m in round_messages],
            [_snap_char(c) for c in characters],
            chat.model_name,
        )

    assert "Алина" not in called_for
    verify = test_factory()
    try:
        assert crud.get_memories_by_character(verify, alina.id) == []
    finally:
        verify.close()


@pytest.mark.asyncio
async def test_information_transfer_after_telling(
    db_session, chat, db_engine
):
    """Alina learns only after Katya tells her in the same location."""
    alina = crud.create_character(
        db_session,
        chat.id,
        schemas.CharacterCreate(name="Алина", location="home", order_index=1),
    )
    maxim = crud.create_character(
        db_session,
        chat.id,
        schemas.CharacterCreate(name="Максим", location="street", order_index=2),
    )
    katya = crud.create_character(
        db_session,
        chat.id,
        schemas.CharacterCreate(name="Катя", location="street", order_index=3),
    )

    # Round 1: kiss on street — Alina absent
    kiss = crud.create_message(
        db_session,
        schemas.MessageCreate(
            chat_id=chat.id,
            role="character",
            character_id=maxim.id,
            content="Я поцеловал Катю.",
            location="street",
        ),
    )
    crud.compute_and_save_presence_for_message(
        db_session,
        kiss,
        [alina, maxim, katya],
        {alina.id: alina.name, maxim.id: maxim.name, katya.id: katya.name},
    )

    captured_r1: dict[str, str] = {}

    async def extract_r1(client, model, character, round_text):
        captured_r1[character.name] = round_text
        return []

    factory = sessionmaker(bind=db_engine)
    with patch(
        "app.memory_service.ollama_client.extract_memories_for_character",
        side_effect=extract_r1,
    ), patch("app.memory_service.SessionLocal", factory):
        await memory_service._extract_and_save_memories(
            httpx.AsyncClient(base_url="http://test"),
            chat.id,
            [_snap_msg(kiss), _snap_msg(kiss)],  # need len >= 2
            [_snap_char(c) for c in (alina, maxim, katya)],
            chat.model_name,
        )

    assert "поцеловал" not in captured_r1.get("Алина", "")

    # Round 2: Katya moves home and tells Alina
    crud.update_character(
        db_session, katya.id, schemas.CharacterUpdate(location="home")
    )
    katya = crud.get_character(db_session, katya.id)
    telling = crud.create_message(
        db_session,
        schemas.MessageCreate(
            chat_id=chat.id,
            role="character",
            character_id=katya.id,
            content="Алина, Максим меня поцеловал на улице.",
            location="home",
        ),
    )
    alina_reply = crud.create_message(
        db_session,
        schemas.MessageCreate(
            chat_id=chat.id,
            role="character",
            character_id=alina.id,
            content="*удивляется*",
            location="home",
        ),
    )
    characters = [alina, maxim, katya]
    crud.compute_and_save_presence_for_round(
        db_session,
        [telling, alina_reply],
        [c.id for c in characters],
        {c.id: c.name for c in characters},
        characters=characters,
    )

    captured_r2: dict[str, str] = {}

    async def extract_r2(client, model, character, round_text):
        captured_r2[character.name] = round_text
        if character.name == "Алина":
            return [
                schemas.ExtractedFact(
                    fact="Катя рассказала мне, что Максим её поцеловал",
                    witnessed=True,
                    importance=0.8,
                    category="событие",
                )
            ]
        return []

    with patch(
        "app.memory_service.ollama_client.extract_memories_for_character",
        side_effect=extract_r2,
    ), patch("app.memory_service.SessionLocal", factory):
        await memory_service._extract_and_save_memories(
            httpx.AsyncClient(base_url="http://test"),
            chat.id,
            [_snap_msg(telling), _snap_msg(alina_reply)],
            [_snap_char(c) for c in characters],
            chat.model_name,
        )

    assert "Алина" in captured_r2
    assert "поцеловал" in captured_r2["Алина"]
    # Maxim still on street — should not get home telling
    assert "рассказала" not in captured_r2.get("Максим", "")

    verify = factory()
    try:
        mem_a = crud.get_memories_by_character(verify, alina.id)
        assert any("Катя" in m.content and "поцелов" in m.content for m in mem_a)
    finally:
        verify.close()


@pytest.mark.asyncio
async def test_memory_isolation_between_characters(db_session, chat, db_engine):
    a, b = create_characters(db_session, chat.id, 2)
    crud.create_memory(
        db_session,
        schemas.MemoryCreate(
            chat_id=chat.id,
            character_id=a.id,
            content="Секретный факт только для Character A о ключе",
            importance=0.9,
        ),
    )
    crud.create_memory(
        db_session,
        schemas.MemoryCreate(
            chat_id=chat.id,
            character_id=b.id,
            content="Другой факт только для Character B о мече",
            importance=0.9,
        ),
    )
    mem_a = crud.get_memories_by_character(db_session, a.id)
    mem_b = crud.get_memories_by_character(db_session, b.id)
    assert all("Character B" not in m.content for m in mem_a)
    assert all("Character A" not in m.content for m in mem_b)
    assert {m.content for m in mem_a}.isdisjoint({m.content for m in mem_b})


def test_memory_retrieval_in_prompt_block(db_session, chat):
    a, b = create_characters(db_session, chat.id, 2)
    crud.create_memory(
        db_session,
        schemas.MemoryCreate(
            chat_id=chat.id,
            character_id=a.id,
            content="Игрок отдал Alice серебряный ключ от склада",
            importance=0.9,
            category="предмет",
        ),
    )
    mem_a = crud.get_memories_by_character(db_session, a.id)
    mem_b = crud.get_memories_by_character(db_session, b.id)
    block_a = prompt_builder.build_memories_block(mem_a)
    block_b = prompt_builder.build_memories_block(mem_b)
    assert "серебряный ключ" in block_a
    assert block_b == ""
    assert "серебряный ключ" not in block_b


@pytest.mark.asyncio
async def test_bad_llm_fact_for_non_witness_grounding(
    db_session, chat, db_engine
):
    """Even if LLM is called, ungrounded leak facts are dropped."""
    alina = crud.create_character(
        db_session,
        chat.id,
        schemas.CharacterCreate(name="Алина", location="home", order_index=1),
    )
    # Only home-local chatter — no kiss in observable text
    m1 = crud.create_message(
        db_session,
        schemas.MessageCreate(
            chat_id=chat.id,
            role="user",
            content="Как дела дома?",
            location="home",
        ),
    )
    m2 = crud.create_message(
        db_session,
        schemas.MessageCreate(
            chat_id=chat.id,
            role="character",
            character_id=alina.id,
            content="Всё спокойно, пью чай на кухне.",
            location="home",
        ),
    )
    crud.compute_and_save_presence_for_message(
        db_session, m1, [alina], {alina.id: alina.name}
    )
    crud.compute_and_save_presence_for_message(
        db_session, m2, [alina], {alina.id: alina.name}
    )

    async def fake_extract(client, model, character, round_text):
        return [
            schemas.ExtractedFact(
                fact="Максим поцеловал меня на улице при всех",
                witnessed=True,
                importance=0.99,
                category="событие",
            )
        ]

    factory = sessionmaker(bind=db_engine)
    with patch(
        "app.memory_service.ollama_client.extract_memories_for_character",
        side_effect=fake_extract,
    ), patch("app.memory_service.SessionLocal", factory):
        await memory_service._extract_and_save_memories(
            httpx.AsyncClient(base_url="http://test"),
            chat.id,
            [_snap_msg(m1), _snap_msg(m2)],
            [_snap_char(alina)],
            chat.model_name,
        )

    verify = factory()
    try:
        assert crud.get_memories_by_character(verify, alina.id) == []
    finally:
        verify.close()


@pytest.mark.asyncio
async def test_memory_attribution_speaker_preserved_same_room(
    db_session, chat, db_engine
):
    """TEST 8 (§20): Boris hears Anna's line in the same room, but the fact
    belongs to Anna — the speaker prefix is preserved in his extraction context,
    so attribution is not lost. Availability of an event ≠ ownership of a fact."""
    anna = await crud.create_character(
        db_session,
        chat.id,
        schemas.CharacterCreate(name="Анна", location="гостиная", order_index=1),
    )
    boris = await crud.create_character(
        db_session,
        chat.id,
        schemas.CharacterCreate(name="Борис", location="гостиная", order_index=2),
    )
    characters = [anna, boris]
    names = {c.id: c.name for c in characters}

    user_msg = await crud.create_message(
        db_session,
        schemas.MessageCreate(
            chat_id=chat.id,
            role="user",
            content="Всем привет!",
            location="гостиная",
        ),
    )
    anna_msg = await crud.create_message(
        db_session,
        schemas.MessageCreate(
            chat_id=chat.id,
            role="character",
            character_id=anna.id,
            content="Я ненавижу кофе.",
            location="гостиная",
        ),
    )
    round_messages = [user_msg, anna_msg]
    await crud.compute_and_save_presence_for_round(
        db_session,
        round_messages,
        [c.id for c in characters],
        names,
        characters=characters,
    )

    captured: dict[str, str] = {}

    async def fake_extract(client, model, character, round_text):
        captured[character.name] = round_text
        # Boris heard it and correctly attributes the dislike to Anna.
        if character.name == "Борис":
            return [
                schemas.ExtractedFact(
                    fact="Анна не любит кофе.",
                    witnessed=True,
                    importance=0.6,
                    category="предмет",
                )
            ]
        return [
            schemas.ExtractedFact(
                fact="Я не люблю кофе.",
                witnessed=True,
                importance=0.6,
                category="предмет",
            )
        ]

    test_factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    with patch(
        "app.memory_service.ollama_client.extract_memories_for_character",
        side_effect=fake_extract,
    ), patch("app.memory_service.AsyncSessionLocal", test_factory):
        await memory_service._extract_and_save_memories(
            httpx.AsyncClient(base_url="http://test"),
            chat.id,
            [_snap_msg(m) for m in round_messages],
            [_snap_char(c) for c in characters],
            chat.model_name,
        )

    # Boris heard the line and the speaker is preserved in his context.
    assert "Анна: Я ненавижу кофе." in captured["Борис"]
    # Anna also sees her own line.
    assert "Я ненавижу кофе." in captured["Анна"]

    verify = test_factory()
    try:
        anna_mem = await crud.get_memories_by_character(verify, anna.id)
        boris_mem = await crud.get_memories_by_character(verify, boris.id)
        # Anna owns the dislike; Boris's fact explicitly names Anna.
        assert any("кофе" in m.content for m in anna_mem)
        assert any("Анна не любит кофе" in m.content for m in boris_mem)
    finally:
        await verify.close()

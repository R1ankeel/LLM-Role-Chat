"""Integration tests for sequential multi-character chat generation."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import httpx
import pytest
from sqlalchemy.orm import sessionmaker

from app import chat_engine
from app import crud
from app import memory_service
from app import schemas
from app.config import settings
from app.schemas import RelationshipDelta
from tests.conftest import create_characters


async def _run_in_current_thread(func, /, *args, **kwargs):
    return func(*args, **kwargs)


@pytest.fixture
def mock_client():
    return httpx.AsyncClient(base_url="http://test")


@pytest.mark.asyncio
async def test_sequential_generation_order_and_context(
    db_session, chat, mock_client
):
    """TEST 5: characters generate in order_index order and see prior replies."""
    characters = await create_characters(db_session, chat.id, 3)
    call_log: list[dict] = []

    async def fake_generate(**kwargs):
        character = kwargs["character"]
        history = kwargs["messages_history"]
        character_messages = [
            m for m in history if m.role == "character"
        ]
        call_log.append(
            {
                "name": character.name,
                "order_index": character.order_index,
                "character_message_count": len(character_messages),
                "character_names_in_history": [
                    m.character_id for m in character_messages
                ],
                "viewer_character_id": kwargs.get("viewer_character_id"),
            }
        )
        yield {
            "type": "response",
            "text": f"Reply from {character.name} with enough text for validation.",
        }

    with patch(
        "app.chat_engine.ollama_client.generate",
        side_effect=fake_generate,
    ), patch("app.chat_engine.asyncio.create_task"), patch(
        "app.chat_engine.asyncio.to_thread",
        side_effect=_run_in_current_thread,
    ):
        messages = []
        async for event in chat_engine.process_user_message_streaming(
            mock_client,
            db_session,
            chat.id,
            "Hello everyone",
        ):
            if event.get("type") == "message":
                messages.append(event["message"])

    assert len(messages) == 4  # user + 3 characters
    assert [entry["name"] for entry in call_log] == [
        "Character A",
        "Character B",
        "Character C",
    ]
    assert call_log[0]["character_message_count"] == 0
    assert call_log[1]["character_message_count"] == 1
    assert call_log[2]["character_message_count"] == 2
    assert call_log[0]["viewer_character_id"] == characters[0].id
    assert call_log[1]["viewer_character_id"] == characters[1].id
    assert call_log[2]["viewer_character_id"] == characters[2].id

    saved = await crud.get_messages_by_chat(db_session, chat.id)
    character_messages = [m for m in saved if m.role == "character"]
    assert [m.character_id for m in character_messages] == [
        characters[0].id,
        characters[1].id,
        characters[2].id,
    ]


@pytest.mark.asyncio
async def test_memory_isolation_per_character(db_session, chat, mock_client):
    """TEST 8: each character receives only its own memories in generate()."""
    characters = await create_characters(db_session, chat.id, 3)
    memory_map = {}
    for character, content in zip(
        characters,
        [
            "Memory unique to Character A",
            "Memory unique to Character B",
            "Memory unique to Character C",
        ],
    ):
        await crud.create_memory(
            db_session,
            schemas.MemoryCreate(
                chat_id=chat.id,
                character_id=character.id,
                content=content,
            ),
        )
        memory_map[character.id] = content

    await crud.upsert_character_summary(
        db_session,
        chat.id,
        characters[0].id,
        "Summary only for Character A",
        through_message_id=0,
    )
    await crud.upsert_character_summary(
        db_session,
        chat.id,
        characters[1].id,
        "Summary only for Character B",
        through_message_id=0,
    )

    captured_memories: dict[str, list[str]] = {}
    captured_summaries: dict[str, str | None] = {}

    async def fake_generate(**kwargs):
        character = kwargs["character"]
        memories = kwargs["memories"]
        captured_memories[character.name] = [m.content for m in memories]
        captured_summaries[character.name] = kwargs.get("summary")
        yield {
            "type": "response",
            "text": f"Reply from {character.name} with enough text for validation.",
        }

    with patch(
        "app.chat_engine.ollama_client.generate",
        side_effect=fake_generate,
    ), patch("app.chat_engine.asyncio.create_task"), patch(
        "app.chat_engine.asyncio.to_thread",
        side_effect=_run_in_current_thread,
    ):
        async for _ in chat_engine.process_user_message_streaming(
            mock_client,
            db_session,
            chat.id,
            "Trigger generation",
        ):
            pass

    for index, character in enumerate(characters):
        label = chr(ord("A") + index)
        contents = captured_memories[f"Character {label}"]
        assert contents == [memory_map[character.id]]
        for other in characters:
            if other.id != character.id:
                assert memory_map[other.id] not in contents

    assert captured_summaries["Character A"] == "Summary only for Character A"
    assert captured_summaries["Character B"] == "Summary only for Character B"
    assert captured_summaries["Character C"] is None


@pytest.mark.asyncio
async def test_per_character_memory_extraction_called(
    db_session, chat, mock_client, db_engine
):
    """Verify per-character memory extraction runs via background task with snapshots."""
    await create_characters(db_session, chat.id, 2)
    background_tasks: list = []

    extracted_calls = []

    async def fake_extract_for_character(client, model, character, text):
        extracted_calls.append(character.name)
        return [f"Fact for {character.name}"]

    real_create_task = asyncio.create_task

    def schedule_task(coro):
        task = real_create_task(coro)
        background_tasks.append(task)
        return task

    from sqlalchemy.ext.asyncio import async_sessionmaker
    test_session_factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)

    async def fake_generate(**kwargs):
        yield {"type": "response", "text": "Valid reply here."}

    with patch(
        "app.memory_service.ollama_client.extract_memories_for_character",
        side_effect=fake_extract_for_character,
    ), patch("app.chat_engine.asyncio.create_task", side_effect=schedule_task), patch(
        "app.chat_engine.asyncio.to_thread", side_effect=_run_in_current_thread
    ), patch(
        "app.chat_engine.ollama_client.generate",
        side_effect=fake_generate,
    ), patch.object(settings, "task_queue_enabled", False), patch(
        "app.chat_engine.AsyncSessionLocal", test_session_factory
    ), patch(
        "app.memory_service.AsyncSessionLocal", test_session_factory
    ):
        async for _ in chat_engine.process_user_message_streaming(
            mock_client, db_session, chat.id, "Test message"
        ):
            pass

        await asyncio.gather(*background_tasks)

    assert len(extracted_calls) == 2
    assert "Character A" in extracted_calls
    assert "Character B" in extracted_calls


@pytest.mark.asyncio
async def test_memory_extraction_with_snapshots_after_session_closed(
    db_session, chat, mock_client, db_engine
):
    """Regression: background memory extraction must not touch detached ORM objects."""
    characters = await create_characters(db_session, chat.id, 2)
    character_snapshots = [
        chat_engine._character_to_snapshot(c) for c in characters
    ]
    round_snapshots = [
        {
            "role": "user",
            "content": "Hello everyone",
            "character_id": None,
        },
        {
            "role": "character",
            "content": "Reply from Character A with enough text.",
            "character_id": characters[0].id,
        },
        {
            "role": "character",
            "content": "Reply from Character B with enough text.",
            "character_id": characters[1].id,
        },
    ]
    chat_id = chat.id
    model_name = chat.model_name

    db_session.close()

    from sqlalchemy.ext.asyncio import async_sessionmaker
    test_session_factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)

    async def fake_extract_for_character(client, model, character, text):
        return [f"Fact for {character.name}"]

    with patch(
        "app.memory_service.ollama_client.extract_memories_for_character",
        side_effect=fake_extract_for_character,
    ), patch("app.memory_service.AsyncSessionLocal", test_session_factory):
        await memory_service._extract_and_save_memories(
            mock_client,
            chat_id,
            round_snapshots,
            character_snapshots,
            model_name,
        )

    verify_session = test_session_factory()
    try:
        for snapshot in character_snapshots:
            memories = await crud.get_memories_by_character(
                verify_session, snapshot["id"]
            )
            assert len(memories) == 1
            assert memories[0].content == f"Fact for {snapshot['name']}"
    finally:
        await verify_session.close()


@pytest.mark.asyncio
async def test_round_id_anchored_on_user_message(db_session, chat, mock_client):
    """Sprint 1 item 9: round_id is r{chat_id}-m{user_message_id}, never utcnow()."""
    await create_characters(db_session, chat.id, 2)
    captured: dict = {}

    def fake_analyze(
        client, chat_id, model_name, round_snapshots, character_snapshots,
        round_id=None,
    ):
        captured["round_id"] = round_id
        captured["user_message_id"] = next(
            m["id"] for m in round_snapshots if m.get("role") == "user"
        )
        return None

    async def fake_generate(**kwargs):
        yield {"type": "response", "text": "Valid reply here."}

    with patch(
        "app.chat_engine._analyze_and_update_relationships",
        new=MagicMock(side_effect=fake_analyze),
    ), patch("app.chat_engine.asyncio.create_task"), patch(
        "app.chat_engine.asyncio.to_thread", side_effect=_run_in_current_thread
    ), patch(
        "app.chat_engine.ollama_client.generate", side_effect=fake_generate
    ):
        async for _ in chat_engine.process_user_message_streaming(
            mock_client, db_session, chat.id, "Test message"
        ):
            pass

    assert captured["round_id"] == f"r{chat.id}-m{captured['user_message_id']}"


@pytest.mark.asyncio
async def test_epistemic_mask_built_and_passed_to_generate(db_session, chat, mock_client):
    """Sprint 2 item 10: epistemic mask block is computed per character and passed
    to generate() (docs/relations.md §10)."""
    await create_characters(db_session, chat.id, 2)
    captured: dict = {"epistemic_calls": [], "generate_blocks": []}

    async def fake_build_epistemic(db, chat_id, character_id, character_name, all_characters, evidenced_target_ids=(), max_edges=None):
        captured["epistemic_calls"].append(
            {
                "character_id": character_id,
                "evidenced_target_ids": sorted(evidenced_target_ids or ()),
            }
        )
        return f"<epistemic_mask>block-{character_id}</epistemic_mask>"

    async def fake_generate(**kwargs):
        captured["generate_blocks"].append(kwargs.get("epistemic_mask_block", ""))
        yield {"type": "response", "text": "Valid reply here."}

    with patch(
        "app.chat_engine._analyze_and_update_relationships",
        new=MagicMock(return_value=None),
    ), patch(
        "app.chat_engine.relationship_service.build_epistemic_mask_block",
        side_effect=fake_build_epistemic,
    ), patch(
        "app.chat_engine.asyncio.create_task"
    ), patch(
        "app.chat_engine.asyncio.to_thread", side_effect=_run_in_current_thread
    ), patch(
        "app.chat_engine.ollama_client.generate", side_effect=fake_generate
    ):
        async for _ in chat_engine.process_user_message_streaming(
            mock_client, db_session, chat.id, "Привет всем"
        ):
            pass

    assert len(captured["epistemic_calls"]) == 2
    assert len(captured["generate_blocks"]) == 2
    for block in captured["generate_blocks"]:
        assert block.startswith("<epistemic_mask>block-")
        assert block.endswith("</epistemic_mask>")


@pytest.mark.asyncio
async def test_epistemic_evidence_detects_direct_interaction(db_session, chat, mock_client):
    """Sprint 2 item 10: with a direct address to B, A's epistemic evidence includes B."""
    await create_characters(db_session, chat.id, 2)
    await crud.create_player_character(db_session, chat.id, "Игрок")
    characters = await crud.get_characters_by_chat(db_session, chat.id)
    a, b = characters

    captured: dict = {}

    async def fake_build_epistemic(db, chat_id, character_id, character_name, all_characters, evidenced_target_ids=(), max_edges=None):
        captured[character_id] = sorted(evidenced_target_ids or ())
        return ""

    async def fake_generate(**kwargs):
        yield {"type": "response", "text": "Valid reply here."}

    with patch(
        "app.chat_engine._analyze_and_update_relationships",
        new=MagicMock(return_value=None),
    ), patch(
        "app.chat_engine.relationship_service.build_epistemic_mask_block",
        side_effect=fake_build_epistemic,
    ), patch(
        "app.chat_engine.asyncio.create_task"
    ), patch(
        "app.chat_engine.asyncio.to_thread", side_effect=_run_in_current_thread
    ), patch(
        "app.chat_engine.ollama_client.generate", side_effect=fake_generate
    ):
        async for _ in chat_engine.process_user_message_streaming(
            mock_client, db_session, chat.id,
            f"Слушай, {b.name}, я хочу тебе кое-что сказать",
            target_character_ids=[b.id],
        ):
            pass

    # A directly addressed B -> A has evidence of B's behavior this round.
    assert b.id in captured[a.id]


@pytest.mark.asyncio
async def test_compute_is_isolated_engine_applied(db_session, chat, mock_client):
    """TEST 5: NPC with a peer in the same location is not isolated even when the
    player is elsewhere; an NPC alone in its location is isolated."""
    await crud.update_chat(
        db_session, chat.id, schemas.ChatUpdate(player_location="kitchen")
    )
    a = await crud.create_character(
        db_session, chat.id,
        schemas.CharacterCreate(name="Anna", location="living_room", order_index=1),
    )
    b = await crud.create_character(
        db_session, chat.id,
        schemas.CharacterCreate(name="Boris", location="living_room", order_index=2),
    )
    c = await crud.create_character(
        db_session, chat.id,
        schemas.CharacterCreate(name="Cid", location="bedroom", order_index=3),
    )

    captured_isolated: dict[str, bool] = {}

    async def fake_generate(**kwargs):
        character = kwargs["character"]
        captured_isolated[character.name] = kwargs.get("is_isolated", False)
        yield {
            "type": "response",
            "text": f"Reply from {character.name} with enough text for validation.",
        }

    with patch(
        "app.chat_engine.ollama_client.generate",
        side_effect=fake_generate,
    ), patch("app.chat_engine.asyncio.create_task"), patch(
        "app.chat_engine.asyncio.to_thread",
        side_effect=_run_in_current_thread,
    ):
        async for _ in chat_engine.process_user_message_streaming(
            mock_client, db_session, chat.id, "Hello everyone",
        ):
            pass

    assert captured_isolated["Anna"] is False  # Boris nearby
    assert captured_isolated["Boris"] is False  # Anna nearby
    assert captured_isolated["Cid"] is True  # alone in bedroom

    assert set(captured_isolated) == {"Anna", "Boris", "Cid"}


@pytest.mark.asyncio
async def test_batch_failure_falls_back_to_per_pair(db_engine, mock_client):
    """Sprint 1 item 8: batch failure -> per-pair fallback; gating still applies."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.relationship_analyzer import BatchAnalysisError
    from app.relationship_service import get_relationship

    factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    async with factory() as db:
        chat = await crud.create_chat(db, schemas.ChatCreate(name="Rel Chat"))
        chars = []
        for i, name in enumerate(["A", "B"], start=1):
            chars.append(
                await crud.create_character(
                    db, chat.id,
                    schemas.CharacterCreate(name=name, personality="x", order_index=i),
                )
            )
        a, b = chars
        await db.commit()

    round_snapshots = [
        {
            "id": 1, "role": "user", "character_id": None,
            "content": "A, как ты относишься к B?",
            "location": "hall", "visibility": "local", "channel": "direct",
            "target_character_ids": [a.id],
        },
        {
            "id": 2, "role": "character", "character_id": a.id,
            "content": "B мне нравится",
            "location": "hall", "visibility": "local", "channel": "direct",
            "target_character_ids": [b.id],
        },
    ]
    character_snapshots = [
        {"id": c.id, "name": c.name, "location": "hall"} for c in (a, b)
    ]

    batch_calls: list = []
    per_pair_calls: list = []

    async def fake_batch(client, model_name, scene_text, pairs, known_pairs):
        batch_calls.append(True)
        raise BatchAnalysisError("boom")

    async def fake_per_pair(client, model_name, **kwargs):
        per_pair_calls.append(kwargs)
        return [
            RelationshipDelta(
                source_character_id=kwargs["source_character_id"],
                target_character_id=kwargs["target_character_id"],
                delta_trust=-5,
                importance=6,
            )
        ]

    with patch("app.chat_engine.AsyncSessionLocal", factory), patch(
        "app.chat_engine.relationship_analyzer.analyze_batch_relationships",
        side_effect=fake_batch,
    ), patch(
        "app.chat_engine.relationship_analyzer.analyze_relationships",
        side_effect=fake_per_pair,
    ):
        await chat_engine._analyze_and_update_relationships(
            mock_client, chat.id, "model-x",
            round_snapshots, character_snapshots,
            round_id=f"r{chat.id}-m1",
        )

    assert batch_calls == [True]
    assert len(per_pair_calls) == 2  # (A,B) and (B,A)

    verify = factory()
    try:
        rel_ab = await get_relationship(verify, a.id, b.id)
        assert rel_ab is not None
        assert rel_ab.trust == 45
    finally:
        await verify.close()

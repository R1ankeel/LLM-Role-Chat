"""WPE 3.0 (Plans/WPE.md) Фаза 6 — Threads/мессенджер + частичное восприятие.

Покрывает:
- Golden #6 (сообщение в мессенджер → ``remote_status=delivered`` независимо
  от локации): ``Thread``/``ThreadParticipantState`` в проде через
  ``create_message``; ``thread_delivery_ids_for_message``; ``perceive()`` отдаёт
  full/full + delivered адресату удалённого канала;
- Golden #15 (групповой тред): участники = автор + адресаты, доставка —
  только адресатам;
- ``apply_character_actions``: ``send_message`` по удалённому каналу создаёт
  тред и участников (источник — tools, И14);
- Golden #17 (стекло): ребро ``visual=full/audio=none`` → full/none, рендер
  без утечки текста (И11);
- Golden #18 (крик из-за стены): сосед + ``loud_sound`` → none/full, атрибуция
  только знакомому голосу (voice familiarity);
- Golden #19 (невидимость): событие в одной локации со стимулом ``invisible``
  → none/full при включённом partial-флаге, full/full при выключенном (откат);
- Voice familiarity: ``voice_familiarity`` + ``perceive_presence_for_character``
  с ``voice_known`` из отношений;
- Renderer в ``ContextBuilder`` (И11): канало-зависимая строка вместо
  legacy-лестницы при включённых обоих флагах, без утечки семантики.

Флаги по умолчанию выключены (инвариант Фазы 0) — каждый тест включает
нужные отдельным ``monkeypatch`` (раздельные откаты).
"""

from __future__ import annotations

import pytest

import app.ollama_client as ollama_client
from app import chat_engine
from app import crud
from app import models
from app import perception
from app import post_round_pipeline
from app import schemas
from app import witness_model
from app.config import settings
from app.context_builder import ContextBuilder

# Защита от известной утечки мока (test_stream_disconnect) — как в Фазе 2–5.
_REAL_GENERATE = ollama_client.generate


@pytest.fixture(autouse=True)
def _reset_wpe_state(monkeypatch):
    monkeypatch.setattr(settings, "world_engine_events_enabled", False)
    monkeypatch.setattr(settings, "world_engine_perception_enabled", False)
    monkeypatch.setattr(settings, "world_engine_recency_tail_enabled", False)
    monkeypatch.setattr(settings, "world_engine_threads_enabled", False)
    monkeypatch.setattr(settings, "world_engine_partial_perception_enabled", False)
    monkeypatch.setattr(settings, "world_engine_actions_enabled", False)
    ollama_client.generate = _REAL_GENERATE
    yield


def _res(visual="none", audio="none", addressed=False, remote="none"):
    return schemas.PerceptionResult(
        visual_level=visual,
        audio_level=audio,
        addressed=addressed,
        remote_status=remote,
    )


async def _create_message(
    db,
    chat_id: int,
    *,
    character_id=None,
    role="character",
    content="hi",
    location="",
    targets=(),
    visibility="local",
    channel="direct",
    stimuli=(),
):
    return await crud.create_message(
        db,
        schemas.MessageCreate(
            chat_id=chat_id,
            character_id=character_id,
            role=role,
            content=content,
            visibility=visibility,
            location=location,
            target_character_ids=list(targets),
            channel=channel,
            stimuli=[s.to_dict() if hasattr(s, "to_dict") else s for s in stimuli],
        ),
    )


async def _create_location(
    db,
    chat_id: int,
    name: str,
    *,
    edges=(),
):
    """Создать локацию с рёбрами проницаемости (объектная форма adjacent_to)."""
    loc = models.Location(
        chat_id=chat_id,
        name=name,
        description="",
        adjacent_to=perception.serialize_adjacency_edges(list(edges)),
    )
    db.add(loc)
    await db.flush()
    await db.commit()
    await db.refresh(loc)
    return loc


async def _setup_world(db, chat, characters, *, locations=(), plain_locations=()):
    """Локации + перемещение персонажей + backfill location_id (Фаза 1)."""
    created = {}
    for name, edges in locations:
        created[name] = await _create_location(db, chat.id, name, edges=edges)
    for name in plain_locations:
        created[name] = await _create_location(db, chat.id, name)
    for cid, loc_name in characters:
        await crud.update_character_location(db, cid, loc_name)
    await crud.backfill_character_location_ids(db, chat.id)
    return created


def _names(characters):
    return {c.id: c.name for c in characters}


# ---------------------------------------------------------------------------
# Golden #6/#15: Threads / мессенджер (WORLD_ENGINE_THREADS_ENABLED)
# ---------------------------------------------------------------------------

async def test_thread_created_on_messenger_message(db_session, chat, three_characters, monkeypatch):
    monkeypatch.setattr(settings, "world_engine_threads_enabled", True)
    a, b, c = three_characters
    msg = await _create_message(
        db_session,
        chat.id,
        character_id=a.id,
        content="секрет в чате",
        targets=(b.id,),
        channel="messenger",
    )
    stmt = (
        __import__("sqlalchemy").select(models.Thread)
        .where(models.Thread.chat_id == chat.id)
    )
    threads = list((await db_session.execute(stmt)).scalars().all())
    assert len(threads) == 1
    thread = threads[0]
    assert thread.channel == "messenger"
    assert msg.id is not None


async def test_group_thread_participants_and_delivery(db_session, chat, three_characters, monkeypatch):
    """Golden #15: групповой тред — участники автор+адресаты, доставка только адресатам."""
    monkeypatch.setattr(settings, "world_engine_threads_enabled", True)
    a, b, c = three_characters
    await _create_message(
        db_session,
        chat.id,
        character_id=a.id,
        content="всем привет",
        targets=(b.id, c.id),
        channel="messenger",
    )
    stmt = (
        __import__("sqlalchemy").select(models.Thread)
        .where(models.Thread.chat_id == chat.id)
    )
    thread = (await db_session.execute(stmt)).scalars().first()
    assert thread is not None
    stmt = __import__("sqlalchemy").select(models.ThreadParticipantState).where(
        models.ThreadParticipantState.thread_id == thread.id
    )
    participants = {
        p.character_id: p.last_delivered_message_id
        for p in (await db_session.execute(stmt)).scalars().all()
    }
    # участники: автор + адресаты
    assert set(participants) == {a.id, b.id, c.id}
    # доставлено — только адресатам
    assert participants[b.id] is not None
    assert participants[c.id] is not None
    assert participants[a.id] is None


async def test_thread_delivery_ids_for_message(db_session, chat, three_characters, monkeypatch):
    monkeypatch.setattr(settings, "world_engine_threads_enabled", True)
    a, b, c = three_characters
    msg = await _create_message(
        db_session,
        chat.id,
        character_id=a.id,
        content="сообщение",
        targets=(b.id,),
        channel="messenger",
    )
    deliveries = await crud.thread_delivery_ids_for_message(db_session, msg)
    assert deliveries == frozenset({b.id})
    # direct-канал — не тред
    direct = await _create_message(
        db_session, chat.id, character_id=a.id, content="вслух"
    )
    assert await crud.thread_delivery_ids_for_message(db_session, direct) == frozenset()


async def test_threads_flag_off_no_thread(db_session, chat, three_characters, monkeypatch):
    monkeypatch.setattr(settings, "world_engine_threads_enabled", False)
    a, b, _c = three_characters
    await _create_message(
        db_session,
        chat.id,
        character_id=a.id,
        content="секрет",
        targets=(b.id,),
        channel="messenger",
    )
    stmt = (
        __import__("sqlalchemy").select(models.Thread)
        .where(models.Thread.chat_id == chat.id)
    )
    threads = list((await db_session.execute(stmt)).scalars().all())
    assert threads == []


async def test_perceive_remote_delivery_independent_of_location(db_session, chat, three_characters, monkeypatch):
    """Golden #6: адресат удалённого канала видит событие независимо от локации."""
    monkeypatch.setattr(settings, "world_engine_perception_enabled", True)
    monkeypatch.setattr(settings, "world_engine_threads_enabled", True)
    a, b, c = three_characters
    await _setup_world(
        db_session,
        chat,
        [(a.id, "Кухня"), (b.id, "Спальня"), (c.id, "Гостиная")],
        plain_locations=["Кухня", "Спальня", "Гостиная"],
    )
    msg = await _create_message(
        db_session,
        chat.id,
        character_id=a.id,
        content="секрет по мессенджеру",
        location="Кухня",
        targets=(b.id,),
        channel="messenger",
    )
    result = await post_round_pipeline.compute_and_save_presence_for_message(
        db_session, msg, [a, b, c], _names(three_characters)
    )
    # адресат доставлен → present, хотя в другой локации
    assert result[b.id] == "present"
    # не-адресат в дальней локации (И11) → absent
    assert result[c.id] == "absent"


async def test_apply_character_actions_send_message_creates_thread(db_session, chat, three_characters, monkeypatch):
    """send_message (tools, И14) по удалённому каналу создаёт тред + участников."""
    monkeypatch.setattr(settings, "world_engine_threads_enabled", True)
    a, b, c = three_characters
    turn = schemas.TurnOutput(
        actions=[
            schemas.Action(
                type="send_message",
                channel="messenger",
                target_character_ids=[b.id, c.id],
                message="всем",
            )
        ]
    )
    applied = await crud.apply_character_actions(
        db_session, chat.id, a, turn, round_id="r1"
    )
    assert len(applied.applied_messages) == 1
    assert applied.applied_messages[0]["channel"] == "messenger"
    stmt = (
        __import__("sqlalchemy").select(models.Thread)
        .where(models.Thread.chat_id == chat.id)
    )
    thread = (await db_session.execute(stmt)).scalars().first()
    assert thread is not None
    assert thread.channel == "messenger"
    stmt = __import__("sqlalchemy").select(models.ThreadParticipantState).where(
        models.ThreadParticipantState.thread_id == thread.id
    )
    participants = {
        p.character_id for p in (await db_session.execute(stmt)).scalars().all()
    }
    assert participants == {a.id, b.id, c.id}


# ---------------------------------------------------------------------------
# Golden #17/#18/#19: двухканальное частичное восприятие
# ---------------------------------------------------------------------------

async def test_perceive_glass_edge(db_session, chat, three_characters, monkeypatch):
    """Golden #17 (стекло): визуально full, аудио none."""
    monkeypatch.setattr(settings, "world_engine_perception_enabled", True)
    a, b, _c = three_characters
    await _setup_world(
        db_session,
        chat,
        [(a.id, "Комната А"), (b.id, "Комната Б")],
        locations=[
            (
                "Комната Б",
                [
                    {
                        "name": "Комната А",
                        "visual_permeability": "full",
                        "audio_permeability": "none",
                    }
                ],
            )
        ],
    )
    msg = await _create_message(
        db_session,
        chat.id,
        character_id=b.id,
        content="секрет за стеклом",
        location="Комната Б",
    )
    result = await post_round_pipeline.compute_and_save_presence_for_message(
        db_session, msg, [a, b], _names(three_characters)
    )
    # стекло: действия видны → present
    assert result[a.id] == "present"
    # рендер не даёт текст (И11)
    line = witness_model.render_perception_line(
        msg,
        perception.perceive(
            world_state=perception.PerceptionWorldState(
                adjacency=perception.build_permeability_index(
                    await crud.get_chat_locations(db_session, chat.id)
                )
            ),
            event=perception.event_from_message(msg),
            observer={
                "character_id": a.id,
                "location": "Комната А",
                "location_id": a.location_id,
            },
        ),
        _names(three_characters),
    )
    assert line == "[Что-то происходит за стеклом: слов не слышно]"
    assert "секрет" not in line


async def test_perceive_scream_through_wall(db_session, chat, three_characters, monkeypatch):
    """Golden #18 (крик из-за стены): loud_sound поднимает muffled → full."""
    monkeypatch.setattr(settings, "world_engine_perception_enabled", True)
    a, b, _c = three_characters
    await _setup_world(
        db_session,
        chat,
        [(a.id, "Комната А"), (b.id, "Комната Б")],
        locations=[("Комната Б", [{"name": "Комната А"}])],
    )
    msg = await _create_message(
        db_session,
        chat.id,
        character_id=b.id,
        content="ПОМОГИТЕ!",
        location="Комната Б",
        stimuli=[{"type": "loud_sound", "audibility": "high"}],
    )
    result = await post_round_pipeline.compute_and_save_presence_for_message(
        db_session, msg, [a, b], _names(three_characters)
    )
    # без отношений (known_voices не построен — флаг partial off) голос знаком
    # по умолчанию → mentioned
    assert result[a.id] in ("mentioned", "audible")
    # рендер: знакомый голос → «голос <имя>», незнакомый → «чей-то голос»
    known = witness_model.render_perception_line(
        msg,
        _res("none", "full"),
        _names(three_characters),
        voice_known=True,
    )
    assert "голос" in known and "Character B" in known
    unknown = witness_model.render_perception_line(
        msg,
        _res("none", "full"),
        _names(three_characters),
        voice_known=False,
    )
    assert unknown == "[Чей-то голос: ПОМОГИТЕ!]"


async def test_perceive_invisible_same_location(db_session, chat, three_characters, monkeypatch):
    """Golden #19 (невидимость): partial on → visual=none/audio=full."""
    monkeypatch.setattr(settings, "world_engine_perception_enabled", True)
    monkeypatch.setattr(settings, "world_engine_partial_perception_enabled", True)
    a, b, _c = three_characters
    await _setup_world(
        db_session,
        chat,
        [(a.id, "Кухня"), (b.id, "Кухня")],
        plain_locations=["Кухня"],
    )
    msg = await _create_message(
        db_session,
        chat.id,
        character_id=a.id,
        content="я невидим",
        location="Кухня",
        stimuli=[{"type": "invisible"}],
    )
    result = await post_round_pipeline.compute_and_save_presence_for_message(
        db_session, msg, [a, b], _names(three_characters)
    )
    # слышно (audio full), но не видно → не present
    assert result[a.id] == "present"  # автор всегда present
    assert result[b.id] != "present"


async def test_perceive_invisible_flag_off_full(db_session, chat, three_characters, monkeypatch):
    """Откат: partial off → невидимость игнорируется, одна локация = full/full."""
    monkeypatch.setattr(settings, "world_engine_perception_enabled", True)
    monkeypatch.setattr(settings, "world_engine_partial_perception_enabled", False)
    a, b, _c = three_characters
    await _setup_world(
        db_session,
        chat,
        [(a.id, "Кухня"), (b.id, "Кухня")],
        plain_locations=["Кухня"],
    )
    msg = await _create_message(
        db_session,
        chat.id,
        character_id=a.id,
        content="я невидим",
        location="Кухня",
        stimuli=[{"type": "invisible"}],
    )
    result = await post_round_pipeline.compute_and_save_presence_for_message(
        db_session, msg, [a, b], _names(three_characters)
    )
    assert result[b.id] == "present"


async def test_audio_only_muffled_no_attribution():
    """audio=muffled → атрибуция запрещена всегда (только «голоса из-за …»)."""
    result = _res("none", "muffled")
    # даже знакомый голос не атрибутируется на muffled
    assert witness_model.perceive_to_presence(result, voice_known=True) == "audible"
    line = witness_model.render_perception_line(
        schemas.MessageRead.model_construct(
            role="character", character_id=1, content="секрет", channel="direct"
        ),
        result,
        {1: "Борис"},
        voice_known=True,
    )
    assert line is not None
    assert "Борис" not in line
    assert "секрет" not in line


# ---------------------------------------------------------------------------
# Voice familiarity (атрибуция при audio-only)
# ---------------------------------------------------------------------------

def test_voice_familiarity_deterministic():
    # есть отношение наблюдателя к автору → голос знаком
    assert witness_model.voice_familiarity(1, 2, {1: {2}}) is True
    # нет отношения → незнаком
    assert witness_model.voice_familiarity(1, 2, {1: {3}}) is False
    assert witness_model.voice_familiarity(1, 2, {}) is False
    # None → константа Фазы 4 (голос знаком) — откат
    assert witness_model.voice_familiarity(1, 2, None) is True
    # без автора — никогда не знаком
    assert witness_model.voice_familiarity(1, None, None) is False


async def test_presence_voice_familiarity_from_relationships(db_session, chat, three_characters, monkeypatch):
    """audio=full + незнакомый голос → audible (не mentioned); знакомый → mentioned."""
    monkeypatch.setattr(settings, "world_engine_perception_enabled", True)
    monkeypatch.setattr(settings, "world_engine_partial_perception_enabled", True)
    a, b, c = three_characters
    await _setup_world(
        db_session,
        chat,
        [(a.id, "Комната Б"), (b.id, "Комната А"), (c.id, "Комната А")],
        locations=[("Комната Б", [{"name": "Комната А"}])],
    )
    # у B есть отношение к A (голос знаком), у C — нет
    db_session.add(
        models.CharacterRelationship(
            chat_id=chat.id,
            source_character_id=b.id,
            target_character_id=a.id,
            relationship_type="friendship",
        )
    )
    await db_session.commit()
    msg = await _create_message(
        db_session,
        chat.id,
        character_id=a.id,
        content="ПОМОГИТЕ!",
        location="Комната Б",
        stimuli=[{"type": "loud_sound", "audibility": "high"}],
    )
    result = await post_round_pipeline.compute_and_save_presence_for_message(
        db_session, msg, [a, b, c], _names(three_characters)
    )
    # знакомый голос → mentioned; незнакомый → audible
    assert result[b.id] == "mentioned"
    assert result[c.id] == "audible"


# ---------------------------------------------------------------------------
# Renderer в ContextBuilder (И11): без утечки семантики при частичном восприятии
# ---------------------------------------------------------------------------

async def test_context_builder_glass_no_text_leak(db_session, chat, three_characters, monkeypatch):
    """Канало-зависимый рендер: стекло → «слов не слышно», текст не утекает."""
    monkeypatch.setattr(settings, "world_engine_perception_enabled", True)
    monkeypatch.setattr(settings, "world_engine_partial_perception_enabled", True)
    monkeypatch.setattr(settings, "enable_witness_filter", True)
    a, b, _c = three_characters
    await _setup_world(
        db_session,
        chat,
        [(a.id, "Комната А"), (b.id, "Комната Б")],
        locations=[
            (
                "Комната Б",
                [
                    {
                        "name": "Комната А",
                        "visual_permeability": "full",
                        "audio_permeability": "none",
                    }
                ],
            )
        ],
    )
    secret = await _create_message(
        db_session,
        chat.id,
        character_id=b.id,
        content="суперсекретный план",
        location="Комната Б",
    )
    window = await crud.get_messages_by_chat(db_session, chat.id)
    built = await ContextBuilder().build(
        db=db_session,
        chat_id=chat.id,
        character=a,
        user_message="что там?",
        general_prompt=chat.general_prompt,
        messages_window=window,
        round_messages=[secret],
        character_names=_names(three_characters),
        character_locations={x.id: getattr(x, "location", "") or "" for x in (a, b)},
        max_tokens=1000,
    )
    assert "суперсекретный план" not in built.recent_text
    assert "Что-то происходит за стеклом" in built.recent_text

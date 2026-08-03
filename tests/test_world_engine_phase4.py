"""WPE 3.0 (Plans/WPE.md) Фаза 4 — Cutover: Renderer + Recency Tail.

Покрывает:
- Renderer (WPE.md §4/§6): ``perceive_to_presence`` (схлопывание двух каналов
  в legacy-лестницу) и ``render_perception_line`` (канало-зависимый текст:
  стекло / крик из-за стены / шум / невидимость);
- Golden #14 (идентичности): presence через ``perceive()`` в
  ``compute_and_save_presence_for_message`` (cutover, флаг
  ``WORLD_ENGINE_PERCEPTION_ENABLED``); ``evidence_mode_from_perception`` —
  тот же гейт адмиссибилити, что ``_evidence_mode``; откат — флаг off даёт
  legacy-решения без регресса;
- Golden #2 (явная адресация = P0): addressed-строки не вытесняются бюджетом;
- Golden #20 (Recency Tail, И15): ``build_system_intervention_block`` в самом
  конце user-сообщения перед generation cue (chat-путь); ``built_context``
  несёт ``recency_tail_text``, который выживает при усечении бюджета.
"""

from __future__ import annotations

import pytest

import app.ollama_client as ollama_client
from app import chat_engine
from app import crud
from app import perception
from app import schemas
from app import witness_model
from app.config import settings
from app.context_builder import ContextBuilder
from app.prompt_builder import build_system_intervention_block
from app.role_isolation import build_generation_cue_for_chat

# Защита от известной утечки мока (test_stream_disconnect) — как в Фазе 2/3.
_REAL_GENERATE = ollama_client.generate


@pytest.fixture(autouse=True)
def _reset_wpe_state(monkeypatch):
    monkeypatch.setattr(settings, "world_engine_events_enabled", False)
    monkeypatch.setattr(settings, "world_engine_perception_enabled", False)
    monkeypatch.setattr(settings, "world_engine_recency_tail_enabled", False)
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
            stimuli=[],
        ),
    )


async def _setup_world(db, chat, characters, *, locations=()):
    """Create locations, move characters, backfill location_ids (Фаза 1)."""
    created = {}
    for name, adjacent in locations:
        created[name] = await crud.create_location(
            db,
            chat.id,
            schemas.LocationCreate(name=name, adjacent_to=list(adjacent)),
        )
    for cid, loc_name in characters:
        await crud.update_character_location(db, cid, loc_name)
    await crud.backfill_character_location_ids(db, chat.id)
    return created


def _names(characters):
    return {c.id: c.name for c in characters}


def _locs(characters, **overrides):
    d = {c.id: getattr(c, "location", "") or "" for c in characters}
    d.update(overrides)
    return d


# ---------------------------------------------------------------------------
# Renderer (WPE.md §4): PerceptionResult → legacy presence ladder
# ---------------------------------------------------------------------------

def test_perceive_to_presence_channel_combos():
    # full/full (одна локация / общая сцена) → present
    assert witness_model.perceive_to_presence(_res("full", "full")) == "present"
    # стекло: действия видны, текст не слышен → present (визуально)
    assert witness_model.perceive_to_presence(_res("full", "none")) == "present"
    # крик, адресованный зрителю → mentioned (атрибуция по голосу)
    assert (
        witness_model.perceive_to_presence(_res("none", "full", addressed=True))
        == "mentioned"
    )
    # крик знакомого голоса → mentioned
    assert (
        witness_model.perceive_to_presence(_res("none", "full"), voice_known=True)
        == "mentioned"
    )
    # незнакомый голос → audible
    assert (
        witness_model.perceive_to_presence(_res("none", "full"), voice_known=False)
        == "audible"
    )
    # шум из-за стены → audible
    assert witness_model.perceive_to_presence(_res("none", "muffled")) == "audible"
    # дальняя локация (И11) → absent
    assert witness_model.perceive_to_presence(_res()) == "absent"


def test_render_perception_line_channel_text():
    char_names = {1: "Борис"}
    # full/full → обычная строка
    line = witness_model.render_perception_line(
        _msg_for_render(role="character", content="привет"),
        _res("full", "full"),
        char_names,
    )
    assert line == "Борис: привет"
    # стекло
    glass = witness_model.render_perception_line(
        _msg_for_render(content="тайный разговор"),
        _res("full", "none"),
        char_names,
    )
    assert glass == "[Что-то происходит за стеклом: слов не слышно]"
    # шум (muffled) — без семантики
    muffled = witness_model.render_perception_line(
        _msg_for_render(content="глухой гул"),
        _res("none", "muffled"),
        char_names,
    )
    assert muffled and "слышишь" in muffled and "гул" not in muffled
    # крик знакомого голоса → атрибуция
    cry = witness_model.render_perception_line(
        _msg_for_render(content="ПОМОГИТЕ!"),
        _res("none", "full"),
        char_names,
        voice_known=True,
    )
    assert cry == "[Ты слышишь голос Борис: ПОМОГИТЕ!]"
    # незнакомый голос
    unknown = witness_model.render_perception_line(
        _msg_for_render(content="ПОМОГИТЕ!"),
        _res("none", "full"),
        char_names,
        voice_known=False,
    )
    assert unknown == "[Чей-то голос: ПОМОГИТЕ!]"
    # невидимость (И11) → None
    assert (
        witness_model.render_perception_line(
            _msg_for_render(content="секрет"), _res(), char_names
        )
        is None
    )


def _msg_for_render(*, role="character", content="hi"):
    return type("Msg", (), {"role": role, "content": content, "character_id": 1})


# ---------------------------------------------------------------------------
# Golden #14: identity — perceive() presence == evidence gate
# ---------------------------------------------------------------------------

def test_evidence_mode_from_perception_mapping():
    assert chat_engine.evidence_mode_from_perception(_res("full", "full")) == "direct"
    assert chat_engine.evidence_mode_from_perception(_res("full", "none")) == "observed"
    assert (
        chat_engine.evidence_mode_from_perception(_res("none", "full", addressed=True))
        == "direct"
    )
    assert (
        chat_engine.evidence_mode_from_perception(_res("none", "full"))
        == "hearsay"
    )
    assert (
        chat_engine.evidence_mode_from_perception(_res("none", "muffled"))
        == "hearsay"
    )
    assert chat_engine.evidence_mode_from_perception(_res()) == "none"


def test_evidence_identity_presence_and_mode_agree():
    """Наблюдаемость (presence) и адмиссибилити (mode) никогда не расходятся:
    presence == absent ⟺ mode == none."""
    combos = [
        _res("full", "full"),
        _res("full", "none"),
        _res("none", "full"),
        _res("none", "muffled"),
        _res(),
        _res("none", "full", addressed=True),
    ]
    for result in combos:
        presence = witness_model.perceive_to_presence(result)
        mode = chat_engine.evidence_mode_from_perception(result)
        assert (presence == "absent") == (mode == "none"), (presence, mode)


# ---------------------------------------------------------------------------
# Golden #14: cutover — presence пишется через perceive() (flag on)
# ---------------------------------------------------------------------------

async def test_cutover_presence_same_location_present(
    db_session, chat, three_characters, monkeypatch
):
    monkeypatch.setattr(settings, "world_engine_perception_enabled", True)
    a, b, c = three_characters
    await _setup_world(
        db_session,
        chat,
        [(a.id, "Кухня"), (b.id, "Кухня"), (c.id, "Гостиная")],
        locations=[("Кухня", ()), ("Гостиная", ("Кухня",))],
    )
    msg = await _create_message(
        db_session,
        chat.id,
        character_id=a.id,
        content="пошли на кухню",
        location="Кухня",
    )
    result = await crud.compute_and_save_presence_for_message(
        db_session, msg, [a, b, c], _names(three_characters)
    )
    # одна локация (по id после backfill) → present
    assert result[a.id] == "present"
    assert result[b.id] == "present"
    # соседняя локация → audible (ребро по умолчанию audio=muffled)
    assert result[c.id] == "audible"


async def test_cutover_presence_distant_absent(
    db_session, chat, three_characters, monkeypatch
):
    monkeypatch.setattr(settings, "world_engine_perception_enabled", True)
    a, b, c = three_characters
    await _setup_world(
        db_session,
        chat,
        [(a.id, "Кухня"), (b.id, "Гостиная"), (c.id, "Спальня")],
        locations=[("Кухня", ()), ("Спальня", ())],
    )
    msg = await _create_message(
        db_session,
        chat.id,
        character_id=a.id,
        content="секрет",
        location="Кухня",
    )
    result = await crud.compute_and_save_presence_for_message(
        db_session, msg, [a, b, c], _names(three_characters)
    )
    assert result[a.id] == "present"
    # Гостиная и Спальня не смежны с Кухней → И11 (никогда не додумывается)
    assert result[b.id] == "absent"
    assert result[c.id] == "absent"


async def test_cutover_flag_off_legacy_unchanged(
    db_session, chat, three_characters, monkeypatch
):
    monkeypatch.setattr(settings, "world_engine_perception_enabled", False)
    a, b, c = three_characters
    await _setup_world(
        db_session,
        chat,
        [(a.id, "Кухня"), (b.id, "Кухня"), (c.id, "Гостиная")],
        locations=[("Кухня", ()), ("Гостиная", ("Кухня",))],
    )
    msg = await _create_message(
        db_session,
        chat.id,
        character_id=a.id,
        content="пошли",
        location="Кухня",
    )
    result = await crud.compute_and_save_presence_for_message(
        db_session, msg, [a, b, c], _names(three_characters)
    )
    # флаг off → legacy `can_character_perceive_event`: без adjacency_index
    # событие из другой локации не слышно вовсе.
    assert result[a.id] == "present"
    assert result[b.id] == "present"
    assert result[c.id] == "absent"


async def test_compute_mvp_presence_perceive_branch(
    db_session, chat, three_characters, monkeypatch
):
    """compute_mvp_presence с world_state решает через perceive()."""
    monkeypatch.setattr(settings, "world_engine_perception_enabled", True)
    a, b, c = three_characters
    await _setup_world(
        db_session,
        chat,
        [(a.id, "Кухня"), (b.id, "Кухня"), (c.id, "Спальня")],
        locations=[("Кухня", ()), ("Спальня", ())],
    )
    locations = await crud.get_chat_locations(db_session, chat.id)
    world_state = perception.PerceptionWorldState(
        adjacency=perception.build_permeability_index(locations)
    )
    msg = await _create_message(
        db_session,
        chat.id,
        character_id=a.id,
        content="привет",
        location="Кухня",
    )
    names = _names(three_characters)
    char_locs = _locs(three_characters)
    p_b = witness_model.compute_mvp_presence(
        msg,
        b.id,
        names,
        viewer_location=char_locs[b.id],
        character_locations=char_locs,
        world_state=world_state,
    )
    p_c = witness_model.compute_mvp_presence(
        msg,
        c.id,
        names,
        viewer_location=char_locs[c.id],
        character_locations=char_locs,
        world_state=world_state,
    )
    assert p_b == "present"
    assert p_c == "absent"


# ---------------------------------------------------------------------------
# Golden #20 (И15): Recency Tail — build + placement + budget survival
# ---------------------------------------------------------------------------

def test_intervention_block_empty_and_format():
    assert build_system_intervention_block([]) == ""
    assert build_system_intervention_block(None) == ""
    block = build_system_intervention_block(["Игрок обращается к тебе прямо сейчас. Отреагируй!"])
    assert block == (
        "[СИСТЕМНОЕ ВМЕШАТЕЛЬСТВО: Игрок обращается к тебе прямо сейчас. Отреагируй!]"
    )
    multi = build_system_intervention_block(["A", "B"])
    assert multi == "[СИСТЕМНОЕ ВМЕШАТЕЛЬСТВО: A / B]"


async def test_character_recency_tail_addressed(
    db_session, chat, three_characters
):
    a, b, c = three_characters
    names = _names(three_characters)
    m = await _create_message(
        db_session,
        chat.id,
        character_id=a.id,
        role="character",
        content="Борис, иди сюда!",
        targets=(b.id,),
    )
    block = witness_model.build_character_recency_tail([m], b.id, names)
    assert "Character A" in block
    assert block.startswith("[СИСТЕМНОЕ ВМЕШАТЕЛЬСТВО:")
    # текст события в Recency Tail не дублируется — только P0-сигнал
    assert "иди сюда" not in block
    # другому персонажу событие не адресовано → пусто
    assert witness_model.build_character_recency_tail([m], c.id, names) == ""


def test_recency_tail_placed_before_generation_cue_chat():
    tail = build_system_intervention_block(["Игрок обращается к тебе. Отреагируй!"])
    messages = ollama_client._build_generation_messages(
        "system",
        "",
        "",
        "<recent_dialogue>Hi</recent_dialogue>",
        "",
        "",
        build_generation_cue_for_chat("Alice"),
        recency_tail_block=tail,
    )
    user_content = messages[1]["content"]
    assert "СИСТЕМНОЕ ВМЕШАТЕЛЬСТВО" in user_content
    assert (
        user_content.index("СИСТЕМНОЕ ВМЕШАТЕЛЬСТВО")
        < user_content.index("Отвечай за Alice")
    )


def test_recency_tail_empty_not_in_prompt():
    messages = ollama_client._build_generation_messages(
        "system",
        "",
        "",
        "<recent_dialogue>Hi</recent_dialogue>",
        "",
        "",
        build_generation_cue_for_chat("Alice"),
        recency_tail_block="",
    )
    assert "СИСТЕМНОЕ ВМЕШАТЕЛЬСТВО" not in messages[1]["content"]


async def test_context_builder_recency_tail_and_addressed_survive_budget(
    db_session, chat, three_characters, monkeypatch
):
    """Golden #2 + #20: addressed-строки и Recency Tail выживают при жёстком
    бюджете, пока не-addressed история вытесняется."""
    monkeypatch.setattr(settings, "world_engine_recency_tail_enabled", True)
    monkeypatch.setattr(settings, "enable_witness_filter", True)
    a, b, c = three_characters
    for index in range(30):
        author = three_characters[index % 3]
        await _create_message(
            db_session,
            chat.id,
            character_id=author.id,
            content=f"обычный диалог номер {index}",
        )
    # P0-событие раунда: обращение к персонажу A
    addressed = await _create_message(
        db_session,
        chat.id,
        character_id=b.id,
        content="А, это тебе!",
        targets=(a.id,),
    )
    window = await crud.get_messages_by_chat(db_session, chat.id)
    built = await ContextBuilder().build(
        db=db_session,
        chat_id=chat.id,
        character=a,
        user_message="ну",
        general_prompt=chat.general_prompt,
        messages_window=window,
        round_messages=[addressed],
        character_names=_names(three_characters),
        character_locations=_locs(three_characters),
        max_tokens=200,
    )
    # Recency Tail существует и выжил бюджет
    assert built.recency_tail_text
    assert "СИСТЕМНОЕ ВМЕШАТЕЛЬСТВО" in built.recency_tail_text
    # addressed-строка попала в recent диалог (P0), не вытеснена
    assert "это тебе" in built.recent_text
    # старые не-адресованные сообщения вытеснены
    assert "обычный диалог номер 0" not in built.recent_text

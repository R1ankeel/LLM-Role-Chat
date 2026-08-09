"""WPE 3.0 (Plans/WPE.md) Фаза 5 — Action Resolution + System Narrator (Ул.1, §5).

Покрывает:
- Action<->Text Consistency Validator (`classify_consistency`): три класса
  `consistent` / `minor_ambiguity` (молчаливое действие) / `contradiction`;
- System Narrator (И16): детерминированные ремарки `role=system` для действий,
  не отражённых в тексте; contradiction -> отклонение + ремарка;
- atomic application действий (`crud.apply_character_actions`): `move_to`
  обновляет `location`+`location_id` и создаёт immutable `WorldEvent(move)`
  (golden #4); невалидное действие не портит валидные (#13);
- `generate()` отдаёт `turn`+`verdict` в response-событии; contradiction ->
  ретрай <=1 с фидбеком; молчаливое действие -> БЕЗ ретрая (golden #5, #16,
  стоимость LLM-вызовов = 1); off-регрессия.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import httpx
import pytest
from sqlalchemy import select

import app.ollama_client as ollama_client
from app import action_resolution
from app import chat_engine
from app import crud
from app import models
from app import prompt_builder
from app import schemas
from app.config import settings
from app.context_state import ctx_state

_REAL_GENERATE = ollama_client.generate


@pytest.fixture(autouse=True)
def _reset_wpe_state(monkeypatch):
    monkeypatch.setattr(settings, "world_engine_events_enabled", False)
    monkeypatch.setattr(settings, "world_engine_actions_enabled", False)
    monkeypatch.setattr(settings, "world_engine_tools_enabled", False)
    monkeypatch.setattr(settings, "wpe_action_consistency_max_retries", 1)
    ollama_client.WPE_TOOLS_STATS = {
        "calls": 0,
        "by_mode": {},
        "schema_valid": 0,
        "with_move_to": 0,
        "with_send_message": 0,
        "with_addressing": 0,
        "latency_ms": [],
    }
    ollama_client._MODEL_TOOL_MODE_CACHE.clear()
    ollama_client.generate = _REAL_GENERATE
    yield


def _turn(actions: list[dict], targets: list[int] | None = None) -> schemas.TurnOutput:
    return schemas.TurnOutput(
        reply_target_character_ids=targets or [],
        actions=[schemas.Action.model_validate(a) for a in actions],
    )


def _move(location: str) -> dict:
    return {"type": "move_to", "location": location}


def _send(message: str = "Привет.", targets: list[int] | None = None) -> dict:
    return {
        "type": "send_message",
        "message": message,
        "target_character_ids": targets or [],
    }


# ---------------------------------------------------------------------------
# Consistency Validator (§5.2)
# ---------------------------------------------------------------------------

def test_classify_no_actions():
    assert action_resolution.classify_consistency(None, "Привет.") == "no_actions"
    assert (
        action_resolution.classify_consistency(_turn([]), "Привет.")
        == "no_actions"
    )


def test_classify_consistent_move():
    turn = _turn([_move("Кухня")])
    assert action_resolution.classify_consistency(turn, "Иду на кухню.") == "consistent"


def test_classify_consistent_move_verb_only():
    turn = _turn([_move("Кухня")])
    assert action_resolution.classify_consistency(turn, "Я уже захожу.") == "consistent"


def test_classify_silent_move_is_minor_ambiguity():
    turn = _turn([_move("Кухня")])
    assert (
        action_resolution.classify_consistency(turn, "Я подумаю об этом.")
        == "minor_ambiguity"
    )


def test_classify_contradiction_move():
    turn = _turn([_move("Кухня")])
    assert (
        action_resolution.classify_consistency(turn, "Никуда не пойду.")
        == "contradiction"
    )


def test_classify_contradiction_send_message():
    turn = _turn([_send("Встретимся.", targets=[2])])
    assert (
        action_resolution.classify_consistency(turn, "Не буду тебе писать.")
        == "contradiction"
    )


def test_classify_consistent_send_message():
    turn = _turn([_send("Встретимся.", targets=[2])])
    assert (
        action_resolution.classify_consistency(turn, "Напишу тебе позже.")
        == "consistent"
    )


def test_classify_mixed_actions_partial_reflection():
    # одно действие отражено, второе молчит -> minor_ambiguity (ремарка только
    # для неотражённого)
    turn = _turn([_move("Кухня"), _move("Комната")])
    assert (
        action_resolution.classify_consistency(turn, "Иду на кухню.")
        == "minor_ambiguity"
    )


def test_reflected_action_indices():
    turn = _turn([_move("Кухня"), _move("Комната")])
    assert action_resolution.reflected_action_indices(turn, "Иду на кухню.") == {0}


def test_build_consistency_feedback_describes_actions():
    turn = _turn([_move("Кухня")])
    feedback = action_resolution.build_consistency_feedback(
        turn, "Никуда не пойду.", "Пётр"
    )
    assert "Кухня" in feedback
    assert "противоречие" in feedback.lower() or "противореч" in feedback


# ---------------------------------------------------------------------------
# System Narrator (И16, §5.10)
# ---------------------------------------------------------------------------

def test_narrator_remark_for_move_matches_plan_example():
    remark = action_resolution.narrator_remark_for_move("Пётр", "Гостиная", "Кухня")
    assert remark == (
        "*[Система: Пётр покидает 'Гостиная' и переходит в 'Кухня']*"
    )


def test_narrator_remark_for_move_without_from():
    remark = action_resolution.narrator_remark_for_move("Аня", "", "Кухня")
    assert remark == "*[Система: Аня перемещается в 'Кухня']*"


def test_build_narrator_remarks_silent_move():
    turn = _turn([_move("Кухня")])
    remarks = action_resolution.build_narrator_remarks(
        "Пётр",
        turn,
        "minor_ambiguity",
        applied_moves=[
            {
                "action_index": 0,
                "location_from": "Гостиная",
                "location_to": "Кухня",
            }
        ],
        applied_messages=[],
        reflected=set(),
    )
    assert len(remarks) == 1
    assert "Система: Пётр покидает 'Гостиная'" in remarks[0]


def test_build_narrator_remarks_consistent_no_remark():
    turn = _turn([_move("Кухня")])
    remarks = action_resolution.build_narrator_remarks(
        "Пётр",
        turn,
        "consistent",
        applied_moves=[
            {
                "action_index": 0,
                "location_from": "Гостиная",
                "location_to": "Кухня",
            }
        ],
        applied_messages=[],
        reflected={0},
    )
    assert remarks == []


def test_build_narrator_remarks_contradiction_rejection():
    turn = _turn([_move("Кухня")])
    remarks = action_resolution.build_narrator_remarks(
        "Пётр",
        turn,
        "contradiction",
        applied_moves=[],
        applied_messages=[],
        reflected=set(),
        rejected=[{"action_index": 0, "type": "move_to"}],
    )
    assert remarks == ["*[Система: Пётр не совершает заявленное перемещение]*"]


def test_build_narrator_remarks_skips_reflected_action():
    # send_message отражён в тексте -> ремарки нет; move молчит -> ремарка есть
    turn = _turn([_move("Кухня"), _send("Позвони.", targets=[2])])
    remarks = action_resolution.build_narrator_remarks(
        "Пётр",
        turn,
        "minor_ambiguity",
        applied_moves=[{"action_index": 0, "location_from": "", "location_to": "Кухня"}],
        applied_messages=[{"action_index": 1, "target_character_ids": [2]}],
        reflected={1},
    )
    assert len(remarks) == 1
    assert "Кухня" in remarks[0]


# ---------------------------------------------------------------------------
# Atomic application (crud.apply_character_actions) — golden #4/#13
# ---------------------------------------------------------------------------

async def _setup_kitchen(db_session, chat):
    return await crud.create_location(
        db_session, chat.id, schemas.LocationCreate(name="Кухня")
    )


async def _world_events_for_char(db_session, character_id: int):
    stmt = (
        select(models.WorldEvent)
        .where(models.WorldEvent.character_id == character_id)
        .order_by(models.WorldEvent.id)
    )
    return list((await db_session.execute(stmt)).scalars().all())


async def test_apply_move_updates_location_and_writes_world_event(
    db_session, chat, three_characters, monkeypatch
):
    monkeypatch.setattr(settings, "world_engine_actions_enabled", True)
    kitchen = await _setup_kitchen(db_session, chat)
    character = three_characters[0]

    result = await crud.apply_character_actions(
        db_session,
        chat.id,
        character,
        _turn([_move("Кухня")]),
        round_id="r1",
    )

    assert len(result.applied_moves) == 1
    assert result.applied_moves[0]["location_to"] == "Кухня"
    assert result.applied_moves[0]["location_id"] == kitchen.id
    assert result.rejected == []

    await db_session.refresh(character)
    assert character.location == "Кухня"
    assert character.location_id == kitchen.id

    events = await _world_events_for_char(db_session, character.id)
    assert len(events) == 1
    event = events[0]
    assert event.event_type == "move"
    assert event.location_from == ""
    assert event.location_to == "Кухня"
    assert event.round_id == "r1"


async def test_apply_invalid_location_rejected(
    db_session, chat, three_characters, monkeypatch
):
    monkeypatch.setattr(settings, "world_engine_actions_enabled", True)
    character = three_characters[0]

    result = await crud.apply_character_actions(
        db_session, chat.id, character, _turn([_move("Несуществующая")])
    )

    assert result.applied_moves == []
    assert result.rejected == [
        {
            "action_index": 0,
            "type": "move_to",
            "reason": "unknown_location",
            "location": "Несуществующая",
        }
    ]
    await db_session.refresh(character)
    assert character.location == ""
    assert await _world_events_for_char(db_session, character.id) == []


async def test_apply_multiple_actions_atomic_valid_survive(
    db_session, chat, three_characters, monkeypatch
):
    monkeypatch.setattr(settings, "world_engine_actions_enabled", True)
    kitchen = await _setup_kitchen(db_session, chat)
    character = three_characters[0]
    other = three_characters[1]

    result = await crud.apply_character_actions(
        db_session,
        chat.id,
        character,
        _turn(
            [
                _move("Кухня"),
                _move("Несуществующая"),
                _send("Увидимся.", targets=[other.id]),
            ]
        ),
        round_id="r2",
    )

    # move применён, невалидный move отклонён, send_message применён (#13)
    assert [m["location_to"] for m in result.applied_moves] == ["Кухня"]
    assert len(result.rejected) == 1
    assert result.rejected[0]["reason"] == "unknown_location"
    assert len(result.applied_messages) == 1
    assert result.applied_messages[0]["target_character_ids"] == [other.id]

    await db_session.refresh(character)
    assert character.location == "Кухня"
    assert character.location_id == kitchen.id

    events = await _world_events_for_char(db_session, character.id)
    assert len(events) == 2
    kinds = sorted(e.event_type for e in events)
    assert kinds == ["move", "speech"]
    move_event = next(e for e in events if e.event_type == "move")
    assert move_event.location_from == ""
    assert move_event.location_to == "Кухня"
    speech_event = next(e for e in events if e.event_type == "speech")
    assert speech_event.round_id == "r2"
    assert "2" in speech_event.target_character_ids


async def test_apply_move_to_same_location_no_event(
    db_session, chat, three_characters, monkeypatch
):
    monkeypatch.setattr(settings, "world_engine_actions_enabled", True)
    await _setup_kitchen(db_session, chat)
    character = three_characters[0]
    character.location = "Кухня"
    await db_session.commit()

    result = await crud.apply_character_actions(
        db_session, chat.id, character, _turn([_move("Кухня")])
    )

    assert len(result.applied_moves) == 1
    assert result.applied_moves[0]["changed"] is False
    assert await _world_events_for_char(db_session, character.id) == []


async def test_apply_empty_turn_noop(db_session, chat, three_characters):
    result = await crud.apply_character_actions(
        db_session, chat.id, three_characters[0], None
    )
    assert result.applied_moves == []
    assert result.applied_messages == []
    assert result.rejected == []


async def test_apply_send_message_invalid_target_rejected(
    db_session, chat, three_characters, monkeypatch
):
    monkeypatch.setattr(settings, "world_engine_actions_enabled", True)
    character = three_characters[0]
    result = await crud.apply_character_actions(
        db_session, chat.id, character, _turn([_send("Куда-то.", targets=[9999])])
    )
    assert result.applied_messages == []
    assert len(result.rejected) == 1
    assert result.rejected[0]["reason"] == "invalid_target"


# ---------------------------------------------------------------------------
# generate(): threading turn+verdict, contradiction retry, silent без retry
# ---------------------------------------------------------------------------

class FakeStreamResponse:
    def __init__(self, lines=None, status=200, error_body=None):
        self._lines = lines or []
        self.status_code = status
        self._error_body = error_body

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def aread(self):
        return (self._error_body or "").encode()

    @property
    def text(self):
        return self._error_body or ""

    async def aiter_lines(self):
        for line in self._lines:
            yield line


def _tool_calls_json(content: str, actions: list[dict]) -> list[dict]:
    return [
        json.dumps({"message": {"content": content}, "done": False}),
        json.dumps(
            {
                "message": {
                    "tool_calls": [
                        {
                            "function": {
                                "name": "take_actions",
                                "arguments": json.dumps(
                                    {
                                        "reply_target_character_ids": [],
                                        "actions": actions,
                                    },
                                    ensure_ascii=False,
                                ),
                            }
                        }
                    ],
                    "done": True,
                }
            }
        ),
    ]


def _fake_character(name="Пётр"):
    import types

    return types.SimpleNamespace(
        id=1,
        name=name,
        personality="Спокойный",
        traits="",
        background="",
        speech_style="",
        example_messages="",
        boundaries="",
        relationships="",
        temperature=None,
    )


async def _run_generate(captured, responses, **flag_overrides):
    def fake_stream(method, url, json=None, **kwargs):
        captured.append(json)
        return FakeStreamResponse(lines=responses[len(captured) - 1])

    async def fake_post(url, json=None, **kwargs):
        response = MagicMock()
        response.raise_for_status = MagicMock()
        response.json.return_value = {
            "message": {
                "role": "assistant",
                "content": "Просто текст без действий, достаточно длинный.",
            }
        }
        return response

    client = httpx.AsyncClient(base_url="http://test")
    client.stream = MagicMock(side_effect=fake_stream)
    client.post = fake_post  # type: ignore[method-assign]

    ctx_state.remove(1)
    overrides = {
        "use_chat_api": True,
        "world_engine_tools_enabled": True,
        "enable_thinking": False,
    }
    overrides.update(flag_overrides)
    patches = [
        patch("app.ollama_client.settings." + key, value)
        for key, value in overrides.items()
    ]
    for p in patches:
        p.start()
    try:
        events = []
        async for event in ollama_client.generate(
            client,
            chat_id=1,
            character=_fake_character(),
            messages_history=[],
            general_prompt="Scene",
            memories=[],
            other_character_names=["Анна"],
        ):
            events.append(event)
        return events
    finally:
        for p in patches:
            p.stop()


async def test_generate_response_event_carries_turn_and_verdict():
    captured = []
    responses = [_tool_calls_json("Иду на кухню.", [_move("Кухня")])]
    events = await _run_generate(captured, responses)

    response = events[-1]
    assert response["type"] == "response"
    assert response["text"] == "Иду на кухню."
    assert response["verdict"] == "consistent"
    turn = response["turn"]
    assert isinstance(turn, schemas.TurnOutput)
    assert turn.actions[0].type == "move_to"
    assert turn.actions[0].location == "Кухня"
    assert "Иду на кухню." == response["text"]


async def test_generate_silent_action_no_retry_llm_cost_one():
    captured = []
    # молчаливый move_to: реплика не обыгрывает действие (И16: без retry)
    responses = [_tool_calls_json("Я подумаю об этом.", [_move("Кухня")])]
    events = await _run_generate(captured, responses)

    assert len(captured) == 1  # стоимость LLM-вызовов = 1
    response = events[-1]
    assert response["verdict"] == "minor_ambiguity"
    assert response["text"] == "Я подумаю об этом."


async def test_generate_contradiction_retries_once_then_accepts():
    captured = []
    responses = [
        _tool_calls_json("Никуда не пойду.", [_move("Кухня")]),
        _tool_calls_json("Иду на кухню.", [_move("Кухня")]),
    ]
    events = await _run_generate(captured, responses)

    assert len(captured) == 2  # ровно один retry
    response = events[-1]
    assert response["verdict"] == "consistent"
    assert response["text"] == "Иду на кухню."
    # фидбек добавлен во второй вызов (не в первый)
    first_user = captured[0]["messages"][1]["content"]
    second_user = captured[1]["messages"][1]["content"]
    assert "<action_consistency>" not in first_user
    assert "<action_consistency>" in second_user


async def test_generate_contradiction_exhausted_returns_contradiction():
    captured = []
    # оба вызова противоречивы -> после retry вердикт contradiction
    responses = [
        _tool_calls_json("Никуда не пойду.", [_move("Кухня")]),
        _tool_calls_json("Нет, не пойду я.", [_move("Кухня")]),
    ]
    events = await _run_generate(captured, responses)

    assert len(captured) == 2
    response = events[-1]
    assert response["verdict"] == "contradiction"
    assert isinstance(response["turn"], schemas.TurnOutput)


async def test_generate_tools_off_turn_none_and_verdict_no_actions():
    captured_post = {}

    async def fake_post(url, json=None, **kwargs):
        captured_post["payload"] = json
        response = MagicMock()
        response.raise_for_status = MagicMock()
        response.json.return_value = {
            "message": {
                "role": "assistant",
                "content": "Просто текст без действий, достаточно длинный.",
            }
        }
        return response

    client = httpx.AsyncClient(base_url="http://test")
    client.post = fake_post  # type: ignore[method-assign]

    ctx_state.remove(1)
    with patch("app.ollama_client.settings.use_chat_api", True), patch(
        "app.ollama_client.settings.world_engine_tools_enabled", False
    ), patch("app.ollama_client.settings.enable_thinking", False):
        events = []
        async for event in ollama_client.generate(
            client,
            chat_id=1,
            character=_fake_character(),
            messages_history=[],
            general_prompt="Scene",
            memories=[],
            other_character_names=["Анна"],
        ):
            events.append(event)

    assert "tools" not in captured_post["payload"]
    assert "format" not in captured_post["payload"]
    response = events[-1]
    assert response["verdict"] == "no_actions"
    assert response["turn"] is None


# ---------------------------------------------------------------------------
# WORLD STATE (Sprint 14): глобальный блок локаций в context-промпт каждого NPC
# ---------------------------------------------------------------------------

def test_world_state_block_unit_dedupes_sorts_and_includes_player():
    block = prompt_builder.build_world_state_block(
        ["Market", "Tavern", "tavern", "", "Market"],
        {10: "Tavern", 20: "", 30: "Kitchen"},
        {10: "Alice", 20: "Bob", 30: "Viktor", 99: "Игрок"},
    )
    assert "<world_state>" in block
    assert "WORLD STATE" in block
    # case-insensitive dedupe: "tavern"/"Tavern" → один пункт, по алфавиту
    assert block.count("- Tavern") == 1
    assert block.index("- Market") < block.index("- Tavern")
    assert "- Alice: Tavern" in block
    assert "- Bob: unknown" in block
    assert "- Viktor: Kitchen" in block
    assert "- Игрок: unknown" in block


def test_world_state_block_empty_without_data():
    assert prompt_builder.build_world_state_block([], {}, {}) == ""
    assert prompt_builder.build_world_state_block(["Tavern"], {}, {}) != ""
    assert prompt_builder.build_world_state_block([], {}, {1: "Alice"}) != ""


def test_world_state_block_first_in_chat_user_message():
    from app.role_isolation import build_generation_cue_for_chat

    messages = ollama_client._build_generation_messages(
        "system-prompt",
        "<character_summary>S</character_summary>",
        "<character_memories>M</character_memories>",
        "<recent_dialogue>D</recent_dialogue>",
        "<scene>Scene</scene>",
        "",
        build_generation_cue_for_chat("Alice"),
        world_state_block=(
            "<world_state>\nWORLD STATE\nДоступные локации:\n- Tavern\n"
            "</world_state>"
        ),
    )
    user = messages[1]["content"]
    assert user.startswith("<world_state>")
    assert user.index("WORLD STATE") < user.index("<recent_dialogue>")
    assert "- Tavern" in user


async def test_world_state_block_in_chat_api_prompt():
    captured = {}

    async def fake_post(url, json=None, **kwargs):
        captured["payload"] = json
        response = MagicMock()
        response.raise_for_status = MagicMock()
        response.json.return_value = {
            "message": {
                "role": "assistant",
                "content": "Ответ Алисы достаточно длинный для валидации.",
            }
        }
        return response

    client = httpx.AsyncClient(base_url="http://test")
    client.post = fake_post  # type: ignore[method-assign]

    ctx_state.remove(1)
    ws = prompt_builder.build_world_state_block(
        ["Tavern", "Market"],
        {1: "Tavern", 2: "Market", 3: "Tavern"},
        {1: "Alice", 2: "Bob", 3: "Игрок"},
    )
    with patch("app.ollama_client.settings.use_chat_api", True), patch(
        "app.ollama_client.settings.enable_thinking", False
    ), patch("app.ollama_client.settings.world_engine_tools_enabled", False):
        events = []
        async for event in ollama_client.generate(
            client,
            chat_id=1,
            character=_fake_character(),
            messages_history=[],
            general_prompt="Scene",
            memories=[],
            other_character_names=["Bob"],
            world_state_block=ws,
        ):
            events.append(event)

    user = captured["payload"]["messages"][1]["content"]
    assert "<world_state>" in user
    assert "WORLD STATE" in user
    assert "- Alice: Tavern" in user
    assert "- Игрок: Tavern" in user
    assert user.index("WORLD STATE") < user.index("Отвечай за")
    assert events[-1]["type"] == "response"


async def test_world_state_block_in_generate_api_prompt():
    captured = {}

    async def fake_post(url, json=None, **kwargs):
        captured["payload"] = json
        response = MagicMock()
        response.raise_for_status = MagicMock()
        response.json.return_value = {
            "response": "Ответ Алисы достаточно длинный для валидации."
        }
        return response

    client = httpx.AsyncClient(base_url="http://test")
    client.post = fake_post  # type: ignore[method-assign]

    ctx_state.remove(1)
    ws = prompt_builder.build_world_state_block(
        ["Tavern"],
        {1: "Tavern", 2: "Tavern"},
        {1: "Alice", 2: "Игрок"},
    )
    with patch("app.ollama_client.settings.use_chat_api", False), patch(
        "app.ollama_client.settings.enable_thinking", False
    ):
        events = []
        async for event in ollama_client.generate(
            client,
            chat_id=1,
            character=_fake_character(),
            messages_history=[],
            general_prompt="Scene",
            memories=[],
            other_character_names=["Bob"],
            summary="Sum",
            world_state_block=ws,
        ):
            events.append(event)

    prompt = captured["payload"]["prompt"]
    assert "<world_state>" in prompt
    assert "WORLD STATE" in prompt
    # блок идёт сразу после system-промпта, до остальных блоков
    assert prompt.index("<world_state>") < prompt.index("<character_summary>")
    assert events[-1]["type"] == "response"


async def test_world_state_block_falls_back_to_built_context():
    captured = {}

    async def fake_post(url, json=None, **kwargs):
        captured["payload"] = json
        response = MagicMock()
        response.raise_for_status = MagicMock()
        response.json.return_value = {
            "message": {
                "role": "assistant",
                "content": "Ответ Алисы достаточно длинный для валидации.",
            }
        }
        return response

    client = httpx.AsyncClient(base_url="http://test")
    client.post = fake_post  # type: ignore[method-assign]

    ctx_state.remove(1)
    built_context = schemas.BuiltContext(
        world_state_text="<world_state>\nWORLD STATE\n- Tavern\n</world_state>",
        budget=schemas.ContextBudget(
            total_tokens=10000,
            system_budget=1000,
            state_budget=500,
            summary_budget=500,
            memory_budget=500,
            retrieved_history_budget=1000,
            recent_history_min_tokens=1000,
            recent_history_max_tokens=3000,
            reserve_tokens=500,
        ),
    )
    with patch("app.ollama_client.settings.use_chat_api", True), patch(
        "app.ollama_client.settings.enable_thinking", False
    ), patch("app.ollama_client.settings.world_engine_tools_enabled", False):
        events = []
        async for event in ollama_client.generate(
            client,
            chat_id=1,
            character=_fake_character(),
            messages_history=[],
            general_prompt="Scene",
            memories=[],
            other_character_names=["Bob"],
            built_context=built_context,
        ):
            events.append(event)

    user = captured["payload"]["messages"][1]["content"]
    assert "<world_state>" in user
    assert "WORLD STATE" in user
    assert events[-1]["type"] == "response"


def _run_in_current_thread(func, /, *args, **kwargs):
    return func(*args, **kwargs)


async def test_world_state_block_in_actual_npc_prompt(db_session, chat):
    """Sprint 14 acceptance: WORLD STATE (локации + персонажи, вкл. игрока)
    попадает в реальный context-промпт каждого NPC через основной пайплайн."""
    await crud.update_chat(
        db_session, chat.id, schemas.ChatUpdate(player_location="tavern")
    )
    await crud.create_location(
        db_session, chat.id, schemas.LocationCreate(name="tavern")
    )
    await crud.create_location(
        db_session, chat.id, schemas.LocationCreate(name="market")
    )
    await crud.create_player_character(db_session, chat.id)
    await crud.create_character(
        db_session, chat.id,
        schemas.CharacterCreate(name="Anna", location="tavern", order_index=1),
    )
    await crud.create_character(
        db_session, chat.id,
        schemas.CharacterCreate(name="Boris", location="market", order_index=2),
    )

    captured_world_state: list[str] = []
    captured_built_world_state: list[str] = []

    async def fake_generate(**kwargs):
        captured_world_state.append(kwargs.get("world_state_block", ""))
        built = kwargs.get("built_context")
        captured_built_world_state.append(
            built.world_state_text if built is not None else ""
        )
        yield {
            "type": "response",
            "text": f"Ответ {kwargs['character'].name} достаточно длинный.",
        }

    client = httpx.AsyncClient(base_url="http://test")
    with patch(
        "app.chat_engine.ollama_client.generate", side_effect=fake_generate
    ), patch("app.chat_engine.asyncio.create_task"), patch(
        "app.chat_engine.asyncio.to_thread", side_effect=_run_in_current_thread
    ):
        async for _ in chat_engine.process_user_message_streaming(
            client, db_session, chat.id, "Всем привет!"
        ):
            pass

    assert captured_world_state, "generate не вызывался ни для одного NPC"
    for ws in captured_world_state:
        assert "<world_state>" in ws
        assert "WORLD STATE" in ws
        assert "- tavern" in ws
        assert "- market" in ws
        assert "Anna" in ws
        assert "Boris" in ws
        assert "Игрок" in ws
    # context_builder переносит блок в BuiltContext (для не-chat-путей)
    assert captured_built_world_state == captured_world_state

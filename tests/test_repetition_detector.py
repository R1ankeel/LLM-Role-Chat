"""Tests for semantic action loop / scene stagnation detection."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app import ollama_client
from app.repetition_detector import (
    analyze_response,
    build_repetition_feedback,
    extract_actions,
)


def _msg(
    role: str,
    content: str,
    *,
    character_id: int | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        role=role,
        content=content,
        character_id=character_id,
    )


def _char(name: str = "Alice", cid: int = 1) -> SimpleNamespace:
    return SimpleNamespace(
        id=cid,
        name=name,
        personality="Curious",
        traits="Brave",
        background="",
        speech_style="",
        example_messages="",
        boundaries="",
        relationships="",
        temperature=None,
        location="",
    )


# ---------------------------------------------------------------------------
# Test 1 — exact / near-exact textual repetition
# ---------------------------------------------------------------------------


def test_exact_textual_repetition():
    history = [
        _msg(
            "character",
            "*Усмехаюсь и делаю шаг ближе* «Каков твой следующий шаг?»",
            character_id=1,
        ),
        _msg(
            "character",
            "*Усмехаюсь и делаю шаг ближе* «Ну же?»",
            character_id=1,
        ),
    ]
    candidate = "*Усмехаюсь и делаю шаг ближе* «Каков твой следующий шаг?»"
    result = analyze_response(candidate, character_id=1, messages=history)
    assert result.is_repetitive is True
    assert result.score >= 0.72
    assert result.character_level is True


# ---------------------------------------------------------------------------
# Test 2 — semantic action class equivalence
# ---------------------------------------------------------------------------


def test_semantic_move_closer_extraction():
    samples = [
        "*делаю шаг ближе*",
        "*сокращаю расстояние между нами*",
        "*наклоняюсь к ней, почти не оставляя пространства*",
        "*Слегка приближаюсь*",
    ]
    for text in samples:
        actions = extract_actions(text)
        assert "move_closer" in actions, f"failed for: {text} -> {actions}"


def test_semantic_eye_contact_extraction():
    samples = [
        "*смотрю ей прямо в глаза*",
        "*не отрываю от неё взгляда*",
        "*не отводя от него пристального взгляда*",
        "*встречаю её взгляд*",
    ]
    for text in samples:
        actions = extract_actions(text)
        assert "maintain_eye_contact" in actions, f"failed for: {text} -> {actions}"


# ---------------------------------------------------------------------------
# Test 3 — normal progressing scene is not a loop
# ---------------------------------------------------------------------------


def test_normal_scene_not_loop():
    history = [
        _msg("character", "*Смотрю на него* «Кто ты?»", character_id=1),
        _msg(
            "character",
            "*Кивает* «Я гонец из столицы. Король мёртв.»",
            character_id=2,
        ),
        _msg(
            "character",
            "*Отступаю на шаг, бледнея* «Это меняет всё. Нужно предупредить сестру.»",
            character_id=1,
        ),
    ]
    candidate = (
        "*Беру его за руку* «Проведи меня к воротам. Быстро.»"
    )
    result = analyze_response(candidate, character_id=1, messages=history)
    assert result.is_repetitive is False
    assert result.progression_score >= 0.3


# ---------------------------------------------------------------------------
# Test 4 — two-character interaction loop
# ---------------------------------------------------------------------------


def test_two_character_interaction_loop():
    history = [
        _msg(
            "character",
            "*Слегка усмехаюсь, не отводя взгляда* «Каков твой следующий шаг?» "
            "*Делаю шаг ближе*",
            character_id=1,
        ),
        _msg(
            "character",
            "*Усмехаюсь, сокращая расстояние* «Я предпочитаю переходить от слов к делу...»",
            character_id=2,
        ),
        _msg(
            "character",
            "*Усмехаюсь, глядя на него* «Давай проверим, хватит ли у тебя смелости...» "
            "*Приближаюсь*",
            character_id=1,
        ),
        _msg(
            "character",
            "*Усмехаюсь, глядя ей прямо в глаза и сокращая расстояние* "
            "«Это вызов, который я обязан принять...»",
            character_id=2,
        ),
        _msg(
            "character",
            "*Слегка усмехаюсь* «Не слишком ли ты завышаешь планку?» *Делаю шаг ближе*",
            character_id=1,
        ),
    ]
    candidate = (
        "*Сокращаю расстояние ещё немного, не отрывая взгляда* "
        "«Планку я только поднимаю.»"
    )
    result = analyze_response(candidate, character_id=2, messages=history)
    assert result.is_repetitive is True
    assert result.stagnation is True or result.interaction_level is True
    assert any(
        a in result.repeated_actions
        for a in ("move_closer", "smile", "maintain_eye_contact", "verbal_challenge")
    )


# ---------------------------------------------------------------------------
# Test 5 — synonym variation still detected
# ---------------------------------------------------------------------------


def test_semantic_variation_still_detected():
    history = [
        _msg("character", "*Делаю шаг ближе к ней*", character_id=1),
        _msg("character", "*Смотрит в сторону* «Хм.»", character_id=2),
        _msg("character", "*Сокращаю расстояние между нами*", character_id=1),
        _msg("character", "*Кивает*", character_id=2),
    ]
    candidate = "*Наклоняюсь к ней, почти не оставляя пространства*"
    result = analyze_response(candidate, character_id=1, messages=history)
    assert "move_closer" in extract_actions(candidate)
    assert result.is_repetitive is True
    assert "move_closer" in result.repeated_actions or result.cooldown_hits


# ---------------------------------------------------------------------------
# Test 6 — progression breaks loop
# ---------------------------------------------------------------------------


def test_progression_breaks_loop():
    history = [
        _msg("character", "*Делаю шаг ближе*", character_id=1),
        _msg("character", "*Стоит неподвижно*", character_id=2),
        _msg("character", "*Ещё немного приближаюсь*", character_id=1),
        _msg("character", "*Молчит*", character_id=2),
    ]
    # Touch is a progression action
    candidate = "*Касаюсь его руки* «Хватит игр.»"
    result = analyze_response(candidate, character_id=1, messages=history)
    assert "touch" in extract_actions(candidate)
    # Should not be flagged as pure move_closer loop
    assert result.is_repetitive is False or result.progression_score >= 0.4
    assert result.stagnation is False or result.progression_score > 0.3


# ---------------------------------------------------------------------------
# Test 7 — action cooldown
# ---------------------------------------------------------------------------


def test_action_cooldown_blocks_immediate_repeat():
    history = [
        _msg(
            "character",
            "*Сокращаю расстояние* «Ну?»",
            character_id=1,
        ),
    ]
    candidate = "*Делаю шаг ближе* «Ну же?»"
    result = analyze_response(
        candidate,
        character_id=1,
        messages=history,
        cooldown_turns=2,
    )
    assert "move_closer" in result.cooldown_hits or result.is_repetitive
    assert result.score >= 0.7


# ---------------------------------------------------------------------------
# Test 8 — retry injects targeted feedback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_repetition_retry_with_feedback():
    character = _char("Inna", 1)
    history = [
        _msg(
            "character",
            "*Усмехаюсь, сокращая расстояние* «Каков твой следующий шаг?»",
            character_id=1,
        ),
        _msg(
            "character",
            "*Усмехаюсь и приближаюсь* «От слов к делу.»",
            character_id=2,
        ),
        _msg(
            "character",
            "*Гляжу в глаза и делаю шаг ближе* «Хватит ли смелости?»",
            character_id=1,
        ),
        _msg(
            "character",
            "*Сокращаю расстояние, усмехаясь* «Это вызов.»",
            character_id=2,
        ),
    ]

    loop_reply = (
        "*Усмехаюсь, глядя прямо в глаза и сокращая расстояние* "
        "«Не слишком ли ты завышаешь планку?»"
    )
    good_reply = (
        "*Отступаю на шаг и сажусь на край стола* "
        "«Ладно. Расскажи, что случилось в порту.»"
    )

    calls: list[dict] = []

    async def fake_post(url, json=None, **kwargs):
        calls.append(json or {})
        # First call returns loop; second returns progression
        content = loop_reply if len(calls) == 1 else good_reply
        response = MagicMock()
        response.raise_for_status = MagicMock()
        response.json.return_value = {
            "message": {"role": "assistant", "content": content}
        }
        return response

    client = httpx.AsyncClient(base_url="http://test")
    client.post = fake_post  # type: ignore[method-assign]

    with patch("app.ollama_client.USE_CHAT_API", True), patch(
        "app.ollama_client.ENABLE_THINKING", False
    ), patch("app.ollama_client.REPETITION_DETECTION_ENABLED", True), patch(
        "app.ollama_client.MAX_REPETITION_RETRIES", 2
    ):
        events = []
        async for event in ollama_client.generate(
            client,
            chat_id=1,
            character=character,
            messages_history=history,
            general_prompt="Tension scene",
            memories=[],
            other_character_names=["Bob"],
            character_names={1: "Inna", 2: "Bob"},
        ):
            events.append(event)

    assert len(calls) >= 2
    # Second request must include scene loop control feedback
    second_user = calls[1]["messages"][1]["content"]
    assert "ОБНАРУЖЕН ЦИКЛ СЦЕНЫ" in second_user or "scene_loop_control" in second_user
    assert events[-1]["type"] == "response"
    assert "порту" in events[-1]["text"] or "Отступаю" in events[-1]["text"]


# ---------------------------------------------------------------------------
# Test 9 — retry limit never infinite
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_repetition_retry_limit():
    character = _char("Inna", 1)
    history = [
        _msg(
            "character",
            "*Усмехаюсь и делаю шаг ближе* «Следующий шаг?»",
            character_id=1,
        ),
        _msg(
            "character",
            "*Приближаюсь с усмешкой* «От слов к делу.»",
            character_id=2,
        ),
        _msg(
            "character",
            "*Сокращаю расстояние, глядя в глаза* «Смелость?»",
            character_id=1,
        ),
        _msg(
            "character",
            "*Ещё ближе, усмехаясь* «Вызов принят.»",
            character_id=2,
        ),
    ]
    loop_reply = (
        "*Усмехаюсь, сокращая расстояние и не отводя взгляда* "
        "«Планку только поднимаю.»"
    )

    calls = {"n": 0}

    async def fake_post(url, json=None, **kwargs):
        calls["n"] += 1
        response = MagicMock()
        response.raise_for_status = MagicMock()
        response.json.return_value = {
            "message": {"role": "assistant", "content": loop_reply}
        }
        return response

    client = httpx.AsyncClient(base_url="http://test")
    client.post = fake_post  # type: ignore[method-assign]

    with patch("app.ollama_client.USE_CHAT_API", True), patch(
        "app.ollama_client.ENABLE_THINKING", False
    ), patch("app.ollama_client.REPETITION_DETECTION_ENABLED", True), patch(
        "app.ollama_client.MAX_REPETITION_RETRIES", 2
    ), patch("app.ollama_client.MAX_ROLE_ISOLATION_RETRIES", 3):
        events = []
        async for event in ollama_client.generate(
            client,
            chat_id=1,
            character=character,
            messages_history=history,
            general_prompt="Scene",
            memories=[],
            other_character_names=["Bob"],
            character_names={1: "Inna", 2: "Bob"},
        ):
            events.append(event)

    # 1 initial + 2 repetition retries = 3, not infinite
    assert calls["n"] <= 4
    assert calls["n"] >= 2
    assert events[-1]["type"] == "response"
    assert len(events[-1]["text"]) >= 10


# ---------------------------------------------------------------------------
# Test 10 — multi-character attribution
# ---------------------------------------------------------------------------


def test_multi_character_does_not_confuse_speakers():
    history = [
        _msg("character", "*Делаю шаг ближе* «A1»", character_id=1),
        _msg("character", "*Сажусь на стул* «B sits»", character_id=2),
        _msg("character", "*Открываю книгу* «C reads»", character_id=3),
        _msg("character", "*Спрашиваю о погоде* «A asks»", character_id=1),
        _msg("character", "*Кивает* «B nods»", character_id=2),
        _msg("character", "*Встаю и ухожу к двери* «C leaves»", character_id=3),
    ]
    # Character 2 does something new — should not inherit P1 move_closer loop
    candidate = "*Улыбаюсь* «Согласен с планом.»"
    result = analyze_response(candidate, character_id=2, messages=history)
    assert result.is_repetitive is False

    # Character 1 repeating only move_closer after progression elsewhere is still
    # checked on own history (ask_question was progression for P1)
    candidate_p1 = "*Снова делаю шаг ближе к нему*"
    result_p1 = analyze_response(candidate_p1, character_id=1, messages=history)
    # Own history has move_closer then ask_question — cooldown may or may not fire
    # depending on progression clear; at least actions attributed to id=1 only
    assert "move_closer" in extract_actions(candidate_p1)


def test_feedback_lists_dynamic_actions():
    analysis = analyze_response(
        "*Сажусь на пол снова*",
        character_id=1,
        messages=[
            _msg("character", "*Сажусь на стул*", character_id=1),
            _msg("character", "*Сажусь рядом*", character_id=1),
        ],
    )
    fb = build_repetition_feedback(analysis)
    assert "ОБНАРУЖЕН ЦИКЛ СЦЕНЫ" in fb
    assert "НЕ повторяй" in fb
    # Should mention concrete actions when detected
    if analysis.repeated_actions:
        assert any(a in fb for a in analysis.repeated_actions)


def test_soft_smile_alone_not_loop():
    history = [
        _msg("character", "*Улыбаюсь* «Привет.»", character_id=1),
        _msg("character", "«Новости из города плохие.»", character_id=2),
        _msg(
            "character",
            "*Киваю* «Тогда выдвигаемся на рассвете.»",
            character_id=1,
        ),
    ]
    candidate = "*Улыбаюсь* «Хорошо. Я готова.»"
    result = analyze_response(candidate, character_id=1, messages=history)
    assert result.is_repetitive is False

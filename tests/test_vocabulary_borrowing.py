"""Regression tests for vocabulary borrowing detection + retry budgets.

Regression target: intimate scenes where two characters legitimately share
physical vocabulary were being flagged as "borrowing", causing retry chains.
The detector must only flag genuinely distinctive foreign vocabulary.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app import ollama_client


def _msg(
    role: str,
    content: str,
    *,
    character_id: int | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(role=role, content=content, character_id=character_id)


def _char(name: str = "Анна", cid: int = 1) -> SimpleNamespace:
    return SimpleNamespace(
        id=cid,
        name=name,
        personality="Спокойная",
        traits="Наблюдательная",
        background="",
        speech_style="Спокойная, немногословная речь.",
        example_messages="",
        boundaries="",
        relationships="",
        temperature=None,
        location="",
    )


class TestCheckVocabularyBorrowing:
    def test_intimate_shared_physical_vocab_not_flagged(self):
        character = _char("Анна", 1)
        history = [
            _msg("character", "*Кладу руку на его плечо* «Не спеши.»", character_id=1),
            _msg("character", "*Берёт её за руку и тянет к себе* «Ты дрожишь.»", character_id=2),
            _msg("character", "*Смотрю ему в глаза* «Это от холода.»", character_id=1),
        ]
        candidate = "*Касаюсь его груди и шепчу* «Ты тоже дрожишь.»"
        result = ollama_client._check_vocabulary_borrowing(
            candidate, character, ["Боб"], history, {1: "Анна", 2: "Боб"}
        )
        assert result == ""

    def test_prose_speech_style_not_flagged_as_borrowing(self):
        character = _char("Анна", 1)
        character.speech_style = (
            "Живая современная речь 20-летней девушки из провинции."
        )
        history = [
            _msg("character", "*Сажусь напротив* «Расскажи о себе.»", character_id=1),
            _msg("character", "*Улыбается* «Я приехал из глухой провинции.»", character_id=2),
        ]
        candidate = "*Киваю* «Провинция изменила тебя.»"
        result = ollama_client._check_vocabulary_borrowing(
            candidate, character, ["Боб"], history, {1: "Анна", 2: "Боб"}
        )
        assert result == ""

    def test_copies_distinctive_foreign_words_flagged(self):
        character = _char("Анна", 1)
        history = [
            _msg("character", "*Смотрю на него* «Кто ты?»", character_id=1),
            _msg(
                "character",
                "*Пожимает плечами* «Я квартирант из Одессы, "
                "живу с потрепанным портфелем.»",
                character_id=2,
            ),
        ]
        candidate = "*Прищуриваюсь* «Квартирант из Одессы с портфелем, значит.»"
        result = ollama_client._check_vocabulary_borrowing(
            candidate, character, ["Боб"], history, {1: "Анна", 2: "Боб"}
        )
        assert result != ""

    def test_empty_own_style_uses_own_history(self):
        character = _char("Анна", 1)
        character.speech_style = ""
        history = [
            _msg("character", "*Провожу рукой по столу* «Расскажи.»", character_id=1),
            _msg("character", "*Кивает* «Дело было ночью у реки.»", character_id=2),
        ]
        candidate = "*Смотрю на него* «Продолжай про реку.»"
        result = ollama_client._check_vocabulary_borrowing(
            candidate, character, ["Боб"], history, {1: "Анна", 2: "Боб"}
        )
        assert result == ""


class TestBorrowingRetryBudget:
    @pytest.mark.asyncio
    async def test_borrowing_retries_use_own_budget(self):
        character = _char("Анна", 1)
        history = [
            _msg("character", "*Смотрю на него* «Кто ты?»", character_id=1),
            _msg(
                "character",
                "*Пожимает плечами* «Я квартирант из Одессы, "
                "живу с потрепанным портфелем.»",
                character_id=2,
            ),
        ]
        reply = "*Прищуриваюсь* «Квартирант из Одессы с портфелем, значит.»"

        fake_once = AsyncMock(return_value=(reply, reply, True, 0, [], 8192, None))
        client = httpx.AsyncClient(base_url="http://test")

        with patch("app.ollama_client.settings.enable_vocabulary_control", True), patch(
            "app.ollama_client.settings.repetition_detection_enabled", False
        ), patch("app.ollama_client.settings.enable_thinking", False), patch(
            "app.ollama_client.settings.max_borrowing_retries", 2
        ), patch("app.ollama_client._generate_once", fake_once):
            events = []
            async for ev in ollama_client.generate(
                client,
                chat_id=1,
                character=character,
                messages_history=history,
                general_prompt="Scene",
                memories=[],
                other_character_names=["Боб"],
                character_names={1: "Анна", 2: "Боб"},
            ):
                events.append(ev)

        # 1 initial + 2 borrowing retries = 3 calls, then accepted (no fallback).
        assert fake_once.await_count == 3
        assert events[-1]["type"] == "response"
        assert len(events[-1]["text"]) > 0

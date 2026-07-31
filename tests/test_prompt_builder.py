"""Tests for prompt_builder character card and system prompt assembly."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.prompt_builder import (
    build_anti_mimicry_block,
    build_character_card,
    build_character_summary_block,
    build_examples_block,
    build_memories_block,
    build_recent_dialogue_block,
    build_system_prompt,
)
from app.role_isolation import build_role_isolation_block


def _character(**kwargs) -> SimpleNamespace:
    defaults = {
        "name": "Алиса",
        "personality": "",
        "traits": "",
        "background": "",
        "speech_style": "",
        "relationships": "",
        "boundaries": "",
        "example_messages": "",
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


class TestBuildCharacterCard:
    def test_minimal_character_card(self):
        card = build_character_card(_character(name="Боб"))
        assert "<character>" in card
        assert "<identity>Ты — Боб.</identity>" in card
        assert "<personality>" not in card
        assert "<traits>" not in card
        assert "<speech_style>" not in card

    def test_full_character_card(self):
        card = build_character_card(
            _character(
                name="Алиса",
                personality="Добрая",
                traits="Любит чай",
                background="Из северного края",
                speech_style="Короткие фразы",
                relationships="Друг игрока",
                boundaries="Не знает магии",
            )
        )
        assert "<personality>Добрая</personality>" in card
        assert "<traits>Любит чай</traits>" in card
        assert "<background>Из северного края</background>" in card
        assert "<speech_style>Короткие фразы</speech_style>" in card
        assert "<relationships>Друг игрока</relationships>" in card
        assert "<boundaries>Не знает магии</boundaries>" in card


class TestBuildExamplesBlock:
    def test_examples_parsing_with_separator(self):
        block = build_examples_block("Первая реплика\n---\nВторая реплика")
        assert "<examples>" in block
        assert "Первая реплика" in block
        assert "Вторая реплика" in block
        assert block.count("---") >= 2

    def test_empty_examples(self):
        assert build_examples_block("") == ""
        assert build_examples_block("   ") == ""

    def test_multiline_without_separator_is_one_example(self):
        text = "*смотрит в сторону*\n— Не знаю… может, и прав."
        block = build_examples_block(text)
        assert "<examples>" in block
        assert "*смотрит в сторону*" in block
        assert "— Не знаю… может, и прав." in block
        # Only the structural separator before the single example (not a split)
        # Body format: header, blank, ---, example — so exactly one ---
        assert block.count("---") == 1
        # Newlines inside the example are preserved
        assert "*смотрит в сторону*\n— Не знаю… может, и прав." in block

    def test_multiline_examples_split_only_by_separator(self):
        text = (
            "*смотрит в сторону*\n"
            "— Не знаю… может, и прав.\n"
            "---\n"
            "*усмехается*\n"
            "— Ладно. Попробуем ещё раз."
        )
        block = build_examples_block(text)
        assert "<examples>" in block
        assert "*смотрит в сторону*\n— Не знаю… может, и прав." in block
        assert "*усмехается*\n— Ладно. Попробуем ещё раз." in block
        # Two examples → two structural --- markers in the body
        assert block.count("---") >= 2

    def test_empty_parts_around_separator_ignored(self):
        block = build_examples_block("---\nПервая\n---\n\n---\nВторая\n---")
        assert "Первая" in block
        assert "Вторая" in block
        # No empty example blocks: each example line is non-empty content
        assert "\n---\n\n---\n" not in block


class TestBuildSystemPrompt:
    def test_isolation_at_end(self):
        prompt = build_system_prompt(
            _character(name="Алиса", speech_style="Сарказм"),
            general_prompt="Таверна",
        )
        rules_pos = prompt.find("<rules>")
        isolation_pos = prompt.find("ТЕКУЩИЙ ПЕРСОНАЖ: Алиса")
        character_close = prompt.find("</character>")

        assert rules_pos != -1
        assert isolation_pos != -1
        assert rules_pos < isolation_pos
        assert character_close < rules_pos
        assert "ТЕКУЩИЙ ПЕРСОНАЖ" not in prompt[:character_close]

    def test_strict_mode_adds_retry_warning(self):
        normal = build_role_isolation_block("Алиса", strict=False)
        strict = build_role_isolation_block("Алиса", strict=True)
        assert len(strict) > len(normal)

        prompt = build_system_prompt(_character(name="Алиса"), "", strict=True)
        assert strict.strip() in prompt

    def test_scene_and_examples_included(self):
        prompt = build_system_prompt(
            _character(
                name="Алиса",
                example_messages="*улыбается* — Привет.\n---\n— Как дела?",
            ),
            general_prompt="Лесная тропа",
        )
        assert "<scene>" not in prompt
        assert "<examples>" in prompt
        assert "Привет." in prompt


class TestMemoryPromptBlocks:
    def test_character_summary_block(self):
        block = build_character_summary_block("Игрок пришёл в таверну.")
        assert "<character_summary>" in block
        assert "Игрок пришёл в таверну." in block
        assert build_character_summary_block("") == ""

    def test_memories_block(self):
        block = build_memories_block([SimpleNamespace(content="Факт один")])
        assert "<character_memories>" in block
        assert "- Факт один" in block
        assert build_memories_block([]) == ""

    def test_recent_dialogue_block(self):
        block = build_recent_dialogue_block("Игрок: Привет")
        assert "<recent_dialogue>" in block
        assert "Игрок: Привет" in block
        assert build_recent_dialogue_block("") == ""


class TestBuildAntiMimicryBlock:
    """Tests for anti-mimicry block generation (P2)."""

    def test_build_anti_mimicry_block_with_prior_replies(self):
        """Anti-mimicry block includes all prior reply names and current character name."""
        prior = [("Bob", "Bob says hi"), ("Carol", "Carol waves")]
        block = build_anti_mimicry_block("Alice", prior)

        assert "Bob" in block
        assert "Carol" in block
        assert "Alice" in block
        assert "НЕ повторяй" in block
        assert "интонацию" in block
        assert "формулировки" in block

    def test_build_anti_mimicry_block_empty_returns_empty(self):
        """Empty prior replies returns empty string."""
        assert build_anti_mimicry_block("Alice", []) == ""

    def test_build_anti_mimicry_block_single_prior_reply(self):
        """Single prior reply is included correctly."""
        prior = [("Bob", "Bob says hello")]
        block = build_anti_mimicry_block("Alice", prior)

        assert "В этом ходе уже ответили: Bob." in block
        assert "Bob says hello" not in block  # Content not included, only names
        assert "Alice" in block

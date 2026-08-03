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
    build_scene_block,
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
        "appearance": "",
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
                appearance="Высокая, каштановые волосы, шрам на щеке",
            )
        )
        assert "<personality>Добрая</personality>" in card
        assert "<traits>Любит чай</traits>" in card
        assert "<background>Из северного края</background>" in card
        assert "<speech_style>Короткие фразы</speech_style>" in card
        assert "<boundaries>Не знает магии</boundaries>" in card
        assert "<appearance>Высокая, каштановые волосы, шрам на щеке</appearance>" in card
        # relationships is added dynamically via build_relationships_block,
        # never rendered inside the static character card.
        assert "<relationships>" not in card

    def test_empty_appearance_not_in_card(self):
        card = build_character_card(_character(name="Боб"))
        assert "<appearance>" not in card


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


class TestBuildSceneBlock:
    """Appearance reaches the scene block only for co-present characters."""

    def _scene_state(self, cl_map):
        return SimpleNamespace(
            time_of_day="",
            custom_state=None,
            character_locations=cl_map,
        )

    def test_appearance_included_for_same_location(self):
        scene_state = self._scene_state(
            {"Алиса": "Таверна", "Боб": "Таверна", "Игрок": "Таверна"}
        )
        block = build_scene_block(
            "Сюжет: вечер в таверне.",
            scene_state,
            current_character_name="Алиса",
            character_locations=scene_state.character_locations,
            character_appearances={
                "Алиса": "Высокая, каштановые волосы",
                "Боб": "Бородатый кузнец с татуировками",
                "Игрок": "",
            },
        )
        assert "Рядом с тобой: Боб, Игрок" in block
        assert "Внешность рядом стоящих: Боб — Бородатый кузнец с татуировками" in block
        # The current character's own appearance is not listed in the scene block
        assert "Высокая, каштановые волосы" not in block

    def test_appearance_excluded_for_other_location(self):
        scene_state = self._scene_state(
            {"Алиса": "Таверна", "Боб": "Подвал", "Игрок": "Таверна"}
        )
        block = build_scene_block(
            "Сюжет: вечер в таверне.",
            scene_state,
            current_character_name="Алиса",
            character_locations=scene_state.character_locations,
            character_appearances={"Боб": "Скрытная фигура в капюшоне"},
        )
        assert "Боб" not in block  # not co-present, name is not listed at all
        assert "Внешность рядом стоящих" not in block
        assert "капюшон" not in block

    def test_empty_appearances_produce_no_line(self):
        scene_state = self._scene_state({"Алиса": "Таверна", "Боб": "Таверна"})
        block = build_scene_block(
            "Сюжет.",
            scene_state,
            current_character_name="Алиса",
            character_locations=scene_state.character_locations,
            character_appearances={"Боб": ""},
        )
        assert "Рядом с тобой: Боб" in block
        assert "Внешность рядом стоящих" not in block

    def test_no_appearances_arg_backward_compatible(self):
        scene_state = self._scene_state({"Алиса": "Таверна", "Боб": "Таверна"})
        block = build_scene_block(
            "Сюжет.",
            scene_state,
            current_character_name="Алиса",
            character_locations=scene_state.character_locations,
        )
        assert "Рядом с тобой: Боб" in block


class TestBuildSceneBlockLocationDescriptions:
    """Location.description renders under the current character's location (§18)."""

    def _scene_state(self, cl_map):
        return SimpleNamespace(
            time_of_day="",
            custom_state=None,
            character_locations=cl_map,
        )

    def test_description_rendered_after_location(self):
        scene_state = self._scene_state({"Алиса": "Гостиная", "Боб": "Кухня"})
        block = build_scene_block(
            "Сюжет.",
            scene_state,
            current_character_name="Алиса",
            character_locations=scene_state.character_locations,
            location_descriptions={
                "Гостиная": "Большая светлая гостиная с диваном и камином.",
                "Кухня": "Тесная кухня с чугунной плитой.",
            },
        )
        assert "Твоя локация: Гостиная" in block
        assert "Большая светлая гостиная с диваном и камином." in block
        # Another location's description must not leak into this character's block
        assert "чугунной плитой" not in block

    def test_no_description_renders_location_only(self):
        scene_state = self._scene_state({"Алиса": "Гостиная"})
        block = build_scene_block(
            "Сюжет.",
            scene_state,
            current_character_name="Алиса",
            character_locations=scene_state.character_locations,
            location_descriptions={"Гостиная": "   "},
        )
        assert "Твоя локация: Гостиная" in block
        assert block.count("Гостиная") == 1

    def test_no_descriptions_arg_backward_compatible(self):
        scene_state = self._scene_state({"Алиса": "Гостиная"})
        block = build_scene_block(
            "Сюжет.",
            scene_state,
            current_character_name="Алиса",
            character_locations=scene_state.character_locations,
        )
        assert "Твоя локация: Гостиная" in block
        assert "Твоя локация: Гостиная\n\n" not in block
        assert "Внешность рядом стоящих" not in block

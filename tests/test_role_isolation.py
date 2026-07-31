"""Unit tests for role isolation utilities."""

from __future__ import annotations

from types import SimpleNamespace

from app.role_isolation import (
    ValidationResult,
    build_fallback_prompt,
    build_generation_cue,
    build_post_history_reinforcement,
    build_role_isolation_block,
    build_stop_sequences,
    contains_perspective_violation,
    find_foreign_speaker_marker,
    get_other_character_names,
    sanitize_and_validate_response,
    sanitize_character_response,
)


def _characters(*names: str) -> list[SimpleNamespace]:
    return [SimpleNamespace(id=i + 1, name=name) for i, name in enumerate(names)]


class TestRoleIsolationUnit:
    def test_three_characters_sanitize_removes_foreign_markers(self):
        other_names = ["Character B", "Character C"]
        raw = (
            "Я оглядываюсь по сторонам.\n"
            "Character B: Это не должно попасть в ответ.\n"
            "Character C: Тоже не должно."
        )
        result = sanitize_character_response(raw, "Character A", other_names)
        assert "Character B:" not in result
        assert "Character C:" not in result
        assert result == "Я оглядываюсь по сторонам."

    def test_five_characters_dynamic_stop_sequences(self):
        all_names = [f"Character {label}" for label in "ABCDE"]
        stops = build_stop_sequences(all_names[1:])

        for name in all_names[1:]:
            assert f"\n{name}:" in stops
            assert f"\n**{name}:**" in stops
        assert "\nИгрок:" in stops
        assert "\nСистема:" in stops

    def test_single_character_stop_sequences(self):
        stops = build_stop_sequences([])
        assert stops == ["\nИгрок:", "\nСистема:"]

    def test_two_characters_isolation_block_is_dynamic(self):
        block = build_role_isolation_block("Character A")
        assert "ТЕКУЩИЙ ПЕРСОНАЖ: Character A" in block

    def test_multi_speaker_output_is_truncated(self):
        raw = (
            "Character A: Я начинаю говорить.\n"
            "Character B: А я продолжаю за другого.\n"
            "Character C: И ещё один."
        )
        result = sanitize_character_response(
            raw,
            "Character A",
            ["Character B", "Character C"],
        )
        assert result == "Я начинаю говорить."

    def test_name_mention_in_text_is_not_truncated(self):
        text = "Я смотрю на Character B и улыбаюсь."
        assert find_foreign_speaker_marker(text, ["Character B"]) is None
        result = sanitize_character_response(
            text,
            "Character A",
            ["Character B"],
        )
        assert result == text

    def test_get_other_character_names(self):
        characters = _characters("Character A", "Character B", "Character C")
        assert get_other_character_names(characters, 2) == [
            "Character A",
            "Character C",
        ]

    def test_generation_cue_uses_dynamic_name(self):
        cue = build_generation_cue("Капитан Рейн")
        assert cue.endswith("Капитан Рейн:")

    def test_bold_foreign_marker_is_detected(self):
        raw = "Моя реплика.\n**Character B:** Чужая реплика."
        result = sanitize_character_response(
            raw,
            "Character A",
            ["Character B"],
        )
        assert result == "Моя реплика."


class TestPostHistoryReinforcement:
    """Tests for anti-override reinforcement placed after history."""

    def test_reinforcement_mentions_current_character(self):
        block = build_post_history_reinforcement("Алиса")
        assert "ТОЛЬКО Алиса" in block
        assert "НЕ описывай" in block
        assert "за других" in block

    def test_reinforcement_is_strong(self):
        block = build_post_history_reinforcement("Боб")
        assert "КРИТИЧЕСКИ ВАЖНО" in block or "ТОЛЬКО" in block


class TestSemanticContamination:
    """Expanded tests for semantic contamination protection."""

    def test_detects_knowledge_of_other_thoughts(self):
        text = "Я вижу, что Character B подумал, что я лгу."
        hard, soft = contains_perspective_violation(text, ["Character B"])
        assert hard is True
        assert soft is False

    def test_detects_emotion_leakage(self):
        text = "Character C чувствовал страх, хотя я этого не говорил."
        hard, soft = contains_perspective_violation(text, ["Character C"])
        assert hard is True
        assert soft is False

    def test_detects_offscreen_knowledge(self):
        text = "Пока меня не было, он решил убежать."
        hard, soft = contains_perspective_violation(text, ["Character B"])
        assert hard is True
        assert soft is False

    def test_detects_speaking_for_others(self):
        text = "Она ответит, что согласна."
        hard, soft = contains_perspective_violation(text, ["Character B"])
        assert hard is True
        assert soft is False

    def test_allows_normal_observation(self):
        text = "Я смотрю на Character B. Он выглядит усталым."
        hard, soft = contains_perspective_violation(text, ["Character B"])
        assert hard is False
        assert soft is False

    def test_sanitize_and_validate_rejects_perspective_violation(self):
        text = "Я знаю, что Character B боится темноты, хотя он мне не говорил."
        result = sanitize_and_validate_response(
            text, "Character A", ["Character B"]
        )
        assert isinstance(result, ValidationResult)
        assert result.is_valid is False
        assert result.hard_violation is True
        assert result.soft_violation is False

    def test_sanitize_and_validate_accepts_clean_response(self):
        text = "Я оглядываюсь. Мне кажется, что здесь опасно."
        result = sanitize_and_validate_response(
            text, "Character A", ["Character B", "Character C"]
        )
        assert isinstance(result, ValidationResult)
        assert result.is_valid is True
        assert result.hard_violation is False
        assert result.soft_violation is False

    def test_detects_assumed_private_memory(self):
        text = "Как ты и говорил мне вчера, когда мы были одни."
        hard, soft = contains_perspective_violation(text, ["Character B"])
        assert hard is True
        assert soft is False


class TestHardSoftSplit:
    """Tests for hard/soft perspective violation split."""

    def test_hard_violation_internal_state(self):
        text = "Он подумал, что я лгу."
        hard, soft = contains_perspective_violation(text, ["Character B"])
        assert hard is True
        assert soft is False

    def test_hard_violation_speaking_for_others(self):
        text = "Он скажет, что согласен."
        hard, soft = contains_perspective_violation(text, ["Character B"])
        assert hard is True
        assert soft is False

    def test_hard_violation_knowledge_claim(self):
        text = "Я знаю, что он хотел уйти."
        hard, soft = contains_perspective_violation(text, ["Character B"])
        assert hard is True
        assert soft is False

    def test_soft_violation_observable_action_smile(self):
        text = "Он улыбнулся и кивнул."
        hard, soft = contains_perspective_violation(text, ["Character B"])
        assert hard is False
        assert soft is True

    def test_soft_violation_observable_action_look(self):
        text = "Она посмотрела на него."
        hard, soft = contains_perspective_violation(text, ["Character B"])
        assert hard is False
        assert soft is True

    def test_soft_violation_observable_action_turn_away(self):
        text = "Он отвернулся и встал."
        hard, soft = contains_perspective_violation(text, ["Character B"])
        assert hard is False
        assert soft is True

    def test_soft_violation_observable_action_sit(self):
        text = "Она села и усмехнулась."
        hard, soft = contains_perspective_violation(text, ["Character B"])
        assert hard is False
        assert soft is True

    def test_soft_violation_wink_shrug_sigh(self):
        text = "Он подмигнул, пожал плечами и вздохнул."
        hard, soft = contains_perspective_violation(text, ["Character B"])
        assert hard is False
        assert soft is True

    def test_soft_violation_laugh_cough(self):
        text = "Она засмеялась и захлебнулась."
        hard, soft = contains_perspective_violation(text, ["Character B"])
        assert hard is False
        assert soft is True

    def test_sanitize_accepts_soft_only(self):
        text = "Я смотрю на Character B. Он улыбнулся."
        result = sanitize_and_validate_response(text, "Character A", ["Character B"])
        assert isinstance(result, ValidationResult)
        assert result.is_valid is True
        assert result.soft_violation is True
        assert result.hard_violation is False

    def test_sanitize_rejects_hard(self):
        text = "Я знаю, что Character B подумал об этом."
        result = sanitize_and_validate_response(text, "Character A", ["Character B"])
        assert isinstance(result, ValidationResult)
        assert result.is_valid is False
        assert result.hard_violation is True
        assert result.soft_violation is False

    def test_sanitize_both_hard_and_soft_hard_wins(self):
        text = "Он улыбнулся, но я знаю, что он подумал о плохом."
        result = sanitize_and_validate_response(text, "Character A", ["Character B"])
        assert isinstance(result, ValidationResult)
        assert result.is_valid is False
        assert result.hard_violation is True
        assert result.soft_violation is True

    def test_name_with_internal_verb_is_hard(self):
        text = "Character B подумал, что я уйду."
        hard, soft = contains_perspective_violation(text, ["Character B"])
        assert hard is True
        assert soft is False

    def test_case_insensitive_patterns(self):
        text = "ОН ПОДУМАЛ. ОН УЛЫБНУЛСЯ."
        hard, soft = contains_perspective_violation(text, ["Character B"])
        assert hard is True
        assert soft is True


class TestFallbackPrompt:
    def test_fallback_prompt_is_very_strict(self):
        prompt = build_fallback_prompt("Элиза", "Мрачный замок")
        assert "ТОЛЬКО за него" in prompt
        assert "НИКОГДА не пиши за других" in prompt
        assert "Элиза:" in prompt

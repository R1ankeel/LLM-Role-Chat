"""Unit tests for role isolation utilities."""

from __future__ import annotations

from types import SimpleNamespace

from app.prompt_builder import (
    build_isolated_block,
    build_negative_prompting_block,
    build_rules_block,
)
from app.role_isolation import (
    ValidationResult,
    build_fallback_prompt,
    build_generation_cue,
    build_generation_cue_for_chat,
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
        assert "ТЫ — Character A." in block
        assert "Ты управляешь ТОЛЬКО своим персонажем Character A." in block
        assert "ВЗАИМОДЕЙСТВИЕ:" in block

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


class TestIsolationBehaviorFreedom:
    """ТЗ §18 tests 1-3, 21-24: isolation restricts authorship, not behavior."""

    FORBIDDEN_BEHAVIOR_PHRASES = (
        "НЕ покидай",
        "не покидай",
        "не иди к игроку",
        "не обращайся к нему",
        "не двигайся в локацию",
        "играй здесь и сейчас",
    )

    def _all_prompt_text(self, name: str) -> str:
        """Full rendered prompt surface: cues, isolation, rules, negative, isolated."""
        texts = [
            build_role_isolation_block(name),
            build_generation_cue(name),
            build_generation_cue_for_chat(name),
            build_rules_block(),
            build_negative_prompting_block(),
            build_isolated_block(),
        ]
        return "\n".join(texts)

    # §18 test 1: персонаж может уйти из своей локации
    def test_may_leave_location_no_ban(self):
        text = self._all_prompt_text("Алиса")
        for phrase in self.FORBIDDEN_BEHAVIOR_PHRASES:
            assert phrase.lower() not in text.lower(), f"Запрет поведения найден: {phrase}"
        assert "покидать текущую локацию" in build_role_isolation_block("Алиса")
        assert "входить в другую локацию" in build_role_isolation_block("Алиса")

    # §18 test 2: персонаж может обратиться к другому персонажу
    def test_may_address_other_character(self):
        block = build_role_isolation_block("Алиса")
        assert "обращаться к ним" in block
        assert "отвечать на их реплики" in block
        assert "реагировать на других персонажей" in block

    # §18 test 3: персонаж не пишет действия другого — hard-violation сохраняется
    def test_does_not_act_for_others_still_enforced(self):
        block = build_role_isolation_block("Алиса")
        assert "НЕ пиши реплики других персонажей" in block
        assert "НЕ принимай решения за других персонажей" in block

        hard, soft = contains_perspective_violation(
            "Боб решил уйти и закрыть дверь", ["Боб"]
        )
        assert hard is True

    # §18 test 21: ответ на обращение может быть коротким
    def test_short_reply_allowed(self):
        cue = build_generation_cue_for_chat("Алиса")
        legacy = build_generation_cue("Алиса")
        assert "может быть коротким" in cue
        assert "может быть коротким" in legacy

    # §18 test 22: нет обязательных 150-250 слов / 3-5 абзацев
    def test_no_hard_length_mandate(self):
        text = self._all_prompt_text("Алиса")
        assert "150-250" not in text
        assert "3-5 абзацев" not in text
        assert "минимум 3-5" not in text

    # §18 test 23: персонаж может начать действие сам
    def test_may_start_action_initiative(self):
        cue = build_generation_cue_for_chat("Алиса")
        assert "развивать свою сцену" in cue
        block = build_role_isolation_block("Алиса")
        assert "самостоятельно начинать разговор" in block
        assert "Ты сам решаешь, как действовать" in block

    # §18 test 24: персонаж может проигнорировать стимул
    def test_may_ignore_stimulus(self):
        text = self._all_prompt_text("Алиса")
        assert "обязан реагировать" not in text
        assert "обязан" not in text
        assert "Ты сам решаешь" in build_role_isolation_block("Алиса")

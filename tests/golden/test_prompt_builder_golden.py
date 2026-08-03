"""Golden tests for prompt builder - snapshot testing of generated prompts."""

from __future__ import annotations

import json
from pathlib import Path
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
from app.role_isolation import (
    build_generation_cue,
    build_generation_cue_for_chat,
)


# Load snapshots from JSON file
SNAPSHOTS_PATH = Path(__file__).parent / "snapshots.json"
with SNAPSHOTS_PATH.open(encoding="utf-8") as f:
    SNAPSHOTS = json.load(f)

SYSTEM_PROMPT_MINIMAL = SNAPSHOTS["SYSTEM_PROMPT_MINIMAL"]
SYSTEM_PROMPT_FULL = SNAPSHOTS["SYSTEM_PROMPT_FULL"]
SYSTEM_PROMPT_STRICT = SNAPSHOTS["SYSTEM_PROMPT_STRICT"]
ANTI_MIMICRY_BLOCK = SNAPSHOTS["ANTI_MIMICRY_BLOCK"]
MEMORIES_BLOCK_WITH_IMPORTANCE = SNAPSHOTS["MEMORIES_BLOCK_WITH_IMPORTANCE"]
GENERATION_CUE_CHAT = SNAPSHOTS["GENERATION_CUE_CHAT"]
GENERATION_CUE_LEGACY = SNAPSHOTS["GENERATION_CUE_LEGACY"]


class _Char(SimpleNamespace):
    """Character mock with all fields."""
    def __init__(self, **kwargs):
        defaults = {
            "name": "Test",
            "personality": "",
            "traits": "",
            "background": "",
            "speech_style": "",
            "relationships": "",
            "boundaries": "",
            "example_messages": "",
        }
        defaults.update(kwargs)
        super().__init__(**defaults)


# ==================== TESTS ====================

class TestGoldenSystemPrompt:
    """Golden tests for build_system_prompt - compare against stored snapshots."""

    def test_system_prompt_minimal(self):
        """Minimal character should produce expected prompt structure."""
        char = _Char(name="Алиса")
        result = build_system_prompt(char, general_prompt="")
        assert result == SYSTEM_PROMPT_MINIMAL

    def test_system_prompt_full(self):
        """Full character should include all sections in correct order."""
        char = _Char(
            name="Алиса",
            personality="Добрая и заботливая",
            traits="Любит чай, читает книги",
            background="Выросла в северной деревне",
            speech_style="Мягкий, использует метафоры природы",
            relationships="Подруга игрока, сестра Бориса",
            boundaries="Не знает боевой магии",
            example_messages=(
                "*улыбается* — Доброе утро, путник. Хочешь чаю?\n"
                "---\n"
                "*поглаживает кота* — Он всегда знает, когда я грустна."
            ),
        )
        result = build_system_prompt(char, general_prompt='Таверна "Уставший путник"')
        assert result == SYSTEM_PROMPT_FULL

    def test_system_prompt_strict_mode(self):
        """Strict mode should append retry warning."""
        char = _Char(name="Алиса")
        result = build_system_prompt(char, general_prompt="", strict=True)
        assert result == SYSTEM_PROMPT_STRICT

    def test_system_prompt_section_order(self):
        """Sections must appear in correct priority order."""
        char = _Char(
            name="Боб",
            personality="P1",
            example_messages="Ex1",
            speech_style="S1",
        )
        result = build_system_prompt(char, general_prompt="Scene")

        char_pos = result.find("<character>")
        ex_pos = result.find("<examples>")
        rules_pos = result.find("<rules>")
        isolation_pos = result.find("ТЫ — Боб")

        # Scene is no longer in system prompt (built by ContextBuilder instead)
        assert "<scene>" not in result
        assert char_pos < ex_pos < rules_pos < isolation_pos


class TestGoldenAntiMimicry:
    """Golden tests for anti-mimicry block."""

    def test_anti_mimicry_block_golden(self):
        """Anti-mimicry block matches expected format."""
        prior = [("Боб", "Боб приветствует"), ("Кэрол", "Кэрол машет рукой")]
        result = build_anti_mimicry_block("Алиса", prior)
        assert result == ANTI_MIMICRY_BLOCK

    def test_anti_mimicry_empty_returns_empty(self):
        """No prior replies returns empty string."""
        assert build_anti_mimicry_block("Алиса", []) == ""

    def test_anti_mimicry_single_reply(self):
        """Single prior reply included correctly."""
        prior = [("Боб", "Боб говорит")]
        result = build_anti_mimicry_block("Алиса", prior)
        assert "В этом ходе уже ответили: Боб." in result
        assert "Боб говорит" not in result
        assert "Алиса" in result


class TestGoldenMemoriesBlock:
    """Golden tests for memories block with importance."""

    def test_memories_block_with_importance(self):
        """Importance > 0.6 shows badge, others don't."""
        memories = [
            SimpleNamespace(content="Игрок подарил Алисе серебряный ключ", importance=0.9),
            SimpleNamespace(content="Алиса встретила таинственного странника у колодца", importance=0.5),
            SimpleNamespace(content="Борис ушел на охоту за вепря", importance=0.3),
        ]
        result = build_memories_block(memories)
        assert result == MEMORIES_BLOCK_WITH_IMPORTANCE

    def test_memories_block_empty(self):
        """Empty memories returns empty string."""
        assert build_memories_block([]) == ""


class TestGoldenGenerationCue:
    """Golden tests for generation cues."""

    def test_generation_cue_for_chat_golden(self):
        """Chat API cue matches expected format."""
        result = build_generation_cue_for_chat("Алиса")
        assert result == GENERATION_CUE_CHAT

    def test_generation_cue_legacy_format(self):
        """Legacy cue includes character name prefix."""
        result = build_generation_cue("Алиса")
        assert "Алиса:" in result
        assert "Ответь за" in result


class TestGoldenExamplesBlock:
    """Golden tests for examples block parsing."""

    def test_examples_with_separator(self):
        """Examples split by --- separator."""
        text = "First example\n---\nSecond example"
        result = build_examples_block(text)
        assert "<examples>" in result
        assert "First example" in result
        assert "Second example" in result
        assert result.count("---") == 2

    def test_examples_empty_returns_empty(self):
        """Empty or whitespace returns empty."""
        assert build_examples_block("") == ""
        assert build_examples_block("   ") == ""

    def test_examples_multiline_preserved(self):
        """Newlines within examples are preserved."""
        text = "*смотрит в сторону*\n— Не знаю… может, и прав."
        result = build_examples_block(text)
        assert "*смотрит в сторону*\n— Не знаю… может, и прав." in result


class TestGoldenCharacterCard:
    """Golden tests for character card building."""

    def test_character_card_minimal(self):
        """Minimal card has only identity."""
        char = _Char(name="Боб")
        card = build_character_card(char)
        assert "<character>" in card
        assert "<identity>Ты — Боб.</identity>" in card
        assert "<personality>" not in card

    def test_character_card_full(self):
        """Full card includes all non-empty sections."""
        char = _Char(
            name="Алиса",
            personality="Добрая",
            traits="Любит чай",
            background="Из северного края",
            speech_style="Короткие фразы",
            relationships="Друг игрока",
            boundaries="Не знает магии",
        )
        card = build_character_card(char)
        assert "<personality>Добрая</personality>" in card
        assert "<traits>Любит чай</traits>" in card
        assert "<background>Из северного края</background>" in card
        assert "<speech_style>Короткие фразы</speech_style>" in card
        assert "<boundaries>Не знает магии</boundaries>" in card


class TestGoldenOtherBlocks:
    """Golden tests for other prompt blocks."""

    def test_character_summary_block(self):
        """Summary block wraps text correctly."""
        block = build_character_summary_block("Игрок пришёл в таверну.")
        assert "<character_summary>" in block
        assert "Игрок пришёл в таверну." in block
        assert build_character_summary_block("") == ""

    def test_recent_dialogue_block(self):
        """Dialogue block wraps correctly."""
        block = build_recent_dialogue_block("Игрок: Привет")
        assert "<recent_dialogue>" in block
        assert "Игрок: Привет" in block
        assert build_recent_dialogue_block("") == ""

    def test_scene_block(self):
        """Scene block wraps general prompt."""
        block = build_scene_block("Лесная тропа")
        assert "<scene>" in block
        assert "Лесная тропа" in block
        assert build_scene_block("") == ""
"""Golden tests for role isolation - snapshot testing of validation logic."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.role_isolation import (
    build_role_isolation_block,
    build_post_history_reinforcement,
    build_generation_cue,
    build_generation_cue_for_chat,
    build_stop_sequences,
    sanitize_character_response,
    find_foreign_speaker_marker,
    is_response_valid,
    contains_perspective_violation,
    sanitize_and_validate_response,
    ValidationResult,
)

# Load snapshots from JSON file
SNAPSHOTS_PATH = Path(__file__).parent / "snapshots_iso.json"
with SNAPSHOTS_PATH.open(encoding="utf-8") as f:
    SNAPSHOTS = json.load(f)

ISOLATION_BLOCK_BASIC = SNAPSHOTS["ISOLATION_BLOCK_BASIC"]
ISOLATION_BLOCK_STRICT = SNAPSHOTS["ISOLATION_BLOCK_STRICT"]
REINFORCEMENT_BLOCK = SNAPSHOTS["REINFORCEMENT_BLOCK"]
GENERATION_CUE_LEGACY = SNAPSHOTS["GENERATION_CUE_LEGACY"]
GENERATION_CUE_CHAT = SNAPSHOTS["GENERATION_CUE_CHAT"]
STOP_SEQUENCES_BASIC = SNAPSHOTS["STOP_SEQUENCES_BASIC"]
SOFT_VIOLATION_RESULT = SNAPSHOTS["SOFT_VIOLATION_RESULT"]

# Load test constants from JSON file
CONSTANTS_PATH = Path(__file__).parent / "test_constants.json"
with CONSTANTS_PATH.open(encoding="utf-8") as f:
    CONSTANTS = json.load(f)

ALIAS = CONSTANTS["ALIAS"]
BOB = CONSTANTS["BOB"]
CAROL = CONSTANTS["CAROL"]
PLAYER = CONSTANTS["PLAYER"]
SYSTEM = CONSTANTS["SYSTEM"]
TEST_VALID_RESPONSE = CONSTANTS["TEST_VALID_RESPONSE"]
TEST_SHORT_RESPONSE = CONSTANTS["TEST_SHORT_RESPONSE"]
SOFT_VIOLATION_TEST = CONSTANTS["SOFT_VIOLATION_TEST"]
STRIP_TEST_1_INPUT = CONSTANTS["STRIP_TEST_1_INPUT"]
STRIP_TEST_1_EXPECTED = CONSTANTS["STRIP_TEST_1_EXPECTED"]
STRIP_TEST_2_INPUT = CONSTANTS["STRIP_TEST_2_INPUT"]
STRIP_TEST_2_EXPECTED = CONSTANTS["STRIP_TEST_2_EXPECTED"]
STRIP_TEST_3_INPUT = CONSTANTS["STRIP_TEST_3_INPUT"]
STRIP_TEST_3_EXPECTED = CONSTANTS["STRIP_TEST_3_EXPECTED"]
FOREIGN_MARKER_TEST = CONSTANTS["FOREIGN_MARKER_TEST"]
FOREIGN_MARKER_EXPECTED = CONSTANTS["FOREIGN_MARKER_EXPECTED"]
SANITIZE_TEST_1_INPUT = CONSTANTS["SANITIZE_TEST_1_INPUT"]
SANITIZE_TEST_1_EXPECTED = CONSTANTS["SANITIZE_TEST_1_EXPECTED"]

# Additional constants for test strings
CURRENT_CHAR_PREFIX = CONSTANTS["CURRENT_CHAR_PREFIX"]
ACTIONS_PREFIX = CONSTANTS["ACTIONS_PREFIX"]
REPLIKI_PREFIX = CONSTANTS["REPLIKI_PREFIX"]
OTHER_REPLIKI = CONSTANTS["OTHER_REPLIKI"]
YOU_ARE_ONLY = CONSTANTS["YOU_ARE_ONLY"]
DO_NOT_DESCRIBE = CONSTANTS["DO_NOT_DESCRIBE"]
DO_NOT_THINK = CONSTANTS["DO_NOT_THINK"]
DO_NOT_ACT_FOR_OTHERS = CONSTANTS["DO_NOT_ACT_FOR_OTHERS"]
ANSWER_ONLY_FOR_YOURSELF = CONSTANTS["ANSWER_ONLY_FOR_YOURSELF"]
STARTS_WITH_NEWLINE_DASH = CONSTANTS["STARTS_WITH_NEWLINE_DASH"]
ENDS_WITH_NEWLINE_DASH = CONSTANTS["ENDS_WITH_NEWLINE_DASH"]
PLAYER_PREFIX = CONSTANTS["PLAYER_PREFIX"]
SYSTEM_PREFIX = CONSTANTS["SYSTEM_PREFIX"]
ALIAS_COLON = CONSTANTS["ALIAS_COLON"]
ALIAS_BOLD = CONSTANTS["ALIAS_BOLD"]
STOP_SEQUENCE_LENGTH = CONSTANTS["STOP_SEQUENCE_LENGTH"]
VALID_RESPONSE = CONSTANTS["VALID_RESPONSE"]
SHORT_RESPONSE = CONSTANTS["SHORT_RESPONSE"]
SPACES_RESPONSE = CONSTANTS["SPACES_RESPONSE"]
EMPTY_RESPONSE = CONSTANTS["EMPTY_RESPONSE"]
HARD_VIOLATION_1 = CONSTANTS["HARD_VIOLATION_1"]
HARD_VIOLATION_2 = CONSTANTS["HARD_VIOLATION_2"]
HARD_VIOLATION_3 = CONSTANTS["HARD_VIOLATION_3"]
SOFT_VIOLATION_1 = CONSTANTS["SOFT_VIOLATION_1"]
SOFT_VIOLATION_2 = CONSTANTS["SOFT_VIOLATION_2"]
NAME_INTERNAL_VERB_1 = CONSTANTS["NAME_INTERNAL_VERB_1"]
NAME_INTERNAL_VERB_2 = CONSTANTS["NAME_INTERNAL_VERB_2"]
CLEAN_TEXT = CONSTANTS["CLEAN_TEXT"]
EMPTY_TEXT = CONSTANTS["EMPTY_TEXT"]
HELLO_TEXT = CONSTANTS["HELLO_TEXT"]
HE_TEXT = CONSTANTS["HE_TEXT"]
SHE_TEXT = CONSTANTS["SHE_TEXT"]
THINKS = CONSTANTS["THINKS"]
FEELS = CONSTANTS["FEELS"]
WANTS = CONSTANTS["WANTS"]
KNOWS = CONSTANTS["KNOWS"]
SMILES = CONSTANTS["SMILES"]
LOOKED_AT = CONSTANTS["LOOKED_AT"]
BOB_THINKS = CONSTANTS["BOB_THINKS"]
CAROL_DECIDES = CONSTANTS["CAROL_DECIDES"]
I_GO_HOME = CONSTANTS["I_GO_HOME"]
HE_SMILED = CONSTANTS["HE_SMILED"]
SHE_LOOKED = CONSTANTS["SHE_LOOKED"]
BOB_THOUGHT = CONSTANTS["BOB_THOUGHT"]
CAROL_DECIDED = CONSTANTS["CAROL_DECIDED"]
BOB_SMILED = CONSTANTS["BOB_SMILED"]
HE_SMILED_SHE_LOOKED = CONSTANTS["HE_SMILED_SHE_LOOKED"]
BOB_THOUGHT_THAT = CONSTANTS["BOB_THOUGHT_THAT"]
SHE_FELT_FEAR = CONSTANTS["SHE_FELT_FEAR"]
I_KNOW_HE_WANTED = CONSTANTS["I_KNOW_HE_WANTED"]
ALISA = CONSTANTS["ALISA"]
BOB_NAME = CONSTANTS["BOB_NAME"]
CAROL_NAME = CONSTANTS["CAROL_NAME"]
PLAYER_NAME = CONSTANTS["PLAYER_NAME"]
SYSTEM_NAME = CONSTANTS["SYSTEM_NAME"]
VALID_RESPONSE_TEXT = CONSTANTS["VALID_RESPONSE_TEXT"]
SHORT_RESPONSE_TEXT = CONSTANTS["SHORT_RESPONSE_TEXT"]
SPACES_RESPONSE_TEXT = CONSTANTS["SPACES_RESPONSE_TEXT"]
EMPTY_RESPONSE_TEXT = CONSTANTS["EMPTY_RESPONSE_TEXT"]
HELLO_WORLD = CONSTANTS["HELLO_WORLD"]
BOB_THOUGHT_BAD = CONSTANTS["BOB_THOUGHT_BAD"]
CLEAN_RESPONSE_TEXT = CONSTANTS["CLEAN_RESPONSE_TEXT"]
FOREIGN_TRUNCATE_INPUT = CONSTANTS["FOREIGN_TRUNCATE_INPUT"]
TOO_SHORT_RESPONSE = CONSTANTS["TOO_SHORT_RESPONSE"]


# ==================== TESTS ====================

class TestGoldenIsolationBlock:
    """Golden tests for build_role_isolation_block."""

    def test_isolation_block_basic(self):
        """Basic isolation block matches snapshot."""
        result = build_role_isolation_block(ALIAS, strict=False)
        assert result == ISOLATION_BLOCK_BASIC

    def test_isolation_block_strict(self):
        """Strict mode appends retry warning."""
        result = build_role_isolation_block(ALIAS, strict=True)
        assert result == ISOLATION_BLOCK_STRICT

    def test_isolation_block_different_names(self):
        """Different character names produce correctly formatted blocks."""
        result = build_role_isolation_block(BOB, strict=False)
        assert f"{CURRENT_CHAR_PREFIX}{BOB}" in result
        assert ACTIONS_PREFIX in result
        assert OTHER_REPLIKI in result
        assert "ВЗАИМОДЕЙСТВИЕ:" in result
        assert "покидать текущую локацию" in result
        assert "входить в другую локацию" in result


class TestGoldenReinforcement:
    """Golden tests for build_post_history_reinforcement."""

    def test_reinforcement_block_golden(self):
        """Reinforcement block matches expected format."""
        result = build_post_history_reinforcement(ALIAS)
        assert result == REINFORCEMENT_BLOCK

    def test_reinforcement_contains_key_elements(self):
        """Block contains all required elements."""
        result = build_post_history_reinforcement(BOB)
        assert f"{YOU_ARE_ONLY}{BOB}" in result
        assert DO_NOT_DESCRIBE in result
        assert DO_NOT_THINK in result
        assert DO_NOT_ACT_FOR_OTHERS in result
        assert ANSWER_ONLY_FOR_YOURSELF in result
        assert result.startswith(STARTS_WITH_NEWLINE_DASH)
        assert result.endswith(ENDS_WITH_NEWLINE_DASH)


class TestGoldenGenerationCue:
    """Golden tests for generation cues."""

    def test_generation_cue_legacy_golden(self):
        """Legacy cue matches snapshot."""
        result = build_generation_cue(ALIAS)
        assert result == GENERATION_CUE_LEGACY

    def test_generation_cue_chat_golden(self):
        """Chat API cue matches snapshot."""
        result = build_generation_cue_for_chat(ALIAS)
        assert result == GENERATION_CUE_CHAT

    def test_generation_cue_legacy_has_prefix(self):
        """Legacy cue includes character name as prefix."""
        result = build_generation_cue(BOB)
        assert result.endswith(f"{BOB}:")

    def test_generation_cue_chat_no_prefix(self):
        """Chat cue does NOT include character name prefix."""
        result = build_generation_cue_for_chat(BOB)
        assert not result.endswith(f"{BOB}:")
        assert BOB in result
        assert "Отвечай за" in result


class TestGoldenStopSequences:
    """Golden tests for build_stop_sequences."""

    def test_stop_sequences_basic(self):
        """Stop sequences for other characters + globals."""
        result = build_stop_sequences([BOB, CAROL])
        assert result == STOP_SEQUENCES_BASIC

    def test_stop_sequences_empty_others(self):
        """Only global stops when no other characters."""
        result = build_stop_sequences([])
        assert result == [PLAYER_PREFIX, SYSTEM_PREFIX]

    def test_stop_sequences_format(self):
        """Each name generates plain and bold variants."""
        result = build_stop_sequences([ALIAS])
        assert ALIAS_COLON in result
        assert ALIAS_BOLD in result
        assert len(result) == STOP_SEQUENCE_LENGTH  # 2 for Алиса + 2 globals


class TestGoldenSanitization:
    """Golden tests for response sanitization."""

    def test_strip_current_character_prefix(self):
        """Current character's own prefix is stripped."""
        from app.role_isolation import strip_current_character_prefix
        result = strip_current_character_prefix(STRIP_TEST_1_INPUT, ALIAS)
        assert result == STRIP_TEST_1_EXPECTED

        result = strip_current_character_prefix(STRIP_TEST_2_INPUT, ALIAS)
        assert result == STRIP_TEST_2_EXPECTED

    def test_strip_current_character_prefix_no_match(self):
        """No prefix when character name doesn't match."""
        from app.role_isolation import strip_current_character_prefix
        result = strip_current_character_prefix(STRIP_TEST_3_INPUT, ALIAS)
        assert result == STRIP_TEST_3_EXPECTED

    def test_find_foreign_speaker_marker(self):
        """Detects foreign speaker markers."""
        result = find_foreign_speaker_marker(FOREIGN_MARKER_TEST, [BOB])
        assert result is not None
        assert result > 0

    def test_find_foreign_speaker_marker_none(self):
        """Returns None when no foreign markers."""
        result = find_foreign_speaker_marker("Привет, как дела?", [BOB])
        assert result is None

    def test_sanitize_character_response_strips_and_truncates(self):
        """Strips own prefix, truncates at foreign marker."""
        result = sanitize_character_response(
            SANITIZE_TEST_1_INPUT,
            ALIAS,
            [BOB]
        )
        assert result == SANITIZE_TEST_1_EXPECTED

    def test_sanitize_character_response_no_foreign(self):
        """No truncation when no foreign markers."""
        result = sanitize_character_response(
            FOREIGN_MARKER_TEST,
            ALIAS,
            [BOB]
        )
        assert result == FOREIGN_MARKER_EXPECTED


class TestGoldenValidation:
    """Golden tests for response validation."""

    def test_is_response_valid_min_length(self):
        """Validates minimum length."""
        assert is_response_valid(VALID_RESPONSE) is True  # 6 chars
        assert is_response_valid(SHORT_RESPONSE) is False  # 2 chars
        assert is_response_valid(SPACES_RESPONSE) is False
        assert is_response_valid(EMPTY_RESPONSE) is False

    def test_is_response_valid_custom_min(self):
        """Custom minimum length."""
        assert is_response_valid(VALID_RESPONSE, min_length=10) is False
        assert is_response_valid(HELLO_WORLD, min_length=10) is True


class TestGoldenPerspectiveViolation:
    """Golden tests for semantic contamination detection."""

    def test_contains_perspective_violation_hard(self):
        """Hard violations detected: internal states of others."""
        hard, soft = contains_perspective_violation(HARD_VIOLATION_1, [BOB])
        assert hard is True
        assert soft is False

        hard, soft = contains_perspective_violation(HARD_VIOLATION_2, [CAROL])
        assert hard is True

        hard, soft = contains_perspective_violation(HARD_VIOLATION_3, [BOB])
        assert hard is True

    def test_contains_perspective_violation_soft(self):
        """Soft violations detected: observable actions of others."""
        hard, soft = contains_perspective_violation(SOFT_VIOLATION_1, [BOB])
        assert hard is False
        assert soft is True

        hard, soft = contains_perspective_violation(SOFT_VIOLATION_2, [CAROL])
        assert hard is False
        assert soft is True

    def test_contains_perspective_violation_name_with_internal_verb(self):
        """Character name + internal verb = hard violation."""
        hard, soft = contains_perspective_violation(NAME_INTERNAL_VERB_1, [BOB])
        assert hard is True

        hard, soft = contains_perspective_violation(NAME_INTERNAL_VERB_2, [CAROL])
        assert hard is True

    def test_contains_perspective_violation_none(self):
        """Clean text has no violations."""
        hard, soft = contains_perspective_violation(CLEAN_TEXT, [BOB])
        assert hard is False
        assert soft is False

    def test_contains_perspective_violation_empty(self):
        """Empty text or no other names returns no violations."""
        hard, soft = contains_perspective_violation(EMPTY_TEXT, [BOB])
        assert hard is False
        assert soft is False

        hard, soft = contains_perspective_violation(HELLO_TEXT, [])
        assert hard is False
        assert soft is False


class TestGoldenSanitizeAndValidate:
    """Golden tests for combined sanitization + validation."""

    def test_sanitize_and_validate_clean_response(self):
        """Clean response passes validation."""
        result = sanitize_and_validate_response(
            CLEAN_RESPONSE_TEXT,
            ALISA,
            [BOB_NAME],
            min_length=10
        )
        assert isinstance(result, ValidationResult)
        assert result.is_valid is True
        assert result.hard_violation is False
        assert result.soft_violation is False
        assert "Привет" in result.cleaned_text

    def test_sanitize_and_validate_hard_violation(self):
        """Hard violation fails validation."""
        result = sanitize_and_validate_response(
            BOB_THOUGHT_BAD,
            ALISA,
            [BOB_NAME],
            min_length=10
        )
        assert result.is_valid is False
        assert result.hard_violation is True

    def test_sanitize_and_validate_soft_violation_passes(self):
        """Soft violation doesn't fail validation (only logged)."""
        result = sanitize_and_validate_response(
            SOFT_VIOLATION_TEST,
            ALISA,
            [BOB_NAME],
            min_length=10
        )
        expected = SOFT_VIOLATION_RESULT
        assert result.is_valid is expected["is_valid"]
        assert result.hard_violation is expected["hard_violation"]
        assert result.soft_violation is expected["soft_violation"]

    def test_sanitize_and_validate_foreign_marker_truncates(self):
        """Foreign speaker marker triggers truncation."""
        result = sanitize_and_validate_response(
            FOREIGN_TRUNCATE_INPUT,
            ALISA,
            [BOB_NAME],
            min_length=10
        )
        assert result.cleaned_text == "Привет"

    def test_sanitize_and_validate_too_short(self):
        """Too short response fails validation."""
        result = sanitize_and_validate_response(
            TOO_SHORT_RESPONSE,
            ALISA,
            [BOB_NAME],
            min_length=10
        )
        assert result.is_valid is False
        assert result.hard_violation is False
        assert result.soft_violation is False
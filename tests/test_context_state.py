"""Unit tests for the dynamic per-chat num_ctx tracking (MIN_CTX/MAX_CTX)."""

import pytest

from app.config import settings
from app.context_state import ContextState

MIN = settings.min_ctx_tokens
MAX = settings.max_ctx_tokens
BUF = settings.ctx_buffer_tokens


@pytest.fixture()
def state():
    return ContextState()


def test_initial_ctx_is_min(state):
    assert state.get(1) == MIN


def test_prompt_below_min_does_not_change_ctx(state):
    state.apply_prompt(1, 3000)
    assert state.get(1) == MIN


def test_prompt_just_under_min_keeps_min(state):
    state.apply_prompt(1, MIN - 1)
    assert state.get(1) == MIN


def test_prompt_above_min_grows_to_prompt_plus_buffer(state):
    num_ctx = state.apply_prompt(1, MIN + 2000)
    assert num_ctx == MIN + 2000 + BUF
    assert state.get(1) == MIN + 2000 + BUF


def test_ctx_is_monotonic_never_shrinks(state):
    state.apply_prompt(1, 15000)
    assert state.get(1) == 15000 + BUF
    state.apply_prompt(1, 12000)
    assert state.get(1) == 15000 + BUF


def test_ctx_only_grows_when_prompt_outgrows_current(state):
    state.apply_prompt(1, 9000)
    assert state.get(1) == 9000 + BUF
    state.apply_prompt(1, 9000 + BUF)
    assert state.get(1) == 9000 + BUF
    state.apply_prompt(1, 9000 + BUF + 1)
    assert state.get(1) == 9000 + BUF + 1 + BUF


def test_ctx_is_capped_at_max(state):
    num_ctx = state.apply_prompt(1, 100000)
    assert num_ctx == MAX
    assert state.get(1) == MAX


def test_reset_returns_to_min(state):
    state.apply_prompt(1, 20000)
    state.reset(1)
    assert state.get(1) == MIN


def test_chats_are_isolated(state):
    state.apply_prompt(1, 20000)
    state.apply_prompt(2, 9000)
    assert state.get(1) == 20000 + BUF
    assert state.get(2) == 9000 + BUF


def test_remove_forgets_chat(state):
    state.apply_prompt(1, 20000)
    state.remove(1)
    assert state.get(1) == MIN

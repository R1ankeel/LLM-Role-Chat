"""Unit tests for the dynamic Context Budget Manager (per-request num_ctx)."""

import logging
from types import SimpleNamespace

from app.context_budget_manager import (
    DEFAULT_CTX_STEPS,
    ContextBudgetManager,
)


def make_cfg(**overrides):
    base = dict(
        min_ctx_tokens=8192,
        max_ctx_tokens=32778,
        response_budget_tokens=2000,
        thinking_reserve_tokens=2048,
        safety_margin_tokens=1000,
        round_context=True,
        context_rounding_steps=DEFAULT_CTX_STEPS,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def manager(**overrides):
    return ContextBudgetManager(make_cfg(**overrides))


def test_rounds_up_to_step():
    # safety = max(0, 10% of 10121) = 1012 => required = 11133 -> 12288
    budget = manager(response_budget_tokens=0, safety_margin_tokens=0).calculate(
        prompt_tokens=10121, history_tokens=0
    )
    assert budget.required_ctx == 11133
    assert budget.final_ctx == 12288


def test_round_up_keeps_exact_step():
    mgr = manager()
    assert mgr._round_up(7321, 32778) == 8192
    assert mgr._round_up(10121, 32778) == 10240
    assert mgr._round_up(12288, 32778) == 12288
    assert mgr._round_up(32778, 32778) == 32778  # never above max


def test_min_ctx_floor():
    budget = manager(response_budget_tokens=0).calculate(
        prompt_tokens=1000, history_tokens=0
    )
    assert budget.required_ctx == 1000 + 1000  # 10% < flat 1000 -> 1000
    assert budget.final_ctx == 8192  # never below MIN_CTX


def test_max_ctx_never_exceeded():
    budget = manager().calculate(prompt_tokens=100000, history_tokens=0)
    assert budget.final_ctx <= 32778
    assert budget.final_ctx == 32778


def test_rounding_capped_by_max_ctx():
    # required 32560 -> step 32768; stays under MAX_CTX=32778
    budget = manager(response_budget_tokens=0, safety_margin_tokens=0).calculate(
        prompt_tokens=29600, history_tokens=0
    )
    assert budget.required_ctx == 29600 + 2960
    assert budget.final_ctx == 32768


def test_required_above_max_uses_max():
    # required 33000 > MAX_CTX -> clamp to 32778 (rounding must not exceed it)
    budget = manager(response_budget_tokens=0, safety_margin_tokens=0).calculate(
        prompt_tokens=30000, history_tokens=0
    )
    assert budget.final_ctx == 32778


def test_thinking_reserves_reasoning_budget():
    mgr = manager(response_budget_tokens=0)
    instant = mgr.calculate(prompt_tokens=7000, history_tokens=0, thinking=False)
    thinking = mgr.calculate(prompt_tokens=7000, history_tokens=0, thinking=True)
    assert instant.reasoning_budget == 0
    assert thinking.reasoning_budget == 2048
    assert thinking.required_ctx == instant.required_ctx + 2048
    assert instant.final_ctx == 8192
    assert thinking.final_ctx == 10240


def test_safety_margin_is_max_of_flat_and_10pct():
    mgr = manager(response_budget_tokens=0, safety_margin_tokens=1000)
    small = mgr.calculate(prompt_tokens=3000, history_tokens=0)
    big = mgr.calculate(prompt_tokens=20000, history_tokens=0)
    assert small.reserve_tokens == 1000  # flat wins for small prompts
    assert big.reserve_tokens == 2000    # 10% wins for large prompts


def test_response_budget_included():
    mgr = manager(response_budget_tokens=1500)
    budget = mgr.calculate(prompt_tokens=4000, history_tokens=0, thinking=False)
    assert budget.response_budget == 1500
    # required = 4000 + 1500 + 1000 = 6500 -> floor 8192
    assert budget.final_ctx == 8192


def test_history_split_and_sum():
    mgr = manager(response_budget_tokens=0, safety_margin_tokens=0)
    budget = mgr.calculate(prompt_tokens=10000, history_tokens=3000)
    assert budget.prompt_tokens == 7000   # non-history part
    assert budget.history_tokens == 3000
    # required = prompt + history + response + reasoning + safety(10%)
    assert budget.required_ctx == 7000 + 3000 + 0 + 0 + 1000
    assert budget.final_ctx == 12288


def test_extra_tokens_extensible():
    mgr = manager(response_budget_tokens=0, safety_margin_tokens=0)
    budget = mgr.calculate(
        prompt_tokens=5000,
        history_tokens=0,
        extra_tokens={"narrator": 500, "planner": 300},
    )
    # required = prompt + extra + 10% safety
    assert budget.required_ctx == 5000 + 800 + 500
    assert dict(budget.extra_tokens) == {"narrator": 500, "planner": 300}


def test_rounding_disabled():
    mgr = manager(response_budget_tokens=0, safety_margin_tokens=0, round_context=False)
    budget = mgr.calculate(prompt_tokens=9000, history_tokens=0)
    assert budget.final_ctx == 9900  # no step rounding


def test_min_ctx_above_max_is_clamped():
    mgr = manager(response_budget_tokens=0, safety_margin_tokens=0)
    budget = mgr.calculate(prompt_tokens=500, history_tokens=0, min_ctx=50000, max_ctx=20000)
    assert budget.final_ctx <= 20000


def test_debug_log_shows_all_stages(caplog):
    with caplog.at_level(logging.DEBUG, logger="app.context_budget_manager"):
        manager(response_budget_tokens=1500, thinking_reserve_tokens=6000).calculate(
            chat_id=7,
            prompt_tokens=4210 + 2843,  # total prompt includes history
            history_tokens=2843,
            thinking=True,
        )
    assert any("Context budget" in r.message for r in caplog.records)
    assert any("prompt=4210" in r.message for r in caplog.records)
    assert any("history=2843" in r.message for r in caplog.records)
    assert any("response=1500" in r.message for r in caplog.records)
    assert any("reasoning=6000" in r.message for r in caplog.records)
    assert any("final=" in r.message for r in caplog.records)

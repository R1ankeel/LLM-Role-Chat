"""Dynamic Context Budget Manager - per-request ``num_ctx`` for the main model.

Replaces the fixed ~32K window (and the monotonic per-chat growth tracked by
``context_state``) with a per-request calculation. Before every main-model
generation the manager computes how large the context window must actually be:

    required_ctx = prompt_tokens + history_tokens
                   + response_budget + reasoning_budget + safety_margin

where ``prompt_tokens`` is the real token count of the fully assembled prompt
(system + character + personality + mood + memory + world + relationships +
story + rules + dynamic injections + hidden prompts), ``history_tokens`` is the
history block rendered inside it, ``response_budget`` reserves the max reply
size, ``reasoning_budget`` is always reserved when Thinking Mode is on, and
``safety_margin`` covers tokenizer variance. The result is clamped to
``[MIN_CTX, MAX_CTX]`` and rounded up to KV-cache-friendly steps so Ollama can
reuse an already allocated cache instead of allocating a new one every request.

This is a *window-level* budget and is distinct from ``schemas.ContextBudget``
(which allocates tokens between context-builder components) and from the
builder's ``context_budget.build_budget``.

Future context sources (Narrator, Planner, Dynamic Story, World Director, ...)
can be accounted for via ``extra_tokens`` without changing the manager.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Mapping

from .config import settings

logger = logging.getLogger(__name__)

# KV-cache-friendly rounding steps (tokens). Ollama keeps a separate KV cache
# per requested num_ctx, so rounding up to a small set of allowed sizes lets
# requests reuse already-allocated caches instead of reallocating.
DEFAULT_CTX_STEPS: tuple[int, ...] = (
    4096,
    6144,
    8192,
    10240,
    12288,
    16384,
    20480,
    24576,
    28672,
    32768,
    40960,
    49152,
    65536,
)


@dataclass(frozen=True)
class ContextBudget:
    """Result of the context-window calculation for one generation call.

    ``prompt_tokens`` is the assembled prompt minus the history block;
    together with ``history_tokens`` it reproduces the real token count of the
    final prompt. ``required_ctx`` is the raw sum before clamping/rounding and
    ``final_ctx`` is the value that must be passed to Ollama as ``num_ctx``.
    """

    required_ctx: int
    prompt_tokens: int
    history_tokens: int
    response_budget: int
    reasoning_budget: int
    reserve_tokens: int
    final_ctx: int
    extra_tokens: tuple[tuple[str, int], ...] = ()

    @property
    def safety_margin(self) -> int:
        return self.reserve_tokens


class ContextBudgetManager:
    """Single source of ``num_ctx`` for every main-model generation call."""

    def __init__(self, cfg=settings) -> None:
        self._cfg = cfg

    @property
    def _steps(self) -> tuple[int, ...]:
        raw = getattr(self._cfg, "context_rounding_steps", None)
        values: list[int] = []
        if raw:
            parts = raw.split(",") if isinstance(raw, str) else list(raw)
            for part in parts:
                try:
                    value = int(str(part).strip())
                except (TypeError, ValueError):
                    continue
                if value > 0:
                    values.append(value)
        if not values:
            return DEFAULT_CTX_STEPS
        return tuple(sorted(set(values)))

    def _round_up(self, value: int, max_ctx: int) -> int:
        """Round ``value`` up to the smallest allowed step, capped at max_ctx."""
        for step in self._steps:
            if step >= value:
                return min(step, max_ctx)
        return max_ctx

    def calculate(
        self,
        *,
        chat_id: int | None = None,
        prompt_tokens: int,
        history_tokens: int = 0,
        response_budget: int | None = None,
        thinking: bool = False,
        extra_tokens: Mapping[str, int] | None = None,
        min_ctx: int | None = None,
        max_ctx: int | None = None,
    ) -> ContextBudget:
        """Compute the context window for one request.

        ``prompt_tokens`` must be the real token count of the already-assembled
        final prompt (including the history block); ``history_tokens`` is the
        token count of just that history block. Both come from the same build
        step and are never re-tokenized here.
        """
        cfg = self._cfg

        total_input = max(0, int(prompt_tokens or 0))
        history = max(0, int(history_tokens or 0))
        non_history = max(0, total_input - history)

        extra: dict[str, int] = {
            str(k): max(0, int(v or 0)) for k, v in (extra_tokens or {}).items()
        }
        extra_total = sum(extra.values())

        response = (
            int(response_budget)
            if response_budget and int(response_budget) > 0
            else max(0, int(getattr(cfg, "response_budget_tokens", 2000)))
        )
        reasoning = (
            max(0, int(getattr(cfg, "thinking_reserve_tokens", 0)))
            if thinking
            else 0
        )
        safety = max(
            max(0, int(getattr(cfg, "safety_margin_tokens", 1000))),
            int(total_input * 0.10),
        )

        required = (
            non_history
            + history
            + extra_total
            + response
            + reasoning
            + safety
        )

        min_ctx_val = (
            int(min_ctx)
            if min_ctx and int(min_ctx) > 0
            else max(0, int(getattr(cfg, "min_ctx_tokens", 8192)))
        )
        max_ctx_val = (
            int(max_ctx)
            if max_ctx and int(max_ctx) > 0
            else max(0, int(getattr(cfg, "max_ctx_tokens", 32778)))
        )
        if min_ctx_val > max_ctx_val:
            min_ctx_val = max_ctx_val

        final = max(required, min_ctx_val)
        final = min(final, max_ctx_val)
        if bool(getattr(cfg, "round_context", True)):
            final = self._round_up(final, max_ctx_val)
            final = max(final, min_ctx_val)

        budget = ContextBudget(
            required_ctx=required,
            prompt_tokens=non_history,
            history_tokens=history,
            response_budget=response,
            reasoning_budget=reasoning,
            reserve_tokens=safety,
            final_ctx=final,
            extra_tokens=tuple(sorted(extra.items())),
        )

        logger.debug(
            "[chat_id=%s] Context budget: prompt=%d history=%d response=%d "
            "reasoning=%d safety=%d extra=%d required=%d final=%d "
            "(min=%d max=%d round=%s)",
            chat_id,
            non_history,
            history,
            response,
            reasoning,
            safety,
            extra_total,
            required,
            final,
            min_ctx_val,
            max_ctx_val,
            bool(getattr(cfg, "round_context", True)),
        )
        return budget


context_budget_manager = ContextBudgetManager()

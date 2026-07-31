"""Token budget computation for the token-aware context builder.

Budgets are soft per-component limits. ``CONTEXT_RECENT_MIN_TOKENS`` is a
soft target (not a hard guarantee): when the total budget is tight, recent
dialogue may drop below it, but components are trimmed in priority order
(retrieved history → memories → summary) before recent dialogue is touched.
"""

from __future__ import annotations

from .config import settings
from .schemas import ContextBudget


def build_budget(max_tokens: int | None = None) -> ContextBudget:
    """Distribute ``max_tokens`` (or the global setting) into component budgets.

    Priority of component budgets (all soft, recent_min is a target):
      1. reserve (never filled with content)
      2. state (SceneState + relationships)  — P0
      3. summary — P2
      4. memories — P2
      5. retrieved history — P3
      6. recent dialogue gets the remainder up to recent_max

    The final recent_max is clamped so the sum of all component budgets never
    exceeds the available budget. For very tight budgets the fixed (P0) state
    is preserved and the P2/P3 components are shrunk first.
    """
    total = (
        int(max_tokens)
        if max_tokens and int(max_tokens) > 0
        else int(settings.max_context_tokens)
    )
    reserve = min(settings.context_reserve_tokens, max(0, int(total * 0.15)))
    available = max(0, total - reserve)

    state = min(settings.context_state_budget, available)

    remaining = available - state
    summary = min(settings.context_summary_budget, remaining)
    remaining -= summary
    memory = min(settings.context_memory_budget, remaining)
    remaining -= memory
    retrieval = min(settings.context_retrieval_budget, remaining)
    remaining -= retrieval

    recent_max = min(settings.context_recent_max_tokens, remaining)
    recent_min = min(settings.context_recent_min_tokens, recent_max)

    return ContextBudget(
        total_tokens=total,
        system_budget=total,
        state_budget=state,
        summary_budget=summary,
        memory_budget=memory,
        retrieved_history_budget=retrieval,
        recent_history_min_tokens=recent_min,
        recent_history_max_tokens=recent_max,
        reserve_tokens=reserve,
    )

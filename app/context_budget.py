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

    if not settings.context_v2_enabled:
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

    # ---- Context Builder v2 (Sprint 13, §23) ---------------------------
    # Per-block sub-budgets carved from the same `available` pool. Priority
    # (§23): reserve → state/P0 (scene) → perception/recent(P0, не усекается) →
    # intent/goal(P1) → relationship(P1) → story(P1) → summary(P2) → memories(P2)
    # → beliefs(P2) → retrieved history(P3). Recent dialogue is a P0 floor:
    # it is reserved BEFORE the P1/P2 blocks so a tight budget never starves it.
    v2_state = min(settings.context_state_budget, available)
    available -= v2_state

    # perception/recent (P0): keep the recent_min floor, cap at recent_max.
    v2_recent_max = min(settings.context_recent_max_tokens, available)
    v2_recent_min = min(settings.context_recent_min_tokens, v2_recent_max)
    available -= v2_recent_max

    world = min(settings.context_v2_world_budget, available)
    available -= world

    perceive = min(settings.context_v2_perceive_budget, available)
    available -= perceive

    goal = min(settings.context_v2_goal_budget, available)
    available -= goal

    relationship = min(settings.context_v2_relationship_budget, available)
    available -= relationship

    story = min(settings.context_v2_story_budget, available)
    available -= story

    v2_summary = min(settings.context_summary_budget, available)
    available -= v2_summary

    relevant_memory = min(settings.context_v2_memory_budget, available)
    available -= relevant_memory

    knowledge = min(settings.context_v2_knowledge_budget, available)
    available -= knowledge

    v2_retrieval = min(settings.context_retrieval_budget, available)
    available -= v2_retrieval

    return ContextBudget(
        total_tokens=total,
        system_budget=total,
        state_budget=v2_state,
        summary_budget=v2_summary,
        memory_budget=relevant_memory,
        retrieved_history_budget=v2_retrieval,
        recent_history_min_tokens=v2_recent_min,
        recent_history_max_tokens=v2_recent_max,
        reserve_tokens=reserve,
        world_budget=world,
        perceive_budget=perceive,
        relationship_budget=relationship,
        goal_budget=goal,
        story_budget=story,
        knowledge_budget=knowledge,
        relevant_memory_budget=relevant_memory,
    )

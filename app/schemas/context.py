"""Схемы контекста и бюджета токенов (Sprint 3, decomposition-sprints.md §4)."""

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class ContextBudget(BaseModel):
    """Token allocation for one character context (soft per-component limits)."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    total_tokens: int
    system_budget: int
    state_budget: int
    summary_budget: int
    memory_budget: int
    retrieved_history_budget: int
    recent_history_min_tokens: int
    recent_history_max_tokens: int
    reserve_tokens: int
    # Context Builder v2 (Sprint 13, Plans/update20.md §23): per-block
    # sub-budgets. Zero when context_v2_enabled is off (legacy budget).
    world_budget: int = 0
    perceive_budget: int = 0
    relationship_budget: int = 0
    goal_budget: int = 0
    story_budget: int = 0
    knowledge_budget: int = 0
    relevant_memory_budget: int = 0


class DroppedItem(BaseModel):
    """A component/candidate dropped to stay within the token budget."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    component: str
    reason: str
    item_id: Optional[int] = None
    preview: str = ""


class ContextDiagnostics(BaseModel):
    """Aggregated ids and counts for observability (no message texts)."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    oldest_included_message_id: Optional[int] = None
    newest_included_message_id: Optional[int] = None
    summary_through_message_id: Optional[int] = None
    retrieved_message_ids: list[int] = Field(default_factory=list)
    recent_message_ids: list[int] = Field(default_factory=list)
    excluded_message_ids: list[int] = Field(default_factory=list)
    memories_candidates: int = 0
    memories_selected: int = 0
    retrieved_events_selected: int = 0
    total_tokens: int = 0


class BuiltContext(BaseModel):
    """Result of ContextBuilder.build — the assembled per-character context."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    dialogue_text: str = ""
    recent_text: str = ""
    retrieved_text: str = ""
    scene_text: str = ""
    summary_text: Optional[str] = None
    memories: list = Field(default_factory=list)
    recency_tail_text: str = ""
    # YOUR STATE (Sprint 3, §23): runtime-состояние персонажа. Заполняется
    # context_builder, рендер — по флагу character_state_enabled.
    state_text: str = ""
    # WHAT YOU KNOW (Sprint 5, §9): beliefs персонажа. Заполняется
    # context_builder, рендер — по флагу beliefs_enabled.
    what_you_know_text: str = ""
    # STORY (Sprint 8, §16): сюжетный блок (фаза + активные потоки top-K +
    # прогресс). Заполняется context_builder, рендер — по флагу story_enabled.
    story_text: str = ""
    # ACTIVE GOAL (Sprint 10, §21/§23): детерминированный intent NPC.
    # Заполняется context_builder/chat_engine, рендер — по флагу npc_intent_enabled.
    active_goal_text: str = ""
    # ACTIVE PLAN (Sprint 10, §22/§23): компактная строка плана NPC.
    # Рендер — по флагу npc_plans_enabled.
    active_plan_text: str = ""
    # CRISIS (Sprint 11, §19): активные кризисные линии («давление в контексте»,
    # data-only). Рендер — по флагу crisis_engine_enabled.
    crisis_text: str = ""
    # Context Builder v2 (Sprint 13, §23): WORLD (сцена), WHAT YOU PERCEIVE
    # (perception-строки раунда), RELATIONSHIP (интерпретации + anchors),
    # RELEVANT MEMORY (reranked memories). Заполняются только при
    # context_v2_enabled; иначе пустые (legacy-блоки остаются в scene_text /
    # relationships_block / memories).
    world_text: str = ""
    perceive_text: str = ""
    relationship_text: str = ""
    relevant_memory_text: str = ""
    total_tokens: int = 0
    token_count_mode: str = "estimated"
    component_tokens: dict[str, int] = Field(default_factory=dict)
    budget: ContextBudget
    dropped_items: list[DroppedItem] = Field(default_factory=list)
    diagnostics: ContextDiagnostics = Field(default_factory=ContextDiagnostics)

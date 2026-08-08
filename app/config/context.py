"""Настройки контекстного билдера и бюджетов токенов (Sprint 2, §4.9)."""

from pydantic import Field




class ContextSettings():
    """Context Builder: token-aware context per character + v2 блоки."""

    # Context Builder (token-aware context per character)
    context_enabled: bool = Field(default=True, alias="CONTEXT_ENABLED")
    max_context_tokens: int = Field(default=60000, alias="MAX_CONTEXT_TOKENS")
    context_recent_min_tokens: int = Field(default=8000, alias="CONTEXT_RECENT_MIN_TOKENS")
    context_recent_max_tokens: int = Field(default=40000, alias="CONTEXT_RECENT_MAX_TOKENS")
    context_memory_budget: int = Field(default=5000, alias="CONTEXT_MEMORY_BUDGET")
    context_retrieval_budget: int = Field(default=5000, alias="CONTEXT_RETRIEVAL_BUDGET")
    context_summary_budget: int = Field(default=4000, alias="CONTEXT_SUMMARY_BUDGET")
    context_state_budget: int = Field(default=3000, alias="CONTEXT_STATE_BUDGET")
    context_reserve_tokens: int = Field(default=3000, alias="CONTEXT_RESERVE_TOKENS")

    # ----- Context Builder v2 (Plans/update20.md §23, Sprint 13) -----
    # Целевая приоритизированная сборка: WORLD/WHAT YOU KNOW/WHAT YOU
    # PERCEIVE/YOUR STATE/RELATIONSHIP/ACTIVE GOAL/RELEVANT MEMORY/STORY.
    # Флаг-канарейка (по умолчанию off): при off legacy-сборка и legacy-блоки
    # (scene → relationships в system) не меняются; при on новые блоки получают
    # отдельные токен-подбюджеты, дублирующие старые блоки удаляются
    # (scene → WORLD, relationships → RELATIONSHIP+anchors).
    context_v2_enabled: bool = Field(
        default=False, alias="CONTEXT_V2_ENABLED"
    )
    # Подбюджеты новых блоков (§23): WORLD (P0, сцена), WHAT YOU PERCEIVE
    # (P0), YOUR STATE (P1), RELATIONSHIP (P1), ACTIVE GOAL (P1), STORY (P1),
    # WHAT YOU KNOW (P2, retrieval-based), RELEVANT MEMORY (P2).
    context_v2_world_budget: int = Field(
        default=3000, alias="CONTEXT_V2_WORLD_BUDGET"
    )
    context_v2_perceive_budget: int = Field(
        default=2500, alias="CONTEXT_V2_PERCEIVE_BUDGET"
    )
    context_v2_relationship_budget: int = Field(
        default=2000, alias="CONTEXT_V2_RELATIONSHIP_BUDGET"
    )
    context_v2_goal_budget: int = Field(
        default=800, alias="CONTEXT_V2_GOAL_BUDGET"
    )
    context_v2_story_budget: int = Field(
        default=2000, alias="CONTEXT_V2_STORY_BUDGET"
    )
    context_v2_knowledge_budget: int = Field(
        default=1500, alias="CONTEXT_V2_KNOWLEDGE_BUDGET"
    )
    context_v2_memory_budget: int = Field(
        default=4000, alias="CONTEXT_V2_MEMORY_BUDGET"
    )
    token_count_mode: str = Field(default="estimated", alias="TOKEN_COUNT_MODE")
    tokenizer_encoding: str = Field(default="", alias="TOKENIZER_ENCODING")
    context_history_load_cap: int = Field(default=2000, alias="CONTEXT_HISTORY_LOAD_CAP")
    context_retrieval_candidates: int = Field(default=30, alias="CONTEXT_RETRIEVAL_CANDIDATES")
    context_message_embedding_enabled: bool = Field(default=False, alias="CONTEXT_MESSAGE_EMBEDDING_ENABLED")
    context_debug: bool = Field(default=False, alias="CONTEXT_DEBUG")

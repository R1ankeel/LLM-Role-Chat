"""Настройки памяти/консолидации/embedding (Sprint 2, §4.9)."""

from pydantic import Field




class MemorySettings():
    """Память: BM25, witness-фильтры, extraction, consolidation, embedding."""

    # History & Memory
    max_memories_per_character: int = Field(default=20, alias="MAX_MEMORIES_PER_CHARACTER")
    recent_memories_for_prompt: int = Field(default=10, alias="RECENT_MEMORIES_FOR_PROMPT")
    memory_relevance_top_k: int = Field(default=5, alias="MEMORY_RELEVANCE_TOP_K")
    enable_relevant_memory_selection: bool = Field(default=True, alias="ENABLE_RELEVANT_MEMORY_SELECTION")
    bm25_k1: float = Field(default=1.5, alias="BM25_K1")
    bm25_b: float = Field(default=0.75, alias="BM25_B")
    bm25_min_score_threshold: float = Field(default=0.1, alias="BM25_MIN_SCORE_THRESHOLD")

    # Summary
    summary_interval_messages: int = Field(default=20, alias="SUMMARY_INTERVAL_MESSAGES")
    summary_max_paragraphs: int = Field(default=3, alias="SUMMARY_MAX_PARAGRAPHS")

    # Witness / Perception
    enable_witness_filter: bool = Field(default=True, alias="ENABLE_WITNESS_FILTER")
    witness_mentioned_snippet_len: int = Field(default=120, alias="WITNESS_MENTIONED_SNIPPET_LEN")
    default_event_visibility: str = Field(default="local", alias="DEFAULT_EVENT_VISIBILITY")
    normalize_locations: bool = Field(default=True, alias="NORMALIZE_LOCATIONS")
    # Heuristic adjacency fallback (shared toponym prefix). Off by default:
    # adjacency uses only explicit `locations.adjacent_to` links (Sprint 2).
    adjacency_fallback_enabled: bool = Field(default=False, alias="ADJACENCY_FALLBACK_ENABLED")

    # Witness-based memory filtering (Phase 4)
    enable_witness_memory_filter: bool = Field(default=True, alias="ENABLE_WITNESS_MEMORY_FILTER")
    memory_importance_decay_days: int = Field(default=7, alias="MEMORY_IMPORTANCE_DECAY_DAYS")
    memory_importance_decay_factor: float = Field(default=0.5, alias="MEMORY_IMPORTANCE_DECAY_FACTOR")
    enable_nested_isolation: bool = Field(default=True, alias="ENABLE_NESTED_ISOLATION")

    # Event visibility values
    event_visibilities: tuple[str, ...] = (
        "private",
        "local",
        "targeted",
        "public",
        "global",
    )

    # Semantic contamination protection
    enable_post_history_reinforcement: bool = Field(default=True, alias="ENABLE_POST_HISTORY_REINFORCEMENT")
    fallback_on_isolation_failure: bool = Field(default=True, alias="FALLBACK_ON_ISOLATION_FAILURE")
    max_role_isolation_retries: int = Field(default=3, alias="MAX_ROLE_ISOLATION_RETRIES")

    # Memory extraction & validation
    enable_memory_fact_validation: bool = Field(default=True, alias="ENABLE_MEMORY_FACT_VALIDATION")
    memory_fact_min_len: int = Field(default=12, alias="MEMORY_FACT_MIN_LEN")
    memory_fact_max_len: int = Field(default=300, alias="MEMORY_FACT_MAX_LEN")
    memory_max_facts_per_round: int = Field(default=3, alias="MEMORY_MAX_FACTS_PER_ROUND")
    memory_near_dup_jaccard: float = Field(default=0.75, alias="MEMORY_NEAR_DUP_JACCARD")
    memory_categories: tuple[str, ...] = (
        "отношения",
        "событие",
        "локация",
        "предмет",
        "другое",
    )

    # Memory Consolidation (P3)
    consolidation_enabled: bool = Field(default=True, alias="CONSOLIDATION_ENABLED")
    consolidation_interval_hours: int = Field(default=24, alias="CONSOLIDATION_INTERVAL_HOURS")
    consolidation_min_cluster_size: int = Field(default=2, alias="CONSOLIDATION_MIN_CLUSTER_SIZE")
    consolidation_similarity_threshold: float = Field(default=0.65, alias="CONSOLIDATION_SIMILARITY_THRESHOLD")
    consolidation_max_memories_per_char: int = Field(default=200, alias="CONSOLIDATION_MAX_MEMORIES_PER_CHAR")
    consolidation_llm_model: str = Field(default="", alias="CONSOLIDATION_LLM_MODEL")  # empty = use default model

    # Adaptive Consolidation (Sprint 12, Plans/update20.md §20) — replaces the 24h
    # timer with a score-based soft/hard/critical trigger. Canary: off by default.
    adaptive_consolidation_enabled: bool = Field(
        default=False, alias="ADAPTIVE_CONSOLIDATION_ENABLED"
    )
    # Score weights: new_messages, events, facts, rel_events, story_events, anchors.
    consolidation_weight_messages: float = Field(default=1.0, alias="CONSOLIDATION_WEIGHT_MESSAGES")
    consolidation_weight_events: float = Field(default=2.0, alias="CONSOLIDATION_WEIGHT_EVENTS")
    consolidation_weight_facts: float = Field(default=3.0, alias="CONSOLIDATION_WEIGHT_FACTS")
    consolidation_weight_rel_events: float = Field(default=4.0, alias="CONSOLIDATION_WEIGHT_REL_EVENTS")
    consolidation_weight_story_events: float = Field(default=5.0, alias="CONSOLIDATION_WEIGHT_STORY_EVENTS")
    consolidation_weight_anchors: float = Field(default=7.0, alias="CONSOLIDATION_WEIGHT_ANCHORS")
    # Soft = memories + summary; hard = full set. Critical = immediate hard.
    consolidation_soft_threshold: float = Field(default=25.0, alias="CONSOLIDATION_SOFT_THRESHOLD")
    consolidation_hard_threshold: float = Field(default=50.0, alias="CONSOLIDATION_HARD_THRESHOLD")
    consolidation_critical_importance: float = Field(
        default=8.0, alias="CONSOLIDATION_CRITICAL_IMPORTANCE"
    )
    # Dedup: critical consolidation no more than N times per round.
    consolidation_critical_max_per_round: int = Field(
        default=2, alias="CONSOLIDATION_CRITICAL_MAX_PER_ROUND"
    )
    # Score-based scheduler poll interval (seconds) when adaptive is on.
    consolidation_poll_seconds: float = Field(
        default=600.0, alias="CONSOLIDATION_POLL_SECONDS"
    )

    # Vector Search / Embeddings (P3)
    embedding_enabled: bool = Field(default=True, alias="EMBEDDING_ENABLED")
    embedding_model: str = Field(default="bge-m3", alias="EMBEDDING_MODEL")
    embedding_dim: int = Field(default=1024, alias="EMBEDDING_DIM")
    embedding_batch_size: int = Field(default=16, alias="EMBEDDING_BATCH_SIZE")
    vector_top_k: int = Field(default=10, alias="VECTOR_TOP_K")
    hybrid_rrf_k: int = Field(default=60, alias="HYBRID_RRF_K")
    hybrid_bm25_weight: float = Field(default=1.0, alias="HYBRID_BM25_WEIGHT")
    hybrid_vector_weight: float = Field(default=1.0, alias="HYBRID_VECTOR_WEIGHT")

    # ----- Memory Architecture v2 (Plans/update20.md §7, Sprint 2) -----
    # Типы памяти (semantic/episodic/social/story) и эмоциональные якоря на
    # единой таблице memories + memory_anchors. Флаг выключен по умолчанию:
    # new-колонки пишутся/читаются только при включённом флаге, legacy-пути
    # (BM25/vector/RRF/witness) не меняются.
    memory_types_enabled: bool = Field(
        default=False, alias="MEMORY_TYPES_ENABLED"
    )
    # Эмоциональные якоря (§7/§13): запись из значимых RelationshipEvent
    # (расширение `_maybe_create_memory_from_event`) и активация top-K в
    # контексте (Sprint 7). Выключен — якоря не пишутся, memory_anchors пуста.
    anchors_enabled: bool = Field(
        default=False, alias="ANCHORS_ENABLED"
    )
    # Cap числа активируемых якорей в контексте отношения (≈3, §7).
    relationship_anchor_max: int = Field(
        default=3, alias="RELATIONSHIP_ANCHOR_MAX"
    )

    # ----- Hybrid Retrieval v2 (Plans/update20.md §14, Sprint 6) -----
    # Детерминированный rerank memories ПОСЛЕ существующего RRF-слияния и ДО
    # witness-boost: `score = w_lex×lex + w_sem×sem + w_emotion×emotion +
    # w_story×story + w_rel×rel + w_recency×recency + w_salience×salience`.
    # Использует сигналы текущего контекста: `memory_type`, `valence/intensity`
    # (эмоциональная ось), активные `story_threads` (сюжетная ось), направленные
    # отношения персонажа (relationship-ось). Флаг выключен по умолчанию —
    # RRF-путь без флага не меняется; BM25 НЕ удаляется (fallback при
    # отсутствии embeddings — semantic-слагаемое отбрасывается, веса
    # нормируются, §14). read-path новых колонок — только при включённом флаге.
    hybrid_rerank_enabled: bool = Field(
        default=False, alias="HYBRID_RERANK_ENABLED"
    )
    # Веса осей rerank (сумма нормируется на 1.0 внутри `rerank_memories`).
    hybrid_rerank_weight_lexical: float = Field(
        default=0.30, alias="HYBRID_RERANK_WEIGHT_LEXICAL"
    )
    hybrid_rerank_weight_semantic: float = Field(
        default=0.25, alias="HYBRID_RERANK_WEIGHT_SEMANTIC"
    )
    hybrid_rerank_weight_emotional: float = Field(
        default=0.10, alias="HYBRID_RERANK_WEIGHT_EMOTIONAL"
    )
    hybrid_rerank_weight_story: float = Field(
        default=0.15, alias="HYBRID_RERANK_WEIGHT_STORY"
    )
    hybrid_rerank_weight_relationship: float = Field(
        default=0.10, alias="HYBRID_RERANK_WEIGHT_RELATIONSHIP"
    )
    hybrid_rerank_weight_recency: float = Field(
        default=0.05, alias="HYBRID_RERANK_WEIGHT_RECENCY"
    )
    hybrid_rerank_weight_salience: float = Field(
        default=0.05, alias="HYBRID_RERANK_WEIGHT_SALIENCE"
    )

    # ----- Structured World Events (Plans/update20.md §15, Sprint 1) -----
    # Пост-раундная event extraction: LLM извлекает из истории раунда
    # структурированные события (action, importance, story/emotional_salience)
    # и причинно-следственные links, которые пишутся в `world_events` /
    # `event_links` рядом со speech/move событиями движка. Флаг выключен
    # по умолчанию — read-path и генерация не меняются (canary).
    event_extraction_enabled: bool = Field(
        default=False, alias="EVENT_EXTRACTION_ENABLED"
    )
    # Модель для event extraction (пустая = берём основную модель генерации).
    event_extraction_model: str = Field(
        default="", alias="EVENT_EXTRACTION_MODEL"
    )
    # События с importance ниже порога не записываются (стоимостной лимит:
    # один LLM-вызов на раунд должен давать только значимые события).
    event_min_importance: float = Field(
        default=3.0, alias="EVENT_MIN_IMPORTANCE"
    )

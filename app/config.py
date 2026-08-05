"""Application configuration using pydantic-settings."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Ollama
    ollama_base_url: str = Field(default="http://localhost:11434", alias="OLLAMA_BASE_URL")
    ollama_timeout: float = Field(default=180.0, alias="OLLAMA_TIMEOUT")

    # Generation
    default_model: str = Field(default="qwen3-coder:30b-a3b-q4_K_M", alias="DEFAULT_MODEL")
    default_temperature: float = Field(default=0.8, alias="DEFAULT_TEMPERATURE")
    enable_thinking: bool = Field(default=True, alias="ENABLE_THINKING")
    use_chat_api: bool = Field(default=True, alias="USE_CHAT_API")

    # History & Memory
    default_history_length: int = Field(default=30, alias="DEFAULT_HISTORY_LENGTH")
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

    # Repetition detection
    repetition_detection_enabled: bool = Field(default=True, alias="REPETITION_DETECTION_ENABLED")
    repetition_window_size: int = Field(default=6, alias="REPETITION_WINDOW_SIZE")
    repetition_threshold: float = Field(default=0.72, alias="REPETITION_THRESHOLD")
    stagnation_threshold: float = Field(default=0.65, alias="STAGNATION_THRESHOLD")
    max_repetition_retries: int = Field(default=2, alias="MAX_REPETITION_RETRIES")
    action_cooldown_turns: int = Field(default=2, alias="ACTION_COOLDOWN_TURNS")
    repetition_text_jaccard: float = Field(default=0.82, alias="REPETITION_TEXT_JACCARD")
    repetition_min_bundle_size: int = Field(default=2, alias="REPETITION_MIN_BUNDLE_SIZE")

    # Anti-mimicry
    enable_anti_mimicry: bool = Field(default=True, alias="ENABLE_ANTI_MIMICRY")
    max_replies_per_character: int = Field(default=2, alias="MAX_REPLIES_PER_CHARACTER")
    enable_vocabulary_control: bool = Field(default=True, alias="ENABLE_VOCABULARY_CONTROL")

    # Scene advancement (Phase 6)
    scene_advancement_enabled: bool = Field(default=True, alias="SCENE_ADVANCEMENT_ENABLED")
    stagnation_max_rounds: int = Field(default=3, alias="STAGNATION_MAX_ROUNDS")
    proactive_action_chance: float = Field(default=0.15, alias="PROACTIVE_ACTION_CHANCE")
    time_advance_interval: int = Field(default=5, alias="TIME_ADVANCE_INTERVAL")
    scene_twist_retry_bonus: float = Field(default=0.15, alias="SCENE_TWIST_RETRY_BONUS")

    # Character avatars (docs/Profile.docx; upload service is Этап B)
    avatar_dir: str = Field(default="app/static/avatars", alias="AVATAR_DIR")
    avatar_max_size_mb: int = Field(default=5, alias="AVATAR_MAX_SIZE_MB")
    avatar_max_dimension: int = Field(default=512, alias="AVATAR_MAX_DIMENSION")
    # Допустимые форматы (проверка по magic-байтам). Константа в коде — как
    # event_visibilities/memory_categories, не читается из env.
    avatar_allowed_types: tuple[str, ...] = ("png", "jpeg", "webp")

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
    token_count_mode: str = Field(default="estimated", alias="TOKEN_COUNT_MODE")
    tokenizer_encoding: str = Field(default="", alias="TOKENIZER_ENCODING")
    context_history_load_cap: int = Field(default=2000, alias="CONTEXT_HISTORY_LOAD_CAP")
    context_retrieval_candidates: int = Field(default=30, alias="CONTEXT_RETRIEVAL_CANDIDATES")
    context_message_embedding_enabled: bool = Field(default=False, alias="CONTEXT_MESSAGE_EMBEDDING_ENABLED")
    context_debug: bool = Field(default=False, alias="CONTEXT_DEBUG")

    # Diagnostic per-NPC generation logging (Plans/locations2.md §21). When on,
    # each generation logs NPC/location + visible/hidden characters and message
    # counts to answer "why doesn't this NPC see that NPC / that message".
    generation_debug: bool = Field(default=False, alias="GENERATION_DEBUG")

    # Dynamic Ollama num_ctx window (KV cache). Starts at MIN_CTX per chat and
    # only grows when the assembled prompt outgrows it, capped by MAX_CTX.
    min_ctx_tokens: int = Field(default=8192, alias="MIN_CTX")
    max_ctx_tokens: int = Field(default=32778, alias="MAX_CTX")
    ctx_buffer_tokens: int = Field(default=100, alias="CTX_BUFFER_TOKENS")
    ctx_safety_factor: float = Field(default=1.3, alias="CTX_SAFETY_FACTOR")

    # Rate limiting
    rate_limit_seconds: int = Field(default=5, alias="RATE_LIMIT_SECONDS")

    # Generation
    min_character_response_length: int = Field(default=10, alias="MIN_CHARACTER_RESPONSE_LENGTH")
    generate_timeout: float = Field(default=180.0, alias="GENERATE_TIMEOUT")

    # Task Queue for Memory Jobs (P3)
    task_queue_enabled: bool = Field(default=True, alias="TASK_QUEUE_ENABLED")
    task_queue_max_retries: int = Field(default=3, alias="TASK_QUEUE_MAX_RETRIES")
    task_queue_retry_min_wait: float = Field(default=5.0, alias="TASK_QUEUE_RETRY_MIN_WAIT")
    task_queue_retry_max_wait: float = Field(default=60.0, alias="TASK_QUEUE_RETRY_MAX_WAIT")
    task_queue_retry_multiplier: float = Field(default=2.0, alias="TASK_QUEUE_RETRY_MULTIPLIER")
    task_queue_max_concurrent: int = Field(default=5, alias="TASK_QUEUE_MAX_CONCURRENT")
    task_queue_retention_days: int = Field(default=30, alias="TASK_QUEUE_RETENTION_DAYS")

    # Vector Search / Embeddings (P3)
    embedding_enabled: bool = Field(default=True, alias="EMBEDDING_ENABLED")
    embedding_model: str = Field(default="bge-m3", alias="EMBEDDING_MODEL")
    embedding_dim: int = Field(default=1024, alias="EMBEDDING_DIM")
    embedding_batch_size: int = Field(default=16, alias="EMBEDDING_BATCH_SIZE")
    vector_top_k: int = Field(default=10, alias="VECTOR_TOP_K")
    hybrid_rrf_k: int = Field(default=60, alias="HYBRID_RRF_K")
    hybrid_bm25_weight: float = Field(default=1.0, alias="HYBRID_BM25_WEIGHT")
    hybrid_vector_weight: float = Field(default=1.0, alias="HYBRID_VECTOR_WEIGHT")

    # Relationship System
    relationship_max_delta: int = Field(default=20, alias="RELATIONSHIP_MAX_DELTA")
    relationship_drivers_max: int = Field(default=4, alias="RELATIONSHIP_DRIVERS_MAX")
    relationship_max_events_in_prompt: int = Field(default=5, alias="RELATIONSHIP_MAX_EVENTS_IN_PROMPT")
    relationship_analyzer_enabled: bool = Field(default=True, alias="RELATIONSHIP_ANALYZER_ENABLED")
    relationship_analyzer_model: str = Field(default="", alias="RELATIONSHIP_ANALYZER_MODEL")
    relationship_valid_types: tuple[str, ...] = (
        "нейтральное", "друг", "близкий_друг", "лучший_друг",
        "союзник", "верный_союзник",
        "соперник", "враг", "заклятый_враг",
        "симпатия", "романтика", "возлюбленные",
        "наставник", "ученик",
        "семья", "родитель", "брат_сестра",
        "незнакомец", "знакомый",
    )
    relationship_transition_rules: dict[str, list[str]] = {
        "нейтральное": ["друг", "союзник", "симпатия", "соперник", "знакомый", "незнакомец"],
        "друг": ["близкий_друг", "лучший_друг", "союзник", "верный_союзник", "симпатия", "нейтральное", "соперник"],
        "близкий_друг": ["лучший_друг", "верный_союзник", "друг", "романтика", "нейтральное"],
        "лучший_друг": ["близкий_друг", "верный_союзник", "нейтральное"],
        "союзник": ["верный_союзник", "друг", "нейтральное", "соперник"],
        "верный_союзник": ["союзник", "друг", "близкий_друг", "нейтральное"],
        "соперник": ["враг", "заклятый_враг", "нейтральное", "знакомый"],
        "враг": ["заклятый_враг", "соперник", "нейтральное"],
        "заклятый_враг": ["враг", "нейтральное"],
        "симпатия": ["романтика", "возлюбленные", "друг", "нейтральное"],
        "романтика": ["возлюбленные", "симпатия", "близкий_друг", "нейтральное"],
        "возлюбленные": ["романтика", "близкий_друг", "нейтральное"],
        "наставник": ["ученик", "верный_союзник", "друг", "нейтральное"],
        "ученик": ["наставник", "верный_союзник", "друг", "нейтральное"],
        "семья": ["родитель", "брат_сестра", "близкий_друг", "нейтральное"],
        "родитель": ["семья", "близкий_друг", "нейтральное"],
        "брат_сестра": ["семья", "близкий_друг", "нейтральное"],
        "незнакомец": ["знакомый", "нейтральное", "соперник"],
        "знакомый": ["друг", "нейтральное", "незнакомец"],
    }
    relationship_min_importance: int = Field(default=3, alias="RELATIONSHIP_MIN_IMPORTANCE")
    relationship_analyze_only_interacting_pairs: bool = Field(
        default=True, alias="RELATIONSHIP_ANALYZE_ONLY_INTERACTING_PAIRS"
    )
    relationship_reflection_delta_cap: int = Field(
        default=5, alias="RELATIONSHIP_REFLECTION_DELTA_CAP"
    )
    relationship_type_change_requires_interaction: bool = Field(
        default=True, alias="RELATIONSHIP_TYPE_CHANGE_REQUIRES_INTERACTION"
    )
    relationship_max_pair_context_lines: int = Field(
        default=20, alias="RELATIONSHIP_MAX_PAIR_CONTEXT_LINES"
    )
    # Batch relationship analyzer (Sprint 1 item 8, docs/relations.md §8):
    # one LLM call for all pairs instead of the per-pair O(N^2) loop; on a
    # broken batch the per-pair analyzer is used as a fallback (§8.4).
    relationship_batch_enabled: bool = Field(
        default=True, alias="RELATIONSHIP_BATCH_ENABLED"
    )
    relationship_batch_fallback: bool = Field(
        default=True, alias="RELATIONSHIP_BATCH_FALLBACK"
    )
    # Open Issues (Sprint 1 items 5-6, docs/relations.md §7, §14)
    relationship_issues_enabled: bool = Field(
        default=True, alias="RELATIONSHIP_ISSUES_ENABLED"
    )
    relationship_issue_text_max: int = Field(
        default=200, alias="RELATIONSHIP_ISSUE_TEXT_MAX"
    )
    relationship_max_issues_in_prompt: int = Field(
        default=3, alias="RELATIONSHIP_MAX_ISSUES_IN_PROMPT"
    )
    relationship_issue_near_dup_jaccard: float = Field(
        default=0.7, alias="RELATIONSHIP_ISSUE_NEAR_DUP_JACCARD"
    )
    # Weighted deterministic proactive boost (Sprint 1 item 7, docs/relations.md §7.4)
    issue_proactive_coeff: float = Field(
        default=0.15, alias="ISSUE_PROACTIVE_COEFF"
    )
    issue_proactive_boost_cap: float = Field(
        default=0.35, alias="ISSUE_PROACTIVE_BOOST_CAP"
    )
    issue_salience_decay_rounds: int = Field(
        default=5, alias="ISSUE_SALIENCE_DECAY_ROUNDS"
    )
    # MVP epistemic mask (Sprint 2 item 10, docs/relations.md §10): a character
    # learns how another treats it only when it had direct/observed evidence this
    # round, and only as an interpretation (never numbers).
    relationship_epistemic_mask_enabled: bool = Field(
        default=True, alias="RELATIONSHIP_EPISTEMIC_MASK_ENABLED"
    )
    relationship_epistemic_max: int = Field(
        default=8, alias="RELATIONSHIP_EPISTEMIC_MAX"
    )
    # Hearsay (Sprint 2 item 12, docs/relations.md §12): second-hand reports.
    # Hearsay is always weaker than direct/observed evidence — the per-round
    # delta cap (deterministic reliability lowers it further: low trust in the
    # teller halves it, a hostile teller->target valence cuts it by 0.7).
    relationship_hearsay_cap: int = Field(
        default=3, alias="RELATIONSHIP_HEARSAY_CAP"
    )
    # Trajectory window (docs/relations.md §11): how many LLM events to include
    relationship_trajectory_window: int = Field(
        default=4, alias="RELATIONSHIP_TRAJECTORY_WINDOW"
    )

    # Decay (Sprint 3 item 16, docs/relations.md §18): per-round decay for jealousy and resentment
    relationship_decay_jealousy_per_round: int = Field(
        default=3, alias="RELATIONSHIP_DECAY_JEALOUSY_PER_ROUND"
    )
    relationship_decay_resentment_per_round: int = Field(
        default=1, alias="RELATIONSHIP_DECAY_RESENTMENT_PER_ROUND"
    )
    # Dynamic decay (Sprint 7, docs/relations.md §18): при `dynamic_decay_enabled`
    # базовая ставка умножается на character_factor из character_state (stress).
    # Направление: выше stress → медленнее затухание (держится за обиду/ревность).
    # Выключен — legacy-поведение (фиксированные ставки выше). Canary.
    dynamic_decay_enabled: bool = Field(
        default=False, alias="DYNAMIC_DECAY_ENABLED"
    )
    dynamic_decay_jealousy_base_rate: int = Field(
        default=3, alias="DYNAMIC_DECAY_JEALOUSY_BASE_RATE"
    )
    dynamic_decay_resentment_base_rate: int = Field(
        default=1, alias="DYNAMIC_DECAY_RESENTMENT_BASE_RATE"
    )
    dynamic_decay_stress_sensitivity: float = Field(
        default=0.5, alias="DYNAMIC_DECAY_STRESS_SENSITIVITY"
    )
    dynamic_decay_factor_min: float = Field(
        default=0.4, alias="DYNAMIC_DECAY_FACTOR_MIN"
    )
    dynamic_decay_factor_max: float = Field(
        default=1.6, alias="DYNAMIC_DECAY_FACTOR_MAX"
    )
    # Reciprocity pipeline (Sprint 7, docs/relations.md §10): направленные дельты
    # зависят от beliefs — уверенность персонажа в факте о другом снижает кап
    # дельты (множитель по confidence). Выключен — legacy-кап без beliefs.
    reciprocity_enabled: bool = Field(
        default=False, alias="RECIPROCITY_ENABLED"
    )
    reciprocity_belief_dampening: float = Field(
        default=0.5, alias="RECIPROCITY_BELIEF_DAMPENING"
    )
    reciprocity_belief_multiplier_min: float = Field(
        default=0.5, alias="RECIPROCITY_BELIEF_MULTIPLIER_MIN"
    )

    # Memory integration (Sprint 3 item 19): create memories for significant relationship events
    relationship_memory_enabled: bool = Field(
        default=True, alias="RELATIONSHIP_MEMORY_ENABLED"
    )
    # Event pruning (Sprint 4 item 3, docs/relations.md §20): oldest events of a
    # pair above this count are folded into a single "archive" event.
    relationship_events_max_per_pair: int = Field(
        default=100, alias="RELATIONSHIP_EVENTS_MAX_PER_PAIR"
    )
    relationship_memory_delta_threshold: int = Field(
        default=10, alias="RELATIONSHIP_MEMORY_DELTA_THRESHOLD"
    )

    relationship_analyzer_prompt: str = Field(
        default=(
            "Проанализируй раунд ролевой игры ниже и определи, как меняются "
            "отношения {source_name} к {target_name}. "
            "Текущий тип отношений: {current_type}. "
            "Текущие метрики — привязанность: {affection}, доверие: {trust}, "
            "влечение: {attraction}, обида: {resentment}, ревность: {jealousy}. "
            "Недавние события: {recent_events}. "
            "Текст раунда: {round_text}. "
            "Верни дельты в диапазоне [-20, +20] по каждой метрике, "
            "предложи новый relationship_type из разрешённых переходов, "
            "краткое описание, причину, важность (1-10) "
            "и update_description: true/false."
        ),
        alias="RELATIONSHIP_ANALYZER_PROMPT",
    )

    # ----- World & Perception Engine 3.0 (Plans/WPE.md) -----
    # Все флаги по умолчанию выключены (Фаза 0 — фундамент без изменения
    # поведения). Каждая фаза включает свой флаг отдельным canary'ем.
    world_engine_locations_enabled: bool = Field(
        default=False, alias="WORLD_ENGINE_LOCATIONS_ENABLED"
    )
    world_engine_tools_enabled: bool = Field(
        default=False, alias="WORLD_ENGINE_TOOLS_ENABLED"
    )
    world_engine_events_enabled: bool = Field(
        default=False, alias="WORLD_ENGINE_EVENTS_ENABLED"
    )
    world_engine_perception_enabled: bool = Field(
        default=False, alias="WORLD_ENGINE_PERCEPTION_ENABLED"
    )
    world_engine_recency_tail_enabled: bool = Field(
        default=False, alias="WORLD_ENGINE_RECENCY_TAIL_ENABLED"
    )
    world_engine_actions_enabled: bool = Field(
        default=False, alias="WORLD_ENGINE_ACTIONS_ENABLED"
    )
    wpe_action_consistency_max_retries: int = Field(
        default=1, alias="WORLD_ENGINE_CONSISTENCY_MAX_RETRIES"
    )
    world_engine_threads_enabled: bool = Field(
        default=False, alias="WORLD_ENGINE_THREADS_ENABLED"
    )
    world_engine_partial_perception_enabled: bool = Field(
        default=False, alias="WORLD_ENGINE_PARTIAL_PERCEPTION_ENABLED"
    )
    world_engine_event_bus_enabled: bool = Field(
        default=False, alias="WORLD_ENGINE_EVENT_BUS_ENABLED"
    )

    # ----- Sensors Model (Plans/update20.md §5.1) -----
    # Отдельный аналитический слой для быстрых фоновых задач (perception-
    # предложения, event classification, emotion/mood, memory-кандидаты,
    # relationship-дельты). Sensors НЕ источник истины и НЕ подменяет основную
    # модель генерации реплик. Пустая `SENSORS_MODEL` = слой выключен.
    # Инфраструктура заведена в Sprint 0, НЕ подключена ни к одному процессу.
    sensors_model: str = Field(default="", alias="SENSORS_MODEL")
    # Мастер-флаг слоя. По умолчанию False (legacy-поведение).
    sensors_enabled: bool = Field(default=False, alias="SENSORS_ENABLED")
    # Per-task флаги (каждый — своя канарейка). Задача активна только если
    # включены и мастер-флаг, и per-task флаг, и задана `SENSORS_MODEL`.
    sensors_perception_enabled: bool = Field(
        default=False, alias="SENSORS_PERCEPTION_ENABLED"
    )
    sensors_event_enabled: bool = Field(
        default=False, alias="SENSORS_EVENT_ENABLED"
    )
    sensors_emotion_enabled: bool = Field(
        default=False, alias="SENSORS_EMOTION_ENABLED"
    )
    sensors_memory_enabled: bool = Field(
        default=False, alias="SENSORS_MEMORY_ENABLED"
    )
    sensors_relationship_enabled: bool = Field(
        default=False, alias="SENSORS_RELATIONSHIP_ENABLED"
    )
    # Отдельный таймаут для sensor-задач (короче, чем у генерации) —
    # graceful degradation §5.1.8: недоступность Sensors не должна влиять на раунд.
    sensors_timeout: float = Field(default=60.0, alias="SENSORS_TIMEOUT")

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

    # ----- Character State (Plans/update20.md §8, Sprint 3) -----
    # Единое runtime-состояние персонажа: emotional_state (JSON map
    # emotion→intensity), mood, stress, physical_state, attention, goals.
    # Хранит ТОЛЬКО то, чего нет в других таблицах (не локацию/не отношения).
    # Пост-раунд детерминированно обновляется emotion_engine'ом из relationship
    # deltas + событий раунда (+ опциональная Sensors-нормализация в рамках
    # caps). Блок YOUR STATE рендерится только при включённом флаге.
    character_state_enabled: bool = Field(
        default=False, alias="CHARACTER_STATE_ENABLED"
    )
    # Caps emotion_engine (det. правила): сколько интенсивности эмоции может
    # добавиться за один раунд и сколько стресса (0..1).
    emotion_round_cap: float = Field(default=0.4, alias="EMOTION_ROUND_CAP")
    stress_round_cap: float = Field(default=0.2, alias="STRESS_ROUND_CAP")
    # Sensors-предложение эмоции может сдвинуть интенсивность не более чем на
    # этот порог за раунд (Sensors НЕ задаёт mood напрямую — только в caps).
    sensors_emotion_intensity_cap: float = Field(
        default=0.3, alias="SENSORS_EMOTION_INTENSITY_CAP"
    )

    # ----- Attention (Plans/update20.md §11, Sprint 4) -----
    # «Воспринято ≠ вошло в сознание». Детерминированный attention score для пары
    # (персонаж, событие) пишется в `message_presence.attention`; используется
    # фильтром memory extraction (attention < LOW → не в память) и хуком в recency
    # tail. НЕ меняет presence-лестницу (риск Sprint 4: только то, что идёт в
    # память, не то, что рендерится в recent history).
    attention_enabled: bool = Field(
        default=False, alias="ATTENTION_ENABLED"
    )
    # Пороги (§11): < LOW — «слышал фоном» (не в память/реакцию);
    # LOW ≤ score < HIGH — «заметил» (в память с пониженной важностью);
    # ≥ HIGH — «в центре внимания» (в память, в recency tail).
    attention_low: float = Field(default=0.35, alias="ATTENTION_LOW")
    attention_high: float = Field(default=0.7, alias="ATTENTION_HIGH")
    # Веса компонентов score (сумма = 1.0, §11):
    #   w_volume (громкость/стимулы), w_distance (same > adjacent > remote),
    #   w_relevance (важность события), w_personal (имя/интерес),
    #   w_emotional (якорь активен), w_novelty (новое vs повтор),
    #   w_relationship (участвует target отношения), w_address (addressed=true).
    attention_weight_volume: float = Field(
        default=0.15, alias="ATTENTION_WEIGHT_VOLUME"
    )
    attention_weight_distance: float = Field(
        default=0.15, alias="ATTENTION_WEIGHT_DISTANCE"
    )
    attention_weight_relevance: float = Field(
        default=0.10, alias="ATTENTION_WEIGHT_RELEVANCE"
    )
    attention_weight_personal: float = Field(
        default=0.25, alias="ATTENTION_WEIGHT_PERSONAL"
    )
    attention_weight_emotional: float = Field(
        default=0.10, alias="ATTENTION_WEIGHT_EMOTIONAL"
    )
    attention_weight_novelty: float = Field(
        default=0.05, alias="ATTENTION_WEIGHT_NOVELTY"
    )
    attention_weight_relationship: float = Field(
        default=0.05, alias="ATTENTION_WEIGHT_RELATIONSHIP"
    )
    attention_weight_address: float = Field(
        default=0.15, alias="ATTENTION_WEIGHT_ADDRESS"
    )
    # Sensors perception-proposal (§5.1.3): `significance` (0..1) может поднять
    # attention score не более чем на эту величину — Sensors НЕ определяет
    # окончательный набор информации (решает `perceive()`/presence) и НЕ
    # принимает решение о внимании; только подсказка в рамках caps.
    sensors_perception_significance_cap: float = Field(
        default=0.15, alias="SENSORS_PERCEPTION_SIGNIFICANCE_CAP"
    )

    # ----- Belief System (Plans/update20.md §9, Sprint 5) -----
    # Структурированные знания/убеждения персонажа (subject/predicate/object,
    # source, confidence 0..1, type fact|belief|suspicion) вместо плоской
    # истины. Персонаж НЕ автоматически знает World Truth — в контекст попадают
    # только его beliefs. Пост-раунд детерминированно обновляются из событий,
    # которые персонаж реально воспринял (presence + attention, §9 pipeline).
    # Постепенное замещение MVP epistemic mask: при `beliefs_enabled=true` mask
    # читает beliefs; при false — mask остаётся fallback (canary).
    beliefs_enabled: bool = Field(default=False, alias="BELIEFS_ENABLED")
    # Cap на число beliefs в контекст-блоке WHAT YOU KNOW (top-K, риск R4).
    beliefs_top_k: int = Field(default=8, alias="BELIEFS_TOP_K")
    # Порог confidence для рендера belief в контекст (ниже — не показываем).
    beliefs_render_confidence: float = Field(
        default=0.3, alias="BELIEFS_RENDER_CONFIDENCE"
    )
    # LLM-suggestion beliefs (suspicion с confidence≤0.5 без прямого наблюдения).
    # Включается ТОЛЬКО после прохождения benchmark gate (§27):
    # `benchmark_structured` на текущей модели, schema-validity ≥ 90%. Пока
    # выключен — только детерминированный direct_observation путь.
    beliefs_llm_suggestion_enabled: bool = Field(
        default=False, alias="BELIEFS_LLM_SUGGESTION_ENABLED"
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

    # ----- Dynamic Story State (Plans/update20.md §16, Sprint 8) -----
    # Сюжет как отдельная ось: Original Plot (chats.original_plot, immutable) +
    # Current Story State (story_states) + Story History (story_events) + Phase.
    # Пост-раунд детерминированно пишутся story_events (проекция extraction
    # world_events) и story_state (активные story_threads, summary, progress).
    # Блок STORY рендерится только при включённом флаге (canary); read-path
    # story_states/story_events/story_threads — только при `story_enabled`.
    story_enabled: bool = Field(default=False, alias="STORY_ENABLED")
    # Cap числа активных потоков в контекст-блоке STORY (top-K, риск R5 —
    # контекст не разрастается).
    story_threads_max: int = Field(default=5, alias="STORY_THREADS_MAX")
    # Порог importance для записи события в story_events (важность сюжета).
    story_event_min_importance: float = Field(
        default=4.0, alias="STORY_EVENT_MIN_IMPORTANCE"
    )
    # Порог importance для создания/обновления активного story_thread.
    story_thread_min_importance: float = Field(
        default=6.0, alias="STORY_THREAD_MIN_IMPORTANCE"
    )
    # Max числа последних сюжетных событий в summary текущего story_state.
    story_summary_max_events: int = Field(
        default=20, alias="STORY_SUMMARY_MAX_EVENTS"
    )

    # ----- Story Consolidation (Plans/update20.md §17, Sprint 9) -----
    # LLM-обновление Current Story State с валидацией (original plot diff,
    # grounding, rollback). Под benchmark gate §27: перед включением — прогон
    # `benchmark_structured` на story-update; при schema-validity < 90% или
    # grounding < порога — только кандидаты-флаги без применения. По умолчанию
    # выключен (canary): legacy-пути не тронуты.
    story_consolidation_enabled: bool = Field(
        default=False, alias="STORY_CONSOLIDATION_ENABLED"
    )
    # Не чаще чем раз в N раундов (§17.1) — стоимостной лимит.
    story_consolidation_interval_rounds: int = Field(
        default=15, alias="STORY_CONSOLIDATION_INTERVAL_ROUNDS"
    )
    # Критическое событие (смерть, предательство, свадьба, milestone — §17.1)
    # затронуло story: importance >= порога в окне → консолидация раньше срока.
    story_consolidation_critical_importance: float = Field(
        default=8.0, alias="STORY_CONSOLIDATION_CRITICAL_IMPORTANCE"
    )
    # Модель для consolidation (пустая = основная модель генерации чата).
    story_consolidation_model: str = Field(
        default="", alias="STORY_CONSOLIDATION_MODEL"
    )
    # Таймаут consolidation-вызова (короче генерации — фоновая задача).
    story_consolidation_timeout: float = Field(
        default=60.0, alias="STORY_CONSOLIDATION_TIMEOUT"
    )
    # Порог confidence: изменение ниже порога не применяется (§17.3).
    story_consolidation_min_confidence: float = Field(
        default=0.5, alias="STORY_CONSOLIDATION_MIN_CONFIDENCE"
    )
    # Окно последних story_events для grounding (п.17.3 hallucination guard).
    story_consolidation_max_recent_events: int = Field(
        default=30, alias="STORY_CONSOLIDATION_MAX_RECENT_EVENTS"
    )

    # ----- NPC Intent + Plans (Plans/update20.md §21/§22, Sprint 10) -----
    # Детерминированный intent перед генерацией (goal/target/approach/urgency/
    # emotion/risk) + долгоживущие маленькие планы NPC. Оба под canary-флагами;
    # при off — legacy-пути не тронуты. Intent — тенденция, не команда (риск
    # Sprint 10, по образцу behavior drivers); планы — «хочу X, но мешает Y»,
    # НЕ GOAP/planner.
    npc_intent_enabled: bool = Field(default=False, alias="NPC_INTENT_ENABLED")
    npc_plans_enabled: bool = Field(default=False, alias="NPC_PLANS_ENABLED")
    # Число последних intent-строк персонажа, читаемых для контекста (топ-N).
    intent_history_max: int = Field(default=3, alias="INTENT_HISTORY_MAX")
    # Пороги approach (§21): risk >= avoid → избегать; risk >= delay и
    # urgency < 0.5 → отложить. Ниже min_urgency цель-кандидат (issue/thread)
    # intent не формирует (не каждый ход имеет intent — §21).
    intent_risk_avoid: float = Field(default=0.8, alias="INTENT_RISK_AVOID")
    intent_risk_delay: float = Field(default=0.6, alias="INTENT_RISK_DELAY")
    intent_min_urgency: float = Field(default=0.15, alias="INTENT_MIN_URGENCY")
    # Веса детерминированного story pressure (§19, Sprint 10 plot_pressure).
    # Сумма нормируется — не обязательно равна 1.
    plot_pressure_weight_issues: float = Field(
        default=0.25, alias="PLOT_PRESSURE_WEIGHT_ISSUES"
    )
    plot_pressure_weight_goals: float = Field(
        default=0.25, alias="PLOT_PRESSURE_WEIGHT_GOALS"
    )
    plot_pressure_weight_stagnation: float = Field(
        default=0.25, alias="PLOT_PRESSURE_WEIGHT_STAGNATION"
    )
    plot_pressure_weight_recent: float = Field(
        default=0.25, alias="PLOT_PRESSURE_WEIGHT_RECENT"
    )
    # Число раундов блокировки, после которых goals_blocked_score ≈ 1 (0..1).
    plot_pressure_goal_blocked_rounds: int = Field(
        default=8, alias="PLOT_PRESSURE_GOAL_BLOCKED_ROUNDS"
    )
    # Порог важности сюжетного события, при котором цель плана считается
    # достигнутой пост-раунд (mark done) — и порог снятия блокировки.
    npc_plan_resolve_importance: float = Field(
        default=7.0, alias="NPC_PLAN_RESOLVE_IMPORTANCE"
    )
    # Порог overlap (доля значимых токенов) для сопоставления имени
    # story_thread с completed_goal при архивации завершённых линий.
    story_thread_archive_overlap: float = Field(
        default=0.5, alias="STORY_THREAD_ARCHIVE_OVERLAP"
    )

    # ----- Crisis Engine (Plans/update20.md §19, Sprint 11) -----
    # Мягкое обнаружение кризисов: детерминированный story pressure (6
    # компонентов §19) → кандидат (правила) → resolution (мягко: story_event +
    # story_thread «Кризис» + boost proactive). Запрещён паттерн
    # `if trust<30: force_argument`: кризис — вероятность, не команда (риск
    # Sprint 11). LLM-оценка типа кризиса — ТОЛЬКО под benchmark gate §27.
    crisis_engine_enabled: bool = Field(
        default=False, alias="CRISIS_ENGINE_ENABLED"
    )
    # Benchmark gate §27: LLM-оценка кризиса (JSON-schema, мягко) включается
    # только после прохождения `benchmark_structured` на crisis-evaluation;
    # иначе — детерминированный pressure + type из правил, без LLM.
    crisis_evaluation_enabled: bool = Field(
        default=False, alias="CRISIS_EVALUATION_ENABLED"
    )
    # Порог совокупного crisis pressure (0..1) для формирования кандидата.
    crisis_pressure_threshold: float = Field(
        default=0.5, alias="CRISIS_PRESSURE_THRESHOLD"
    )
    # «Проблема долго не разрешена»: open issue без упоминания ≥ N раундов
    # (rounds_since_last_mention) — сигнал неразрешённости конфликта.
    crisis_min_issue_age_rounds: int = Field(
        default=4, alias="CRISIS_MIN_ISSUE_AGE_ROUNDS"
    )
    # Веса компонентов crisis pressure (§19): базовая story pressure
    # (issues/goals/stagnation/recent), траектория отношений, конфликт
    # убеждений. Сумма нормируется — не обязана быть 1.
    crisis_weight_base: float = Field(
        default=0.5, alias="CRISIS_WEIGHT_BASE"
    )
    crisis_weight_trajectory: float = Field(
        default=0.3, alias="CRISIS_WEIGHT_TRAJECTORY"
    )
    crisis_weight_beliefs: float = Field(
        default=0.2, alias="CRISIS_WEIGHT_BELIEFS"
    )
    # Cap мягкого proactive boost, добавляемого вовлечённым в кризис персонажам.
    crisis_boost_cap: float = Field(
        default=0.3, alias="CRISIS_BOOST_CAP"
    )
    # Минимальная важность story_event/thread кризиса (importance записи).
    crisis_event_importance: float = Field(
        default=7.0, alias="CRISIS_EVENT_IMPORTANCE"
    )
    # Префикс имени сюжетной линии кризиса («Кризис: ...»).
    crisis_thread_prefix: str = Field(
        default="Кризис", alias="CRISIS_THREAD_PREFIX"
    )


settings = Settings()
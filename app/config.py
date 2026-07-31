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
        "relationship",
        "event",
        "location",
        "item",
        "other",
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
    relationship_max_events_in_prompt: int = Field(default=5, alias="RELATIONSHIP_MAX_EVENTS_IN_PROMPT")
    relationship_analyzer_enabled: bool = Field(default=True, alias="RELATIONSHIP_ANALYZER_ENABLED")
    relationship_analyzer_model: str = Field(default="", alias="RELATIONSHIP_ANALYZER_MODEL")
    relationship_valid_types: tuple[str, ...] = (
        "neutral", "friend", "close_friend", "best_friend",
        "ally", "trusted_ally",
        "rival", "enemy", "bitter_enemy",
        "crush", "romantic", "lover",
        "mentor", "student",
        "family", "parent", "sibling",
        "stranger", "acquaintance",
    )
    relationship_transition_rules: dict[str, list[str]] = {
        "neutral": ["friend", "ally", "crush", "rival", "acquaintance", "stranger"],
        "friend": ["close_friend", "best_friend", "ally", "trusted_ally", "crush", "neutral", "rival"],
        "close_friend": ["best_friend", "trusted_ally", "friend", "romantic", "neutral"],
        "best_friend": ["close_friend", "trusted_ally", "neutral"],
        "ally": ["trusted_ally", "friend", "neutral", "rival"],
        "trusted_ally": ["ally", "friend", "close_friend", "neutral"],
        "rival": ["enemy", "bitter_enemy", "neutral", "acquaintance"],
        "enemy": ["bitter_enemy", "rival", "neutral"],
        "bitter_enemy": ["enemy", "neutral"],
        "crush": ["romantic", "lover", "friend", "neutral"],
        "romantic": ["lover", "crush", "close_friend", "neutral"],
        "lover": ["romantic", "close_friend", "neutral"],
        "mentor": ["student", "trusted_ally", "friend", "neutral"],
        "student": ["mentor", "trusted_ally", "friend", "neutral"],
        "family": ["parent", "sibling", "close_friend", "neutral"],
        "parent": ["family", "close_friend", "neutral"],
        "sibling": ["family", "close_friend", "neutral"],
        "stranger": ["acquaintance", "neutral", "rival"],
        "acquaintance": ["friend", "neutral", "stranger"],
    }
    relationship_analyzer_prompt: str = Field(
        default=(
            "Analyze the roleplay round below and determine how {source_name}'s "
            "relationship with {target_name} changes. "
            "Current relationship type: {current_type}. "
            "Current metrics — affection: {affection}, trust: {trust}, "
            "attraction: {attraction}, resentment: {resentment}, jealousy: {jealousy}. "
            "Recent events: {recent_events}. "
            "Round text: {round_text}. "
            "Return deltas in range [-20, +20] per metric, "
            "suggested new relationship_type from allowed transitions, "
            "a short description, reason, importance (1-10), "
            "and update_description: true/false."
        ),
        alias="RELATIONSHIP_ANALYZER_PROMPT",
    )


settings = Settings()
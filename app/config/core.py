"""Базовые настройки: base/url/model/история/генерация/ctx (Sprint 2, §4.9)."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class SettingsBase(BaseSettings):
    """Base/url/model/история/генерация + динамическое num_ctx-окно."""

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

    # History
    default_history_length: int = Field(default=30, alias="DEFAULT_HISTORY_LENGTH")

    # Dynamic Ollama num_ctx window (KV cache). Starts at MIN_CTX per chat and
    # only grows when the assembled prompt outgrows it, capped by MAX_CTX.
    min_ctx_tokens: int = Field(default=8192, alias="MIN_CTX")
    max_ctx_tokens: int = Field(default=32778, alias="MAX_CTX")
    ctx_buffer_tokens: int = Field(default=100, alias="CTX_BUFFER_TOKENS")
    ctx_safety_factor: float = Field(default=1.3, alias="CTX_SAFETY_FACTOR")

    # ----- Dynamic Context Budget Manager (per-request num_ctx) -----
    # Каждый вызов основной модели сам считает свой num_ctx: реальные токены
    # собранного prompt + история + ответ + thinking-резерв + safety margin,
    # затем clamp в [MIN_CTX, MAX_CTX] и round вверх до шагов из
    # CONTEXT_ROUND_STEPS (переиспользование KV cache). MIN_CTX/MAX_CTX выше.
    # Максимально возможный размер ответа, включаемый в расчёт окна.
    response_budget_tokens: int = Field(default=2000, alias="RESPONSE_BUDGET_TOKENS")
    # Thinking Mode резервирует дополнительный запас под reasoning (всегда,
    # даже если модель использует его не полностью).
    thinking_reserve_tokens: int = Field(default=2048, alias="THINKING_RESERVE")
    # Дополнительный запас сверх расчёта: max(SAFETY_MARGIN, 10% от prompt).
    safety_margin_tokens: int = Field(default=1000, alias="SAFETY_MARGIN")
    # Округлять num_ctx вверх до шагов из CONTEXT_ROUND_STEPS.
    round_context: bool = Field(default=True, alias="ROUND_CONTEXT")
    # Шаги округления (через запятую). Пусто = дефолтный список в коде.
    context_rounding_steps: str = Field(
        default="",
        alias="CONTEXT_ROUND_STEPS",
    )

    # Rate limiting
    rate_limit_seconds: int = Field(default=5, alias="RATE_LIMIT_SECONDS")

    # Generation
    min_character_response_length: int = Field(default=10, alias="MIN_CHARACTER_RESPONSE_LENGTH")
    generate_timeout: float = Field(default=180.0, alias="GENERATE_TIMEOUT")

    # Debug observability contour (Plans/update20.md §29.1). Read-only
    # /debug/* endpoints are only served when enabled (local dev default).
    debug_enabled: bool = Field(default=False, alias="DEBUG_ENABLED")

    # Diagnostic per-NPC generation logging (Plans/locations2.md §21). When on,
    # each generation logs NPC/location + visible/hidden characters and message
    # counts to answer "why doesn't this NPC see that NPC / that message".
    generation_debug: bool = Field(default=False, alias="GENERATION_DEBUG")

    # Anti-mimicry
    enable_anti_mimicry: bool = Field(default=True, alias="ENABLE_ANTI_MIMICRY")
    max_replies_per_character: int = Field(default=2, alias="MAX_REPLIES_PER_CHARACTER")
    enable_vocabulary_control: bool = Field(default=True, alias="ENABLE_VOCABULARY_CONTROL")
    # Borrowing retries use their own budget (independent of repetition/isolation).
    max_borrowing_retries: int = Field(default=2, alias="MAX_BORROWING_RETRIES")

    # Scene advancement (Phase 6)
    scene_advancement_enabled: bool = Field(default=True, alias="SCENE_ADVANCEMENT_ENABLED")
    stagnation_max_rounds: int = Field(default=3, alias="STAGNATION_MAX_ROUNDS")
    proactive_action_chance: float = Field(default=0.15, alias="PROACTIVE_ACTION_CHANCE")
    time_advance_interval: int = Field(default=5, alias="TIME_ADVANCE_INTERVAL")
    scene_twist_retry_bonus: float = Field(default=0.15, alias="SCENE_TWIST_RETRY_BONUS")

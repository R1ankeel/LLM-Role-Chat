"""Настройки сюжета, NPC intent/plans, crisis engine (Sprint 2, §4.9)."""

from pydantic import Field




class StorySettings():
    """Story: state, consolidation, NPC intent/plans, crisis engine."""

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

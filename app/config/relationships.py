"""Настройки системы отношений: аналитика, issues, decay, reciprocity (Sprint 2, §4.9)."""

from pydantic import Field




class RelationshipSettings():
    """Отношения: аналитик, issues, decay, anti-inflation, reciprocity, memory."""

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
    relationship_family_types: tuple[str, ...] = Field(
        default=("семья", "родитель", "брат_сестра"),
        alias="RELATIONSHIP_FAMILY_TYPES",
    )
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

    # Anti-inflation (docs/relations.md §27): deterministically slow down metric
    # growth so relationships do not hit the ceiling after a few warm rounds.
    # 1) Growth resistance: positive deltas are scaled by
    #    ((100 - current) / 100) ** exponent before clamping, so high values
    #    approach 100 asymptotically. Decay (kind="decay") is never affected.
    relationship_growth_resistance_exponent: float = Field(
        default=1.5, alias="RELATIONSHIP_GROWTH_RESISTANCE_EXPONENT"
    )
    # 2) Per-importance delta cap: the effective per-round cap for a pair is
    #    min(existing mode cap, CAP_BY_IMPORTANCE[importance]). A "compliment"
    #    (importance 1-2) can move a metric at most a few points per round.
    relationship_cap_by_importance: dict[int, int] = Field(
        default={
            1: 2, 2: 3, 3: 5, 4: 7, 5: 10, 6: 13, 7: 16, 8: 20, 9: 25, 10: 30,
        },
        alias="RELATIONSHIP_CAP_BY_IMPORTANCE",
    )
    # 3) Saturation guard: if a metric already gained >= threshold over the
    #    recent window (snapshot-based trajectory), further positive deltas are
    #    scaled by factor (floor 1). Defaults match RELATIONSHIP_TRAJECTORY_WINDOW.
    relationship_saturation_window: int = Field(
        default=4, alias="RELATIONSHIP_SATURATION_WINDOW"
    )
    relationship_saturation_threshold: int = Field(
        default=25, alias="RELATIONSHIP_SATURATION_THRESHOLD"
    )
    relationship_saturation_factor: float = Field(
        default=0.3, alias="RELATIONSHIP_SATURATION_FACTOR"
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

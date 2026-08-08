"""Настройки Sensors-слоя, attention, character state, beliefs (Sprint 2, §4.9)."""

from pydantic import Field




class SensorsSettings():
    """Sensors-слой + attention + character state + beliefs (canary-флаги)."""

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
    # num_ctx / num_predict на каждую sensor-задачу (только как default в `.env`).
    # Меньший num_ctx ускоряет вызовы; num_predict ограничивает ответ (короткие JSON).
    sensors_emotion_num_ctx: int = Field(default=8000, alias="SENSORS_EMOTION_NUM_CTX")
    sensors_emotion_num_predict: int = Field(default=512, alias="SENSORS_EMOTION_NUM_PREDICT")
    sensors_perception_num_ctx: int = Field(
        default=16000, alias="SENSORS_PERCEPTION_NUM_CTX"
    )
    sensors_perception_num_predict: int = Field(
        default=1024, alias="SENSORS_PERCEPTION_NUM_PREDICT"
    )
    sensors_memory_num_ctx: int = Field(default=16000, alias="SENSORS_MEMORY_NUM_CTX")
    sensors_memory_num_predict: int = Field(
        default=1024, alias="SENSORS_MEMORY_NUM_PREDICT"
    )
    sensors_relationship_num_ctx: int = Field(
        default=16000, alias="SENSORS_RELATIONSHIP_NUM_CTX"
    )
    sensors_relationship_num_predict: int = Field(
        default=1024, alias="SENSORS_RELATIONSHIP_NUM_PREDICT"
    )
    sensors_scene_state_num_ctx: int = Field(
        default=8000, alias="SENSORS_SCENE_STATE_NUM_CTX"
    )
    sensors_scene_state_num_predict: int = Field(
        default=1024, alias="SENSORS_SCENE_STATE_NUM_PREDICT"
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

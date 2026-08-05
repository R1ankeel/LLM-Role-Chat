# Конфигурация

Все настройки задаются через переменные окружения / файл `.env` (utf-8), читаются pydantic-settings. Валидные ключи описаны в `app/config.py` (alias'ы), рекомендованные значения — в `.env.example`. Настройки не из `.env.example` имеют дефолты в коде; ниже указаны дефолты из `config.py`, а где `.env.example` их меняет — в скобках.

## Ollama

| ключ | дефолт | описание |
|---|---|---|
| `OLLAMA_BASE_URL` | `http://localhost:11434` | базовый URL Ollama |
| `OLLAMA_TIMEOUT` | `180.0` | таймаут запросов (сек) |

## Generation

| ключ | дефолт | описание |
|---|---|---|
| `DEFAULT_MODEL` | `qwen3-coder:30b-a3b-q4_K_M` | модель по умолчанию |
| `DEFAULT_TEMPERATURE` | `0.8` | температура генерации |
| `ENABLE_THINKING` | `true` | думание модели по умолчанию |
| `USE_CHAT_API` | `true` | `/api/chat` вместо `/api/generate` |
| `MIN_CHARACTER_RESPONSE_LENGTH` | `10` | мин. длина ответа персонажа |
| `GENERATE_TIMEOUT` | `180.0` | таймаут генерации (сек) |

## История и память

| ключ | дефолт | описание |
|---|---|---|
| `DEFAULT_HISTORY_LENGTH` | `30` | длина истории в legacy-режиме |
| `MAX_MEMORIES_PER_CHARACTER` | `20` | лимит воспоминаний на персонажа |
| `RECENT_MEMORIES_FOR_PROMPT` | `10` | свежих воспоминаний в промпт |
| `MEMORY_RELEVANCE_TOP_K` | `5` | top-K релевантных воспоминаний |
| `ENABLE_RELEVANT_MEMORY_SELECTION` | `true` | BM25-выбор релевантных |
| `BM25_K1` | `1.5` | параметр BM25 |
| `BM25_B` | `0.75` | параметр BM25 |
| `BM25_MIN_SCORE_THRESHOLD` | `0.1` | порог скоринга BM25 |

## Саммари

| ключ | дефолт | описание |
|---|---|---|
| `SUMMARY_INTERVAL_MESSAGES` | `20` | саммари каждые N сообщений |
| `SUMMARY_MAX_PARAGRAPHS` | `3` | макс. абзацев в саммари |

## Witness / Perception

| ключ | дефолт | описание |
|---|---|---|
| `ENABLE_WITNESS_FILTER` | `true` | фильтрация истории по свидетелям |
| `WITNESS_MENTIONED_SNIPPET_LEN` | `120` | сниппет упоминания |
| `DEFAULT_EVENT_VISIBILITY` | `local` | видимость события по умолчанию |
| `NORMALIZE_LOCATIONS` | `true` | нормализация названий локаций |

Видимости событий (фиксированный список): `private`, `local`, `targeted`, `public`, `global`.

## Witness-фильтрация памяти и изоляция

| ключ | дефолт | описание |
|---|---|---|
| `ENABLE_WITNESS_MEMORY_FILTER` | `true` | только свидетельствованное — в память |
| `MEMORY_IMPORTANCE_DECAY_DAYS` | `7` | период затухания важности |
| `MEMORY_IMPORTANCE_DECAY_FACTOR` | `0.5` | множитель затухания |
| `ENABLE_NESTED_ISOLATION` | `true` | вложенная изоляция локаций |

## Защита от семантического загрязнения

| ключ | дефолт | описание |
|---|---|---|
| `ENABLE_POST_HISTORY_REINFORCEMENT` | `true` | усиление истории после генерации |
| `FALLBACK_ON_ISOLATION_FAILURE` | `true` | fallback при сбое изоляции |
| `MAX_ROLE_ISOLATION_RETRIES` | `3` | ретраи role isolation |

## Извлечение и валидация памяти

| ключ | дефолт | описание |
|---|---|---|
| `ENABLE_MEMORY_FACT_VALIDATION` | `true` | валидация фактов памяти |
| `MEMORY_FACT_MIN_LEN` | `12` | мин. длина факта |
| `MEMORY_FACT_MAX_LEN` | `300` | макс. длина факта |
| `MEMORY_MAX_FACTS_PER_ROUND` | `3` | макс. фактов за раунд |
| `MEMORY_NEAR_DUP_JACCARD` | `0.75` | порог почти-дубликатов |

Категории памяти (фиксированный список): `отношения`, `событие`, `локация`, `предмет`, `другое`.

## Консолидация памяти (P3)

| ключ | дефолт | описание |
|---|---|---|
| `CONSOLIDATION_ENABLED` | `true` | консолидация по расписанию |
| `CONSOLIDATION_INTERVAL_HOURS` | `24` | интервал (часы) |
| `CONSOLIDATION_MIN_CLUSTER_SIZE` | `2` | мин. размер кластера |
| `CONSOLIDATION_SIMILARITY_THRESHOLD` | `0.65` | порог схожести |
| `CONSOLIDATION_MAX_MEMORIES_PER_CHAR` | `200` | потолок воспоминаний |
| `CONSOLIDATION_LLM_MODEL` | (пусто) | модель; пусто = default model |
| `ADAPTIVE_CONSOLIDATION_ENABLED` | `false` | score-based триггер (Sprint 12, §20) вместо 24h-таймера |
| `CONSOLIDATION_WEIGHT_MESSAGES` | `1` | вес новых сообщений в score |
| `CONSOLIDATION_WEIGHT_EVENTS` | `2` | вес world-событий |
| `CONSOLIDATION_WEIGHT_FACTS` | `3` | вес фактов памяти |
| `CONSOLIDATION_WEIGHT_REL_EVENTS` | `4` | вес relationship-событий |
| `CONSOLIDATION_WEIGHT_STORY_EVENTS` | `5` | вес story-событий |
| `CONSOLIDATION_WEIGHT_ANCHORS` | `7` | вес эмоциональных якорей |
| `CONSOLIDATION_SOFT_THRESHOLD` | `25` | порог soft-консолидации |
| `CONSOLIDATION_HARD_THRESHOLD` | `50` | порог hard-консолидации |
| `CONSOLIDATION_CRITICAL_IMPORTANCE` | `8.0` | порог критического события |
| `CONSOLIDATION_CRITICAL_MAX_PER_ROUND` | `2` | дедуп critical N/раунд |
| `CONSOLIDATION_POLL_SECONDS` | `600` | интервал poll score-схедьюлера |

## Детекция повторов

| ключ | дефолт | описание |
|---|---|---|
| `REPETITION_DETECTION_ENABLED` | `true` | включена |
| `REPETITION_WINDOW_SIZE` | `6` | окно анализа |
| `REPETITION_THRESHOLD` | `0.72` | порог повтора |
| `STAGNATION_THRESHOLD` | `0.65` | порог стагнации |
| `MAX_REPETITION_RETRIES` | `2` | ретраи при повторе |
| `ACTION_COOLDOWN_TURNS` | `2` | кулдаун действий |
| `REPETITION_TEXT_JACCARD` | `0.82` | Jaccard для текста |
| `REPETITION_MIN_BUNDLE_SIZE` | `2` | мин. размер бандла |

## Анти-мимикрия

| ключ | дефолт | описание |
|---|---|---|
| `ENABLE_ANTI_MIMICRY` | `true` | включена |
| `MAX_REPLIES_PER_CHARACTER` | `2` | макс. реплик персонажа за раунд |
| `ENABLE_VOCABULARY_CONTROL` | `true` | контроль лексики |

## Продвижение сцены (Phase 6)

| ключ | дефолт | описание |
|---|---|---|
| `SCENE_ADVANCEMENT_ENABLED` | `true` | включено |
| `STAGNATION_MAX_ROUNDS` | `3` | раундов стагнации до твиста |
| `PROACTIVE_ACTION_CHANCE` | `0.15` | шанс проактивного действия |
| `TIME_ADVANCE_INTERVAL` | `5` | не используется: движок не меняет время автоматически (время задаёт пользователь через `PATCH /chats/{id}/scene`) |
| `SCENE_TWIST_RETRY_BONUS` | `0.15` | бонус шанса твиста при ретрае |

## Context Builder (токен-ориентированный контекст)

| ключ | дефолт (config) | `.env.example` | описание |
|---|---|---|---|
| `CONTEXT_ENABLED` | `true` | `true` | `false` = legacy-обрезка истории |
| `MAX_CONTEXT_TOKENS` | `60000` | `16000` | общий бюджет на персонажа |
| `CONTEXT_RECENT_MIN_TOKENS` | `8000` | `5000` | мин. свежего диалога |
| `CONTEXT_RECENT_MAX_TOKENS` | `40000` | `9000` | макс. свежего диалога |
| `CONTEXT_MEMORY_BUDGET` | `5000` | `2000` | бюджет воспоминаний |
| `CONTEXT_RETRIEVAL_BUDGET` | `5000` | `1500` | бюджет ретрива |
| `CONTEXT_SUMMARY_BUDGET` | `4000` | `2000` | бюджет саммари |
| `CONTEXT_STATE_BUDGET` | `3000` | `1500` | бюджет состояния |
| `CONTEXT_RESERVE_TOKENS` | `3000` | `3000` | резерв на промпт-скелет |
| `TOKEN_COUNT_MODE` | `estimated` | `estimated` | `estimated` (быстро) / `exact` (tiktoken + `TOKENIZER_ENCODING`) |
| `TOKENIZER_ENCODING` | (пусто) | (пусто) | кодировка tiktoken |
| `CONTEXT_HISTORY_LOAD_CAP` | `2000` | `2000` | потолок загрузки истории |
| `CONTEXT_RETRIEVAL_CANDIDATES` | `30` | `30` | кандидаты BM25-ретрива |
| `CONTEXT_MESSAGE_EMBEDDING_ENABLED` | `false` | — | эмбеддинг сообщений |
| `CONTEXT_DEBUG` | `false` | `false` | диагностика в логах |

## Диагностика генерации (Локации 2.0, §21)

| ключ | дефолт | описание |
|---|---|---|
| `GENERATION_DEBUG` | `false` | по-NPC DEBUG-лог в `chat_engine` (NPC, локации, visible/hidden персонажи, visible/filtered сообщения). Выключен на production |

Приоритет бюджета: резерв → state (P0) → summary/memory (P2) → retrieval (P3) → свежий диалог (остаток).

## Character State (Sprint 3, `Plans/update20.md §8`)

| ключ | дефолт | описание |
|---|---|---|
| `CHARACTER_STATE_ENABLED` | `false` | единое runtime-состояние персонажа: писать/читать `character_states` пост-раунд + рендерить блок `YOUR STATE` |
| `EMOTION_ROUND_CAP` | `0.4` | макс. прирост интенсивности одной эмоции за раунд |
| `STRESS_ROUND_CAP` | `0.2` | макс. прирост стресса за раунд (0..1) |
| `SENSORS_EMOTION_INTENSITY_CAP` | `0.3` | макс. сдвиг интенсивности от Sensors-предложения эмоции за раунд |

Подробно: `docs/character_state.md`. При `CHARACTER_STATE_ENABLED=false` (default)
таблица не пишется и не читается, поведение равно legacy.

## Belief System (Sprint 5, `Plans/update20.md §9`)

| ключ | дефолт | описание |
|---|---|---|
| `BELIEFS_ENABLED` | `false` | belief-система: писать/читать `beliefs` пост-раунд + рендерить блок `WHAT YOU KNOW`; mask читает beliefs вместо «неизвестно» |
| `BELIEFS_TOP_K` | `8` | cap beliefs персонажа в контекст (top-K по confidence) |
| `BELIEFS_RENDER_CONFIDENCE` | `0.3` | порог уверенности: beliefs ниже — не рендерить |
| `BELIEFS_LLM_SUGGESTION_ENABLED` | `false` | LLM-suggestion beliefs (только после benchmark gate `§27`, schema-validity ≥ 90%) |

Подробно: `docs/beliefs.md`. При `BELIEFS_ENABLED=false` (default) таблица
не пишется и не читается, остаётся MVP epistemic mask (canary, `§26`).

## Attention (Sprint 4, `Plans/update20.md §11`)

| ключ | дефолт | описание |
|---|---|---|
| `ATTENTION_ENABLED` | `false` | слой «воспринято ≠ вошло в сознание»: считать/писать `message_presence.attention` и фильтровать память/recency tail |
| `ATTENTION_LOW` | `0.35` | нижний порог: `score < LOW` — «слышал фоном» (не в память/реакцию) |
| `ATTENTION_HIGH` | `0.7` | верхний порог: `score ≥ HIGH` — «в центре внимания» (в память, в recency tail) |
| `ATTENTION_WEIGHT_VOLUME` | `0.15` | вес громкости (громкие стимулы / audio_level) |
| `ATTENTION_WEIGHT_DISTANCE` | `0.15` | вес близости (same > adjacent > remote по presence) |
| `ATTENTION_WEIGHT_RELEVANCE` | `0.10` | вес важности события (своя речь/игрок/персонаж/система) |
| `ATTENTION_WEIGHT_PERSONAL` | `0.25` | вес упоминания имени наблюдателя |
| `ATTENTION_WEIGHT_EMOTIONAL` | `0.10` | вес активного эмоционального якоря |
| `ATTENTION_WEIGHT_NOVELTY` | `0.05` | вес новизны (новое vs повтор) |
| `ATTENTION_WEIGHT_RELATIONSHIP` | `0.05` | вес участия target отношения наблюдателя |
| `ATTENTION_WEIGHT_ADDRESS` | `0.15` | вес addressed=true (в target_character_ids) |
| `SENSORS_PERCEPTION_SIGNIFICANCE_CAP` | `0.15` | макс. подъём attention score от Sensors perception-proposal (§5.1.3) |

Подробно: `docs/attention.md`. При `ATTENTION_ENABLED=false` (default) attention
не считается (NULL в БД), memory/recency фильтры ведут себя как раньше;
presence-лестница и рендер recent history не меняются.

## Hybrid Retrieval v2 (Sprint 6, `Plans/update20.md §14`)

| ключ | дефолт | описание |
|---|---|---|
| `HYBRID_RERANK_ENABLED` | `false` | детерминированный rerank memories ПОСЛЕ RRF, ДО witness-boost (оси + сигналы контекста) |
| `HYBRID_RERANK_WEIGHT_LEXICAL` | `0.30` | вес BM25-подобного overlap запроса/памяти (отпадает при отсутствии запроса) |
| `HYBRID_RERANK_WEIGHT_SEMANTIC` | `0.25` | вес cosine-похожести embeddings (отпадает при отсутствии embeddings) |
| `HYBRID_RERANK_WEIGHT_EMOTIONAL` | `0.10` | вес эмоциональной релевантности (intensity + \|valence\|) |
| `HYBRID_RERANK_WEIGHT_STORY` | `0.15` | вес story memory + overlap с активными story_threads |
| `HYBRID_RERANK_WEIGHT_RELATIONSHIP` | `0.10` | вес участия target отношения текущего контекста |
| `HYBRID_RERANK_WEIGHT_RECENCY` | `0.05` | вес свежести памяти |
| `HYBRID_RERANK_WEIGHT_SALIENCE` | `0.05` | вес salience памяти |

Подробно: `docs/retrieval.md`. При `HYBRID_RERANK_ENABLED=false` (default) RRF-путь
не меняется, BM25 не удаляется (fallback при отсутствии embeddings).


## Динамический num_ctx (KV window)

| ключ | дефолт | описание |
|---|---|---|
| `MIN_CTX` | `8192` | стартовое окно на чат |
| `MAX_CTX` | `32778` | потолок окна |
| `CTX_BUFFER_TOKENS` | `100` | буфер на рост |
| `CTX_SAFETY_FACTOR` | `1.3` | коэфф. запаса |

Окно только растёт: если `prompt_tokens > current_ctx`, оно увеличивается (с запасом), но не выше `MAX_CTX`. Сбрасывается при создании чата.

## Rate limit

| ключ | дефолт | описание |
|---|---|---|
| `RATE_LIMIT_SECONDS` | `5` | мин. интервал между раундами |

## Аватар персонажа (Этап A/B плана profile-avatar-appearance)

| ключ | дефолт | описание |
|---|---|---|
| `AVATAR_DIR` | `app/static/avatars` | каталог хранения файлов аватаров (отдаётся на `/static`) |
| `AVATAR_MAX_SIZE_MB` | `5` | лимит размера загружаемого файла |
| `AVATAR_MAX_DIMENSION` | `512` | сторона ресайза (px) |

Допустимые форматы (`png`, `jpeg`, `webp`) — константа в коде (`config.py: avatar_allowed_types`), проверка по magic-байтам. Используются на Этапе B (upload/validate); на Этапе A ключи только добавлены в конфиг и `.env.example`.

## Task queue (P3)

| ключ | дефолт | описание |
|---|---|---|
| `TASK_QUEUE_ENABLED` | `true` | очередь задач памяти |
| `TASK_QUEUE_MAX_RETRIES` | `3` | ретраи |
| `TASK_QUEUE_RETRY_MIN_WAIT` | `5.0` | мин. пауза (сек) |
| `TASK_QUEUE_RETRY_MAX_WAIT` | `60.0` | макс. пауза (сек) |
| `TASK_QUEUE_RETRY_MULTIPLIER` | `2.0` | множитель backoff |
| `TASK_QUEUE_MAX_CONCURRENT` | `5` | параллельных задач |
| `TASK_QUEUE_RETENTION_DAYS` | `30` | хранение задач (дней) |

## Эмбеддинги / векторный поиск (P3)

| ключ | дефолт | описание |
|---|---|---|
| `EMBEDDING_ENABLED` | `true` | эмбеддинги включены |
| `EMBEDDING_MODEL` | `bge-m3` | модель эмбеддингов |
| `EMBEDDING_DIM` | `1024` | размерность |
| `EMBEDDING_BATCH_SIZE` | `16` | батч |
| `VECTOR_TOP_K` | `10` | top-K векторного ретрива |
| `HYBRID_RRF_K` | `60` | константа RRF |
| `HYBRID_BM25_WEIGHT` | `1.0` | вес BM25 |
| `HYBRID_VECTOR_WEIGHT` | `1.0` | вес векторов |

## Система отношений

| ключ | дефолт | описание |
|---|---|---|
| `RELATIONSHIP_MAX_DELTA` | `20` | макс. дельта метрик за раунд |
| `RELATIONSHIP_DRIVERS_MAX` | `4` | макс. драйверов |
| `RELATIONSHIP_MAX_EVENTS_IN_PROMPT` | `5` | событий в промпте |
| `RELATIONSHIP_ANALYZER_ENABLED` | `true` | включён |
| `RELATIONSHIP_ANALYZER_MODEL` | (пусто) | модель анализатора; пусто = default |
| `RELATIONSHIP_ANALYZER_PROMPT` | см. код | шаблон промпта анализатора |
| `RELATIONSHIP_MIN_IMPORTANCE` | `3` | мин. важность события для журнала |
| `RELATIONSHIP_ANALYZE_ONLY_INTERACTING_PAIRS` | `true` | только пары, участвовавшие в раунде |
| `RELATIONSHIP_REFLECTION_DELTA_CAP` | `5` | потолок дельты рефлексии |
| `RELATIONSHIP_TYPE_CHANGE_REQUIRES_INTERACTION` | `true` | смена типа только при взаимодействии |
| `RELATIONSHIP_MAX_PAIR_CONTEXT_LINES` | `20` | строк контекста пары |
| `RELATIONSHIP_EVENTS_MAX_PER_PAIR` | `100` | макс. сырых событий пары; старые сворачиваются в `kind="archive"` (Sprint 4 п.21) |

Валидные типы отношений (фиксированный список) и разрешённые переходы (`relationship_transition_rules`) — см. `config.py:165-194`.

### Batch-анализатор (§8)

| ключ | дефолт | описание |
|---|---|---|
| `RELATIONSHIP_BATCH_ENABLED` | `true` | один LLM-вызов на все пары |
| `RELATIONSHIP_BATCH_FALLBACK` | `true` | при сбое — per-pair анализатор |

### Open issues (§7)

| ключ | дефолт | описание |
|---|---|---|
| `RELATIONSHIP_ISSUES_ENABLED` | `true` | включены |
| `RELATIONSHIP_ISSUE_TEXT_MAX` | `200` | макс. длина текста issue |
| `RELATIONSHIP_MAX_ISSUES_IN_PROMPT` | `3` | issues в промпт |
| `RELATIONSHIP_ISSUE_NEAR_DUP_JACCARD` | `0.7` | порог почти-дубликатов |
| `ISSUE_PROACTIVE_COEFF` | `0.15` | коэфф. проактивного буста |
| `ISSUE_PROACTIVE_BOOST_CAP` | `0.35` | потолок буста |
| `ISSUE_SALIENCE_DECAY_ROUNDS` | `5` | раундов затухания салиентности |

### Epistemic mask (§10)

| ключ | дефолт | описание |
|---|---|---|
| `RELATIONSHIP_EPISTEMIC_MASK_ENABLED` | `true` | персонаж «знает» о метриках только при прямом/наблюдаемом свидетельстве |
| `RELATIONSHIP_EPISTEMIC_MAX` | `8` | макс. таких пар в контексте |

## World & Perception Engine 3.0 (Plans/WPE.md)

Фаза 0: все флаги по умолчанию **`false`** — фундамент без изменения поведения.
Фаза 1 (канонические локации, read-path) реализована 08-04; флаг
`WORLD_ENGINE_LOCATIONS_ENABLED` по-прежнему **`false`** — включает сравнение
локаций по `location_id`, откат — выключение флага (возврат к строкам).
Фаза 2 (tool-calling `take_actions`, shadow) реализована 08-04; флаг
`WORLD_ENGINE_TOOLS_ENABLED` по-прежнему **`false`** — включает ветку
tools/format в генерации: действия извлекаются в `TurnOutput`, логируются,
**не применяются**; откат — выключение флага (генерация текст-only).
Каждая фаза включает свой флаг отдельным canary'ем.

Фаза 3 (dual-write + shadow) и Фаза 4 (Cutover + Recency Tail) реализованы
08-04; их флаги по-прежнему **`false`**. Включение `WORLD_ENGINE_PERCEPTION_ENABLED`
переводит presence (таблица `MessagePresence` + witness-фильтрация) на
двухканальный `perceive()` с Renderer (`witness_model.perceive_to_presence`);
включение `WORLD_ENGINE_RECENCY_TAIL_ENABLED` добавляет блок
`[СИСТЕМНОЕ ВМЕШАТЕЛЬСТВО: ...]` (P0-события: адресация) в самый конец
user-сообщения перед generation cue, защищённый от усечения бюджетом.
Флаги независимы (раздельные canary'и); откат — выключить оба.

Фаза 5 (Action Resolution + System Narrator) реализована 08-04; флаг
`WORLD_ENGINE_ACTIONS_ENABLED` по-прежнему **`false`** — включает применение
действий из tools (`turn.actions`): `move_to` обновляет `location`+`location_id`
и создаёт immutable `WorldEvent(move)` одной транзакцией, `send_message`
валидирует адресатов; Consistency Validator (`app/action_resolution`) даёт
вердикты `consistent` / `minor_ambiguity` (молчаливое действие → System
Narrator **без ретрая**) / `contradiction` (ретрай ≤1 внутри `generate()` с
фидбеком, затем отклонение + ремарка); regex-канал движения
(`detect_character_movement`) при включённом флаге — safety-net (И4), активен
только при выключенном флаге. Откат — выключить флаг (возврат к пост-раундовому
regex-пути). Тюнинг-настройка `WPE_ACTION_CONSISTENCY_MAX_RETRIES` (по
умолчанию `1`) ограничивает contradiction-ретраи внутри `generate()`.

Фаза 6 (Threads/мессенджер + двухканальное частичное восприятие) реализована
08-04; флаги по-прежнему **`false`**. Включение `WORLD_ENGINE_THREADS_ENABLED`
переводит `create_message` по удалённому каналу
(magic/phone/radio/messenger) на запись `Thread` + `ThreadParticipantState`
(участники = автор + адресаты; доставка — только адресатам); адресат получает
`remote_status=delivered` независимо от локации через
`world_state.thread_deliveries` в `perceive()` (Golden #6/#15). Включение
`WORLD_ENGINE_PARTIAL_PERCEPTION_ENABLED` включает частичное восприятие по
каналам: проницаемость рёбер `visual_permeability`/`audio_permeability`,
громкость (loud_sound поднимает muffled→full), невидимость (стимул `invisible`
→ visual=none/audio=full в одной локации) и voice familiarity (атрибуция по
голосу из `CharacterRelationship`: знакомый — «голос <имя>», незнакомый —
«чей-то голос»); Renderer `ContextBuilder` при обоих включённых флагах строит
канало-зависимые строки (`render_perception_line`), не утекая семантику (И11).
Флаги независимы; откат — выключить любой из них (partial → бинарный full/none
по каналам, треды — отдельно).

Фаза 7 (Event Bus / Interrupts) реализована 08-04; флаг по-прежнему **`false`**.
Включение `WORLD_ENGINE_EVENT_BUS_ENABLED` переводит цикл раунда
(`chat_engine.process_user_message_streaming`) на очередь приоритетов
`app/round_engine.py` (`run_round` — единственная оркестрирующая функция):
разбуженные NPC идут впереди плановых (внутри — FIFO), плановый порядок —
исходный `order_index`. Буждение по адресации (`addressed=true`): игрок→NPC —
`target_character_ids` user-сообщения первым ходом; NPC→NPC —
`target_character_ids` реплики. Один ответ на NPC за раунд, повторные буждения
игнорируются (И17) — без зацикливания. Откат — выключить флаг
(`run_round_fixed` — исходный фиксированный порядок без изменения поведения).

Фаза 8 (Уборка, реализована 08-04): deprecated text-only путь генерации удалён —
при недоступных tools/format генерация падает с `RuntimeError` (И14,
структурированные действия обязательны); regex-детекторы
(`detect_character_movement`, `_detect_communication_channel`) — только
legacy-safety-net с deprecation-логом `[WPE-P8]`, источник истины —
`turn.actions` из tools/format. Аудит legacy-полей (§6 v2) закрыт: `Message.visibility`
и `character.location`-строка — read-only legacy-bridge (write-path пишут также
`location_id`).


| ключ | дефолт | описание (фаза) |
|---|---|---|
| `WORLD_ENGINE_LOCATIONS_ENABLED` | `false` | канонические локации, сравнение по `location_id` (Фаза 1, реализована) |
| `WORLD_ENGINE_TOOLS_ENABLED` | `false` | tool-calling `take_actions` в shadow: извлечение, логирование, не применяются (Фаза 2, реализована) |
| `WORLD_ENGINE_EVENTS_ENABLED` | `false` | `WorldEvent` dual-write атомарно с `Message` + shadow `perceive()` 2 канала, классификация расхождений (Фаза 3, реализована) |
| `WORLD_ENGINE_PERCEPTION_ENABLED` | `false` | cutover на `PerceptionResult`/Renderer: presence пишется через `perceive()` (Фаза 4, реализована) |
| `WORLD_ENGINE_RECENCY_TAIL_ENABLED` | `false` | Recency Tail в хвост промпта, P0-адресация (Фаза 4, реализована) |
| `WORLD_ENGINE_ACTIONS_ENABLED` | `false` | применение действий + System Narrator + Consistency Validator (Фаза 5, Ул.1, реализована) |
| `WORLD_ENGINE_CONSISTENCY_MAX_RETRIES` | `1` | contradiction-ретраи ≤ N внутри `generate()` (Фаза 5, тюнинг) |
| `WORLD_ENGINE_THREADS_ENABLED` | `false` | Thread/ThreadParticipantState в проде: доставка по удалённому каналу независимо от локации (Фаза 6, реализована) |
| `WORLD_ENGINE_PARTIAL_PERCEPTION_ENABLED` | `false` | частичное восприятие по каналам: рёбра + громкость + невидимость + voice familiarity (Фаза 6, Ул.2, реализована) |
| `WORLD_ENGINE_EVENT_BUS_ENABLED` | `false` | Event Bus / буждение NPC: очередь приоритетов, один ответ за раунд (Фаза 7, Ул.5, реализована) |

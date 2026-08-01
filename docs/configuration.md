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
| `TIME_ADVANCE_INTERVAL` | `5` | интервал смены времени |
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

Приоритет бюджета: резерв → state (P0) → summary/memory (P2) → retrieval (P3) → свежий диалог (остаток).

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

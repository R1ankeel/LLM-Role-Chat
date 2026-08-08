# План декомпозиции монолитов AI Roleplay Chat

> Статус: **черновик для обсуждения** (ревизия 2026-08-08)
> Дата: 2026-08-02, актуализировано 2026-08-08
> Область: backend (`app/`) и frontend (`frontend/src/`, `app/static/`)
> Код не изменяется в рамках этого документа — это только план.

---

## 1. Цель и методология

Проект вырос в набор крупных файлов-«богов», каждый из которых смешивает несколько
ответственностей. Цель декомпозиции — **без изменения поведения** разбить файлы на
модули с единственной ответственностью, убрать циклические зависимости и сделать код
тестируемым/обслуживаемым.

Принципы:

1. **Рефакторинг без изменения поведения** — только перенос кода, переименования,
   разбиение импортов. Никакой новой бизнес-логики в рамках этой работы.
2. **Каждый шаг зелёный** — после каждого этапа обязателен прогон `pytest`
   (в проекте 85 тестовых файлов + golden-снапшоты + eval-harness) и
   `python -m compileall app` / запуск сервера.
3. **Сначала развязка зависимостей, потом разбиение файлов** — нельзя резать файл,
   пока в нём остались циклические/частные импорты наружу.
4. **Поэтапно, приоритет по выгоде** — начинаем с самого крупного и самого связного
   (`crud.py`, `chat_engine.py`, `ollama_client.py`), затем сервисы и CRUD, затем frontend.

---

## 2. Метрики и критерии кандидатов

Пороговые значения (по результатам замера кода 2026-08-08):

| Критерий | Порог |
|---|---|
| Размер файла | > 800 строк или > 30 КБ |
| Число зон ответственности в файле | > 2 |
| Число внутренних модулей, на которые импортирует файл | > 6 |
| Наличие циклических импортов с другими модулями | любой |
| Использование приватных (`_`) функций других модулей | любое |
| Слой импортирует слой «выше» себя (напр. `crud` → сервисы) | любой |

---

## 3. Карта текущей архитектуры

```
app/
  main.py                  # FastAPI app, lifespan, фоновые воркеры (mem jobs, consolidation scheduler)
  config.py                # 895 строк: Settings-монолит (~250 полей)
  database.py              # engine + ВСЯ DDL-схема инлайн (ensure_schema, ~1220 строк) + сессии
  models.py                # 27 ORM-классов в одном файле (1043 строки)
  schemas.py               # ВСЕ Pydantic-схемы (1372 строки)
  crud.py                  # «БОГ БД»: 4313 строк, 12+ доменов + сервисные вызовы
  chat_engine.py           # ОРКЕСТРАТОР: streaming-пайплайн + отношения + регенерация + story + LoRA
  ollama_client.py         # HTTP-транспорт + streaming + генерация + извлечение + WPE-тулы + модели
  memory_service.py        # BM25 + извлечение + консолидация + суммы + rerank + фоновые джобы
  relationship_service.py  # CRUD отношений + дельты + issues + decay + блоки контекста
  relationship_analyzer.py # LLM-анализ отношений (single + batch)
  relationship_interpreter.py
  repetition_detector.py   # эвристики повторов (840 строк)
  context_builder.py       # сборка контекста, retrieval, обрезка (1036 строк)
  context_state.py / context_budget.py / context_budget_manager.py
  prompt_builder.py        # генераторы блоков промптов (1054 строки)
  witness_model.py / perception.py / attention.py / stimuli.py / movement.py  # WPE-перцепция
  action_resolution.py     # консистентность действий / narrator
  role_isolation.py
  belief_service.py / character_state.py / emotion_engine.py  # состояния персонажей
  round_engine.py          # EventBus раунда (interrupts)
  wpe_shadow.py            # shadow-perception (WPE shadow)
  post_round_pipeline.py   # оркестратор 11 пост-раундных стадий (673 строки)
  event_service.py         # извлечение событий раунда
  pending_intervention.py  # реестр вмешательств игрока
  npc_plans.py / plot/     # сюжет: story_state, story_events, story_threads, intent, plot_pressure,
                           # story_consolidation (888), crisis_engine (809)
  task_queue.py / generation_tracker.py / ratelimit.py / token_counter.py
  embedding_service.py / avatar_service.py
  lora_manager.py (509) / lora_validation.py
  sensors_service.py / sensors/schemas.py
  routers/                 # HTTP-слой (chat_engine 402, relationships 471, debug 282, chats, characters,
                           # locations, jobs, lora)
app/static/                # ЛЕГАСИ-SPA: app.js (2621 стр.), style.css, index.html, debug.html
frontend/src/              # Vue 3 + Pinia frontend
  components/{characters,chat,common,layout,scene,settings}/
  stores/  api/  mocks/  types/  utils/  composables/
```

> **Важно (2026-08-08):** с момента первой версии плана (2026-08-02) файлы выросли
> в 1,5–3,5 раза (см. §4), появилось ~20 новых модулей (WPE-подсистема, сюжетный
> движок `plot/`, LoRA, sensors, пост-раундный конвейер). План ниже актуализирован
> под текущее состояние и дополнен новыми кандидатами.

---

## 4. Кандидаты на декомпозицию — Backend

### 4.1 `app/crud.py` — **4313 строк (164,7 КБ)** — **приоритет 0 (был 4.5)**

Файл вырос в 3,5 раза с момента плана (было 1224 строки) и теперь «бог БД» для
12+ доменов. Хуже того, **crud начал импортировать сервисы** (`memory_service`,
`embedding_service`, `perception`, `witness_model`, `attention`, `wpe_shadow`,
`sensors_service`, `belief_service`) — нарушение слоёв (см. §7.1).

| Ответственность | Диапазон (ориентировочно) |
|---|---|
| Chats + clear-операции | 35–201 |
| Characters (вкл. player, location sync) | 201–361 |
| Messages + world_event builder | 386–560 |
| Memories + witness-фильтр + rerank + anchors | 561–1135 |
| Anchors + consolidation state | 1136–1348 |
| Summaries | 1348–1409 |
| Presence / attention | 1409–1899 |
| Character locations + actions | 1899–2135 |
| Threads (messenger) | 2141–2307 |
| Locations (CRUD + backfill) | 2307–2776 |
| Scene state + присутствие | 2776–2880 |
| Rounds + round events | 2880–3097 |
| Character state | 3097–3213 |
| Beliefs | 3213–3340 |
| World events (round/chat) | 3340–3754 |
| Story state / story threads | 3475–3891 |
| Intents / NPC plans | 3891–4046 |
| LoRA adapters + chat config | 4093–4313 |

**Выделяемые модули** (`app/crud/` — тонкий слой доступа, по домену):

- `crud/chats.py`, `crud/characters.py`, `crud/messages.py`, `crud/memories.py`,
  `crud/summaries.py`, `crud/presence.py`, `crud/scene.py`, `crud/rounds.py`,
  `crud/locations.py`, `crud/threads.py`, `crud/events.py`, `crud/story.py`,
  `crud/state.py` (character state + beliefs), `crud/intents.py`, `crud/plans.py`,
  `crud/lora.py`.

Файл-агрегатор `crud/__init__.py` реэкспортирует публичные функции, чтобы существующие
импорты (`from . import crud`) продолжали работать без правки всех потребителей на
первом шаге.

**Условие до резки:** удалить импорты сервисов из `crud` (см. §7.1) — сервисные
вызовы (presence через `witness_model`/`perception`, rerank через `memory_service`,
wpe через `wpe_shadow`) должны быть инвертированы: сервис зовёт crud, а не наоборот.

---

### 4.2 `app/chat_engine.py` — 3118 строк (135,8 КБ) — **приоритет 1**

Вырос с 2044 до 3118 строк. Помимо прежнего streaming-пайплайна теперь здесь:
LoRA-резолв модели, story-блок, эпистемические evidence/belief, WPE-интеграция
(movement, stimuli, round_engine), sensors-предложения, пост-раундный конвейер.

| Диапазон | Функция | Ответственность |
|---|---|---|
| 549–1619 | `process_user_message_streaming` | основной streaming-пайплайн: SSE, присутствие, память, отношения, повторы, ретраи, LoRA, story, WPE |
| 1619–2088 | `_analyze_and_update_relationships` | анализ отношений после раунда |
| 2088–2166 | `_run_sensors_relationship_proposal` | sensors-предложения по отношениям |
| 2166–2586 | `_run_per_pair_analysis` + evidence/constrain | попарный анализ, сдерживание дельт |
| 2586–2609 | `process_user_message` | non-streaming точка входа |
| 2609–3118 | `regenerate_message_streaming` | регенерация сообщения |

**Выделяемые модули** (`app/pipeline/`):

- `pipeline/streaming.py` — `process_user_message_streaming` (конвейер сообщения).
- `pipeline/regeneration.py` — `regenerate_message_streaming`.
- `pipeline/relations.py` — анализ отношений + per-pair + hearsay cap
  (переезжают ближе к `relationship_service`).
- `pipeline/session.py` — `process_user_message`, общие хелперы раунда.
- `pipeline/lora.py` — `resolve_generation_model`, `lora_first_apply_warning`.
- `pipeline/story.py` — `_chat_story_block`, `_chat_plot_text`, belief evidence.

**Условие до резки:** удалить зависимость от частных функций — через `pipeline/*`
импортировать только публичные API модулей.

---

### 4.3 `app/ollama_client.py` — 3032 строки (118 КБ) — **приоритет 1**

Вырос с 1877 до 3032 строк. Транспорт + генерация + извлечение + WPE-тулы +
управление моделями (LoRA) в одном файле.

| Диапазон | Функция | Ответственность |
|---|---|---|
| 82–291 | `_llm_lock_for`, `llm_request`, tool-mode цепочки, WPE-парсинг `TurnOutput` | блокировка, режимы тулов, разбор turn-вывода |
| 291–446 | `_ConfigProxy` | прокидывание конфига (кандидат на удаление — `config.py` уже покрывает) |
| 446–705 | `_resolve_thinking`, `_character_*`, `format_history*`, `_messages_to_prompt`, `_build_*_payload` | форматирование истории/промптов |
| 705–1106 | `_call_ollama*`, `_stream_*` | HTTP-транспорт + SSE-стриминг |
| 1106–1788 | `_invoke_llm`, `_generate_once` | генерация: ретраи, role-isolation, повторы, анти-мимикрия, feedback-петли |
| 1788–1917 | `_vocab_key`, `_check_vocabulary_borrowing` | контроль словаря (анти-заимствования) |
| 1917–2335 | `generate` | публичная генерация |
| 2335–2760 | `_extract_json_payload`, `parse_extracted_facts`, `extract_memories_*`, `summarize_*` | извлечение памяти/сцен |
| 2760–2889 | `_build_event_extraction_messages`, `extract_round_events` | извлечение событий |
| 2889–3032 | `list_models`, `upload_adapter_file`, `create_model`, `delete_model`, `check_capabilities` | управление моделями/LoRA |

**Выделяемые модули** (`app/llm/`):

- `llm/transport.py` — `_call_*`, `_stream_*`, `_read_ollama_error`, `_ConfigProxy`, `llm_request`.
- `llm/prompting.py` — форматирование истории, payload-билдеры, `_messages_to_prompt`.
- `llm/generation.py` — `_invoke_llm`, `_generate_once`, `generate`, vocabulary borrowing.
- `llm/tasks.py` — извлечение памяти, суммаризация, scene-state, event extraction.
- `llm/wpe.py` — tool-calling, `_parse_tool_calls`, `_parse_turn_output_json`, tool-mode chain.
- `llm/models.py` — `list_models`, `create_model`, `delete_model`, `upload_adapter_file`, `check_capabilities`.
- `llm/lock.py` — глобальная сериализация Ollama-запросов (`_llm_lock_for`).

**Важно:** `relationship_analyzer.py` и `sensors_service.py` импортируют приватные
функции из `ollama_client`. До резки вводим публичный фасад `llm.generation.invoke_json(...)`
и переводим потребителей на него.

---

### 4.4 `app/relationship_service.py` — 1861 строка (69,8 КБ) — **приоритет 2**

| Ответственность | Функции |
|---|---|
| CRUD отношений | `get_or_create_relationship` … `update_relationship_fields` (107–267) |
| Валидация переходов / метрик | `validate_transition`, `validate_relationship_type_update`, `clamp_metric` (267–388) |
| Применение дельт | `apply_delta`, saturation guard (388–528) |
| Блоки контекста | `build_relationships_block`, `build_behavior_drivers_block`, `build_epistemic_mask_block` (578–863) |
| Issues (создание/резолв/тики/boost) | 863–1371 |
| Decay и прунинг событий | `apply_decay`, `prune_relationship_events` (1371–1592) |
| Кормление памяти из событий | `_maybe_create_memory_*` (1592–1800) |
| Траектория | `get_trajectory_events`, `recent_gain`, `build_trajectory_block` (1800–1861) |

**Выделяемые модули** (`app/relationships/`):

- `relationships/crud.py`, `relationships/deltas.py`, `relationships/issues.py`,
  `relationships/decay.py`, `relationships/blocks.py`, `relationships/memory_feed.py`,
  `relationships/trajectory.py`, `relationships/validation.py`.

**Условие:** внутри `relationship_service` уже есть локальные импорты `crud`
(596, 618, 698, 744, 1427, 1683, 1780) — это следы цикла
`relationship_service ↔ crud`, которые нужно зафиксировать до переноса.

---

### 4.5 `app/memory_service.py` — 2191 строка (77,9 КБ) — **приоритет 2**

Вырос с 1233 до 2191 строки. Добавились rerank-сигналы и адаптивная консолидация.

| Ответственность | Диапазон |
|---|---|
| BM25-поиск | класс `SimpleBM25` (179–267) |
| Rerank (гибридный ранжировщик) | `RerankSignals`/`rerank_memories` (267–477) |
| Валидация извлечённых фактов | `classify_memory_type`, `validate_extracted_facts` (477–725) |
| Наблюдаемый контекст / witness | `get_observable_context_for_character` (725–888) |
| Извлечение и сохранение памяти | `_extract_and_save_memories` (888–1073) |
| Суммаризации | `_maybe_update_summaries` (1073–1179) |
| Post-round джобы | `process_post_round`, `_process_post_round_job` (1179–1263) |
| Консолидация/кластеризация | `_cluster_*`, `_merge_*`, `_consolidate_*` (1263–1573) |
| Адаптивная консолидация | `evaluate_consolidation`, `schedule_adaptive_consolidation` (1573–2098) |
| Embedding-джобы | 2098–2191 |

**Выделяемые модули** (`app/memory/`):

- `memory/retrieval.py` — `SimpleBM25` и гибридный поиск + rerank.
- `memory/extraction.py`, `memory/consolidation.py`, `memory/summaries.py`,
  `memory/jobs.py`, `memory/validation.py`, `memory/adaptive.py`.

**Условие:** разорвать цикл `crud ↔ memory_service` (см. §7.1). `SimpleBM25` и
rerank-сигналы не зависят от ORM — переносятся первыми как чистые модули.

---

### 4.6 `app/schemas.py` — 1372 строки (49,5 КБ)

Все Pydantic-схемы в одном файле (72 класса). **Выделяемые модули** (`app/schemas/`):
`chat.py`, `character.py`, `message.py`, `memory.py`, `relationship.py`,
`scene.py`, `context.py`, `job.py`, `story.py`, `belief.py`, `state.py`
(character state/emotion), `lora.py`, `perception.py` (TurnOutput/Action).
Схемы взаимно импортируют друг друга (напр. `BuiltContext`, `IssueDelta`,
`RelationshipDelta`) — сначала проверяем ацикличность, затем реэкспорт через
`schemas/__init__.py`.

---

### 4.7 `app/database.py` — 1361 строка (57,9 КБ)

`ensure_schema` (111–1330) содержит **~1220 строк** DDL-схемы — скрытая «миграция»
выросла вдвое с момента плана. Выделить:

- `db/engine.py` — engine, pragma, `init_db`, сессии.
- `db/schema.py` — вся DDL из `ensure_schema` (алгоритм остаётся идемпотентным).
- `db/models.py` — переезд `models.py` рядом с БД (см. §4.13).

---

### 4.8 `app/models.py` — 1043 строки (53,8 КБ) — **новый кандидат**

27 ORM-классов в одном файле: `Chat`, `Location`, `WorldEvent`, `Thread`,
`ThreadParticipantState`, `Character`, `Message`, `MessagePresence`, `Memory`,
`CharacterSummary`, `SceneState`, `MemoryJob`, `RelationshipIssue`,
`CharacterRelationship`, `RelationshipEvent`, `CharacterState`, `Belief`,
`StoryState`, `StoryThread`, `StoryEvent`, `EventLink`, `MemoryAnchor`, `Intent`,
`NpcPlan`, `ConsolidationState`, `LoRAAdapter`, `ChatLoRAAdapter`.

**Выделяемые модули** (`app/models/` пакет, по домену): `chat.py`, `character.py`,
`message.py`, `memory.py`, `relationship.py`, `presence.py`, `scene.py`,
`story.py` (StoryState/StoryThread/StoryEvent/EventLink), `intent.py`,
`state.py` (CharacterState/Belief), `lora.py`. `models/__init__.py` реэкспортирует
все классы — `Base.metadata` и существующие импорты не меняются.

---

### 4.9 `app/config.py` — 895 строк (53,8 КБ) — **новый кандидат**

`Settings`-монолит (~250 полей, все в одном классе). Разделение на под-классы с
композицией (`SettingsBase` + доменные миксины) или на пакет `config/`:

- `config/core.py` — base, url, model, история;
- `config/memory.py` — память/консолидация/embedding;
- `config/context.py` — бюджет контекста;
- `config/relationships.py` — отношения/issues/decay;
- `config/repetition.py`, `config/wpe.py`, `config/story.py`, `config/sensors.py`,
  `config/lora.py`, `config/task_queue.py`, `config/avatar.py`.

Синглтон `settings = Settings()` и доступ `settings.<attr>` сохраняются.

---

### 4.10 `app/repetition_detector.py` — 840 строк (27,6 КБ)

Один файл = пакет эвристик: извлечение действий, лексические скоринг-функции,
cooldown, interaction-loop, progression/stagnation, итоговая `analyze_response`.

**Выделяемые модули** (`app/repetition/`): `actions.py`, `scoring.py`,
`analyzer.py`, `feedback.py`. Файл хорошо изолирован, но велик.

---

### 4.11 `app/context_builder.py` — 1036 строк (43 КБ)

`ContextBuilder.build` (110–659) — сборка; `_assemble_recent`, `_trim_*`,
`_select_retrieved` — обрезка/отбор; `_load_presence_map` — обращение к БД;
`_build_story_block` (941) — обращение к `plot`.

**Выделяемые модули** (`app/context/`): `retrieval.py`, `assembly.py`,
`trimming.py`, `story.py`. Файл уже разбит на `_методы`, перенос механический.

---

### 4.12 `app/prompt_builder.py` — 1054 строки (43,5 КБ)

Большой набор `build_*`-функций (60+ функций). **Выделяемые модули** (`app/prompt/`):
`character.py` (карточка/личность/анти-мимикрия), `blocks.py`
(правила, память, диалог), `scene.py`, `extraction.py` (извлечение/суммаризация),
`relationships.py`, `story.py`, `state.py`. На первом шаге — реэкспорт из
`prompt_builder.py` для совместимости. Часть функций уже тонкие обёртки над
`character_state`/`npc_plans`/`story_state` — см. §7.4.

---

### 4.13 `app/perception.py` — 760 строк (29,4 КБ) — **новый кандидат**

WPE-перцепция: локации/adjacency, уровни восприятия, permeability, world-state,
события. Файл вырос с ~300 до 760 строк. **Выделяемые модули** (`app/perception/`):

- `perception/locations.py` — `normalize_location`, `locations_match`, adjacency, toponym;
- `perception/levels.py` — `get_perception_level`, `can_character_perceive_event`;
- `perception/world.py` — `PerceptionWorldState`, `build_permeability_index`;
- `perception/events.py` — `event_from_message`, `can_character_perceive_event`.

Сопутствующие мелкие модули WPE (без жёсткой необходимости резать):
`witness_model.py` (633), `attention.py` (283), `stimuli.py` (207), `movement.py`
(182), `action_resolution.py` (321), `round_engine.py` (115). См. §7.6.

---

### 4.14 `app/plot/` — пакет с двумя «богами» — **новый кандидат**

`story_consolidation.py` — **888 строк**, `crisis_engine.py` — **809 строк**.
Внутри уже разбитого пакета выросли монолиты:

- `story_consolidation.py`: парсинг/валидация JSON (`_parse_*`, `validate_*`),
  grounding (`_thread_grounded`), LLM-invoke (`_invoke_consolidation`), применение
  (`_apply_consolidation`), триггер (`maybe_consolidate_story`).
  → `plot/consolidation/parse.py`, `grounding.py`, `llm.py`, `apply.py`, `scheduler.py`.
- `crisis_engine.py`: скоринг (`compute_crisis_pressure`, `trajectory_score_*`),
  кандидаты, LLM-оценка (`_evaluate_crisis_llm`), мягкое применение
  (`_apply_crisis_softly`), блок (`build_crisis_block`), запуск (`run_crisis_engine`).
  → `plot/crisis/pressure.py`, `scoring.py`, `llm.py`, `apply.py`, `block.py`.

---

### 4.15 `app/post_round_pipeline.py` — 673 строки (24,1 КБ) — **новый кандидат**

Оркестратор 11 пост-раундных стадий. Уже хорошо структурирован (каждая стадия —
отдельная `_stage_*` функция), но это файл-«диспетчер» из 11 областей. Дальнейшая
декомпозиция логична, если стадии начнут расти: `post_round/` пакет с
`stages/*.py` (presence, events, memory, relationships, character_state, beliefs,
story, story_threads, plans, crisis, adaptive) и `post_round/run.py`.
На текущий момент — кандидат с низким приоритетом (без изменения поведения,
каждая стадия уже изолирована).

---

### 4.16 `app/lora_manager.py` — 509 строк (23,2 КБ) — **новый кандидат**

LoRA-рантайм: совместимость, валидация, ключи, менеджер. Парный `lora_validation.py`
(149) уже выделен. Дальнейшая декомпозиция по мере роста: `lora/compat.py`,
`lora/runtime.py`, `lora/keys.py`. На текущий момент — кандидат со средним
приоритетом (дополнительно смотреть на `routers/lora.py` и frontend `LoRASettings.vue`).

---

### 4.17 Маршрутизаторы (routers/)

- `routers/relationships.py` — 471 строка, содержит бизнес-логику поверх CRUD.
  Вынести «поведение» в сервисный слой, оставить в роутере HTTP-адаптацию.
- `routers/chat_engine.py` — 402 строки: SSE-обработка; оставить, вынести
  формирование событий/полезной нагрузки в `pipeline`.
- `routers/debug.py` — 282 строки: преимущественно сериализация для debug-страницы;
  можно вынести `_serialize_*` в `services/debug_render.py`, роутер оставить тонким.
- `routers/chats.py`, `characters.py`, `jobs.py`, `locations.py`, `lora.py` —
  нормального размера, не трогать.

---

## 5. Кандидаты на декомпозицию — Frontend

### 5.1 `frontend/src/components/characters/RelationshipPairDetail.vue` — 817 строк — **приоритет 3**

Смешивает: просмотр пары, историю, issues, траекторию, форму.
- Разбить на дочерние компоненты (`RelationshipHistory`, `IssueList`,
  `TrajectoryTimeline`, `RelationshipForm`) и composables
  (`useRelationshipPair`, `useIssueActions`).

### 5.2 `frontend/src/components/layout/Sidebar.vue` — 737 строк — **приоритет 3**

Смешивает: список чатов, создание чата, переименование, удаление, поиск.
- Вынести `NewChatDialog.vue`, `ChatListItem.vue`, `RenameChatDialog.vue`,
  composable `useChatSidebar`.

### 5.3 `frontend/src/components/settings/LoRASettings.vue` — 630 строк — **новый кандидат**

Смешивает: список адаптеров, совместимость, CRUD-форму, привязку к чату.
- Вынести `LoRAAdapterForm.vue`, `LoRAAdapterListItem.vue`, `LoRACompatibilityBadge.vue`,
  composable `useLoRAForm` (логика в `stores/lora.ts` уже есть).

### 5.4 `frontend/src/components/settings/CharacterProfileModal.vue` — 593 строки — **новый кандидат**

Смешивает: профиль, память, состояние, восприятие.
- Разбить на `CharacterProfile`, `CharacterMemoryTab`, `CharacterStateTab`,
  composable `useCharacterProfile`.

### 5.5 `frontend/src/components/chat/Composer.vue` — 551 строка — **новый кандидат**

Смешивает: ввод, авторесайз, countdown, intervention-редактор.
- Вынести `InterventionEditor.vue`, composable `useComposer`.

### 5.6 `frontend/src/mocks/data.ts` (811) и `mocks/service.ts` (786) — **приоритет 4**

Легаси-мок-слой для разработки. Когда API стабилизирован — удалить, а не
декомпозировать (проверить использование в сторах). Если оставлять — разбить по
доменам (`mocks/characters.ts`, `mocks/chats.ts`, …) и генерировать данные из
типов, а не дублировать.

### 5.7 Прочие

- `stores/messages.ts` (374) — на грани; если продолжит расти — вынести SSE/потоковую
  логику в `composables/useStreaming.ts`. Пока не трогать.
- `stores/relationships.ts` (183) — нормального размера, следить за ростом.
- `RelationshipGraph.vue` (372) — вынести layout-логику графа в composable
  `useRelationshipGraph` (в составе 5.1).

---

## 6. Легаси `app/static/` — не декомпозировать, а выводить из эксплуатации

`app/static/app.js` — 2621 строка vanilla-JS SPA (105 КБ), `style.css` — 1342 строки.
По существующему плану `Plans/frontend-app.md` новый frontend строится на Vue 3.
Поэтому:

1. НЕ рефакторить `app.js` — это работа на выброс.
2. После перехода на Vue frontend и удаления legacy-страниц — вынести
   `app/static/*` в архив/удалить, оставив только `favicon`.
3. Обновить `app/main.py` (раздача static) после проверки, что Vue-сборка покрывает
   все маршруты (root, chat, health, models).
4. `app/static/debug.html` (229 строк) — служебная debug-страница `routers/debug.py`;
   при переносе `routers/debug.py` сохранить маршрут.

---

## 7. Сквозные проблемы, которые надо решить до/вместе с резкой

### 7.1 Циклические импорты и нарушение слоёв

- **`crud ↔ memory_service`** (взаимный импорт, причём `crud` импортирует
  `memory_service` и `embedding_service` на верхнем уровне — нарушение слоя
  «crud = только доступ к БД»). **План:** выделить чистые функции (BM25, rerank,
  скоринг) в `memory/retrieval.py` без ORM; «гибридный поиск» и rerank из `crud.py`
  перенести в `memory/`; из `crud` убрать сервисные вызовы.
- **`crud` → `witness_model`/`perception`/`attention`/`wpe_shadow`/`sensors_service`/
  `belief_service`** (локальные импорты против циклов). **План:** сервисный слой
  должен звать crud, а не наоборот; присутствие/внимание/белифы пересчитываются
  сервисами (`memory_service`, `post_round_pipeline`) поверх чистого `crud`.
- **`relationship_service ↔ crud`** (локальный импорт `crud` внутри функций).
  **План:** определить направление зависимости: сервис → crud (только). Память из
  событий создаётся через явный интерфейс `memory/` без импорта `crud` внутри.
- **`task_queue ↔ memory_service`** (диспетчер джобов → обработчики). **План:**
  ввести регистрацию обработчиков (паттерн `handler registry`) вместо прямого
  импорта обработчиков из `task_queue`.
- **`wpe_shadow ↔ crud`** (локальный импорт против цикла `crud -> wpe_shadow`).
  **План:** перенос shadow-perception в сервис, вызывающий crud.

### 7.2 Использование приватных функций

- `relationship_analyzer` импортирует `_invoke_llm`, `_extract_json_payload` из
  `ollama_client`. **План:** публичный фасад `llm/generation.py::invoke_json(...)`
  и перевод потребителей. После этого приватные функции можно скрыть.

### 7.3 Логика в роутерах

`routers/relationships.py` — бизнес-логика поверх CRUD. **План:** перенос в
сервисный слой (`relationships/`), роутер остаётся «тонким» (валидация + статусы).

### 7.4 Дублирование сборки блоков контекста

Блоки собираются в нескольких местах:

- `build_relationships_block` — в `relationship_service` (async, данные из БД),
  `prompt_builder` (строки), `context_builder` (тонкая обёртка над `prompt_builder`).
- `build_your_state_block` — дублируется в `character_state` и `prompt_builder`
  (последний уже обёртка через `from .character_state import ...`).
- `build_active_plan_block` — дублируется в `npc_plans` и `prompt_builder`
  (обёртка). `build_story_block` — в `story_state` и `prompt_builder` (обёртка).

**План:** единый слой `prompt/` — генераторы строк; сервисы возвращают данные;
обёртки `context_builder`/`prompt_builder` над `character_state`/`npc_plans`/
`story_state` консолидировать в одном месте (в `prompt/`), чтобы не было двух
файлов-владельцев одной функции.

### 7.5 `config.py` и `models.py` — монолиты, которые удобнее всего резать первыми

Их разбиение не требует развязки циклов: `config` — один класс без зависимостей,
`models` — ORM-классы без бизнес-логики. Рекомендуется выполнить раньше остального
(см. этапы 2–3 в §9).

### 7.6 WPE-подсистема — связный пакет с мелкими файлами

`perception`, `witness_model`, `attention`, `stimuli`, `movement`,
`action_resolution`, `round_engine`, `wpe_shadow`, `event_service`,
`pending_intervention` — новая WPE-область, растущая точечными модулями.
Сейчас каждый файл в пределах нормы (кроме `perception.py` 760). Резка только
`perception.py` (§4.13); остальные — оставить и следить, не объединять в
искусственные пакеты раньше времени.

---

## 8. Целевая структура пакета `app/` (после декомпозиции)

```
app/
  main.py  ratelimit.py  generation_tracker.py  token_counter.py
  config/
    core.py  memory.py  context.py  relationships.py  repetition.py
    wpe.py  story.py  sensors.py  lora.py  task_queue.py  avatar.py
  llm/
    lock.py  transport.py  prompting.py  generation.py  tasks.py  wpe.py  models.py
  pipeline/
    streaming.py  regeneration.py  relations.py  session.py  lora.py  story.py
  memory/
    retrieval.py  extraction.py  consolidation.py  summaries.py  jobs.py
    validation.py  adaptive.py
  relationships/
    crud.py  deltas.py  issues.py  decay.py  blocks.py  memory_feed.py
    trajectory.py  validation.py
    analyzer/  (single.py  batch.py  parse.py)
    interpreter.py
  context/
    builder.py  budget.py  state.py  story.py
  repetition/
    actions.py  scoring.py  analyzer.py  feedback.py
  prompt/
    character.py  blocks.py  scene.py  extraction.py  relationships.py
    story.py  state.py
  perception/
    locations.py  levels.py  world.py  events.py
    witness.py  attention.py  stimuli.py  movement.py  action_resolution.py
    round_engine.py  shadow.py
  plot/
    story_state.py  story_events.py  story_threads.py  intent.py  plot_pressure.py
    consolidation/  (parse.py  grounding.py  llm.py  apply.py  scheduler.py)
    crisis/  (pressure.py  scoring.py  llm.py  apply.py  block.py)
  db/
    engine.py  schema.py  sessions.py  models.py
  crud/
    chats.py  characters.py  messages.py  memories.py  summaries.py
    presence.py  scene.py  rounds.py  locations.py  threads.py  events.py
    story.py  state.py  intents.py  plans.py  lora.py
  schemas/
    chat.py  character.py  message.py  memory.py  relationship.py  scene.py
    context.py  job.py  story.py  belief.py  state.py  lora.py  perception.py
  services/
    avatar.py  embeddings.py  task_queue.py  post_round/  (stages/*, run.py)
    event_service.py  belief.py  character_state.py  emotion.py  npc_plans.py
    lora.py  sensors.py  pending_intervention.py  debug_render.py
  routers/
    __init__.py  chat_engine.py  chats.py  characters.py  relationships.py
    jobs.py  locations.py  lora.py  debug.py
```

Для каждого пакета — `__init__.py` с реэкспортом публичного API, чтобы внешние
потребители (роутеры, `main.py`, тесты) не менялись мгновенно.

---

## 9. Поэтапный план (порядок и проверки)

> Каждый этап заканчивается: `pytest -q` (зелёный) + ручная проверка одного
> раунда чата (SSE streaming работает).

| № | Этап | Действия | Проверка |
|---|---|---|---|
| 0 | Базовая линия | зафиксировать текущие результаты тестов и golden-снапшотов | `pytest -q`, eval-набор |
| 1 | Развязать циклы | §7.1: `memory/retrieval.py` (чистый BM25+rerank), фасад `llm` (7.2), registry джобов | `pytest` зелёный |
| 2 | `config/` | разбить `config.py` на миксины/пакет (самый безопасный шаг) | `pytest` + запуск API |
| 3 | `models/` | разбить `models.py` на доменные модули + реэкспорт | `init_db` на пустой БД + `pytest` |
| 4 | `db/` | вынести `database.py::ensure_schema` DDL → `db/schema.py`, сессии → `db/engine.py` | `init_db` на пустой БД + `pytest` |
| 5 | `schemas/` | разбить `schemas.py` на пакет с реэкспортом | `pytest` + запуск API |
| 6 | `crud/` | разбить `crud.py` на доменные модули + реэкспорт через `__init__` (самый крупный шаг — на 2–3 под-этапа) | `pytest` |
| 7 | `llm/` | выделить `lock/transport/prompting/generation/models` из `ollama_client.py` | `pytest` + WPE-тесты |
| 8 | `pipeline/` | выделить `streaming.py` + `regeneration.py` из `chat_engine.py` (на 2 под-этапа) | полный round-trip тест + `test_chat_engine.py`, `test_stream_disconnect.py` |
| 9 | `pipeline/relations.py` | перенести анализ отношений из `chat_engine.py` | `test_relationship_*`, `test_role_isolation.py` |
| 10 | `relationships/` | разбить `relationship_service.py` | `test_relationship_*` |
| 11 | `memory/` | extraction/summaries/consolidation/adaptive/jobs из `memory_service.py` | `test_memory_*`, `test_consolidation.py`, `test_task_queue.py`, `test_adaptive_consolidation.py` |
| 12 | `repetition/` | разбить `repetition_detector.py` | `test_repetition_detector.py`, `test_ollama_chat.py` |
| 13 | `context/`, `prompt/` | разбить `context_builder.py` и `prompt_builder.py`; консолидировать обёртки §7.4 | `test_context_*`, `test_prompt_builder_golden` |
| 14 | `perception/` | разбить `perception.py` | `test_perception*`, `test_locations_perception.py`, `test_world_engine_phase*` |
| 15 | `plot/` | разбить `story_consolidation.py` и `crisis_engine.py` | `test_story_consolidation.py`, `test_crisis_engine.py`, `test_story_state.py` |
| 16 | Роутеры | вынести логику из `routers/relationships.py`, `routers/debug.py` в сервисный слой | `test_relationship_issues_endpoint.py`, `test_debug_router.py` |
| 17 | Frontend Vue | §5.1–5.5 (поэлементно) | `npm run build` (`vue-tsc`) |
| 18 | Legacy static | §6: удалить/заархивировать `app/static/app.js` | ручная проверка Vue-сборки через сервер |
| 19 | Чистка | удалить реэкспорт-фасады, если не нужны; актуализировать `docs/` | полный `pytest` + eval |

---

## 10. Риски и стратегия

| Риск | Смягчение |
|---|---|
| Регрессия streaming-пайплайна (SSE, отключение клиента, ретраи) | этап 8 на два под-этапа; прогон `test_stream_disconnect.py`, golden `tests/golden/*`, eval `tests/eval/` |
| Скрытая миграция схемы (`ensure_schema`) сломается при переносе | этап 4 на копии `ai_chat.db`; идемпотентность сохраняется, не меняем SQL |
| `crud` уже импортирует сервисы — при переносе легко получить регрессии присутствия/внимания | этап 1 (развязка слоёв) ДО этапа 6; каждый сервисный вызов из crud фиксируется тестом |
| WPE-тесты (`test_world_engine_phase*`, `test_perception*`) зависят от тонких функций | перенос без изменения кода; все `_parse_*`/`build_*` сохраняются как есть |
| Реэкспорт-фасады замаскируют старые импорты | фасады временные; в этап 19 удаляются, контролируются `rg "from . import crud"` |
| Циклы импортов всплывут после переноса | перед каждым переносом — статический обход зависимостей; направление: роутер → сервис → crud/db |
| `SimpleBM25`/rerank извлечение из memory_service меняет численные результаты | перенос без изменений кода; golden-тесты по памяти |
| Legacy frontend и Vue конфликтуют в static | выключение legacy только после полного покрытия маршрутов Vue-сборкой |

---

## 11. Критерии завершения

1. Нет файлов > 600 строк в `app/` (кроме `prompts/ru.json` и static-ассетов).
2. Нет циклических импортов (`python -c "import app.main"` + статический анализ).
3. `crud/` и `db/` не импортируют сервисный слой (однонаправленные зависимости).
4. Приватные функции не пересекают границы модулей.
5. Роутеры не содержат бизнес-логики.
6. `pytest -q` и eval-набор зелёные до и после каждого этапа.
7. Vue-frontend собирается (`npm run build`), legacy `app/static/app.js` выведен
   из эксплуатации.

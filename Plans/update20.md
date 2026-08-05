# Roleplay Engine — архитектурный аудит и план развития до state-driven архитектуры

> Дата: 2026-08-04 · Статус: **аудит + roadmap, без изменения кода**
> Код-база: `ai-roleplay-chat/`. Этот документ — результат глубокого аудита
> существующих подсистем (memory, relationships, perception, locations,
> world events, context, story prompt) и план перехода к единой
> state-driven архитектуре, в которой LLM не является единственным
> источником истины о состоянии мира.
>
> **Ревью-правки (2026-08-04)**: добавлены §15.0 (canonical event source),
> §24.1 (LLM budget), §24.2 (feature profiles), §27 (quality metrics +
> benchmark gate), §29.1 (observability), §29.2 (ADR belief system);
> Sprint 1 расширен до post-round pipeline; DoD и appendices обновлены.
> Порядок спринтов 0–13 и цель не меняются.
>
> **Sensors Model (2026-08-04)**: добавлен §5.1 — отдельный аналитический слой
> (SensorsService + `SENSORS_MODEL` из `.env`) для быстрых фоновых задач
> (perception-предложения, event classification, emotion/mood, memory-кандидаты,
> relationship-дельты). Sensors — НЕ источник истины и НЕ подменяет основную
> модель генерации. Инфраструктура в Sprint 0, подключение — фоновые процессы
> по спринтам; генерация персонажей не меняется (§5.1, R13–R16).
>
> **Sprint 1 (2026-08-05)**: ✅ ВЫПОЛНЕН — Structured World Events + Post-Round
> Pipeline (§15): `world_events` +action/importance/salience, `event_links`,
> `relationship_events.event_id`; раундная event extraction (`event_service` +
> Sensors hook) под флагом `EVENT_EXTRACTION_ENABLED` (default false);
> оркестратор `app/post_round_pipeline.py` вынесен из `chat_engine`. Тесты:
> **840 passed / 28 pre-existing / 0 новых**. См. «Sprint 1 — Статус».
>
> **Sprint 2 (2026-08-05)**: ✅ ВЫПОЛНЕН — Memory Architecture v2: типы + якоря
> (§7): `memories` +memory_type/event_id/valence/intensity (default 'semantic'),
> детерминированный fallback-классификатор (`classify_memory_type`),
> `ExtractedFact.memory_type` в extraction-промпте, anchor-запись из значимых
> RelationshipEvent (расширение `_maybe_create_memory_from_event`), Sensors
> memory-hook (§5.1.3) в `_extract_and_save_memories`; canary-флаги
> `MEMORY_TYPES_ENABLED`/`ANCHORS_ENABLED` (default false). Тесты:
> **869 passed / 28 pre-existing / 0 новых** (29 новых тестов). См.
> «Sprint 2 — Статус».
>
> **Sprint 3 (2026-08-05)**: ✅ ВЫПОЛНЕН — Character State (§8): единое
> runtime-состояние персонажа в `character_states` (эмоции/стресс/mood/физика/
> внимание/цели) без дублирования локаций и отношений. Детерминированный
> `emotion_engine` (эмоции из relationship deltas + событий раунда, stress,
> mood-вывод, decay, капы за раунд); опциональная Sensors-нормализация эмоций
> только в рамках caps (`sensors_emotion_enabled`, Sensors НЕ задаёт mood);
> пост-раунд стадия `character_state` в pipeline; блок `YOUR STATE` в контексте
> (рендер по флагу `CHARACTER_STATE_ENABLED`, default false); canary-флаги
> `EMOTION_ROUND_CAP`/`STRESS_ROUND_CAP`/`SENSORS_EMOTION_INTENSITY_CAP`.
> Тесты: **894 passed / 28 pre-existing / 0 новых** (24 новых теста). См.
> «Sprint 3 — Статус».
>
> **Sprint 4 (2026-08-05)**: ✅ ВЫПОЛНЕН — Attention (§11): слой «воспринято ≠
> вошло в сознание». Детерминированный `attention.py` (score 0..1 из 8
> компонентов с нормированными весами), колонка `message_presence.attention`
> (REAL NULL, идемпотентный ALTER), фильтр memory extraction (attention < LOW →
> не в память) и recency tail (не в реакцию) при `ATTENTION_ENABLED` (default
> false); Sensors perception-proposal hook (§5.1.3) в presence round pass
> (significance только в рамках `SENSORS_PERCEPTION_SIGNIFICANCE_CAP`).
> Presence-лестница и рендер recent history НЕ меняются. Тесты:
> **914 passed / 28 pre-existing / 0 новых** (20 новых тестов). См.
> «Sprint 4 — Статус».
>
> **Sprint 6 (2026-08-05)**: ✅ ВЫПОЛНЕН — Hybrid Retrieval v2 (§14): детерминированный
> rerank memories ПОСЛЕ RRF, ДО witness-boost (ось memory_type/valence/intensity +
> сигналы контекста: отношения, story_threads; веса в config; fallback BM25 при
> отсутствии embeddings); canary-флаг `HYBRID_RERANK_ENABLED` (default false),
> RRF-путь без флага не меняется, BM25 не удаляется. Тесты:
> **946 passed / 28 pre-existing / 0 новых** (24 новых теста). См.
> «Sprint 6 — Статус».
>
> Принцип: **не строить новые подсистемы поверх существующих**. Сначала найти
> существующую реализацию, расширять её; если она мешает развитию — явно
> вынести миграцию/рефакторинг в отдельный спринт. Дублирование запрещено.
>
> Соглашение об откате: все новые механизмы внедряются под флагами
> (по образцу `WORLD_ENGINE_*` из `Plans/WPE.md`), каждый спринт имеет
> независимый canary-флаг и критерий выхода; регрессионный барьер —
> текущий золотой набор тестов (`tests/test_world_engine_phase*.py`,
> `tests/golden/*`, 771 passed / 28 pre-existing) без роста числа падений.

---

## 1. Executive Summary

Проект уже прошёл большой путь: есть зрелые подсистемы отношений
(метрики, события, issues, decay, trajectory, epistemic mask, hearsay,
triads), память (BM25+vector+RRF, witness-фильтр, summaries, consolidation),
восприятие (двухканальный `perceive()`, presence-лестница, проницаемость
локаций, треды), Event Bus и Action Resolution. Всё это хорошо изолировано
флагами и покрыто тестами.

Главный архитектурный дефицит — **отсутствие структурированного состояния
мира и персонажей как источника истины**:

1. **Story** — статичный `chat.general_prompt`, вставляется в `<scene>` блок
   как «Сюжет: …» (`prompt_builder.build_scene_block:219`). Динамического
   состояния сюжета, фаз, истории сюжетных событий нет. Поля
   `SceneCustomState.plot_flags / active_events / active_goal / active_goals`
   существуют в схеме, но **движок их никогда не пишет** — их устанавливает
   только пользователь через PATCH scene.
2. **Character State** — нет эмоционального состояния персонажа
   (`mood`/`stress` есть только глобально в `SceneCustomState`), нет
   physical_state, attention, intent, personal goals.
3. **Belief System** — намеренно отложена (`docs/relations.md §22
   «Не реализовывать: полноценную belief-system»). Есть только MVP
   `epistemic_mask`.
4. **Memory** — единая плоская таблица `memories` с категорией; нет
   разделения semantic/episodic/social/story, нет эмоциональных якорей,
   нет связи memory↔relationship↔event кроме `source_message_ids`.
5. **Consolidation** — фиксированный 24h-таймер, не адаптивный, не видит
   relationship/story/emotional-данные; `consolidation ≠ summary` не выдержан.
6. **Attention** — нет слоя «воспринято ≠ вошло в сознание».
7. **NPC Intent/Plans** — нет; `active_goals` — пользовательские, не движок.
8. **Event graph** — `world_events` есть, но без полей причинности
   (cause/caused_by/consequences), без importance/salience, без action-структуры.
9. **Retrieval** — BM25+vector+RRF есть, но нет reranking по salience /
   story-relevance / relationship-relevance / involvement.

Итоговая цель — модель:

```text
WORLD STATE → WORLD EVENTS → PERCEPTION → ATTENTION → BELIEFS/KNOWLEDGE →
EMOTION → RELATIONSHIPS → STORY STATE → CHARACTER INTENT → CONTEXT BUILDER
→ LLM → ACTION/RESPONSE → WORLD EVENT → WORLD STATE UPDATE

(фоновый аналитический слой: SENSORS MODEL → предложения → Game Engine Rules)
```

Движок хранит структурированное состояние; LLM решает, как персонаж
воспринимает, решает и выражает. Фоновые аналитические задачи (perception-
предложения, event classification, emotion/mood, memory-кандидаты,
relationship-дельты) могут выполняться отдельной лёгкой **Sensors Model**
(§5.1), которая только предлагает результаты — финальное решение и все
изменения состояния остаются за движком и его правилами. Ниже — спринтовый
план из 14 спринтов (0–13) с полным перечнем файлов, БД-миграций, тестов,
рисков и порядком реализации.

---

## 2. Current Architecture

См. `docs/architecture.md` (актуально). Кратко:

```
Браузер → FastAPI (app/routers) → chat_engine.process_user_message_streaming
    → run_round (EventBus, app/round_engine.py)
    → per-NPC: presence → ContextBuilder.build → ollama_client.generate (tools/format)
    → post-round: presence round pass → extract_scene_state → stagnation
    → фон: relationship analyzer (batch), memory post_round
База: SQLite (sync+async), app/models.py, app/crud.py, app/database.py
LLM: локальный Ollama (chat/generate/embed), bge-m3 для эмбеддингов
```

Ключевые оркестрирующие точки:

| Точка | Место |
|---|---|
| Раунд | `chat_engine.process_user_message_streaming` `app/chat_engine.py:342` |
| round_id | `f"r{chat_id}-m{user_message_id}"` `app/chat_engine.py:400` |
| Цикл NPC | `app/round_engine.py` `run_round` / `run_round_fixed`; шаг — `_round_step` `chat_engine.py:591` |
| Контекст | `app/context_builder.py` `ContextBuilder.build:91` |
| Бюджет | `app/context_budget.py` `build_budget` |
| Генерация | `app/ollama_client.py` `generate:1542`, `_build_generation_messages:945` |
| Post-round отношения | `chat_engine._analyze_and_update_relationships:1220` |
| Post-round память | `chat_engine.py:1200` → `memory_service.process_post_round:852` |
| Сцена | `ollama_client.extract_scene_state`; `crud.upsert_scene_state:2045` |

---

## 3. Existing Systems Audit

### 3.1 Memory

**Система**: пост-раундовое извлечение фактов (LLM, 0–3 факта/персонаж),
валидация (grounding ≥22%, near-dup Jaccard ≥0.75, анти-другое-сознание),
хэш-дедупликация, importance, категория, эмбеддинги (bge-m3, BLOB float32),
гибридный поиск BM25+vector+RRF, witness-фильтр (`present|told`), summaries
(инкрементальные, watermark `through_message_id`), consolidation (24h,
Jaccard-кластеризация + LLM-слияние), decay (вероятностный, 7 дней).

**Файлы/классы/функции**:
- `app/memory_service.py` — `process_post_round:852`, `_extract_and_save_memories:589`,
  `validate_extracted_facts:415`, `SimpleBM25:177`, `select_relevant_memories:222`,
  `_maybe_update_summaries:737`, `consolidate_memories_job:1056`,
  `_consolidate_character_memories:997`, `_merge_memory_cluster_llm:946`,
  `decay_memory_importance` (crud), enqueue-хелперы.
- `app/crud.py` — `get_hybrid_memories_for_characters:839`,
  `get_relevant_memories_for_characters:787`, `filter_memories_by_witness:613`,
  `_apply_witness_boost:678`, `ensure_memory_limit:586`, `create_memory:531`,
  `_touch_memory_access:733`.
- `app/embedding_service.py` — `EmbeddingService.embed_batch:43`, `pack/unpack:88-97`,
  `cosine_similarity:99`.
- `app/task_queue.py` — `MemoryJobQueue` (enqueue/run_job/rety/dead_letter),
  `memory_job_queue` синглтон.
- `app/models.py` — `Memory:315`, `CharacterSummary:346`, `MemoryJob:388`.
- `app/schemas.py` — `MemoryCategory:15`, `ExtractedFact:428`, `MemoryRead:404`.
- `app/database.py` — миграции memories (хэши, importance, embedding…).

**БД-модели**: `memories`, `character_summaries`, `memory_jobs`.

**Ограничения**:
- категория — единственная ось; нет типа памяти (semantic/episodic/social/story);
- нет эмоциональных якорей и связи с отношениями (кроме текстовой категории);
- `context_message_embedding_enabled=False` — эмбеддинги только у memories;
- векторный поиск — brute-force cosine по всем кандидатам (нет ANN-индекса);
- decay контрабандой ослабляется `_touch_memory_access` при каждой загрузке;
- consolidation обрабатывает только top-200 memories персонажа и не выходит
  за рамки таблицы memories.

**Переиспользовать**: всю инфраструктуру (extraction, retrieval, queue,
embeddings, summaries, validation). **Изменить**: добавить типы памяти,
anchors, связь с событиями; расширить retrieval reranking.

### 3.2 Relationships

**Система**: направленные рёбра `source→target`, 5 метрик (0–100), тип из
whitelist с графом переходов; batch analyzer (один LLM-вызов на все пары) +
per-pair fallback; deterministic evidence gating (`direct|observed|hearsay|none`);
open issues (whitelist, sanitize, lifecycle, salience, proactive boost);
trajectory (snapshot-based events); decay (jealousy −3, resentment −1/раунд);
epistemic mask (интерпретация без чисел, только при evidence);
hearsay (детерминированный cap через trust); triads (MVP);
memory integration (события |delta|≥10 → memory категории «отношения»).

**Файлы/классы/функции**:
- `app/relationship_service.py` — `get_or_create_relationship:97`,
  `apply_delta:307`, `update_relationship_fields:199`, `apply_decay:1104`,
  `create_issue:710`, `resolve_issue:774`, `tick_open_issues:1068`,
  `proactive_boost_from_issues:1007`, `build_epistemic_mask_block:534`,
  `build_behavior_drivers_block:492`, `build_trajectory_block:1436`,
  `prune_relationship_events`, `_maybe_create_memory_from_event:1267`.
- `app/relationship_analyzer.py` — `analyze_batch_relationships:590`,
  `_build_batch_prompt:354`, `_parse_batch_response:503`, `_build_analyzer_prompt:40`.
- `app/relationship_interpreter.py` — `interpret:123`, `format_interpretation:189`,
  `weighted_behavior_drivers:302`, `format_interpretation_from_other:238`,
  `decline_name:44`.
- `app/chat_engine.py` — `_build_pair_relationship_context:1698`,
  `_evidence_mode:1845`, `_constrain_pair_delta:1923`, `_run_per_pair_analysis:1637`,
  `_compute_epistemic_evidence:214`, `evidence_mode_from_perception:1862`.
- `app/routers/relationships.py` — graph/issues/timeline/analyze endpoints.
- `app/models.py` — `CharacterRelationship:452`, `RelationshipEvent:500`,
  `RelationshipIssue:409`.
- `app/schemas.py` — `RelationshipDelta:655`, `IssueDelta:635`, `IssueType:619`.

**БД-модели**: `character_relationships`, `relationship_events`, `relationship_issues`.

**Ограничения**:
- decay жёсткий константами, не зависит от типа метрики/характера;
- нет «интерпретации поведения → update» как отдельного звена (Reciprocity
  достигается через analyzer, но формализованного pipeline
  behavior→perception→interpretation→update нет);
- эмоциональные якоря не хранятся (только events в trajectory);
- `relationship_analyzer_prompt` в config не используется кодом (мёртвый);
- player→NPC рёбра не отслеживаются по дизайну.

**Переиспользовать**: всё (метрики, events, issues, gating, decay как базу,
epistemic mask как прообраз beliefs). **Изменить**: dynamic decay, anchors,
reciprocity pipeline, связь с beliefs.

### 3.3 Perception / Locations / World Events / Witness

**Система**: двухканальный `perceive()` (visual/audio), `PerceptionWorldState`
(adjacency permeability + thread deliveries), проницаемость рёбер, стимулы,
voice familiarity, presence-лестница `present|mentioned|audible|absent|told`,
Renderer `render_perception_line`, Recency Tail, `WorldEvent` (append-only),
threads/доставка, Event Bus, Action Resolution (move_to/send_message),
System Narrator, shadow-режим WPE.

**Файлы/классы/функции**:
- `app/perception.py` — `perceive:641`, `PerceptionWorldState:472`,
  `build_permeability_index:583`, `same_canonical_location:612`,
  `can_character_perceive_event:309` (legacy), `get_perception_level:180`.
- `app/witness_model.py` — `compute_mvp_presence:74`, `perceive_to_presence:143`,
  `voice_familiarity:167`, `render_perception_line:214`, `format_line_for_presence:322`,
  `build_character_recency_tail:266`, `filter_history_for_memory_extraction:515`.
- `app/stimuli.py`, `app/movement.py` (legacy safety-net), `app/action_resolution.py`
  (`classify_consistency:174`, `reflected_action_indices:202`),
  `app/wpe_shadow.py`, `app/round_engine.py`.
- `app/crud.py` — `apply_character_actions:1354`, `_build_perception_world_state:1078`,
  `compute_and_save_presence_for_message/_for_round`,
  `thread_delivery_ids_for_message:1648`, `backfill_character_location_ids:1786`.
- `app/models.py` — `Location:70`, `WorldEvent:103`, `Thread:145`,
  `ThreadParticipantState:174`, `MessagePresence:293`.
- `app/schemas.py` — `PerceptionResult:835`, `Action:850`, `TurnOutput:871`.

**БД-модели**: `locations`, `world_events`, `threads`, `thread_participant_states`,
`message_presence`.

**Ограничения**:
- `WorldEvent` без причинности/importance/salience/action-структуры
  (только `event_type`, `location_from/to`, `target_character_ids`);
- `PerceptionResult` без identity/semantic/intent/certainty/distance/source;
- attention отсутствует (всё воспринятое одинаково «видно»);
- все WPE-флаги по умолчанию выключены (`config.py:318-347`) — нужен канареечный
  запуск перед надстройкой поверх.

**Переиспользовать**: `perceive()`, Renderer, Event Bus, Action Resolution,
permeability. **Изменить**: расширить `PerceptionResult`, добавить attention.

### 3.4 Context Builder / Prompt Builder / Chat Engine

**Система**: токено-бюджетный сборка контекста персонажа; приоритет
reserve → state(P0) → summary(P2) → memories(P2) → retrieval(P3) → recent(P1);
frontier split по саммари; BM25-retrieval внутри билдера; Recency Tail;
prompt-блоки: character card, scene (включая статичный «Сюжет: general_prompt»),
relationships (интерпретация без чисел), drivers, open issues, epistemic mask,
memories, summary, recent dialogue, retrieval, generation cue.

**Файлы/классы/функции**:
- `app/context_builder.py` — `ContextBuilder.build:91`, `_select_retrieved:543`,
  `_assemble_recent:467`, `_trim_memories:661`.
- `app/context_budget.py` — `build_budget:15`.
- `app/context_state.py` — `ContextState` (динамический num_ctx).
- `app/prompt_builder.py` — `build_system_prompt:369`, `build_scene_block:191`,
  `build_user_context_message:314`, `build_memories_block:164`,
  `build_summary_block:135`, `build_system_intervention_block:347`,
  `build_open_issues_block:672`, `build_epistemic_mask_block:699`.
- `app/ollama_client.py` — `generate:1542`, `_generate_once:1022`,
  `_build_generation_messages:945`, `extract_scene_state:2192`,
  `_invoke_llm:926`, streaming с retry/валидацией.
- `app/chat_engine.py` — раунд, `_round_step:591`, `_effective_prior_replies:96`,
  `_detect_communication_channel` (legacy), regeneration path:2048.
- `app/role_isolation.py`, `app/repetition_detector.py`,
  `app/pending_intervention.py`, `app/generation_tracker.py`.

**Ограничения**:
- «Сюжет» = статичный текст; story state не структурирован;
- нет блоков WHAT YOU KNOW / WHAT YOU PERCEIVE / YOUR STATE / ACTIVE GOAL /
  RELEVANT MEMORY / STORY как отдельных приоритизированных сущностей;
- эмоции/стресс персонажа не попадают в контекст (только глобальный
  mood/tension из custom_state).

### 3.5 Story / Scene State

**Система**: `chat.general_prompt` (пользовательский сюжет, статичный);
`scene_states` (time_of_day, character_locations JSON, custom_state JSON:
weather/mood/tension/plot_flags/active_goal/active_events/active_goals/…);
`extract_scene_state` (LLM: только локации + time_of_day, но time_of_day
движком не пишется); смена локаций — только через Action(move_to) /
пользовательский PATCH.

**Файлы**: `app/models.py` `SceneState:372`, `app/schemas.py` `SceneCustomState:509`,
`routers/chats.py` (GET/PATCH scene), `prompt_builder.build_scene_block:191`,
`ollama_client.SCENE_STATE_JSON_SCHEMA:335`.

**Ограничения**: нет динамического сюжета; `plot_flags/active_events/active_goal`
никогда не пишутся движком; нет фаз истории; нет story history; нет
консолидации сюжета.

---

## 4. Current Problems

1. **Статичный сюжет.** `general_prompt` → `<scene>` «Сюжет: …». Никакой
   эволюции, фаз, отслеживания прогресса целей. `plot_flags/active_events`
   — «мёртвые» поля (пишет только пользователь).
2. **Нет character state.** Эмоции/стресс/состояние тела/внимание/intent
   не хранятся per-character; только глобальный mood/tension.
3. **Нет belief system.** Персонажи не имеют структурированного знания/убеждений;
   эпистемическая маска — только интерпретация чужих отношений без уверенности.
4. **Плоская память.** Нет типов (semantic/episodic/social/story), нет
   эмоциональных якорей, нет связей с отношениями/событиями как данных.
5. **Консолидация по таймеру.** 24h независимо от активности; не покрывает
   relationship/story/anchors; не различает soft/hard/critical.
6. **Нет attention.** Всё воспринятое попадает в память/реакции одинаково.
7. **Нет intent/plans у NPC.** Персонаж не имеет goal/urgency/approach до генерации.
8. **Event graph без причинности.** `world_events` — журнал, не граф.
9. **Retrieval без контекстных сигналов.** Нет salience/story/relationship-сигналов
   в ранжировании.
10. **Нет обратной связи LLM→state.** LLM-ответ не порождает структурированное
    обновление мира (только actions в `turn.actions` для move/send).

---

## 5. Target Architecture

```text
WORLD STATE
   ↓
WORLD EVENTS ────────────────► event graph (cause/consequence)
   ↓
PERCEPTION ──► PerceptionResult (visual/audio/identity/semantic/intent)
   ↓
ATTENTION ──► attended? → filters what enters consciousness
   ↓
BELIEF UPDATE ──► beliefs (subject/predicate/object/confidence/source)
   ↓
EMOTION UPDATE ──► character_state (mood/stress/physical)
   ↓
RELATIONSHIP UPDATE ──► metrics + anchors + trajectory (existing)
   ↓
STORY UPDATE ──► current_story_state / threads / phases
   ↓
CHARACTER STATE ──► unified runtime state per character
   ↓
CHARACTER INTENT ──► goal/target/urgency/approach/emotion/risk
   ↓
CONTEXT BUILDER ──► budgeted blocks (WORLD/WHAT YOU KNOW/WHAT YOU PERCEIVE/
                     YOUR STATE/RELATIONSHIP/ACTIVE GOAL/RELEVANT MEMORY/STORY)
   ↓
LLM ──► reply text + actions
   ↓
ACTION / RESPONSE
   ↓
WORLD EVENT (append)
   ↓
WORLD STATE UPDATE
```

Принципы:

- **Sources of truth разделены**: World truth (структурированное состояние
  мира/сюжета/отношений) живёт в БД; персонаж знает только то, что прошло
  через Perception→Attention→Beliefs.
- **Canonical event source**: `world_events` — единственный источник событий;
  memory/relationship/story/anchors/beliefs — проекции через `event_id` (§15.0).
- **LLM консолидирует, движок валидирует**: предложения LLM (deltas, issues,
  story updates) всегда проходят deterministic gates (как evidence gating в
  отношениях). LLM не может изменить Original Plot.
- **Каждая новая подсистема** наследует паттерн WPE: флаг → канареечный
  запуск → откат → удаление legacy после стабильности.
- **Пост-раунд — единый pipeline**: вся пост-раундовая логика идёт через
  `post_round_pipeline.py` (stages), а не инлайн в `chat_engine.py` (Sprint 1, §24).
- **Одно хранилище фактов**: memories остаются единой таблицей, типы и anchors
  добавляются колонками/смежными таблицами, а не новой параллельной памятью.
- **Sensors Model — отдельный аналитический слой**: небольшая LLM для быстрых
  фоновых аналитических задач (perception-предложения, event classification,
  emotion/mood, memory-кандидаты, relationship-дельты). Она НЕ подменяет
  основную модель генерации реплик и не является источником истины (§5.1).

---

## 5.1 Sensors Model (аналитический слой)

Отдельная **Sensors Model** — маленькая (≈9B) LLM, выполняющая быстрые фоновые
аналитические задачи. Это аналитический слой, а НЕ источник истины: она
возвращает **структурированные предложения**, а итоговое изменение состояния
всегда выполняет движок по своим правилам, лимитам, decay и нормализации.

### 5.1.1 Конфигурация

- Новая переменная окружения `SENSORS_MODEL` (например `SENSORS_MODEL=some-9b-model`).
  Название модели **не зашивается в код**.
- Читается через существующий механизм конфигурации (`app/config.py`,
  pydantic-settings, `.env`): `sensors_model: str = ""` (пусто = Sensors выключен).
- **Отдельная настройка**: не влияет на `DEFAULT_MODEL`/основную модель чата;
  генерация реплик персонажей продолжает использовать `chat.model_name`
  (основную модель). Существующая конфигурация основной модели не меняется.
- Провайдер: используется **существующий** механизм подключения к LLM
  (`app/ollama_client.py`, `_invoke_llm` / `_call_ollama_chat`), отдельный клиент
  не создаётся. Если Ollama не поддерживает модель — Sensors деградирует, а не
  падает (§5.1.8).

### 5.1.2 Сервис: `SensorsService`

Новый слой `app/sensors_service.py` (или `app/sensors/` пакет). Единый интерфейс
для всех sensor-запросов:

```text
SensorsService
├── task_name (str)                     — какая задача (perception/event/emotion/…)
├── build_prompt(task, minimal_context) — короткий специализированный prompt
├── invoke(model=sensors_model)         — вызов через существующий LLM-клиент
├── validate(result, task_schema)       — JSON-schema валидация
└── result → движку                     — структурированный результат или None
```

Каждый тип анализа имеет **собственную JSON-схему** (§5.1.6). Сервис не пишет в
БД и не меняет состояние — только возвращает валидированный результат.

### 5.1.3 Задачи (первый этап)

| Задача | Вход (минимальный контекст) | Выход (предложение) | Кто решает итог |
|---|---|---|---|
| Perception | событие + observer (локация, stimuli, address) | что персонаж *потенциально* может услышать/увидеть/заметить; обращение; информацию из соседней локации; значимость события | движок (`perceive()`, presence) — доступность информации только по правилам |
| Event classification | сырое событие/реплика | тип события, участники, источник, интенсивность, потенциальная значимость, слышимость/видимость, нужна ли дальнейшая обработка | движок (важность/салиенс, запись) |
| Emotion / Mood | событие + текущее состояние персонажа | `{emotion, intensity, mood_delta}` | движок (caps, нормировка) — Sensors не задаёт mood напрямую |
| Memory extraction | реплика/событие + контекст | кандидаты `{text, importance}` | движок (валидация, witness, лимиты, запись) |
| Relationship analysis | событие + пара (source→target) + текущие метрики | `{affection_delta, trust_delta, resentment_delta, jealousy_delta}` | движок (evidence gating, caps, decay, normalization) |

### 5.1.4 Главный принцип: Sensors ≠ источник истины

Sensors Model **не может** самостоятельно:

- изменять БД / записывать память;
- менять отношения или настроение;
- перемещать персонажей;
- определять окончательный набор доступной информации (решает `perceive()`);
- генерировать финальную реплику персонажа.

Она возвращает структурированный результат, который затем обрабатывается
игровым движком через существующие правила, лимиты и нормализацию. Диаграмма:

```text
         USER INPUT
              │
              ▼
      Sensors Model (9B) ──► предложения (perception/emotion/memory/…)
              │
      ┌───────┼───────────┐
      ▼       ▼           ▼
 Perception Emotion     Memory (кандидаты)
      │       │           │
      └───────┼───────────┘
              ▼
        Game Engine Rules ──► валидация/нормализация/запись
              │
              ▼
         Main Model (26B+) ──► реплика персонажа (без изменений)
```

### 5.1.5 Изоляция от основной генерации

- `SENSORS_MODEL` и основная модель — **две разные роли**:
  `SENSORS_MODEL → background analysis`, `MAIN_MODEL → character generation`.
- В существующем коде генерации персонажей (`chat_engine._round_step`,
  `ollama_client.generate`) Sensors Model **не используется** — там остаётся
  `chat.model_name`/основная модель. Никакой замены основной модели на Sensors.
- Sensor-вызовы выполняются в пост-раунд pipeline (фон, task queue), не блокируя
  раунд и не попадая в синхронный путь генерации.

### 5.1.6 Structured Output

- Приоритет — **структурированный JSON output** (Ollama `format` + JSON-schema,
  как в `extract_scene_state`); не полагаться на свободный текст там, где результат
  может быть структурой.
- Каждый тип анализа — собственная схема. Примеры:

```json
// event classification
{ "event_type": "speech", "source_character": "Anna",
  "targets": ["Peter"], "importance": 0.6 }
// emotion
{ "emotion": "anger", "intensity": 0.7, "confidence": 0.82 }
```

- JSON **валидируется** перед передачей движку (схемы в `app/sensors/schemas.py`).
- Некорректный JSON / отсутствие схемы → результат отбрасывается, движок
  использует детерминированный путь; основной игровой цикл не падает (§5.1.8).

### 5.1.7 Производительность и минимальный контекст

- Sensors предназначена для **снижения стоимости и времени** фоновых операций.
- Каждой sensor-задаче передаётся **только минимально необходимый контекст**:
  текущая реплика, нужные события, краткая инфа о персонажах, локация,
  параметры состояния. **Не дублировать** весь context window основной модели.
- Sensor-задачи ограничены по частоте (per-round/per-N раундов) и числу вызовов;
  учёт в §24.1 LLM Budget.

### 5.1.8 Graceful degradation

Если `SENSORS_MODEL` не задана / модель недоступна / timeout / некорректный JSON /
ошибка запроса — **основной игровой цикл не падает**:

- каждый sensor-вызов обёрнут try/except (паттерн existing: memory extraction,
  scene extraction);
- при недоступности Sensors — детерминированный fallback (существующие пути
  perception/memory/relationships/scene остаются);
- при отсутствии `SENSORS_MODEL` — sensor-слой полностью выключен, поведение
  равно текущему (legacy).

### 5.1.9 Логирование

- Логи sensor-запросов на уровне debug/info: какая задача, какая модель,
  успех/ошибка, длительность.
- **Не логировать** полные промпты/контекст на production (чрезмерный объём);
  при `debug` — краткие сниппеты.

### 5.1.10 Тестирование

Отдельный тестовый файл `tests/test_sensors.py` (и при необходимости
`test_sensors_service.py`):

- `SENSORS_MODEL` корректно читается из `.env` (пустая → Sensors выключен);
- Sensors использует именно `SENSORS_MODEL`, а не основную модель;
- основная модель продолжает использоваться для генерации персонажей;
- Sensors не подменяет генерацию персонажа;
- корректный JSON Sensors успешно обрабатывается;
- некорректный JSON не ломает игровой цикл (возврат к fallback);
- ошибка/timeout Sensors не приводит к падению основного цикла;
- Sensors не изменяет БД напрямую (только возвращает предложение);
- результат Sensors проходит через игровые правила перед изменением состояния;
- при отключённом Sensors функциональность основной генерации продолжает работать.

---

## 6. Data Model Changes

Сводка новых таблиц/колонок (детали и миграции — §E и спринты):

| Сущность | Тип | Назначение | Спринт |
|---|---|---|---|
| `story_states` | таблица | Original Plot (immutable) + Current Story State + Story Phase | 8 |
| `story_threads` | таблица | активные сюжетные линии | 10 |
| `story_events` | таблица | **проекция** `world_events` для сюжета (event_id FK; round/event/actors/location/cause/consequences/importance) | 8 |
| `character_states` | таблица | runtime-состояние персонажа (emotion/mood/stress/physical/attention/focus/goals) | 3 |
| `beliefs` | таблица | знания/убеждения персонажа (subject/predicate/object/source/confidence/type) | 5 |
| `memory_anchors` | таблица | эмоциональные якоря отношения (event_id/emotion/valence/intensity/importance/ts) | 2/7 |
| `event_links` | таблица | причинно-следственные рёбра событий (event_id→caused_by) | 1 |
| `intents` | таблица | intent NPC на ход (goal/target/urgency/approach/emotion/risk) | 10 |
| `npc_plans` | таблица | долгоживущие маленькие планы NPC | 10 |
| `memories.type` | колонка | semantic/episodic/social/story | 2 |
| `memories.event_id` | колонка | FK на каноническое событие-источник | 2 |
| `memories.valence/intensity` | колонки | эмоциональная окраска | 2 |
| `relationship_events.event_id` | колонка | FK на каноническое событие (проекция отношения) | 2 |
| `memory_anchors.event_id` | колонка | FK на каноническое событие (уже есть) | 2/7 |
| `world_events.location_id` | колонка | каноническая локация события | 1 |
| `world_events.importance/story_salience/emotional_salience` | колонки | значимость | 1 |
| `world_events.action` | колонка | структурированное действие (actor/action/target/object) | 1 |
| `chats.original_plot` | колонка | выделенный неизменяемый замысел (из general_prompt, миграция) | 8 |
| `chats.story_prompt` | колонка | текущее story prompt (эволюционирующее) | 8 |
| `chats.story_enabled` | колонка/флаг | включение динамического сюжета | 8 |
| `relationship_decay_profile` | колонка/конфиг | динамический decay (тип метрики × характер) | 7 |
| `beliefs` источник → epistemic mask | замена | beliefs заменяют MVP-маску постепенно | 5 |

Принцип: **не создавать огромный `WorldState`-объект в памяти**. Доступ к
состоянию мира остаётся через существующие таблицы + агрегирующий сервис
(`world_state.py`), который по round_id собирает нужный срез для
perception/context/intent.

---

## 7. Memory Architecture

### Текущее vs целевое

Сейчас: `memories(content, importance, category, embedding, source_message_ids)`.

Целевое — **единая таблица `memories` + колонки типа**, без новой параллельной
системы:

```text
memory_type: semantic | episodic | social | story   (новая колонка)
event_id:    FK → world_events.id (nullable)         (новая колонка)
valence:     [-1..1]                                  (новая колонка)
intensity:   [0..1]                                   (новая колонка)
```

- **Semantic** — факты о мире: «Борис боится собак» (источник — direct
  observation/введено игроком; категория «другое/локация/предмет»).
- **Episodic** — «Анна спасла Бориса» (событие, привязка к `world_events.id`).
- **Social** — «Борис унизил Анну» / «Анна простила Бориса» (события между
  персонажами; категория «отношения», связь с `relationship_events`).
- **Story** — сюжетная информация «Мы ищем Николая» (привязка к `event_id`
  канонического `world_events`; проекция story не дублируется в памяти).

Классификация типа — расширение существующего `extract_memories_for_character`
(LLM уже возвращает `category`): добавить `memory_type` в `ExtractedFact` и
валидацию в `validate_extracted_facts`. Детерминированный fallback:
`category=="отношения" → social`, событийный текст → episodic, упоминание
локации/объекта → semantic, привязка к story → story.

### Эмоциональные якоря (отдельная таблица `memory_anchors`)

Направленное отношение `source→target` получает anchors:

```text
id, relationship_id (FK), event_id (FK world_events),
emotion (str), valence ([-1,1]), intensity ([0,1]), importance,
timestamp
```

Якоря пишутся из: (а) значимых `RelationshipEvent` (|delta|≥10, type change —
уже есть `_maybe_create_memory_from_event` — расширить до записи anchor);
(б) memory категории «отношения» с высокой valence. Активация якорей — в
`context_builder`: при наличии отношения source→target и текущем событии с
этим target — поднимать top-K якорей по (importance × recency) в блок
`RELATIONSHIP MEMORY`, не больше `relationship_anchor_max` (≈3). Якоря НЕ
дублируются в общем списке memories.

---

## 8. Character State

Новая таблица `character_states` (одна строка на персонажа в чате):

```text
character_id (FK, unique)
emotional_state  (JSON: map emotion→intensity, e.g. {"suspicion":0.7,"relief":0.2})
mood             (str, из interpreter: neutral/tense/hopeful/…)
stress           (float 0..1)
physical_state   (JSON: energy, wounds, conditions — free-form, пишется LLM)
attention        (str current_focus: «следит за Борисом», NULL)
current_focus_id (FK character_id nullable — на кого смотрит)
active_goal      (str, из intent/сцены)
personal_goals   (JSON list)
updated_round_id (str)
```

**Важно**: НЕ дублировать существующие данные:
- локация — берётся из `characters.location/location_id` (не хранить в state);
- отношения — из `character_relationships` (не хранить);
- окружение — из `scene_states` (не хранить).

`character_states` хранит ТОЛЬКО то, чего нет в других таблицах: эмоции,
стресс, физическое состояние, внимание, цели. Обновление — пост-раунд
детерминированно (эмоция из relationship deltas + события текущего раунда
через deterministic `emotion_engine`) + опциональная LLM-нормализация
(структурированный JSON, как scene extraction). Потребитель — `context_builder`
(блок `YOUR STATE`) и `intent`-слой.

Эмоциональное обновление — **детерминированное правило**, не LLM-фантазия:
например `trust↓ + resentment↑ → suspicion`, `jealousy↑ → tension`,
`affection↑ + proximity → warmth`. LLM может только «настроить» интенсивность
в рамках caps.

---

## 9. Belief System

Новая таблица `beliefs`:

```text
id, character_id, chat_id
subject (str), predicate (str), object (str)   — триплет «Борис предал Анну»
source (enum: direct_observation | heard | told_by | inference | rumor | memory)
confidence (float 0..1)
type (enum: fact | belief | suspicion)         — различие «знает» vs «полагает»
created_at, updated_at
world_truth_ref (FK world_events.id nullable)  — если подтверждено миром
```

**Ключевая концепция**: персонаж НЕ автоматически знает World Truth.
`world_truth` — отдельная концепция (в `world_events`/`story_events`/данных
игрока); `belief` — субъективное знание персонажа. Персонажу в контекст
попадают ТОЛЬКО его `beliefs`, никогда World Truth, кроме как через
perception→belief pipeline.

**Pipeline**: `world_event → perceive → attention → belief update`:
- direct observation (visual=full, присутствие) → `direct_observation`, confidence ↑;
- услышал (audio=full, знакомый голос) → `heard`;
- told_by (сообщил другой персонаж) → `told_by`, confidence зависит от
  `trust(believer→teller)` (аналог hearsay в отношениях);
- inference (детерминированная логика по событиям) → `inference`;
- rumor (muffled/анонимный слух) → `rumor`, низкая confidence;
- memory (восстановлено из memories) → `memory`.

**Связь с epistemic mask**: MVP-маска (`relationship_interpreter.format_interpretation_from_other`)
расширяется: вместо «неизвестно» — использовать beliefs персонажа. Постепенно
beliefs заменяют mask как источник, mask остаётся fallback при отключённом
флаге `beliefs_enabled`.

**Защита от hallucination**: belief update для фактов — только из событий,
которые персонаж реально воспринял (perception+attention). LLM может
предложить belief (например «Борис солгал») только как `suspicion` с
`confidence≤0.5` без прямого наблюдения; подтверждение/опровержение —
через последующие события и `world_truth_ref`.

---

## 10. Perception 2.0

Текущий `PerceptionResult` (`schemas.py:835`) имеет `visual_level/audio_level/
addressed/remote_status`. Существующая изоляция локаций и двухканальность
**сохраняются**. Целевое расширение:

```text
visual_level, audio_level            (есть)
identity: known | unknown            (детерминированно: voice familiarity / co-presence)
semantic: none | partial | full      (muffled→partial, full audio→full, none)
intent:   unknown | inferred         (из addressed/стимулов: крик по имени → inferred)
certainty: 0..1                      (детерминированно из каналов)
distance:  same | adjacent | remote  (из графа локаций)
source:   author_id | None           (только при identity=known)
```

**Без утечки изоляции**: никакая новая деталь не может быть «додумана».
`semantic=partial` рендерится как фрагмент («голоса, обрывки слов») без
содержания; `identity=unknown` → Renderer пишет «чей-то голос» (уже есть в
`render_perception_line`). Ключевое требование (Golden-сценарии Phase 6–7)
не регрессировать: персонаж за стеной не получает визуальное, в другой
локации — ничего.

Примеры целевых уровней (детерминированные таблицы в `perception.py`):

| Ситуация | visual | audio | identity | semantic | intent | distance |
|---|---|---|---|---|---|---|
| тот же `location_id` | full | full | known | full | inferred | same |
| сосед через стену, обычный разговор | none | muffled | unknown | partial | unknown | adjacent |
| крик/обращение по имени через стену | none | full | known (голос/имя) | full | inferred | adjacent |
| стекло (visual=full,audio=none) | full | none | known | none (визуал) | inferred | adjacent |
| далёкая локация без ребра | none | none | unknown | none | unknown | remote |

**Способ**: расширяем `perceive()` (она уже принимает event/observer/world_state)
детерминированными вычислениями; Renderer расширяется в `witness_model`.
Независимый флаг `PERCEPTION_V2_ENABLED`; откат — старый 4-польный результат.

---

## 11. Attention

Новый слой между Perception и Interpretation/Memory. Не каждое воспринятое
событие входит в сознание персонажа.

```
Perception → Attention → Interpretation → Memory / Reaction
```

Детерминированная оценка внимания для пары (персонаж, событие):

```text
attention_score = w_volume × (громкость/стимулы)
                + w_distance × (same > adjacent > remote)
                + w_relevance × (важность события для персонажа)
                + w_personal_salience × (упоминание имени/интереса)
                + w_emotional_salience × (эмоциональный якорь активен)
                + w_novelty × (новое vs повтор)
                + w_relationship_relevance × (в событии участвует target отношения)
                + w_direct_address × (addressed=true)
```

Пороги:
- `attention < LOW` → событие «слышал фоном»: в память НЕ идёт, в реакцию НЕ идёт,
  рендерится как атмосфера (если вообще).
- `LOW ≤ attention < HIGH` → «заметил»: в память (с пониженной важностью), в
  reaction — опционально.
- `attention ≥ HIGH` → «в центре внимания»: в память, в belief/emotion update,
  в recency tail.

**Примеры из постановки**: падение стакана в соседней комнате —
perception=yes, attention=low; крик персонажа по имени — perception=yes,
attention=very high. Реализация: `app/attention.py` (чистая функция) +
`attention`-флаг; хук в `_round_step` (перед `memory_service`) и в
`context_builder` (для фильтрации recency tail). Сохранять attention score
в `message_presence` (новая колонка `attention REAL NULL`) — детерминированно,
для observability и для memory_service.

---

## 12. Relationship Evolution

Существующая система не заменяется. Расширения:

### 12.1 Dynamic Decay

Сейчас: `apply_decay` (`relationship_service.py:1104`) — фиксированные
`jealousy −3`, `resentment −1` за раунд. Целевое:

```text
decay_rate(metric, character) = base_rate[metric] × character_factor[metric]
```

- `affection` — медленный (или 0, как сейчас);
- `trust` — почти отсутствует (не «не общались → доверие исчезло»);
- `resentment` — очень медленный;
- `jealousy` — быстрый;
- `attraction` — медленный.

Фактор характера — из `character_states.personal_goals`/темперамента (конфиг
карта «персонаж → множитель», по умолчанию 1.0). Правило: **отсутствие
взаимодействия НЕ обнуляет метрики**; decay действует только как затухание
эмоциональной интенсивности, память о событии (trajectory/anchor) остаётся.

### 12.2 Reciprocity pipeline (behavior → perception → interpretation → update)

Формализовать звено: поведение A (событие раунда) → как B воспринял
(perception+attention) → интерпретация B (детерминированная: `format_interpretation_from_other`
уже есть) → update B→A. Ключевое: **изменение B→A зависит от beliefs B**
(п.9). Пример постановки: A проявляет симпатию, B игнорирует → если A верит,
что B занят — дельта слабая; если A считает, что B пренебрегает — affection↓,
resentment↑, insecurity↑. Уверенность beliefs → множитель капа (аналог hearsay).

### 12.3 Relationship anchors activation

Якоря из `memory_anchors` активируются текущим контекстом: событие с target
«Борис оставляет Анну» активирует якорь «Борис уже бросал Анну» → повышается
вероятность соответствующей реакции (через block в context + повышение
attention). НЕ превращать в огромный список: в промпт — только top-K
(`relationship_anchor_max=3`).

---

## 13. Emotional Memory

= `memory_anchors` (п.7) + связь с `relationship_events` + активация в
`context_builder`. Каждый anchor имеет `event_id, emotion, valence, intensity,
importance, timestamp`. Источники: `_maybe_create_memory_from_event`
(расширить: помимо memory писать anchor), `resolve_issue` (примирение →
позитивный anchor). Активация — по top-(importance×recency) при отношении
source→target и текущем событии с участием target. Флаг `anchors_enabled`.

---

## 14. Hybrid Retrieval

BM25 не заменяется. Целевой pipeline:

```text
BM25 + Embedding similarity (RRF — есть) + Salience + Recency + Story relevance
    + Relationship relevance + Personal involvement
        → Reranking → Final memories
```

Разделить оси оценки:

```text
lexical relevance      — BM25 (есть)
semantic relevance     — cosine (есть)
emotional relevance    — по valence/intensity anchors активация
story relevance        — по memory_type=story + совпадение активных story_threads
relationship relevance — по участию персонажей текущего контекста в memories социального типа
```

**Reranking** — новая функция `rerank_memories(candidates, context)` в
`memory_service.py` (после существующего RRF, до witness-boost):
`score_final = w_lex×lex + w_sem×sem + w_emotion×emotion + w_story×story + w_rel×rel + w_recency×recency + w_salience×salience`.

- **Индексация**: existing BLOB embeddings + brute-force cosine — остаётся для
  MVP; отдельный ANN-индекс (sqlite-vec/FAISS) — опциональный P3, НЕ блокер.
- **Обновление**: при консолидации/merge — пересчёт embedding объединённой
  memory (уже есть enqueue embed_memory).
- **Удаление/архивация**: memory удаляется (prune) — anchors и links
  чистятся каскадом.
- **Fallback**: при отсутствии embeddings — чисто BM25 (есть), reranking
  без semantic-слагаемого (веса нормируются).
- **Стоимость**: reranking — детерминированный, без LLM; embedding-вызовы —
  уже в очереди; новые LLM-вызовы не вводятся.

---

## 15. Event Architecture

### 15.0 Canonical Event Source (source of truth)

`world_events` — **единственный канонический источник истины о событиях**. Никакая
другая система не пишет «свой» параллельный журнал событий. Все потребители —
**проекции** канонической строки, связанные через `event_id` (FK → `world_events.id`):

| Проекция | Таблица | Ключ связи |
|---|---|---|
| Story-события | `story_events` | `event_id` FK → `world_events.id` |
| Память | `memories` | `event_id` FK → `world_events.id` |
| Отношения | `relationship_events` | `event_id` FK → `world_events.id` |
| Эмоц. якоря | `memory_anchors` | `event_id` FK → `world_events.id` |
| Убеждения | `beliefs.world_truth_ref` | FK → `world_events.id` |

Правила:

1. **Одна строка на событие.** Event extraction (Sprint 1) пишет ОДИН
   `world_events`; все остальные системы читают его и порождают свои строки
   (memory/relationship/story/anchor/belief) с тем же `event_id`. Дублирование
   «события» в нескольких таблицах не происходит: только проекции.
2. **Проекции дедуплицируются по `event_id`**: если `event_id` уже обработан
   системой — повторная обработка пропускается (идемпотентность, аналог
   watermark `through_message_id` в memory). Уникальный индекс
   `(table, event_id)` где применимо.
3. **Запись проекций — в том же пост-раунд pipeline**, в фиксированном порядке:
   world_events → memories/anchors → relationship_events → story_events → beliefs.
4. **Удаление/коррекция** канонического события каскадом чистит проекции
   (по `event_id`), а не оставляет рассинхронизированные копии.
5. **`story_events` НЕ дублирует поля `world_events`**, а ссылается на него;
   поля-надстройки (story_thread_id, importance для сюжета) допустимы как
   проекционная разметка.

### 15.1 Event / Causal Graph

`world_events` расширяется колонками:

```text
id, chat_id, character_id, message_id, event_type (speech|move|action|system|narrator|belief_confirm)
round_id, target_character_ids, created_at
+ location_id (FK locations, nullable)
+ action (JSON: {type, actor, action_verb, target, object})
+ importance (int 0..10)
+ story_salience (float 0..1)
+ emotional_salience (float 0..1)
+ visibility (string — legacy-bridge уже есть у messages)
```

Новая таблица `event_links` (причинность):

```text
id, chat_id, event_id (FK world_events), caused_by_event_id (FK world_events),
kind (causes|consequence|goal_step|resolution)
```

**Что уже есть**: `world_events` append-only, `message_id`/`round_id` связь,
`location_from/to` для move, `target_character_ids`. **Что добавляется**:
action-структура, importance/salience, causal links.

### 15.2 Event Extraction

Структурированное извлечение «что произошло в раунде» из раундных сообщений
(per-раунд, LLM с JSON-schema, как `extract_scene_state`): actor/action/target/
object/location/cause/consequence/importance/emotional_salience/story_salience.
Результат пишется в `world_events` (новые `action`-строки) и `event_links`.
Это фундамент для memory (event_id), beliefs (источники), story (story_events),
crisis (важные события). Фильтр восприятия не применим на уровне extraction —
extraction фиксирует World Truth; персонажи получают только то, что прошло
perception.

### 15.3 Raw → Event → Memory → Summary

```text
RAW MESSAGES → EVENT EXTRACTION → STRUCTURED EVENTS → MEMORY → SUMMARY
```

Не удалять raw messages. Стратегия хранения:
- raw history — `messages` (существует, не трогаем);
- recent history — окно `context_history_load_cap` (существует);
- event history — `world_events` (расширенная);
- summary — `character_summaries` (существует);
- long-term memory — `memories` (существует, + type/anchor).

---

## 16. Dynamic Story State

### 16.1 Разделение

Новые поля чата:

- `chats.original_plot` — неизменяемый пользовательский замысел. Начальное
  значение — миграция из `general_prompt`. **LLM не может его менять**
  (на уровне service — намеренно нет write-path; endpoint доступен только
  игроку/пользователю).
- `chats.story_prompt` — текущее story prompt (эволюционирует через
  consolidation). Начальное значение = general_prompt.
- `chats.general_prompt` — остаётся как есть (legacy), но ContextBuilder
  начинает использовать `story_prompt` при `story_enabled`.

### 16.2 Current Story State

`story_states`:

```text
id, chat_id,
original_plot (Text, immutable),
current_story (Text JSON или структурированный JSON:
   {summary, active_threads, completed_goals, progress, phase,
    characters: {name: {relationships_override, notes}}}),
story_phase (str: "формирование группы" | "охота на апостолов" | ...),
updated_round_id,
updated_at
```

### 16.3 Story History

`story_events` (проекция канонических `world_events`, см. §15.0):

```text
id, event_id (FK world_events), chat_id, round_id,
event (str), actors (JSON), location,
cause, consequences,
importance (int),
story_thread_id (FK nullable),
created_at
```

### 16.4 Story Phase

Фазы не жёстко заданы заранее: движок хранит `story_phase` как строку;
консолидация может предложить смену фазы (валидируется: только среди
зарегистрированных фаз в `original_plot`/допустимых движком, или помечается
как «новая фаза» на усмотрение пользователя). Пользователь может задать фазы
явно (PATCH scene), движок может предложить.

---

## 17. Story Consolidation

### 17.1 Trigger

- по флагу `story_consolidation_enabled`;
- пост-раунд, если с последней консолидации ≥ `story_consolidation_interval_rounds`
  (по умолчанию 15) ИЛИ критическое событие (смерть, предательство, свадьба,
  milestone — см. п.20 critical events) затронуло story.

### 17.2 Входы → LLM → выход

```text
Original Plot + Current Story State + Recent Story Events + Relevant Story Memories
        ↓ (LLM, JSON-schema, T низкая)
Updated Current Story State
```

Выход — структурированный JSON:

```text
{ "completed_goals": [...],
  "progress": {...},
  "new_threads": [{name, actors, importance}],
  "updated_threads": [...],
  "archived_threads": [...],
  "character_state_changes": [...],   // отношения/роли внутри сюжета
  "phase_change": str|null,
  "summary": "..." }
```

### 17.3 Валидация и защита

- **Защита Original Plot**: diff-проверка — LLM не может менять
  зарегистрированные факты original_plot (персонажи, цели, ключевые события).
  Если предложение противоречит original_plot → rejected + фидбек.
- **Hallucination guard**: новые threads/цели только при наличии подтверждающих
  story_events текущего окна (grounding, аналог memory fact validation);
  прогресс целей только на основе событий (progress ≤ факт. событий).
- **Rollback**: результат пишется в новую строку `story_states` (versioned) или
  во временную строку; при невалидном JSON/нарушении правил — предыдущая
  версия остаётся, ошибка логируется, раунд не ломается.
- **Числа-уверенности**: confidence полей в JSON; ниже порога — не применяется.

---

## 18. Plot Engine

Модуль `app/plot/` создаётся ТОЛЬКО если он реально инкапсулирует новую
логику (story state, threads, crisis, consolidation). Проверка текущей
структуры (`app/`) показывает: story-логики в отдельных файлах нет —
`plot/` оправдан:

```text
app/plot/
    __init__.py
    story_state.py        — чтение/запись story_states, phase
    story_threads.py      — создание/архивация потоков
    story_events.py       — запись story_events из раунда
    story_consolidation.py— LLM-консолидация (п.17)
    crisis_engine.py      — п.19
    plot_pressure.py      — расчёт story pressure (п.19)
    intent.py             — п.21
```

Не плодить: `plot/` не дублирует relationship-логику; story использует
relationship-данные через чтение (без копий).

---

## 19. Crisis Engine

Цель: не заставлять сюжет развиваться искусственно, а обнаруживать
естественные критические точки. Запрещён паттерн `if trust<30: force_argument`.

```text
STORY PRESSURE (детерминированная, по шкале 0..1):
  = w_issues × unresolved_issues_score
  + w_trajectory × (отрицательная траектория: resentment/jealousy рост, trust/affection падение)
  + w_goals × (блокировка личной цели персонажа)
  + w_stagnation × (стагнация сюжета, rounds без важных событий)
  + w_beliefs × (конфликт убеждений)
  + w_recent × (интенсивность недавних событий)

CRISIS CANDIDATE (правила, детерминированные):
  высокая story pressure + (продолжительное взаимодействие пары) + (проблема долго
  не разрешена) + (противоположные интенты) → кандидат

CRISIS EVALUATION (LLM, JSON-schema, мягко):
  {candidate: bool, type: direct_conflict|admission|question|discovery|third_party|
                   world_event|secret_hiding|departure|goal_change, confidence}

CRISIS RESOLUTION (детерминированная):
  - кандидат не применяется напрямую; он лишь повышает attention/pressure
    и шанс proactive-action (boost) у вовлечённых персонажей;
  - результат в `story_events` + `story_threads` (новый поток «кризис»);
  - никаких форсированных аргументов.
```

Флаг `crisis_engine_enabled`; кандидаты логируются, применяются мягко.

---

## 20. Adaptive Consolidation

Заменить фиксированный 24h-таймер. Состояние отслеживается с последней
консолидации (новые counters на чат — таблица `consolidation_state` или
вычисление по `messages`/`world_events`/`relationship_events`):

```text
consolidation_score =
    new_messages × 1
  + new_events × 2
  + new_facts × 3
  + relationship_events × 4
  + story_events × 5
  + emotional_anchors × 7
```

(веса — конфиг, по умолчанию из постановки, с учётом проекта: события/факты
уже дороже сообщений — предлагаю `messages×1, events×2, facts×3, rel_events×4,
story_events×5, anchors×7`.)

Пороги:
- **soft** — консолидация только memories (кластеризация + merge) и summary;
- **hard** — полная консолидация: memories + summary + relationship evidence +
  anchors + story update + embedding/index refresh;
- **critical event** — немедленная hard-консолидация независимо от score.

Критичные события: смерть, предательство, признание, свадьба, начало/конец
отношений, важное раскрытие, сюжетный milestone, смерть важного NPC,
завершение главной цели. Детекция critical — детерминированная
(`is_critical_event(event)` по action/importance + whitelist), и
LLM-предложение помечается как `suspicion` до подтверждения.

Простаивающий чат НЕ консолидируется по таймеру (score≈0 → skip).

**Consolidation ≠ Summary**: consolidation = event extraction + fact extraction
+ memory merge + relationship evidence + emotional anchors + story update +
summary update + embedding/index update. Summary — только один из результатов.
Хук: `memory_service.consolidate_memories_job` расширяется на весь набор,
вызывается scheduler по score (замена `_consolidation_scheduler` в `main.py:62`).

---

## 21. NPC Intent

Новый слой перед генерацией — `app/plot/intent.py`:

```text
Intent:
  goal          (str)      — «узнать, где Николай»
  target        (character_id|None)
  approach      (direct|indirect|avoid|delay)
  urgency       (0..1)
  emotion       (str)
  risk          (0..1)
```

- Источник: `character_states.active_goal` + текущий контекст + story threads
  + открытые issues + beliefs (если «подозревает», approach=indirect).
- Формируется **детерминированно** (правила) до генерации; LLM реализует intent
  естественным языком и НЕ изобретает состояние мира.
- Блок `ACTIVE GOAL` в контексте.
- Не каждый ход имеет intent: если у персонажа нет цели — блок отсутствует.

---

## 22. NPC Plans

Долгоживущие маленькие планы (`npc_plans`):

```text
id, character_id, goal, next_step, blocked_by, priority, status (active|blocked|done|abandoned),
created_round_id, updated_at
```

НЕ GOAP/planner. Предназначение: «я хочу сделать X, но сейчас мне мешает Y».
Один активный план на персонажа (обычно). Создание — детерминированное или
через consolidation; обновление `next_step/blocked_by` — пост-раунд по
событиям. В context — компактная строка `ACTIVE PLAN`. Флаг `npc_plans_enabled`.

---

## 23. Context Builder Evolution

Новая приоритизированная сборка. Целевой блок персонажа:

```text
WORLD            — сцена: время, погода, локация, co-present (из scene_states) [P0]
WHAT YOU KNOW    — beliefs (только свои, confidence>порог) [P2, retrieval-based]
WHAT YOU PERCEIVE— perception-строки текущего раунда (present/audible/…) [P0]
YOUR STATE       — эмоции/стресс/физическое состояние из character_states [P1]
RELATIONSHIP     — интерпретация + drivers + anchors активация [P1]
ACTIVE GOAL      — intent [P1]
RELEVANT MEMORY  — reranked memories (п.14) [P2]
STORY            — current story state: активные threads + прогресс [P1/P2]
```

Правила бюджета (в `context_budget.build_budget`):
- **обязательное (не усекать)**: P0 — perceive/recent-tail/instructions;
- **retrieval-based**: WHAT YOU KNOW (beliefs top-K), RELEVANT MEMORY;
- **сжимаемое**: STORY (только активные потоки, top-K), RELATIONSHIP (top-K
  рёбер, уже есть cap `relationship_drivers_max`);
- **не сокращать**: инструкции, generation cue, recency tail.

Порядок приоритета (обновлённый): reserve → state(P0) → perception/recent(P0)
→ intent/goal(P1) → relationship(P1) → story(P1) → summary(P2) → memories(P2)
→ beliefs(P2) → retrieved history(P3). Токен-бюджет — тот же механизм
`context_budget`; новые блоки получают отдельные подбюджеты из `context_reserve`
и `MAX_CONTEXT_TOKENS`.

---

## 24. Sprint Plan

> 14 спринтов: Sprint 0 (подготовка) + Sprint 1–13. Каждый спринт: цель,
> почему сейчас, изменения, файлы, БД, тесты, риски, критерии готовности,
> что НЕ делать. Флаги-канарейки по образцу WPE.

### Sprint 0 — Подготовка: вынести story из general_prompt, закрепить фундамент

- **Цель**: подготовить данные и структуру без изменения поведения:
  1. миграция `chats.original_plot` / `chats.story_prompt` (начало = general_prompt),
     `story_enabled=false`;
  2. миграция `world_events.location_id` (nullable FK), backfill из строковой `location`
     (аналог `backfill_character_location_ids`), идемпотентно;
  3. завести пустые таблицы `character_states`, `beliefs`, `story_states`,
     `story_threads`, `story_events`, `event_links`, `memory_anchors`,
     `intents`, `npc_plans` (CREATE IF NOT EXISTS, как WPE Phase 0) — read-path не читает;
  4. аудит-документ: пометить legacy-поля (mood/tension в custom_state, plot_flags,
     active_events, active_goal);
  5. **Sensors Model инфраструктура** (§5.1): `SENSORS_MODEL` в `app/config.py`
     (pydantic-settings, `.env`); каркас `app/sensors_service.py` (интерфейс
     task→prompt→invoke→validate→return) + JSON-схемы задач в
     `app/sensors/schemas.py`; флаги `sensors_enabled` (default false),
     `sensors_<task>_enabled`; НИКАКОГО подключения к процессам в Sprint 0 —
     только инфраструктура и тесты, поведение не меняется.
- **Почему сейчас**: всё новое строится на event-графе и story-разделении;
  данные должны существовать до кода; Sensors-слой подключается к существующим
  фоновым процессам постепенно (§5.1.3), поэтому его каркас заводится как
  фундамент вместе со schema.
- **Архитектура**: нет поведенческих изменений; только schema + backfill +
  Sensors-каркас под флагом off.
- **Изменяемые файлы**: `app/models.py`, `app/database.py`, `app/crud.py`
  (backfill), `app/config.py` (флаги + `SENSORS_MODEL`), `scripts/backfill_plot_fields.py`,
  `scripts/backfill_event_location_ids.py`; новые `app/sensors_service.py`,
  `app/sensors/schemas.py`, `tests/test_sensors.py`.
- **БД**: `ALTER TABLE chats ADD COLUMN original_plot/story_prompt/story_enabled`;
  `ALTER TABLE world_events ADD COLUMN location_id`; 10 новых таблиц.
- **Тесты**: идемпотентность миграций на копии прод-БД; backfill с отчётом;
  `test_sensors.py` (§5.1.10): чтение `SENSORS_MODEL` из `.env`, изоляция от
  основной модели, graceful degradation (некорректный JSON/timeout не роняют цикл),
  Sensors не пишет в БД, не подменяет генерацию, при off — legacy-поведение.
- **Риски**: миграция на больших БД — чтение по одному чату, batch; Sensors
  недоступность не должна задеть раунд — только инфраструктура без подключения.
- **Критерий**: 771+ passed, 28 pre-existing, 0 новых; откат — снятие колонок
  (или игнорирование read-path); Sensors-каркас собран, но выключен.
- **НЕ делать**: не подключать новые таблицы к read-path; не подключать Sensors
  к процессам в этом спринте; не трогать генерацию персонажей.

#### Sprint 0 — Статус (2026-08-04): ✅ ВЫПОЛНЕН

- **Сделано** (без изменения поведения):
  1. `chats.original_plot/story_prompt/story_enabled` — колонки + миграция;
     `backfill_plot_fields()` (копирует `general_prompt`, идемпотентно, `story_enabled=false`),
     скрипт `scripts/backfill_plot_fields.py`, отчёт `PlotBackfillReport`;
  2. `world_events.location_id` (nullable FK → locations.id, ON DELETE SET NULL) + миграция;
     `backfill_event_location_ids()` (через `resolve_location_name` + shared-scene правило),
     скрипт `scripts/backfill_event_location_ids.py`, отчёт `EventLocationBackfillReport`;
  3. 10 пустых таблиц + `consolidation_state` (по §20/E): `character_states`, `beliefs`,
     `story_states`, `story_threads`, `story_events`, `event_links`, `memory_anchors`,
     `intents`, `npc_plans`, `consolidation_state` — `CREATE TABLE IF NOT EXISTS` + индексы;
     read-path не читает;
  4. аудит legacy-полей — см. `docs/legacy.md` (mood/tension в custom_state, plot_flags,
     active_events, active_goal);
  5. Sensors Model инфраструктура (§5.1): `SENSORS_MODEL`/`sensors_enabled`/
     `sensors_<task>_enabled`/`sensors_timeout` в `app/config.py`; каркас
     `app/sensors_service.py` (`SensorsService`: task→build_prompt→invoke→validate→run,
     `SENSOR_SYSTEM_PROMPT`, `_parse_sensor_json`, graceful degradation — None на
     timeout/ошибке/некорректном JSON); JSON-схемы 5 задач в `app/sensors/schemas.py`
     (`SENSOR_SCHEMAS`, `get_schema`, `validate_sensor_result`); к процессам НЕ подключено.
- **Изменённые файлы**: `app/models.py`, `app/database.py`, `app/crud.py`, `app/config.py`,
  `.env.example` (+ `.env`), `app/sensors_service.py`, `app/sensors/schemas.py`,
  `app/sensors/__init__.py`, `scripts/backfill_plot_fields.py`,
  `scripts/backfill_event_location_ids.py`, `tests/test_sensors.py`,
  `tests/test_sprint0_schema.py`, `docs/database.md`, `docs/sensors.md`, `docs/legacy.md`.
- **БД**: итог — 25 таблиц (`Base.metadata.create_all`); `ensure_schema` идемпотентна
  на legacy-схеме (проверено на копии: колонки chats добавлены, `location_id` добавлен,
  новые таблицы созданы, повторный запуск не падает).
- **Тесты**: `tests/test_sprint0_schema.py` (идемпотентность миграций, backfill с отчётом),
  `tests/test_sensors.py` (§5.1.10). Полный прогон: **818 passed, 28 failed** — все 28
  падений pre-existing (подтверждено сравнением с `214b57e` на копии базы: те же
  модули/тесты + 3 флаки-улучшения: `memory_attribution_speaker_preserved_same_room`,
  `job_enqueue_creates_record` стали проходить; 3 падения `test_context_state` —
  следствие локального `.env` с `MIN_CTX=32778` и воспроизводятся на baseline).
  Критерий «771+ passed, 28 pre-existing, 0 новых» соблюдён.
- **Откат**: удаление новых колонок/таблиц (миграции идемпотентны); Sensors-каркас
  выключен (`SENSORS_MODEL=""`, `sensors_enabled=false`), поведение равно legacy.

### Sprint 1 — Structured World Events + Post-Round Pipeline (P0)

- **Цель**: event-граф как источник истины «что произошло»; вынести пост-раунд
  логику из `chat_engine.py` в отдельный оркестратор.
- **Почему**: все нижестоящие системы (memory, beliefs, story, crisis) читают события;
  `chat_engine.py` (~2500 строк) не должен расти — пост-раунд конвейер выносится.
- **Изменения**: колонки `world_events` (action JSON, importance, story_salience,
  emotional_salience), таблица `event_links`; раундная **event extraction**
  (LLM JSON-schema → new world_events rows + links) в пост-раунд
  (`chat_engine` post-round → `event_service.extract_round_events`); флаг
  `event_extraction_enabled` (default false). Новый `app/post_round_pipeline.py`:
  оркестратор пост-раундовых шагов (presence round pass → event extraction →
  memory extraction → relationships → story), вызывается из `chat_engine`
  вместо инлайн-кода; каждый шаг — отдельная вызываемая функция (stages),
  изолированная try/except, чтобы один упавший шаг не ломал раунд.
  **Sensors hook (event classification, §5.1.3)**: при `sensors_event_enabled`
  и `SENSORS_MODEL` — SensorsService предлагает `{event_type, source_character,
  targets, importance, audibility/visibility}`, движок сохраняет правила
  extraction/importance; Sensors — только предложение, не пишет события сам.
- **Файлы**: новые `app/event_service.py`, `app/post_round_pipeline.py`; правки
  `app/chat_engine.py` (пост-раунд заменяется на вызов pipeline),
  `app/ollama_client.py` (extraction prompt), `app/crud.py`, `app/models.py`,
  `app/schemas.py`, `app/config.py`, `app/prompts/ru.json`.
- **БД**: ALTER `world_events` (+5 колонок), CREATE `event_links`,
  `relationship_events.event_id` FK (проекция), `memory_anchors.event_id` — уже FK.
- **Тесты**: `test_event_extraction.py` (extraction валидна, links корректны,
  откат по флагу, не ломает раунд); `test_post_round_pipeline.py` (порядок
  stages, изоляция ошибок, идемпотентность по event_id); golden-регрессия.
- **Риски**: cost (один LLM-вызов на раунд) — лимит на важность; недоступность
  schema — RuntimeError без падения раунда (обёртка try/except → skip).
- **Критерий**: extraction пишет события+links для canary; pipeline запускается
  вместо старого пост-раунда при флаге, без регрессий; рефакторинг без
  изменения поведения при `event_extraction_enabled=false`.
- **НЕ делать**: не менять `perceive()`; не дублировать messages; не вносить
  новые LLM-вызовы сверх extraction.

#### Sprint 1 — Статус (2026-08-05): ✅ ВЫПОЛНЕН

- **Сделано** (без изменения поведения при `event_extraction_enabled=false`):
  1. `world_events` + `action` (JSON Text NOT NULL DEFAULT '{}'), +`importance`,
     +`story_salience`, +`emotional_salience` (nullable) — модели и идемпотентные
     ALTER в `ensure_schema` (существующие строки сохраняются, `action`='{}');
  2. `relationship_events.event_id` (nullable FK → `world_events.id`, ON DELETE
     SET NULL) + индекс `ix_rel_events_event_id`; `memory_anchors.event_id` — уже FK;
  3. `app/event_service.py`: `extract_round_events(client, db, chat_id,
     round_messages, ...)` → `EventExtractionResult` — LLM JSON-schema вызов
     (по образцу `extract_scene_state`) + **Sensors hook (§5.1.3)**: при
     `sensors_event_enabled` + `SENSORS_MODEL` SensorsService предлагает
     `{event_type, source_character, targets, importance, audibility/visibility}`,
     движок применяет свои правила (салиенс 0.5 детерминированно, лимит важности,
     запись через `crud.save_round_events`); Sensors НЕ пишет события;
     Sensors недоступен → детерминированный LLM-путь;
  4. `app/crud.py`: `save_round_events(db, chat_id, events, round_id=...)` —
     запись `world_events` (резолв имен → character_id, локации → location_id,
     неизвестные → NULL без падения) + `event_links` из `causes` (индексы в списке);
     идемпотентность по `round_id` (детект через `importance IS NOT NULL`);
     лимит `EVENT_MIN_IMPORTANCE`;
  5. `app/post_round_pipeline.py`: `run_post_round_pipeline(...)` — оркестратор
     стадий **presence → event extraction → memory → relationships → story**;
     каждая стадия — отдельная функция в try/except (падение одной не ломает
     раунд), memory/relationship планируются как background-задачи (инъекция
     коллбеков — без циклической зависимости); story — каркас (Sprint 8-11);
  6. `app/chat_engine.py`: пост-раунд инлайн-код (presence final pass,
     memory job, relationship task) заменён вызовом pipeline; `_analyze_and_update_relationships`
     и `memory_service.process_post_round` передаются как коллбеки;
  7. `app/ollama_client.py`: `EVENT_EXTRACTION_JSON_SCHEMA` +
     `extract_round_events()` (JSON-schema, fallback без schema); `app/prompts/ru.json`
     + `event_extraction` шаблон; `app/prompt_builder.py` + `build_event_extraction_*`;
  8. `app/config.py`: `EVENT_EXTRACTION_ENABLED` (default false),
     `EVENT_EXTRACTION_MODEL`, `EVENT_MIN_IMPORTANCE` (default 3.0); `.env.example` + `.env`.
- **Изменённые файлы**: `app/models.py`, `app/database.py`, `app/crud.py`,
  `app/schemas.py`, `app/config.py`, `app/chat_engine.py`, `app/ollama_client.py`,
  `app/prompt_builder.py`, `app/prompts/ru.json`, `.env.example` (+ `.env`);
  новые `app/event_service.py`, `app/post_round_pipeline.py`,
  `tests/test_event_extraction.py`, `tests/test_post_round_pipeline.py`,
  `tests/test_sprint1_schema.py`.
- **БД**: `ensure_schema` идемпотентна на «прод-схеме после Sprint 0» (проверено
  на копии: +4 колонки world_events, +event_id relationship_events, повторный
  запуск безопасен, существующие строки не теряются). `event_links` заведена в Sprint 0.
- **Тесты**: `tests/test_event_extraction.py` (11: extraction валидна, links
  корректны, лимит важности, идемпотентность, откат по флагу, Sensors-hook +
  fallback), `tests/test_post_round_pipeline.py` (7: полнота стадий, подмножество
  stages, изоляция ошибок, идемпотентность по round_id, background-планирование),
  `tests/test_sprint1_schema.py` (4: миграции + идемпотентность + дата-потери нет).
  Полный прогон: **840 passed, 28 failed** — те же 28 pre-existing падений, 0 новых
  (22 новых теста проходят; baseline `214b57e` даёт 818/28).
- **Откат**: снять колонки/таблицы или оставить флаг off — поведение равно
  legacy (`extract_round_events` не вызывается, pipeline-стадия event extraction
  — no-op); read-path никогда не читал новые поля.

### Sprint 2 — Memory Architecture v2: типы + якоря (P0)

- **Цель**: semantic/episodic/social/story память + эмоциональные якоря на
  единой таблице memories.
- **Почему**: retrieval (Sprint 6), relationship anchors (Sprint 7), story
  (Sprint 8) зависят от типов памяти.
- **Изменения**: колонки `memories.memory_type/event_id/valence/intensity`;
  `ExtractedFact.memory_type`; детерминированный fallback-классификатор;
  таблица `memory_anchors`; расширение `_maybe_create_memory_from_event` →
  anchor-запись; флаг `memory_types_enabled`.
  **Sensors hook (memory extraction, §5.1.3)**: при `sensors_memory_enabled` —
  SensorsService предлагает кандидатов `{facts: [{text, importance}]}` из
  событий/реплик; движок прогоняет их через существующую валидацию
  (`validate_extracted_facts`), witness-фильтр и лимиты перед записью; Sensors
  память сам не пишет.
- **Файлы**: `app/memory_service.py`, `app/crud.py`, `app/models.py`,
  `app/schemas.py`, `app/ollama_client.py`, `app/prompt_builder.py` (теги),
  `app/relationship_service.py`.
- **БД**: ALTER memories (+4 колонки), CREATE memory_anchors.
- **Тесты**: `test_memory_types.py`, `test_memory_anchors.py` (создание,
  активация top-K, отсутствие дублей в context).
- **Риски**: дедупликация по content_hash не учитывает type — ключ
  `(character_id, content_hash)` оставить, type не входит в hash; миграция
  существующих → type="semantic" default.
- **Критерий**: типы пишутся, anchors пишутся; retrieval не сломан.
- **НЕ делать**: не создавать вторую таблицу памяти.

#### Sprint 2 — Статус (2026-08-05): ✅ ВЫПОЛНЕН

- **Сделано** (без изменения поведения при `memory_types_enabled=false` и
  `anchors_enabled=false`):
  1. `app/models.py`: `Memory` + `memory_type` (String(20), NOT NULL, default
     'semantic'), +`event_id` (nullable FK → `world_events.id`, ON DELETE SET
     NULL, index), +`valence`/`intensity` (Float nullable); индексы
     `ix_memories_char_type (character_id, memory_type)`,
     `ix_memories_event (event_id)`;
  2. `app/database.py`: идемпотентные ALTER для `memories` (memory_type NOT NULL
     DEFAULT 'semantic' — существующие строки получают 'semantic' без дата-потерь,
     event_id/valence/intensity nullable) + CREATE INDEX IF NOT EXISTS; **фикс
     `ensure_schema`**: inspector на `inspect(conn)` (то же соединение
     транзакции), иначе миграция падает на прод-БД с данными (см. ниже);
  3. `app/schemas.py`: `MemoryType` (semantic|episodic|social|story),
     `normalize_memory_type()` (пустое/невалидное → None);
     `MemoryBase`/`MemoryUpdate`/`ExtractedFact` + memory_type/valence/intensity/
     event_id + валидаторы;
  4. `app/crud.py`: `create_memory` — пустой memory_type → default 'semantic'
     (type НЕ входит в content_hash, дедуп ключ `(character_id, content_hash)`
     не меняется); `update_memory` — nullable-колонки; новые **anchor CRUD**:
     `create_memory_anchor` (клампинг valence/intensity/importance),
     `anchor_activation_score` (importance × recency = 1/(1+возраст_дней)),
     `select_top_anchors` (top-K активация + дедуп по event_id — один канонический
     источник в контексте), `get_anchors_for_relationship(s)`;
  5. `app/memory_service.py`: `classify_memory_type` (category отношения→social,
     локация/предмет→semantic, событие→episodic, story-маркеры→story, иначе
     semantic; LLM-тип приоритетен); `validate_extracted_fact` проставляет
     fallback-тип; **Sensors memory-hook (§5.1.3)**: при `sensors_memory_enabled`
     SensorsService предлагает `{facts:[{text, importance}]}` → `_sensors_proposal_to_facts`
     → существующая `validate_extracted_facts`/witness-фильтр/лимиты → запись
     (Sensors память сам НЕ пишет); тип пишется только при
     `memory_types_enabled=true`;
  6. `app/ollama_client.py`/`app/prompt_builder.py`/`app/prompts/ru.json`:
     extraction-промпт + `memory_type` (не обязательно, fallback-классификатор);
  7. `app/relationship_service.py`: `_maybe_create_memory_from_event` и
     `_maybe_create_memory_from_resolved_issue` → `memory_type='social'` (при
     флаге); при `anchors_enabled=true` — запись якоря
     (`create_memory_anchor`) из значимого события (эмоция/валентность по знаку
     дельт, intensity=|max_delta|/100, importance=event.importance/10); Sensors
     якоря не предлагает;
  8. `app/config.py`: `MEMORY_TYPES_ENABLED`, `ANCHORS_ENABLED` (default false),
     `RELATIONSHIP_ANCHOR_MAX` (default 3); `.env.example` + `.env`.
- **Изменённые файлы**: `app/models.py`, `app/database.py`, `app/schemas.py`,
  `app/crud.py`, `app/memory_service.py`, `app/relationship_service.py`,
  `app/config.py`, `app/ollama_client.py`, `app/prompt_builder.py`,
  `app/prompts/ru.json`, `.env.example` (+ `.env`); новые
  `tests/test_memory_types.py`, `tests/test_memory_anchors.py`.
- **БД**: `ensure_schema` идемпотентна на «прод-схеме после Sprint 0»
  (проверено на копии с данными: +4 колонки memories, существующие строки →
  memory_type='semantic', повторный запуск безопасен). Попутно исправлен
  скрытый дефект: inspector читал БД отдельным соединением и не видел таблиц
  незакоммиченной транзакции — миграция падала (`NoSuchTableError: scene_states`)
  на БД с записями в `memories` (см. тест `test_sprint2_migration_backfills_semantic`).
- **Тесты**: `tests/test_memory_types.py` (17: normalize/classifier/validate/
  parse/create/update + миграции), `tests/test_memory_anchors.py` (12: клампинг,
  score, top-K активация, дедуп по event_id, группировка, запись якоря из
  события под флагом, отказ записи без флага). Полный прогон: **869 passed,
  28 failed** — те же 28 pre-existing падений (подтверждено baseline `git stash`:
  те же 28 падают без изменений), 0 новых.
- **Откат**: флаги off по умолчанию — поведение равно legacy (тип не пишется,
  якоря не пишутся, read-path не читает новые поля); колонки/индексы можно
  снять отдельной миграцией.

### Sprint 3 — Character State (P0)

- **Цель**: единое runtime-состояние персонажа без дублирования существующих данных.
- **Почему**: эмоции/стресс/внимание/цели нужны beliefs, intent, context, crisis.
- **Изменения**: таблица `character_states`; deterministic `emotion_engine`
  (обновление эмоций из relationship deltas + событий раунда); опциональная
  LLM-нормализация (JSON); флаг `character_state_enabled`; блок `YOUR STATE` в
  context (за Sprint 13 — просто заполняем, рендер по флагу).
  **Sensors hook (emotion/mood, §5.1.3)**: при `sensors_emotion_enabled` —
  SensorsService возвращает предложение `{emotion, intensity, mood_delta}`;
  `emotion_engine` применяет его только в рамках caps и правил; Sensors НЕ
  задаёт настроение напрямую.
- **Файлы**: новые `app/character_state.py`, `app/emotion_engine.py`;
  правки `chat_engine.py` (пост-раунд), `crud.py`, `models.py`, `schemas.py`,
  `context_builder.py`, `prompt_builder.py`.
- **БД**: CREATE character_states.
- **Тесты**: `test_character_state.py` (эмоции детерминированно меняются по
  дельтам; откат; нет дублирования location/relationships).
- **Риски**: не сломать perception — state не влияет на presence.
- **Критерий**: state пишется и читается; контекст не регрессирует.
- **НЕ делать**: не хранить локацию/отношения в state.

#### Sprint 3 — Статус (2026-08-05): ✅ ВЫПОЛНЕН

- **Сделано** (без изменения поведения при `character_state_enabled=false` и
  `sensors_emotion_enabled=false`):
  1. `app/emotion_engine.py` (новый, чистый модуль без БД/LLM):
     `EMOTIONS` (warmth/relief/hope/suspicion/tension/resentment/hurt/fear),
     `normalize_emotional_state` (неизвестные ключи отбрасываются, clamp 0..1),
     `relationship_emotion_deltas` (фикс-правила: affection/trust/attraction↑ →
     warmth/relief/hope; resentment↑ → resentment; jealousy↑ → tension/suspicion;
     trust/affection/attraction↓ → suspicion/hurt; кап `emotion_round_cap` на
     эмоцию за раунд), `stress_delta` (события emotional_salience>0.5 +
     негативные дельты, кап `stress_round_cap`), `decay_stress`/`decay_emotional_state`
     (мягкое затухание), `derive_mood` (mood ВСЕГДА выводится движком из
     эмоций+стресса; Sensors mood напрямую не задаёт), `apply_sensors_proposal`
     (сдвиг интенсивности только в рамках `sensors_intensity_cap` × confidence),
     `compute_state_update` (полный детерминированный update);
  2. `app/character_state.py` (новый): `state_to_dict`, `build_your_state_block`
     (рендер `<your_state>` эмоции ≥ `RENDER_INTENSITY_THRESHOLD`, mood, stress,
     physical, attention, active_goal — БЕЗ локации/отношений),
     `collect_round_inputs` (relationship deltas kind='llm' + world events с
     emotional_salience), `update_states_from_round` (get_or_create пустой строки,
     Sensors-emotion предложение при `sensors_service.is_enabled("emotion")`,
     `compute_state_update` → `update_character_state`; report
     {states, updated, sensors_used});
  3. `app/crud.py`: `get_character_state`, `get_character_states_for_chat`,
     `get_or_create_character_state` (пустая строка, commit, refresh),
     `update_character_state` (частичное, stress clamp 0..1),
     `get_relationship_events_for_round` (join RelationshipEvent×Relationship,
     kind='llm', round_id), `get_world_events_for_round` (только extraction-события,
     emotional_salience IS NOT NULL);
  4. `app/schemas.py`: `CharacterStateRead` (JSON-валидаторы через
     `normalize_emotional_state`); `BuiltContext.state_text` (вариант YOUR STATE);
  5. `app/prompt_builder.py`: `build_your_state_block`/`state_block_from_dict`;
  6. `app/context_builder.py`: параметр `character_state`, рендер `state_block`
     под флагом (фиксированный блок, не усекается), `state_text` в BuiltContext,
     счётчик в `component_tokens["character_state"]`;
  7. `app/ollama_client.py`: `your_state_block` в `_build_generation_messages`
     и legacy-пути из `built_context.state_text`;
  8. `app/chat_engine.py`: `_round_step` — загрузка `crud.get_character_state`
     под флагом (ошибка → warning, раунд не падает) и передача в
     `context_builder.build(..., character_state=...)`;
  9. `app/post_round_pipeline.py`: стадия `character_state` ПОСЛЕ relationships,
     ПЕРЕД story (events раунда уже в БД; relationship deltas — best-effort, т.к.
     анализатор фоновый); no-op при флаге off; изоляция try/except;
  10. `app/config.py` + `.env.example`/`.env`: `CHARACTER_STATE_ENABLED`,
      `EMOTION_ROUND_CAP` (0.4), `STRESS_ROUND_CAP` (0.2),
      `SENSORS_EMOTION_INTENSITY_CAP` (0.3).
- **Изменённые файлы**: `app/config.py`, `app/chat_engine.py`,
  `app/context_builder.py`, `app/crud.py`, `app/ollama_client.py`,
  `app/post_round_pipeline.py`, `app/prompt_builder.py`, `app/schemas.py`,
  `.env.example` (+ `.env`), `tests/test_post_round_pipeline.py` (отчёт стадии);
  новые `app/emotion_engine.py`, `app/character_state.py`,
  `tests/test_character_state.py`.
- **БД**: `character_states` уже создана в Sprint 0 (CREATE IF NOT EXISTS,
  unique character_id) — новых миграций не потребовалось.
- **Тесты**: `tests/test_character_state.py` (24: детерминированные правила
  эмоций/стресса/mood/decay, Sensors-caps, запись из deltas+events, отсутствие
  location/relationships в state, идемпотентность (одна строка на персонажа),
  откат по флагу, Sensors-failure → детерминированный путь, рендер YOUR STATE,
  CharacterStateRead). Полный прогон: **894 passed, 28 failed** — те же 28
  pre-existing падений (task_queue/context_state/memory_service/embeddings и др.,
  к Sprint 3 не относятся), 0 новых.
- **Откат**: флаг off по умолчанию — state не пишется и не читается, блок
  YOUR STATE не рендерится; таблица существовала до спринта.

### Sprint 4 — Attention (P1)

- **Цель**: «воспринято ≠ вошло в сознание».
- **Почему**: без attention memory/beliefs захламляются; критично для scenario
  «крик по имени».
- **Изменения**: `app/attention.py` (детерминированный score), колонка
  `message_presence.attention`; фильтр в `_round_step` и memory extraction
  (attention<LOW → не в память); хук в recency tail; флаг `attention_enabled`.
  **Sensors hook (perception proposal, §5.1.3)**: при `sensors_perception_enabled` —
  SensorsService предлагает, что персонаж *потенциально* может воспринять
  (услышать/увидеть/заметить/обращение/инфо из соседней локации/значимость);
  движок сохраняет решение о доступности только через существующий `perceive()`/
  presence-лестницу; Sensors не определяет окончательный набор информации.
- **Файлы**: новые `app/attention.py`; правки `perception.py` (известность
  автора), `witness_model.py`, `chat_engine.py`, `memory_service.py`,
  `context_builder.py`, `models.py`, `crud.py`, `config.py`.
- **БД**: ALTER message_presence (+attention REAL).
- **Тесты**: `test_attention.py` (падение стакана=low, крик по имени=high,
  якорь активирует attention, откат).
- **Риски**: регрессия рендера — attention меняет только то, что идёт в память,
  не то, что рендерится в recent history.
- **Критерий**: attention пишется; extraction/context фильтруют по порогу.
- **НЕ делать**: не менять presence-лестницу.

#### Sprint 4 — Статус (2026-08-05): ✅ ВЫПОЛНЕН

- **Сделано** (без изменения поведения при `attention_enabled=false` —
  presence-лестница и рендер recent history не тронуты, attention не пишется):
  1. `app/attention.py` (новый, чистый модуль без БД/LLM):
     `attention_bucket` (LOW/MEDIUM/HIGH; `None` → HIGH — legacy-откат),
     `compute_attention_score` (взвешенная сумма 8 компонентов §11: volume
     из громких стимулов/`audio_level`, distance из presence-лестницы,
     relevance из роли события + Sensors-significance, personal из упоминания
     имени, emotional из активного якоря, novelty, relationship из targets
     отношений наблюдателя, address из addressed=true; своя речь = 1.0),
     `attention_weights` (нормировка на 1.0), `apply_sensors_significance`
     (подсказка только в рамках `SENSORS_PERCEPTION_SIGNIFICANCE_CAP`);
  2. `app/models.py`: `MessagePresence.attention` (REAL NULL) — колонка для
     score пары (персонаж, событие); NULL = attention не считался (флаг off);
  3. `app/database.py`: идемпотентный `ALTER TABLE message_presence
     ADD COLUMN attention REAL NULL` в `ensure_schema` (существующие БД
     обновляются на старте);
  4. `app/schemas.py`: `MessagePresenceCreate.attention` (Optional[float],
     0..1);
  5. `app/crud.py`: `upsert_message_presence_batch` пишет attention при
     создании и обновляет только при явно переданном (None не затирает);
     `get_attention_map` (только не-NULL, пусто при флаге off);
     `_attention_context_for_chat` (rel_targets + anchor_authors одним
     заходом, 2 запроса); `_attention_score_for` (score + Sensors
     significance); attention встроен в `compute_and_save_presence_for_message`
     (синхронный путь) и `compute_and_save_presence_for_round` (пост-раунд);
  6. Sensors perception-proposal (§5.1.3): только в presence round pass —
     один вызов `sensors_service.run(task="perception")` на раунд при
     `attention_enabled`; `significance` применяется в рамках caps; Sensors
     НЕ определяет доступность информации (решает `perceive()`/presence) и
     НЕ принимает решение о внимании; недоступен Sensors → детерминированный
     путь (graceful degradation);
  7. `app/witness_model.py`: `filter_history_for_memory_extraction` получила
     `attention_map` — события с attention < LOW исключаются из memory-
     контекста (reason=`low_attention_background`), даже при present/told;
     `build_character_recency_tail` получила `attention_map` — такие события
     не идут в реакцию/recency tail;
  8. `app/memory_service.py`: `get_observable_context_for_character` +
     `attention_map`; `_extract_and_save_memories` и summarization-путь грузят
     `crud.get_attention_map` и передают в фильтр;
  9. `app/context_builder.py`: `_load_attention_map` (chunks) + передача в
     recency tail; recent history рендер не меняется;
  10. `app/chat_engine.py`: загрузка attention_map в обоих путях генерации
      и передача в fallback `build_character_recency_tail`;
  11. `app/post_round_pipeline.py`: стадия `presence` получает `client` для
      Sensors perception-proposal;
  12. `app/config.py` + `.env.example`/`.env`: `ATTENTION_ENABLED` (false),
      `ATTENTION_LOW` (0.35), `ATTENTION_HIGH` (0.7),
      `ATTENTION_WEIGHT_VOLUME/DISTANCE/RELEVANCE/PERSONAL/EMOTIONAL/NOVELTY/
      RELATIONSHIP/ADDRESS` (0.15/0.15/0.10/0.25/0.10/0.05/0.05/0.15),
      `SENSORS_PERCEPTION_SIGNIFICANCE_CAP` (0.15).
- **Изменённые файлы**: `app/attention.py` (новый), `app/models.py`,
  `app/database.py`, `app/schemas.py`, `app/crud.py`, `app/witness_model.py`,
  `app/memory_service.py`, `app/context_builder.py`, `app/chat_engine.py`,
  `app/post_round_pipeline.py`, `app/config.py`, `.env.example` (+ `.env`),
  `tests/test_attention.py` (новый).
- **БД**: `message_presence` + `attention REAL NULL` (идемпотентный ALTER в
  `ensure_schema`; новых таблиц нет).
- **Тесты**: `tests/test_attention.py` (20: сценарии из постановки — падение
  стакана=low, крик по имени=high, своя речь=1.0; якорь/новизна/имя меняют
  ровно свою весовую компоненту; пороги bucket и откат None→HIGH; Sensors-caps;
  запись attention через presence round pass (флаг on); откат (флаг off → NULL,
  `get_attention_map` пуст); upsert не затирает существующее значение; memory
  filter исключает low и включает high; recency tail исключает low и сохраняет
  legacy без карты). Полный прогон: **914 passed, 28 failed** — те же 28
  pre-existing падений (task_queue/context_state/memory_service/embeddings и др.,
  к Sprint 4 не относятся), 0 новых.
- **Откат**: `ATTENTION_ENABLED=false` по умолчанию — attention не считается
  (NULL в БД), memory/recency фильтры ведут себя как раньше (None → HIGH);
  presence-лестница и рендер recent history не изменены.

### Sprint 5 — Belief System (P0)

- **Цель**: beliefs (знание/убеждение) вместо плоской истины.
- **Почему**: персонажи не должны автоматически знать World Truth.
- **Изменения**: таблица `beliefs`; `belief_service.py` — pipeline
  event→perception→attention→belief update (источники, confidence, тип);
  валидация (не из событий, которые персонаж не воспринял → suspicion);
  контекст-блок `WHAT YOU KNOW` (top-K beliefs); постепенное замещение
  MVP epistemic mask при `beliefs_enabled=true` (mask остаётся fallback);
  флаг `beliefs_enabled` (default false).
- **Benchmark gate**: перед включением LLM-suggestion beliefs — прогон
  `benchmark_structured` на текущей модели (§27); при schema-validity < 90% —
  только детерминированный direct_observation.
- **Файлы**: новые `app/belief_service.py`; правки `chat_engine.py`
  (`_compute_epistemic_evidence` → belief-aware), `relationship_service.py`
  (mask может читать beliefs), `context_builder.py`, `prompt_builder.py`,
  `crud.py`, `models.py`, `schemas.py`, `config.py`.
- **БД**: CREATE beliefs.
- **Тесты**: `test_beliefs.py` — персонаж не знает то, что не воспринял;
  told_by снижает/повышает confidence по trust; suspicion без подтверждения;
  откат к mask.
- **Риски**: переизбыток beliefs → cap на context (top-K); регрессия изоляции
  (beliefs пишутся только из персонального perception).
- **Критерий**: beliefs не противоречат presence; изоляция сохранена.
- **НЕ делать**: не давать LLM напрямую менять beliefs без grounding.

#### Sprint 5 — Статус (2026-08-05): ✅ ВЫПОЛНЕН

- **Сделано** (без изменения поведения при `beliefs_enabled=false`; canary):
  1. `app/belief_service.py` (новый, детерминированный pipeline §9): `BELIEF_SOURCES`
     /`BELIEF_TYPES`, `source_for_presence` (present→direct_observation,
     mentioned→heard, audible→rumor, told→told_by, absent→None), базовые confidence
     (direct_observation 0.85, heard 0.7, told_by 0.5, inference 0.6, rumor 0.3,
     memory 0.5), `told_by_confidence(trust)` = 0.2+0.6·(trust/100),
     `compute_confidence`, `belief_type` (fact при ≥0.75 + direct/подтверждение;
     belief при ≥0.5; иначе suspicion), `merge_confidence` (max, cap 0..1),
     `triplet_from_event`, `collect_round_inputs` (все world events раунда),
     `update_beliefs_from_round` (per-character: presence→source, attention gating
     при `attention_enabled` и `attention < attention_low`, dedupe по триплету,
     trust→told_by confidence, upsert/merge, world_truth_ref для direct; отчёт
     {characters, written, updated, skipped}), `_trust_to_teller` (get_relationship);
  2. `app/crud.py`: `get_beliefs_for_character` (top-K по confidence, пусто при
     `beliefs_enabled=false` — read-path canary), `get_beliefs_for_chat`,
     `_find_belief`, `upsert_belief` (валидация source/type, clamp confidence,
     merge), `delete_belief`; хелперы пайплайна `get_presence_for_message`,
     `get_attention_for_message`, `get_round_world_events` (все события раунда
     с message_id/action — движковые speech/move тоже);
  3. `app/context_builder.py`: `_build_what_you_know_block` (top-K + порог
     `beliefs_render_confidence`, рендер только при `beliefs_enabled`),
     параметр `what_you_know_block` в `build()`, счёт токенов
     (component_tokens["what_you_know"]), поле `BuiltContext.what_you_know_text`;
  4. `app/prompt_builder.py`: `build_what_you_know_block` (блок `<what_you_know>`
     с маркерами «Ты знаешь/Ты полагаешь/Ты подозреваешь» + уверенность; data-only);
  5. `app/ollama_client.py`: `what_you_know_block` прокинут через `build_chat_messages`,
     `_generate_once`, `generate`; рендер в обоих путях (chat-messages и context_parts);
  6. `app/chat_engine.py`: `_compute_epistemic_evidence` → async, при
     `beliefs_enabled` расширяется `_belief_evidenced_ids` (beliefs по subject-имени);
  7. `app/relationship_service.py`: `build_epistemic_mask_block` при
     `beliefs_enabled` читает beliefs (`_beliefs_by_subject`/`_epistemic_belief_line`)
     вместо «неизвестно»;
  8. `app/post_round_pipeline.py`: стадия `beliefs` после character_state
     (no-op при `beliefs_enabled=false`);
  9. `app/config.py`: `BELIEFS_ENABLED` (false), `BELIEFS_TOP_K` (8),
     `BELIEFS_RENDER_CONFIDENCE` (0.3), `BELIEFS_LLM_SUGGESTION_ENABLED` (false);
     `app/schemas.py`: `BeliefSource`, `BeliefType`, `BeliefRead`.
- **Тесты**: `test_beliefs.py` (8): персонаж не узнаёт невоспринятое (absent);
  present → direct_observation/fact + world_truth_ref; низкий attention
  («слышал фоном») → skip; told_by по trust (90 → 0.74, 10 → 0.26/suspicion,
  без ребра → 0.5); неподтверждённый слух → suspicion без world_truth_ref;
  read-path пуст при `beliefs_enabled=false` (mask fallback). Плюс правка
  `test_post_round_pipeline.py` (стадия beliefs в наборе).
- **Полный прогон**: 873 passed; 28 failed — все в НЕ тронутых модулях
  (task_queue, memory_service/memory_perception, embeddings, context_state,
  token_counter, stream_disconnect) — пред-существующие поломки тестов
  (async-сигнатуры create_characters/run_job и т.п.), к beliefs не относятся.
- **НЕ сделано** (задел §9): LLM-suggestion beliefs — за benchmark gate (§27);
  пока только детерминированный direct_observation-путь.

### Sprint 6 — Hybrid Retrieval v2 (P1)

- **Цель**: reranking с salience/story/relationship-сигналами.
- **Почему**: контекст переполняется нерелевантными memories.
- **Изменения**: `rerank_memories` в `memory_service.py`; использование
  `memory_type`, `valence/intensity`, story threads, отношения текущего
  контекста; веса в config; fallback при отсутствии embeddings; флаг
  `hybrid_rerank_enabled`.
- **Файлы**: `memory_service.py`, `crud.py`, `config.py`, `context_builder.py`
  (передача сигналов: текущие отношения/threads).
- **БД**: нет (только чтение новых колонок).
- **Тесты**: `test_hybrid_rerank.py` (story memory выше при активном thread;
  эмоциональная релевантность при anchors; fallback BM25).
- **Риски**: изменение порядка memories в контексте — golden-снэпшоты
  переснять только для блоков memories.
- **Критерий**: rerank улучшает precision на eval-сценарии; RRF-путь без флага
  не меняется.
- **НЕ делать**: не удалять BM25.

#### Sprint 6 — Статус (2026-08-05): ✅ ВЫПОЛНЕН

- **Сделано** (без изменения поведения при `hybrid_rerank_enabled=false`; canary):
  1. `app/memory_service.py`: `@dataclass RerankSignals` (relationship_target_names,
     active_threads) и `RerankContext(RerankSignals)` (query_text, query_embedding);
     `rerank_weights()` (нормировка на 1.0); детерминированные оси:
     `_lexical_overlap` (BM25-подобный overlap запроса/памяти), `_semantic_similarity`
     (cosine по embeddings, отпадает при их отсутствии), `_emotional_relevance`
     (intensity+0.5·|valence|), `_story_relevance` (story memory + overlap с
     active_threads 1.0; без threads 0.3; без overlap 0.5), `_relationship_relevance`
     (target в signal-именах 1.0, fallback по category), `_recency_score`,
     `_salience_score`; `rerank_memories(candidates, context, weights=None)` —
     стабильная сортировка (без query_text lexical-ось отпадает);
  2. `app/crud.py`: `from __future__ import annotations` (цикл crud↔memory_service);
     `build_rerank_signals(db, chat_id, character_ids, character_names)` — читает
     отношения (source/target) и активные story_threads, пусто при `false`;
     `_apply_rerank(...)` — no-op при `hybrid_rerank_enabled=false`/без сигналов;
     применён в `get_relevant_memories_for_characters` и
     `get_hybrid_memories_for_characters` ПОСЛЕ BM25/RRF и ДО `_apply_witness_boost`;
  3. `app/chat_engine.py`: оба retrieval call-site собирают `rerank_signals` через
     `crud.build_rerank_signals` (try/except) при `hybrid_rerank_enabled` и передают
     в retrieval;
  4. `app/context_builder.py`: `build()` принимает `rerank_signals`; в секции
     «7. memories» при `hybrid_rerank_enabled` + сигналы — детерминированный
     re-order через `rerank_memories` (пустой query);
  5. `app/config.py`: блок «Hybrid Retrieval v2» — `HYBRID_RERANK_ENABLED=false`,
     `HYBRID_RERANK_WEIGHT_{LEXICAL,SEMANTIC,EMOTIONAL,STORY,RELATIONSHIP,
     RECENCY,SALIENCE}` (0.30/0.25/0.10/0.15/0.10/0.05/0.05); `.env.example` и
     `.env` дополнены.
- **Тесты**: `test_hybrid_rerank.py` (24): нормировка весов; оси lexical/semantic/
  emotional/story/relationship/recency/salience по отдельности; rerank-сценарии
  (story memory выше при активном thread, эмоциональная релевантность при anchors);
  fallback BM25 без embeddings; стабильность сортировки; `build_rerank_signals`
  (отношения/threads, пусто при `false`); интеграция BM25/RRF-пути; RRF-путь без
  флага не меняется.
- **Полный прогон**: 946 passed; 28 failed — все в НЕ тронутых модулях
  (task_queue, memory_service/memory_perception, embeddings, context_state,
  token_counter, stream_disconnect) — пред-существующие поломки тестов
  (async-сигнатуры create_characters/run_job и т.п.), подтверждены на чистом
  master (git stash), к rerank не относятся.
- **НЕ сделано** (задел §14): переснять golden-снэпшоты для блоков memories не
  потребовалось — порядок меняется только под флагом (canary), default-off.

### Sprint 7 — Relationship Evolution v2 (P1)

- **Цель**: dynamic decay + reciprocity pipeline + anchor activation.
- **Почему**: постановка: «не общались → доверие не исчезает»; направленные
  изменения зависят от beliefs.
- **Изменения**: `apply_decay` → динамический профиль (base_rate × character_factor,
  из config/character_state); Reciprocity pipeline (behavior→perception→
  interpretation→update) с учётом beliefs (множитель капа по confidence);
  anchor activation в context (top-K); флаги `dynamic_decay_enabled`,
  `reciprocity_enabled`, `anchors_enabled`.
  **Sensors hook (relationship analysis, §5.1.3)**: при `sensors_relationship_enabled` —
  SensorsService предлагает `{affection_delta, trust_delta, resentment_delta,
  jealousy_delta}` для пары source→target; дельты применяются только через
  существующую систему правил (evidence gating, `_constrain_pair_delta`, caps,
  decay, normalization); Sensors отношения напрямую не меняет.
- **Файлы**: `relationship_service.py`, `relationship_interpreter.py` (drivers
  используют beliefs), `chat_engine.py` (pipeline hook), `context_builder.py`,
  `prompt_builder.py`, `config.py`, `models.py`.
- **БД**: ALTER character_relationships (+decay_profile JSON nullable) или
  конфиг-карта (без миграции) — предпочтительно конфиг + character_states.
- **Тесты**: `test_relationship_decay_dynamic.py`, `test_relationship_reciprocity_v2.py`
  (belief «занят» → слабее дельта), `test_anchors_activation.py`.
- **Риски**: регрессия evidence gating — новое звено не отключает gating;
  оскалляции метрик — caps остаются.
- **Критерий**: decay не обнуляет trust при неактивности; anchor-блок ≤ cap.
- **НЕ делать**: не зеркалировать рёбра; не ломать trajectory.

#### Sprint 7 — Статус (2026-08-05): ✅ ВЫПОЛНЕН

- **Сделано** (всё под canary-флагами; при выключенных — legacy-пути не тронуты):
  1. `app/config.py`: блок «Relationship Evolution v2» — `DYNAMIC_DECAY_ENABLED=false`,
     `DYNAMIC_DECAY_JEALOUSY_BASE_RATE=3`, `DYNAMIC_DECAY_RESENTMENT_BASE_RATE=1`,
     `DYNAMIC_DECAY_STRESS_SENSITIVITY=0.5`, `DYNAMIC_DECAY_FACTOR_MIN=0.4`,
     `DYNAMIC_DECAY_FACTOR_MAX=1.6`; `RECIPROCITY_ENABLED=false`,
     `RECIPROCITY_BELIEF_DAMPENING=0.5`, `RECIPROCITY_BELIEF_MULTIPLIER_MIN=0.5`;
  2. `app/relationship_service.py`:
     - `_dynamic_decay_factor(state)` — чистый хелпер: factor = 1 + sensitivity·(0.5−stress),
       клампинг в [factor_min, max], 1.0 без state/stress;
     - `apply_decay` — при `dynamic_decay_enabled` грузит `character_states` чата одним
       запросом и масштабирует base_rate × factor per-rel; legacy-путь без флага
       идентичен; decay-событие только при пересечении десятка (не тронуто);
     - `compute_reciprocity_belief_multiplier(db, source_id, target_name)` —
       clamp(1 − dampening·max_confidence, min, 1.0) по beliefs source о target;
       1.0 при выключенных флагах/нет belief/ошибке (не роняет раунд);
     - `build_relationships_block` — при `anchors_enabled` грузит якоря одним запросом
       (`crud.get_anchors_for_relationships`), top-K через `crud.select_top_anchors`
       (importance×recency, дедуп event_id), рендер «якорь: {emotion} (важность {x})»;
     - `build_behavior_drivers_block` — при `beliefs_enabled` добавляет belief-драйверы
       «Ты знаешь/подозреваешь/полагаешь, что …» с весом по confidence;
  3. `app/chat_engine.py`:
     - `_constrain_pair_delta` — observed/hearsay cap умножается на
       `pair_ctx["reciprocity_belief_multiplier"]` (floor 1), direct не трогается;
     - в `_analyze_and_update_relationships` при `reciprocity_enabled` для каждой пары
       вычисляется и кэшируется множитель;
     - `_run_sensors_relationship_proposal(db, chat_id, client, pairs, round_id)` —
       Sensors relationship hook: одна пайра за раунд (none-отсекаются), результат
       SensorsService.run(task="relationship") проходит `_constrain_pair_delta` и
       применяется через `apply_delta`;
  4. Sensors-слой relationship уже поддерживал задачу (JSON-схема + инструкция +
     `is_enabled`), правок не потребовалось.
- **Тесты**: `test_relationship_decay_dynamic.py` (14), `test_relationship_reciprocity_v2.py`
  (13), `test_anchors_activation.py` (9): формула фактора + клампинг + нейтральность
  без state; slow/fast decay по stress (сравнение с legacy); affection/trust/attraction
  не затухают; decay-событие на границе десятка; множитель по confidence (сильная →
  кап слабее, min-клампинг, case/whitespace, ошибка БД benign); cap scaled в
  observed/hearsay, direct не тронут, floor 1; рендер якорей в блоке, top-K,
  дедуп event_id, пусто при выключенном флаге, ошибка загрузки benign.
- **Полный прогон**: 982 passed; 28 failed — все в НЕ тронутых модулях
  (task_queue, memory_service/memory_perception, embeddings, context_state,
  token_counter, stream_disconnect) — пред-существующие поломки тестов
  (async-сигнатуры create_characters/run_job и т.п.), подтверждены на чистом
  master, к Sprint 7 не относятся.
- **НЕ сделано** (задел §18/§10): полный Sensors relationship hook в каждый раунд —
  реализован как одна пайра за раунд (cost); decay_profile на таблице —
  предпочтительно конфиг + character_states (как запланировано).

### Sprint 8 — Dynamic Story State (P0)

- **Цель**: Original Plot + Current Story State + Story History + Phase.
- **Почему**: сюжет — главная отсутствующая ось.
- **Изменения**: `app/plot/story_state.py`, `story_events.py`; запись
  `story_events` из раундных events (Sprint 1) + важные события;
  `chats.original_plot/story_prompt/story_enabled` (Sprint 0) используются;
  `story_phase`; блок `STORY` в контексте (активные потоки, прогресс);
  флаг `story_enabled` (canary → global).
- **Файлы**: новые `app/plot/{__init__,story_state,story_events}.py`;
  правки `chat_engine.py` (пост-раунд hook), `crud.py`, `models.py`,
  `schemas.py`, `context_builder.py`, `prompt_builder.py`, `routers/chats.py`
  (API story state GET/PATCH — только пользователь может править original_plot).
- **БД**: CREATE story_states, story_events, story_threads (Sprint 0 уже создал,
  здесь — write-path).
- **Тесты**: `test_story_state.py` (запись, фазы, события, isolation).
- **Риски**: рассинхрон original_plot/story_prompt — API только для
  пользователя; контекст не разрастается (top-K потоков).
- **Критерий**: story пишется и рендерится; исходное general_prompt не меняется.
- **НЕ делать**: не позволять LLM менять original_plot.

### Sprint 9 — Story Consolidation (P1)

- **Цель**: LLM-обновление Current Story State с валидацией.
- **Почему**: story state должен эволюционировать.
- **Изменения**: `app/plot/story_consolidation.py`; trigger (rounds/critical);
  JSON-schema контракт; валидация (original plot diff, grounding, rollback);
  флаг `story_consolidation_enabled`.
- **Benchmark gate**: перед включением LLM-consolidation — `benchmark_structured`
  на story-update (§27); при grounding < порога — только кандидаты-флаги,
  без применения.
- **Файлы**: новый `story_consolidation.py`; правки `chat_engine.py` (hook),
  `main.py` (не по таймеру — по score, см. Sprint 12), `prompts/ru.json`,
  `ollama_client.py` (consolidation вызов).
- **БД**: versioned story_states (доп. колонка `version`).
- **Тесты**: `test_story_consolidation.py` (завершённые цели уходят из active,
  прогресс сохраняется, новые потоки из событий, original plot не искажается,
  невалидное обновление → rollback).
- **Риски**: hallucination — grounding и confidence; стоимость — не чаще
  `story_consolidation_interval_rounds`.
- **Критерий**: story state корректно эволюционирует на scenario 100+ раундов.
- **НЕ делать**: не давать LLM менять original_plot/фазы без валидации.

### Sprint 10 — Plot Engine, NPC Intent + Plans (P1)

- **Цель**: plot-модуль (threads, intent, plans) и долгоживущие планы NPC.
- **Почему**: NPC должны иметь цель и мешающие обстоятельства.
- **Изменения**: `plot_pressure.py`, `intent.py` (п.21), `npc_plans` (п.22),
  `story_threads.py` (архивация завершённых); детерминированное формирование
  intent перед генерацией; блок `ACTIVE GOAL`/`ACTIVE PLAN`; флаги
  `npc_intent_enabled`, `npc_plans_enabled`.
- **Файлы**: новые `app/plot/{plot_pressure,intent,story_threads}.py`,
  `app/npc_plans.py`; правки `chat_engine.py` (intent в `_round_step`),
  `context_builder.py`, `prompt_builder.py`, `crud.py`, `models.py`, `schemas.py`.
- **БД**: CREATE intents, npc_plans (Sprint 0 уже создал — write-path).
- **Тесты**: `test_intent.py`, `test_npc_plans.py` (план блокируется, next_step
  обновляется, один активный план).
- **Риски**: intent не должен выглядеть как «режиссёр» — тенденция, не команда
  (по образцу drivers).
- **Критерий**: intent формируется без LLM-фантазий о мире; планы живут.
- **НЕ делать**: не строить GOAP.

### Sprint 11 — Crisis Engine (P2)

- **Цель**: мягкое обнаружение кризисов.
- **Почему**: постановка §5 — естественные кризисы.
- **Изменения**: `app/plot/crisis_engine.py` (pressure/candidate/evaluation/
  resolution); мягкое применение (boost attention/action, новые story_threads);
  флаг `crisis_engine_enabled`.
- **Benchmark gate**: LLM-оценка кризиса (§27) — только после прохождения
  `benchmark_structured` на crisis-evaluation; иначе — детерминированный
  pressure без LLM.
- **Файлы**: новый `crisis_engine.py`; правки `chat_engine.py` (hook пост-раунд),
  `context_builder.py` (pressure в контексте), `config.py`.
- **БД**: story_events/story_threads используются.
- **Тесты**: `test_crisis_engine.py` (нет форсированных аргументов; кандидаты
  только при pressure+неразрешённость; resolution пишет thread).
- **Риски**: сюжет становится слишком детерминированным — кризис = вероятность,
  не команда.
- **Критерий**: на scenario с затянутым конфликтом engine предложит crisis.
- **НЕ делать**: `if trust<30: force_argument`.

### Sprint 12 — Adaptive Consolidation (P1)

- **Цель**: замена 24h-таймера на score-based soft/hard/critical.
- **Почему**: постановка §17; idle-чат не консолидируется.
- **Изменения**: `consolidation_state` counters; `compute_consolidation_score`;
  soft/hard/critical; `is_critical_event`; расширение
  `consolidate_memories_job` → полный набор (memory+summary+relationship+
  anchors+story+index); замена `_consolidation_scheduler` (`main.py:62`);
  флаг `adaptive_consolidation_enabled`.
- **Файлы**: `memory_service.py`, `main.py`, `config.py`, `crud.py`,
  `relationship_service.py`, `plot/story_consolidation.py`.
- **БД**: CREATE consolidation_state (chat_id, last_soft_at, last_hard_at,
  counters).
- **Тесты**: `test_adaptive_consolidation.py` (idle не консолидирует; критическое
  событие → немедленно; score пороги).
- **Риски**: стоимость — critical может дорого стоить при частых событиях →
  дедупликация critical (не чаще N раз/раунд).
- **Критерий**: Scenario 5 (consolidation) проходит.
- **НЕ делать**: не смешивать summary и consolidation.

### Sprint 13 — Context Builder Evolution (P2)

- **Цель**: финальная приоритизированная сборка с новыми блоками.
- **Почему**: все подсистемы готовы — время интеграции в промпт.
- **Изменения**: новые блоки WORLD/WHAT YOU KNOW/WHAT YOU PERCEIVE/YOUR STATE/
  RELATIONSHIP/ACTIVE GOAL/RELEVANT MEMORY/STORY; обновлённый
  `context_budget.build_budget`; токен-подбюджеты; удаление дублирующих
  старых блоков (scene → WORLD, relationships → RELATIONSHIP+anchors);
  флаг `context_v2_enabled` (canary → global), legacy-блоки остаются при off.
- **Файлы**: `context_builder.py`, `context_budget.py`, `prompt_builder.py`,
  `config.py`, `ollama_client.py` (передача новых блоков), `chat_engine.py`,
  `routers/debug.py` (полная сводка state + pipeline), `static/debug.html`.
- **БД**: нет.
- **Тесты**: `test_context_v2.py` (приоритеты, усечение, откат, golden-снэпшоты
  обновляются только при флаге), scenario-тесты 1–5; quality-прогон §27 на
  `full_state_driven`.
- **Риски**: изменение промпта влияет на генерацию — канареечный запуск,
  сравнение на eval-наборе; перерасход токенов — новые блоки в отдельных
  подбюджетах.
- **Критерий**: контекст соответствует целевой структуре п.23; регрессий нет.
- **НЕ делать**: не увеличивать max_context без причины; не выдавать персонажу
  World Truth.

---

## 24.1 LLM Budget (вызовы на раунд)

**Счётчик LLM-вызовов на один user-раунд** после внедрения всех подсистем.
Критично: генерация раунда остаётся единственным синхронным вызовом; всё
пост-раундовое — в фоне (task queue), не блокирует ответ пользователю.

### Текущий baseline (реальный замер существующего движка)

| Вызов | Синхронно/фон | Примечание |
|---|---|---|
| `generate` per NPC (N NPC) | синхронно | N = число NPC в раунде |
| `extract_scene_state` (1) | фон | пост-раунд |
| relationship analyzer batch (1) | фон | `_analyze_and_update_relationships` |
| memory extraction (per char, до K) | фон | 0–3 факта/персонаж |
| summarize (амортиз.) | фон | каждые ~20 сообщений |
| consolidation (24h) | фон | 1/сутки |

### Целевое (полная state-driven, все флаги on)

| Вызов | Синхронно/фон | Кол-во/раунд | Когда |
|---|---|---|---|
| `generate` per NPC | синхронно | N (без изменений) | раунд |
| event extraction (§1) | фон | **1** | пост-раунд |
| scene extraction | фон | 1 | пост-раунд |
| relationship analyzer batch | фон | 1 | пост-раунд |
| memory extraction | фон | до K | пост-раунд |
| **emotion normalization** | фон | 0–1 (детерм. fallback; LLM только при расхождении) | пост-раунд |
| **belief suggestion** | фон | 0 (детерм. pipeline; LLM только по порогу сомнения) | пост-раунд |
| **story consolidation** | фон | ≤1 / `interval_rounds` (≈0.07 при 15) | пост-раунд по score |
| **crisis evaluation** | фон | 0–1 (только при кандидате) | пост-раунд |
| **adaptive consolidation** | фон | ≤1 / score-порог | фон |
| summarize | фон | амортиз. | пост-раунд |
| **Sensors: perception proposal** | фон | 0–1 (при `sensors_perception_enabled`) | пост-раунд |
| **Sensors: event classification** | фон | 0–1 (при `sensors_event_enabled`) | пост-раунд |
| **Sensors: emotion/mood** | фон | 0–1 (при `sensors_emotion_enabled`) | пост-раунд |
| **Sensors: memory candidates** | фон | 0–1 (при `sensors_memory_enabled`) | пост-раунд |
| **Sensors: relationship** | фон | 0–1 (при `sensors_relationship_enabled`) | пост-раунд |

Итого **добавляется ≤ 4 вызовов/раунд** основных + **до +5 sensor-вызовов/раунд**
(только при соответствующих `sensors_*_enabled`), все фоновые и дешевле генерации.
Sensors-вызовы идут на **отдельную модель `SENSORS_MODEL`** (маленькая, быстрая),
не на основную; при пустой `SENSORS_MODEL` или недоступности — sensor-задачи
пропускаются (fallback на детерминированный путь, §5.1.8).
Пиковый worst-case: 1 генерация (sync) + 5 фон + 5 sensor-фон. Бюджет закрепляется
в DoD: **ни один новый вызов не добавляется в синхронный путь**; каждый новый
вызов имеет try/except, лимит частоты и считается в этом разделе. Рост при новом
спринте = обязательная строка «+N LLM-вызовов/раунд» здесь, иначе спринт не
принимается.

---

## 24.2 Feature Profiles (поддерживаемые профили флагов)

Не тестировать все комбинации ~18 флагов. Три **поддерживаемых профиля** —
гарантированные связки флагов, на которых прогоняется тестовый набор:

| Профиль | Флаги | Назначение |
|---|---|---|
| `legacy` | все новые флаги OFF (вкл. Sensors) | текущее поведение; регрессионный эталон (golden) |
| `story_lite` | WPE(перцепция/events/actions/threads) + story + character_state + memories(types/anchors) + Sensors(perception/event/emotion/memory/relationship, если задан `SENSORS_MODEL`); НЕ beliefs, НЕ attention, НЕ crisis, НЕ intent | промежуточный «сюжет-ориентированный» путь для сценариев §27 (story/perception/memory); Sensors включён как аналитический слой |
| `full_state_driven` | все флаги ON (вкл. Sensors при заданном `SENSORS_MODEL`) | целевое состояние; полный контекст §23 |

Правила:

1. **Профиль = конфиг** (`app/config.py`, функция `resolve_profile(name)`),
   который устанавливает связку флагов; профили можно переопределять точечно
   (например `full_state_driven + beliefs_enabled=false` для A/B).
2. **Sensors и профили**: `SENSORS_MODEL` — ортогональная настройка; Sensors
   активен только если `SENSORS_MODEL` задан И включён соответствующий
   `sensors_<task>_enabled`. В `legacy` Sensors выключен всегда.
3. **Тестовый набор §27** прогоняется минимум на `legacy` и `story_lite`;
   полный сценарий-набор — на `full_state_driven` (ночью/в CI eval).
4. **Каждый новый флаг обязан** быть отнесён к одному из профилей (или новому
   профилю — только через явное решение); «флаг вне профилей» запрещён.
5. **Критерий выхода спринта**: изменение не ломает `legacy` (golden), работает
   в целевом профиле, включено в него.
6. `story_lite` — это профиль **тестирования**, а не отдельный этап/спринт в
   roadmap: реализация по-прежнему идёт по спринтам 0–13, просто
   промежуточные сценарии запускаются под связкой флагов `story_lite`.

---

## 25. Dependency Graph

```text
Sprint 0 (schema foundation)
   ↓
Sprint 1 Structured Events ─────────────┐
   ↓                                    │
Sprint 2 Memory Types + Anchors ────────┤
   ↓                                    │
Sprint 3 Character State ───────────────┤
   ↓                                    │
Sprint 4 Attention                      │
   ↓                                    │
Sprint 5 Beliefs ───────────────────────┤
   ↓                                    │
Sprint 6 Hybrid Retrieval v2 (нужны 2, 3)│
   ↓                                    │
Sprint 7 Relationship Evolution v2 (нужны 3, 5, 2)
   ↓                                    │
Sprint 8 Dynamic Story State (нужны 1, 2)
   ↓                                    │
Sprint 9 Story Consolidation (нужен 8)  │
   ↓                                    │
Sprint 10 Intent + Plans (нужны 3, 8, 5)
   ↓                                    │
Sprint 11 Crisis Engine (нужны 7, 8, 9, 3)
   ↓                                    │
Sprint 12 Adaptive Consolidation (нужны 2, 7, 9)
   ↓                                    │
Sprint 13 Context Builder Evolution (нужны ВСЕ)
```

**Почему именно такой порядок**:
- события → память → состояние персонажа → beliefs → отношения → сюжет →
  кризисы: каждая следующая система читает выход предыдущей;
- Attention ставится до Beliefs (фильтр входа);
- Intent/Plans после story (цели связаны с сюжетом) и до контекста (входят в
  блок ACTIVE GOAL);
- Context Builder последним — интеграция всех данных;
- Adaptive Consolidation после story/anchors, т.к. score включает их.

---

## 26. Migration Strategy

1. **Ничего не удалять сразу**. Новые системы — под флагами; legacy-пути
   остаются как fallback (паттерн WPE Phase 0–8).
2. **Идемпотентные миграции** через `ensure_schema` (`app/database.py`) —
   ALTER TABLE ... ADD COLUMN / CREATE TABLE IF NOT EXISTS, бэкафиллы отдельными
   скриптами (`scripts/`) с отчётом (по образцу `backfill_character_location_ids`).
3. **Перенос данных**: `general_prompt → original_plot/story_prompt` (copy, не
   move); `world_events.location → location_id` (backfill по имени);
   `memories` default type="semantic" для существующих.
4. **Включение флагов**: Sprint 0–5 канареечно на тестовых чатах; Sprint 8
   (story) и Sprint 13 (context v2) — canary → global после стабильности.
5. **Откат**: выключение флага возвращает предыдущее поведение; read-path новых
   таблиц не влияет на старые пути.
6. **Удаление legacy**: только после того, как новый путь стабилен ≥N раундов
   и golden-набор зелёный; каждый случай — отдельным PR (как Фаза 8 WPE).

---

## 27. Testing Strategy

### Quality metrics («стало лучше», не только «тесты не сломались»)

Существующий harness `tests/eval/` (`harness.py`, `metrics.py`, `mock_llm.py`,
`run_eval.py`, `scenarios/*.yaml`) — база для метрик качества, а не только
регрессии. Целевые метрики качества по каждому спринту:

| Метрика | Источник | Порог |
|---|---|---|
| `fact_recall_at_5` / `fact_recall_at_k` | `tests/eval/metrics.py` | ≥70% |
| `isolation_violation_rate` | `tests/eval/metrics.py` | <10% |
| `witness_leakage` (знание невоспринятого) | `tests/eval/metrics.py` | 0% |
| `scene_state_consistency` | `tests/eval/metrics.py` | ≥порог |
| **NEW `belief_groundedness`** | beliefs ≤ (персонально воспринятые события); без leak | 100% на сценарии 3 |
| **NEW `story_state_fidelity`** | story_state не противоречит original_plot; прогресс ≤ факт. событий | 100% |
| **NEW `retrieval_precision@k`** | доля релевантных memories в top-K | ≥ baseline + X% |
| **NEW `anchor_activation_precision`** | активированные anchors относятся к контексту | ≥80% |
| **NEW `decay_neutrality`** | trust не падает при неактивности | 0 регрессий |

Правила:

1. Каждый спринт добавляет/обновляет **не менее одной** метрики качества и
   **сценарий** в `tests/eval/scenarios/`; без этого спринт не принимается.
2. Прогон: `python -m tests.eval.run_eval --mode mock` (быстрый, каждая PR) +
   `--mode real` (ночью, реальный Ollama). Пороги — в `tests/eval/metrics.py`.
3. **Сравнение «до/после»**: для спринтов, меняющих контекст (6, 7, 8, 13),
   прогон `fact_recall`/`retrieval_precision` на одних сценариях до и после;
   ухудшение → спринт не принимается даже при зелёных unit-тестах.
4. `story_lite`/`full_state_driven` профили (§24.2) — отдельные прогоны.

### Model benchmark gate (перед массовым LLM: beliefs/story/crisis)

Перед массовым использованием LLM для структурированных полей
(belief suggestion, story consolidation, crisis evaluation) обязателен
**бенчмарк текущей модели** на этих задачах:

- `tests/eval/scenarios/benchmark_structured.jsonl` — корпус «событие →
  ожидаемый belief/story-update/crisis-evaluation»;
- метрики: schema-validity (валидный JSON по schema), grounding (поля не
  противоречат входным событиям), precision/recall по `must_contain`;
- запуск `--mode real` против текущей модели (по умолчанию
  `qwen3-coder:30b-a3b-q4_K_M`);
- **gate**: если schema-validity < 90% или grounding < порога — LLM-вызов для
  этой задачи **не включается**, остаётся детерминированный fallback (belief =
  только direct_observation; consolidation = только флаги-кандидаты; crisis =
  без LLM-оценки, только pressure) до смены модели/промпта;
- результат фиксируется в `docs/benchmarks/` перед Sprint 5 (beliefs),
  Sprint 9 (consolidation), Sprint 11 (crisis).

### Unit tests
- `perception.py`, `attention.py`, `belief_service.py`, `emotion_engine.py`,
  `plot/*` — чистые функции, детерминированные таблицы.
- `memory_types`, `anchors`, `rerank`, `decay_dynamic`, `crisis` — локальная логика.
- `test_sensors.py` (§5.1.10) — `SENSORS_MODEL` из `.env`, изоляция от основной
  модели, JSON-валидация, fallback, «не пишет в БД», «не подменяет генерацию».

### Integration tests
- event → perception → memory (world_event → perceive → attention → belief/memory);
- event → relationship → story (action → relationship delta → story_thread).

### Scenario tests (новые, длинные RP)
1. **Dynamic Story (100+ раундов)**: сюжет обновляется; завершённые цели уходят
   из active state; прогресс сохраняется; новые линии появляются; Original Plot
   не искажается.
2. **Relationship**: A любит B, B нейтрален → A не получает автоматической
   взаимности; поведение B влияет на A; resentment/affection меняются постепенно.
3. **Perception**: A в комн.1, B в комн.2, C в комн.3 — A слышит только своё;
   A не знает визуальные события комн.2; B не знает событий комн.3;
   частичная слышимость корректна (muffled).
4. **Memory**: важное событие 100+ раундов назад → находится semantic retrieval;
   anchor активируется; нерелевантные памяти не забивают контекст.
5. **Consolidation**: idle чат не консолидируется; активный — по threshold;
   critical event — немедленно.

### Регрессионный барьер
- `pytest` полный: 771 passed / 28 pre-existing (текущий набор) без роста
  числа падений после каждого спринта.
- Golden-тесты `tests/golden/*` — снэпшоты промпта; при изменениях блоков
  снэпшоты переснимаются только для новых блоков и под флагом.

---

## 28. Risks

| # | Риск | Уровень | Митигация |
|---|---|---|---|
| R1 | Регрессия location isolation | high | perceive() не трогаем до Sprint 13; attention/beliefs пишутся только из персонального perception |
| R2 | Cross-character knowledge leakage | high | belief update только из own perception; context рендерит только собственные beliefs; golden-тесты isolation |
| R3 | Memory attribution errors | medium | witness-фильтр остаётся; event_id обязателен для episodic/social |
| R4 | Excessive context size | medium | подбюджеты блоков; top-K caps (beliefs/threads/anchors); резерв |
| R5 | LLM hallucinated story state | high | grounding (события окна), confidence, original_plot diff, rollback |
| R6 | Relationship oscillation | medium | caps дельт сохраняются; dynamic decay медленный; gating не отключается |
| R7 | Excessive emotional changes | medium | emotion caps; LLM-нормализация в пределах caps |
| R8 | Plot становится слишком детерминированным | medium | crisis = вероятность; intent = тенденция; никаких форсированных событий |
| R9 | Excessive consolidation cost | medium | score-пороги; дедупликация critical; ограничение частоты |
| R10 | Excessive retrieval cost | medium | rerank без LLM; ANN — P3; лимиты кандидатов |
| R11 | Миграции на живых данных | medium | идемпотентность, batch-бэкафиллы, отчёт, откат |
| R12 | Изменение промпта меняет генерацию | medium | канареечный запуск, eval-сравнение, golden-пересъёмка под флагом |
| R13 | Sensors выходит из строя (недоступна/timeout/битый JSON) | medium | try/except на каждый sensor-вызов; детерминированный fallback; `SENSORS_MODEL` пуст → слой выключен; тесты degradation |
| R14 | Sensors «подменяет» истину (решает за движок) | high | принцип §5.1.4: Sensors только предлагает; все изменения через существующие правила/gates; тесты «Sensors не пишет в БД», «результат проходит через правила» |
| R15 | Случайное использование Sensors-модели в генерации реплик | high | изоляция §5.1.5: генерация использует только `chat.model_name`; тест «Sensors не подменяет генерацию»; греп на `sensors_model` вне `sensors_service` |
| R16 | Рост числа LLM-вызовов от Sensors | medium | лимит per-round (≤1 на задачу), учёт в §24.1, флаги по задачам, минимальный контекст |

---

## 29. Open Questions

1. **Порог важности события** для event extraction (importance ≥ 4?) — нужен
   эмпирический замер на реальных чатах.
2. **Кто задаёт story phases**: пользователь (PATCH) или движок (консолидация
   предлагает, пользователь подтверждает)? По умолчанию — пользователь может
   задать, движок предлагает через `phase_change`.
3. **Beliefs и player**: игрок имеет beliefs? Сейчас рёбер player→NPC нет.
   Предложение: игрок = «World Truth интерфейс», NPC = beliefs. Уточнить.
4. **Частота event extraction** при коротких раундах — всегда или только при
   ≥2 репликах? (по образцу memory `len(round_snapshots) < 2 → skip`).
5. **Что делать с `relationship_analyzer_prompt`** (мёртвый конфиг) — удалить.
6. **Emotional anchors vs relationship trajectory** — не дублируются ли? Якорь =
   событие с эмоцией; trajectory = дельты. Уточнить рендер (оба, но anchors
   только при активации).
7. **Нужен ли player emotion state** — вероятно нет (игрок = пользователь).
8. **`context_message_embedding_enabled`** — включать ли эмбеддинги сообщений
   для retrieval событий (P3)?
9. **Sensors и провайдеры**: если Ollama недоступна для `SENSORS_MODEL`
   (модель не загружена), а основная модель работает — sensors-задачи просто
   пропускаются (fallback). Нужен ли отдельный retry/свой таймаут для Sensors
   (меньше, чем у генерации)?
10. **Sensors задержка**: sensor-вызовы в пост-раунд pipeline асинхронны, но
    при включённых `sensors_*_enabled` увеличивают время пост-раунда. Нужен ли
    timeout на sensor-задачу отдельно от `ollama_timeout`?

---

## 29.1 Observability (debug API/UI)

Новый read-only отладочный контур, чтобы видеть state-driven внутренности без
grep по БД. Отдельные GET-эндпоинты (только чтение, не менять состояние):

| Endpoint | Отдаёт |
|---|---|
| `GET /chats/{id}/debug/state` | сводка: story_states, character_states, beliefs, intents, активные story_threads |
| `GET /chats/{id}/debug/beliefs?character_id=` | beliefs персонажа (тип, confidence, world_truth_ref) |
| `GET /chats/{id}/debug/threads` | активные/архивные story_threads |
| `GET /chats/{id}/debug/events?limit=` | event-граф: world_events + event_links (причинность) |
| `GET /chats/{id}/debug/anchors?relationship_id=` | memory_anchors отношения |
| `GET /chats/{id}/debug/pipeline` | последний пост-раунд pipeline: какие stages выполнены, ошибки, LLM-бюджет |

Реализация:
- новый `app/routers/debug.py`, `APIRouter`, все endpoints `GET` только;
- маппинг на существующие таблицы через `crud.py` (никакой новой БД);
- **debug UI**: минимальная HTML-страница `app/static/debug.html` (или отдельный
  эндпоинт `/debug/{chat_id}`), отображающая сводку state; входит в Sprint 13
  (контекст), но эндпоинты — по мере появления таблиц (Sprint 1 events, 3
  character_state, 5 beliefs, 8 story, 10 intent);
- **безопасность**: только localhost/с включённым `debug_enabled`; отсутствие —
  прежнее поведение.
- Уже существующая observability (jobs API, relationship timeline, `generation_debug`,
  summary-словарь `_analyze_and_update_relationships`) сохраняется; debug-контур
  их дополняет, не дублируя.

---

## 29.2 ADR: пересмотр решения «не делать belief system»

**Контекст**: `docs/relations.md §22` фиксировал «Не реализовывать: полноценную
belief-system». На этом плане решение **пересматривается** в части
структурированных beliefs как данных.

| Вопрос | Решение |
|---|---|
| Почему старое решение было разумным | MVP без beliefs был дешевле; epistemic mask покрывал «не знать числа», изоляция держалась на presence |
| Почему пересмотрен | задачи постановки (beliefs, вторичная интерпретация, эмпирические знания, сомнение) требуют структурированного «что персонаж считает» с источником и уверенностью; mask не хранит источник/confidence и не различает fact/belief/suspicion |
| Что меняется | вводится таблица `beliefs` + детерминированный pipeline (§9) |
| Что НЕ меняется | принцип изоляции: belief пишется только из персонального perception→attention; mask остаётся fallback под флагом; «World Truth» по-прежнему не выдаётся персонажу |
| Обновление документа | `docs/relations.md` §22 дополнить строкой: «решение по belief-system пересмотрено — см. Plans/update20.md §9, §29.2» |
| Owner / Status | Спринт 5, `beliefs_enabled=false` по умолчанию; Accepted после benchmark gate |

---

## 30. Definition of Done

Общие критерии для каждого спринта:
- [ ] Флаг-канарейка, по умолчанию выключен (кроме Sprint 13 после canary).
- [ ] Флаг отнесён к профилю §24.2; `legacy`-путь не сломан (golden).
- [ ] Миграции идемпотентны и откатываемы; бэкафиллы с отчётом.
- [ ] Unit + integration + scenario-тесты спринта написаны и зелёные.
- [ ] Полный `pytest`: 771 passed / 28 pre-existing без роста числа падений.
- [ ] Golden-снэпшоты: изменены только блоки, попадающие под новый флаг.
- [ ] Изоляция ролей/локаций не регрессировала (golden-набор Phase 6–7).
- [ ] Новые LLM-вызовы имеют бюджет/лимит и не блокируют раунд (try/except).
- [ ] **LLM-бюджет**: раздел §24.1 обновлён строкой «+N вызовов/раунд»;
      в синхронный путь вызовы не добавляются.
- [ ] **Quality metric**: ≥1 метрика качества + eval-сценарий обновлены (§27),
      сравнение до/после зелёное; модель прошла benchmark gate (§27) для
      LLM-полей.
- [ ] **Observability**: debug-эндпоинты для новых таблиц добавлены (§29.1).
- [ ] **Sensors** (когда слой активен): `SENSORS_MODEL` из `.env`; sensor-вызовы
      идут только на Sensors-модель и только через `SensorsService`; генерация
      персонажей использует основную модель; некорректный JSON/timeout →
      fallback без падения цикла; Sensors не пишет в БД; результат проходит
      через игровые правила (§5.1.10, R13–R15).
- [ ] Документация обновлена (`docs/architecture.md`, `docs/database.md`,
      `docs/relations.md`, `README.md` при необходимости).
- [ ] Откат проверен тестом (флаг off → прежнее поведение).

---

# A. Таблица всех предложенных изменений

| Feature | Current State | Target State | Priority | Dependencies | Sprint |
|---|---|---|---|---|---|
| Structured World Events | `world_events` без причинности/важности | action/importance/salience + `event_links` + extraction | P0 | — | 1 |
| Memory types | flat `memories` + category | memory_type + event_id + valence/intensity | P0 | 1 | 2 |
| Emotional anchors | нет (только events в trajectory) | `memory_anchors`, активация top-K | P1 | 2, 3 | 2/7 |
| Character State | только глобальный mood/tension | `character_states` (emotion/stress/physical/attention/goals) | P0 | 1 | 3 |
| Attention | нет | `attention.py` + score + фильтр | P1 | 3 | 4 |
| Belief System | MVP epistemic mask | `beliefs` + pipeline + confidence | P0 | 1,3,4 | 5 |
| Hybrid Retrieval v2 | BM25+vector+RRF | + reranking (salience/story/relationship) | P1 | 2,3,8 | 6 |
| Dynamic Story State | статичный general_prompt | original_plot + current story + phase + history | P0 | 1,2 | 8 |
| Story Consolidation | нет | LLM-update с валидацией/rollback | P1 | 8 | 9 |
| Plot Engine | нет | `app/plot/` (threads, pressure, consolidation) | P1 | 8,9,7 | 8–11 |
| Crisis Engine | нет | pressure/candidate/evaluation/resolution | P2 | 7,8,9 | 11 |
| Adaptive Consolidation | 24h таймер | score soft/hard/critical | P1 | 2,7,9 | 12 |
| NPC Intent | нет (active_goals пользовательские) | intent layer перед генерацией | P1 | 3,5,8 | 10 |
| NPC Plans | нет | `npc_plans` маленькие планы | P2 | 3,8 | 10 |
| Relationship Evolution v2 | фикс. decay, без beliefs | dynamic decay + reciprocity pipeline + anchors | P1 | 3,5,2 | 7 |
| Perception 2.0 | visual/audio/addressed/remote | + identity/semantic/intent/certainty/distance/source | P2 | 4 | (после 13) |
| Context Builder v2 | scene/recent/retrieval/memories | WORLD/KNOW/PERCEIVE/STATE/REL/G/MEMORY/STORY | P1 | все | 13 |
| WorldState объединение | `PerceptionWorldState` минимальный | `world_state.py` агрегатор (без монолита) | P2 | 8,9,3 | 10–13 |
| Canonical event source | world_events = журнал; проекции без связи | world_events = единственный источник истины; все потребители — проекции через event_id | P0 | 1 | 1+ |
| Post-round pipeline | пост-раунд инлайн в chat_engine (~2500 строк) | `post_round_pipeline.py` оркестратор stages | P0 | 1 | 1 |
| Feature profiles | ~18 независимых флагов | `legacy` / `story_lite` / `full_state_driven` связки | P0 | 0–13 | 0+ |
| Quality metrics | только регрессия (771/28) | eval-метрики качества + benchmark gate | P0 | 1,6,7,8,13 | каждый |
| Observability debug | нет | debug API + UI (state/beliefs/threads/events/pipeline) | P1 | 1,3,5,8,10,13 | 1+ |
| **Sensors Model** | нет; все фоновые анализы — основная модель | отдельная `SENSORS_MODEL` (из `.env`), `SensorsService`, JSON-schema, graceful degradation; предложения проходят через правила движка | P0 | 0 | 0+ |
| Sensors: perception | детерминированный `perceive()` (без LLM) | Sensors предлагает потенциальное восприятие; доступность решает движок | P1 | 0,4 | 4+ |
| Sensors: event classification | event extraction основной моделью | Sensors предлагает тип/участников/importance; движок пишет события | P1 | 0,1 | 1+ |
| Sensors: emotion/mood | deterministic `emotion_engine` | Sensors возвращает `{emotion, intensity, mood_delta}`; применяется через caps | P1 | 0,3 | 3+ |
| Sensors: memory candidates | memory extraction основной моделью | Sensors предлагает `{facts}`; валидация/witness/лимиты — движок | P1 | 0,2 | 2+ |
| Sensors: relationship | relationship analyzer основной моделью | Sensors предлагает дельты; gating/caps/decay — движок | P1 | 0,7 | 7+ |

---

# B. Sprint Roadmap

```text
Sprint 0  — Подготовка: schema foundation, backfill (P0) ✅ (2026-08-04)
Sprint 1  — Structured World Events (P0) ✅ (2026-08-05)
Sprint 2  — Memory Architecture v2: типы + якоря (P0) ✅ (2026-08-05)
Sprint 3  — Character State (P0) ✅ (2026-08-05)
Sprint 4  — Attention (P1) ✅ (2026-08-05)
Sprint 5  — Belief System (P0)
Sprint 6  — Hybrid Retrieval v2 (P1) ✅ (2026-08-05)
Sprint 7  — Relationship Evolution v2 (P1) ✅ (2026-08-05)
Sprint 8  — Dynamic Story State (P0)
Sprint 9  — Story Consolidation (P1)
Sprint 10 — Plot Engine + NPC Intent + NPC Plans (P1)
Sprint 11 — Crisis Engine (P2)
Sprint 12 — Adaptive Consolidation (P1)
Sprint 13 — Context Builder Evolution (P2)
(опционально) — Perception 2.0 расширение PerceivedResult, WorldState-агрегатор
```

---

# C. Архитектурная схема

```text
SENSORS MODEL (SensorsService, SENSORS_MODEL из .env) ──► предложения
   │  (perception/event/emotion/memory/relationship)     (JSON, валидация)
   ▼
WORLD STATE (tables: chats, locations, characters, scene_states, story_states)
   ↓
WORLD EVENTS (world_events + event_links) ──► event graph
   ↓
PERCEPTION (perceive → PerceptionResult) ──► присутствие, изоляция
   ↓
ATTENTION (attention score) ──► что входит в сознание
   ↓
BELIEF UPDATE (belief_service → beliefs)
   ↓
EMOTION UPDATE (emotion_engine → character_states)
   ↓
RELATIONSHIP UPDATE (relationship_service + anchors)
   ↓
STORY UPDATE (plot/story_state + story_events + consolidation)
   ↓
CHARACTER STATE (unified runtime state)
   ↓
CHARACTER INTENT (plot/intent → npc_plans)
   ↓
CONTEXT BUILDER (budgeted blocks)
   ↓
MAIN MODEL (ollama_client: текст + take_actions)   ← генерация персонажей,
    ↓                                                  всегда основная модель
ACTION / RESPONSE (apply_character_actions → WorldEvent)
    ↓
WORLD EVENT (append)   ──► world_events = единственный источник истины
    ↓                        (проекции через event_id: memories /
WORLD STATE UPDATE            relationship_events / story_events /
    (post-round pipeline)     anchors / beliefs)
```

---

# D. Список файлов

## Существующие файлы (изменяемые по спринтам)

| Файл | Спринты |
|---|---|
| `app/models.py` | 0,1,2,3,4,5,7,8,10 |
| `app/database.py` | 0,1,2,3,4,5,8,10,12 |
| `app/crud.py` | 0,1,2,3,4,5,7,8,10,12 |
| `app/config.py` | 0–13 |
| `app/schemas.py` | 0–13 |
| `app/chat_engine.py` | 0–13 |
| `app/context_builder.py` | 2,3,4,5,6,7,8,10,11,13 |
| `app/context_budget.py` | 13 |
| `app/prompt_builder.py` | 2,3,5,7,8,10,13 |
| `app/ollama_client.py` | 0 (Sensors: существующий `_invoke_llm`/`format`),1,2,3,5,9,13 |
| `app/memory_service.py` | 2,4,6,12 |
| `app/relationship_service.py` | 2,5,7,12 |
| `app/relationship_interpreter.py` | 5,7 |
| `app/witness_model.py` | 4,13 |
| `app/perception.py` | 4,(post-13 Perception 2.0) |
| `app/round_engine.py` | 10,13 |
| `app/main.py` | 12 |
| `app/routers/chats.py` | 8,9 |
| `app/routers/relationships.py` | 5 (beliefs в mask) |
| `app/prompts/ru.json` | 1,2,3,5,9,13 |

## Новые файлы

| Файл | Спринт |
|---|---|
| `scripts/backfill_plot_fields.py`, `scripts/backfill_event_location_ids.py` | 0 |
| `app/event_service.py`, `app/post_round_pipeline.py` | 1 |
| `app/routers/debug.py`, `app/static/debug.html` | 1+ (растёт по спринтам) |
| `tests/eval/scenarios/benchmark_structured.jsonl` | 5 |
| `docs/benchmarks/` (результаты benchmark gate) | 5,9,11 |
| `app/character_state.py`, `app/emotion_engine.py` | 3 |
| `app/attention.py` | 4 |
| `app/belief_service.py` | 5 |
| `app/plot/__init__.py`, `app/plot/story_state.py`, `app/plot/story_events.py` | 8 |
| `app/plot/story_consolidation.py` | 9 |
| `app/plot/plot_pressure.py`, `app/plot/intent.py`, `app/plot/story_threads.py` | 10 |
| `app/npc_plans.py` | 10 |
| `app/plot/crisis_engine.py` | 11 |
| `app/world_state.py` | 10–13 |
| `app/emotion_service.py` (если вынесем из engine) | 3 |
| `app/sensors_service.py` (SensorsService, §5.1) | 0 |
| `app/sensors/__init__.py`, `app/sensors/schemas.py` (JSON-схемы задач) | 0 |
| `tests/test_sensors.py` | 0 |
| `.env.example` (добавить `SENSORS_MODEL=`) | 0 |

## Новые тестовые файлы

`tests/test_event_extraction.py`, `tests/test_post_round_pipeline.py`,
`test_memory_types.py`, `test_memory_anchors.py`,
`test_character_state.py`, `test_attention.py`, `test_beliefs.py`,
`test_hybrid_rerank.py`, `test_relationship_decay_dynamic.py`,
`test_relationship_reciprocity_v2.py`, `test_story_state.py`,
`test_story_consolidation.py`, `test_intent.py`, `test_npc_plans.py`,
`test_sensors.py` (SENSORS_MODEL из .env; используется именно Sensors-модель,
а не основная; генерация персонажей не подменяется; корректный JSON — ок;
некорректный JSON/timeout — fallback без падения цикла; Sensors не пишет в БД;
результат проходит через игровые правила; при off — legacy-поведение),
`test_crisis_engine.py`, `test_adaptive_consolidation.py`,
`test_context_v2.py`, `tests/scenarios/test_scenario_dynamic_story.py`,
`test_scenario_relationship.py`, `test_scenario_perception.py`,
`test_scenario_memory.py`, `test_scenario_consolidation.py`.

---

# E. Database Migration Plan

Порядок миграций (все идемпотентны, через `ensure_schema`):

1. **Sprint 0** — `chats.+original_plot/story_prompt/story_enabled`; `world_events.+location_id`
   (nullable FK); 10 новых таблиц (`character_states`, `beliefs`, `story_states`,
   `story_threads`, `story_events`, `event_links`, `memory_anchors`, `intents`,
   `npc_plans`, `consolidation_state`) — CREATE IF NOT EXISTS. Backfill:
   original_plot/story_prompt из general_prompt; world_events.location_id из
   `location`.
2. **Sprint 1** — `world_events.+action/importance/story_salience/emotional_salience`;
   `event_links` (в Sprint 0 уже создана — здесь write-path);
   `relationship_events.+event_id` FK nullable (проекция канонического события);
   `story_events.+event_id` FK (проекция, если таблица создана в Sprint 0).
3. **Sprint 2** — ✅ `memories.+memory_type/event_id/valence/intensity` (default
   memory_type='semantic'); `memory_anchors` write-path; индексы:
   `ix_memories_char_type (character_id, memory_type)`,
   `ix_memories_event (event_id)`, `ix_anchors_rel (relationship_id)`.
4. **Sprint 3** — `character_states` (unique character_id); индексы нет (мало строк).
5. **Sprint 4** — `message_presence.+attention REAL NULL`; индекс нет.
6. **Sprint 5** — `beliefs` + индексы `ix_beliefs_char (character_id)`,
   `ix_beliefs_subject (subject)`.
7. **Sprint 7** — `character_relationships.+decay_profile TEXT NULL` (опционально;
   предпочтительно конфиг).
8. **Sprint 8** — `story_states.+version INTEGER default 1`; write-path.
9. **Sprint 12** — `consolidation_state` counters; `memory_jobs` новых типов
   (`adaptive_consolidation`), job_type TEXT без CHECK — совместимо.
10. **Sprint 13** — без миграций (context v2).

Все ALTER — `IF NOT EXISTS` (проверка через PRAGMA table_info), CREATE —
`IF NOT EXISTS`. Backfill-скрипты отдельно, с отчётом, exit 1 при неоднозначных
случаях (по образцу `backfill_location_ids`).

---

# F. Risk Register

| ID | Риск | Impact | Likelihood | Mitigation | Owner |
|---|---|---|---|---|---|
| R1 | Регрессия location isolation | high | medium | perceive() не трогаем; golden Phase 6–7; attention/beliefs из own perception | Perception |
| R2 | Cross-character knowledge leakage | high | medium | beliefs только из персонального восприятия; witness-фильтр; контекст — только свои beliefs | Belief/Context |
| R3 | Memory attribution errors | medium | medium | event_id обязателен; witness-фильтр `present|told`; validation | Memory |
| R4 | Excessive context size | medium | medium | подбюджеты, top-K, резерв, отсечение по приоритету | Context |
| R5 | LLM hallucinated story state | high | medium | grounding, confidence, original_plot diff, rollback, versioning | Story |
| R6 | Relationship oscillation | medium | medium | caps, dynamic decay, gating не отключается | Relationship |
| R7 | Excessive emotional changes | medium | low | caps эмоций, LLM-нормализация в пределах caps | CharacterState |
| R8 | Plot too deterministic | medium | medium | кризис = вероятность; intent = тенденция | Plot |
| R9 | Excessive consolidation cost | medium | medium | score, дедупликация critical, лимит частоты | Consolidation |
| R10 | Excessive retrieval cost | medium | low | rerank без LLM; ANN P3; лимит кандидатов | Retrieval |
| R11 | Миграции на живых данных | medium | medium | идемпотентность, batch, отчёт, откат | Database |
| R12 | Prompt change alters generation | medium | high | канарейки, eval, golden под флагом | Context/Gen |
| R13 | Sensors out of service (недоступна/timeout/битый JSON) | medium | medium | try/except на каждый вызов; детерм. fallback; пустой `SENSORS_MODEL` → выключено; degradation-тесты | Sensors |
| R14 | Sensors «решает за движок» (становится источником истины) | high | medium | принцип §5.1.4; все изменения через правила/gates; тесты «не пишет в БД»/«через правила» | Sensors/Engine |
| R15 | Случайное использование Sensors-модели в генерации | high | low | изоляция §5.1.5; тест «не подменяет генерацию»; греп `sensors_model` вне `sensors_service` | Sensors/Gen |
| R16 | Рост числа LLM-вызовов от Sensors | medium | medium | лимит per-round (≤1/задача), §24.1, флаги по задачам, минимальный контекст | Sensors |

---

# G. Рекомендуемый порядок реализации

**Порядок: Sprint 0 → 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9 → 10 → 11 → 12 → 13.**

Обоснование:

1. **Sprint 0 и 1 (события)** — фундамент. Без event-графа memory/beliefs/story
   не имеют источника истины. Sprint 0 чисто данных, без поведения — нулевой
   риск регрессий.
2. **Sprint 2–3 (память-типы и character state)** — два независимых столпа:
   «что персонаж помнит» и «в каком он состоянии». Оба читают события.
3. **Sprint 4–5 (attention → beliefs)** — фильтр входа, затем субъективное
   знание. Логическая цепочка perception→attention→belief.
4. **Sprint 6 (retrieval)** — использует типы памяти; улучшает качество
   контекста до того, как контекст начнёт получать новые блоки.
5. **Sprint 7 (relationships v2)** — использует beliefs и anchors; минимальный
   риск, т.к. основа уже зрелая.
6. **Sprint 8–9 (story)** — сюжет требует событий, памяти и отношений;
   story consolidation — сразу после state.
7. **Sprint 10 (intent/plans/plot)** — после story; цели персонажей связаны
   с сюжетом.
8. **Sprint 11 (crisis)** — потребляет pressure из отношений/сюжета/состояния.
9. **Sprint 12 (adaptive consolidation)** — связывает всё в единый цикл
   обслуживания.
10. **Sprint 13 (context v2)** — последняя интеграция: все подсистемы готовы,
    промпт собирается из них единообразно.

Этот порядок минимизирует архитектурный долг (каждая система читает
существующие, а не параллельные данные), минимизирует риск регрессий
(флаги + golden + канарейки) и не требует глобального рефакторинга
существующего работающего движка.

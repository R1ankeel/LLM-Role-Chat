# План декомпозиции монолитов AI Roleplay Chat

> Статус: **черновик для обсуждения**
> Дата: 2026-08-02
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
   (в проекте ~45 тестовых файлов + golden-снапшоты + eval-harness) и
   `python -m compileall app` / запуск сервера.
3. **Сначала развязка зависимостей, потом разбиение файлов** — нельзя резать файл,
   пока в нём остались циклические/частные импорты наружу.
4. **Поэтапно, приоритет по выгоде** — начинаем с самого крупного и самого связного
   (`chat_engine.py`, `ollama_client.py`), затем сервисы и CRUD, затем frontend.

---

## 2. Метрики и критерии кандидатов

Пороговые значения (по результатам замера кода):

| Критерий | Порог |
|---|---|
| Размер файла | > 800 строк или > 30 КБ |
| Число зон ответственности в файле | > 2 |
| Число внутренних модулей, на которые импортирует файл | > 6 |
| Наличие циклических импортов с другими модулями | любой |
| Использование приватных (`_`) функций других модулей | любое |

---

## 3. Карта текущей архитектуры

```
app/
  main.py                  # FastAPI app, lifespan, фоновые воркеры (mem jobs, consolidation scheduler)
  config.py                # настройки
  database.py              # engine + ВСЯ DDL-схема инлайн (ensure_schema) + сессии
  models.py                # ORM-модели
  schemas.py               # ВСЕ Pydantic-схемы
  crud.py                  # ВСЕ DB-операции (8+ доменов)
  chat_engine.py           # ОРКЕСТРАТОР: streaming-пайплайн + отношения + регенерация
  ollama_client.py         # HTTP-транспорт + streaming + генерация + извлечение памяти/сцен
  memory_service.py        # BM25 + извлечение + консолидация + суммы + фоновые джобы
  relationship_service.py  # CRUD отношений + дельты + issues + decay + блоки контекста
  relationship_analyzer.py # LLM-анализ отношений (single + batch)
  relationship_interpreter.py
  repetition_detector.py   # эвристики повторов
  context_builder.py       # сборка контекста, retrieval, обрезка
  context_state.py / context_budget.py
  prompt_builder.py        # генераторы блоков промптов
  witness_model.py / perception.py  # модель «свидетелей» и восприятия
  role_isolation.py
  task_queue.py / generation_tracker.py / ratelimit.py / token_counter.py
  embedding_service.py / avatar_service.py
  routers/                 # HTTP-слой (chat_engine, chats, characters, relationships, jobs)
app/static/                # ЛЕГАСИ-SPA: app.js (2621 стр.), style.css, index.html
frontend/src/              # новый Vue 3 + Pinia frontend
  components/{characters,chat,common,layout,scene,settings}/
  stores/  api/  mocks/  types/  utils/  composables/
```

---

## 4. Кандидаты на декомпозицию — Backend

### 4.1 `app/chat_engine.py` — 2044 строки (89,9 КБ) — **приоритет 1**

Самое крупное и связное место. Один файл = оркестратор всего раунда.

| Диапазон | Функция | Строк | Ответственность |
|---|---|---|---|
| 287–895 | `process_user_message_streaming` | ~610 | основной streaming-пайплайн: SSE, присутствие, память, отношения, повторы, ретраи |
| 895–1312 | `_analyze_and_update_relationships` | ~420 | анализ отношений после раунда |
| 1312–1640 | `_run_per_pair_analysis` | ~330 | попарный анализ (соревнование и т.п.) |
| 1640–1671 | `_compute_hearsay_effective_cap` | ~30 | слухи/эффективный предел |
| 1671–1694 | `process_user_message` | ~25 | non-streaming точка входа |
| 1694–2044 | `regenerate_message_streaming` | ~350 | регенерация сообщения |

**Выделяемые модули** (`app/pipeline/`):

- `pipeline/streaming.py` — `process_user_message_streaming` (конвейер сообщения).
- `pipeline/regeneration.py` — `regenerate_message_streaming`.
- `pipeline/relations.py` — `_analyze_and_update_relationships`, `_run_per_pair_analysis`,
  `_compute_hearsay_effective_cap` (переезжают ближе к `relationship_service`).
- `pipeline/session.py` — `process_user_message`, общие хелперы раунда.

**Условие до резки:** удалить зависимость от частных функций — через `pipeline/*`
импортировать только публичные API модулей.

---

### 4.2 `app/ollama_client.py` — 1877 строк (68,7 КБ) — **приоритет 1**

Транспорт + генерация + извлечение задач в одном файле.

| Диапазон | Функция | Ответственность |
|---|---|---|
| 67–390 | `_ConfigProxy` | прокидывание конфига (кандидат на удаление — `config.py` уже покрывает) |
| 394–505 | `_call_ollama`, `_call_ollama_chat`, `_read_ollama_error` | HTTP-транспорт |
| 505–691 | `_stream_ollama_generate`, `_stream_ollama_chat` | SSE-стриминг от Ollama |
| 691–1390 | `_invoke_llm`, `_generate_once`, `generate`, `_rank` | генерация: ретраи, role-isolation, повторы, анти-мимикрия, feedback-петли |
| 1651–1746 | `extract_memories_for_character`, `summarize_for_character`, `extract_memories_unified`, `extract_scene_state` | «задачи» извлечения/суммаризации |

**Выделяемые модули** (`app/llm/`):

- `llm/transport.py` — `_call_*`, `_stream_*`, `_read_ollama_error`, `_ConfigProxy`.
- `llm/generation.py` — `_invoke_llm`, `_generate_once`, `generate`, `_rank`,
  ретраи и feedback-петли.
- `llm/tasks.py` — извлечение памяти, суммаризация, scene-state (здесь же пересечение
  с `memory_service` и `crud`).

**Важно:** `relationship_analyzer.py` сейчас импортирует приватные
`_invoke_llm`, `_extract_json_payload` из `ollama_client`. До резки вводим
публичный фасад `llm.generation.invoke_json(...)` и переводим потребителя на него.

---

### 4.3 `app/relationship_service.py` — 1452 строки (54,1 КБ) — **приоритет 2**

| Ответственность | Функции |
|---|---|
| CRUD отношений | `get_or_create_relationship`, `get_relationship`, `list_*`, `update_relationship_fields` (97–299) |
| Применение дельт | `apply_delta` (299–420) |
| Блоки контекста | `build_relationships_block`, `build_behavior_drivers_block`, `build_epistemic_mask_block` (463–630) |
| Issues (создание/резолв/тики/boost) | 702–1096 |
| Decay и прунинг событий | `apply_decay`, `prune_relationship_events` (1096–1259) |
| Кормление памяти из событий | `_maybe_create_memory_from_event`, `_maybe_create_memory_from_resolved_issue` (1259–1409) |
| Траектория | `get_trajectory_events` (1409+) |

**Выделяемые модули** (`app/relationships/`):

- `relationships/crud.py`, `relationships/deltas.py`, `relationships/issues.py`,
  `relationships/decay.py`, `relationships/blocks.py`, `relationships/memory_feed.py`,
  `relationships/trajectory.py`.

**Условие:** внутри `relationship_service` уже есть локальные импорты `crud`,
`schemas.MemoryCreate` внутри функций — это следы цикла `relationship_service ↔ crud`,
которые нужно зафиксировать до переноса.

---

### 4.4 `app/memory_service.py` — 1233 строки (42,1 КБ) — **приоритет 2**

| Ответственность | Диапазон |
|---|---|
| BM25-поиск | класс `SimpleBM25` (177–589, ~410 строк) |
| Извлечение и сохранение памяти | `_extract_and_save_memories` (589–737) |
| Суммаризации | `_maybe_update_summaries` (737–826) |
| Post-round джобы | `process_post_round`, `_process_post_round_job` (826–910) |
| Консолидация/кластеризация | `_cluster_memories_by_similarity`, `_merge_memory_cluster_llm`, `_consolidate_character_memories`, `consolidate_memories_job` (910–1140) |
| Embedding-джобы | 1140–1233 |

**Выделяемые модули** (`app/memory/`):

- `memory/retrieval.py` — `SimpleBM25` и гибридный поиск (учитывая, что `crud.py`
  содержит `get_hybrid_memories_for_characters` — вероятно, его пора перенести сюда).
- `memory/extraction.py`, `memory/consolidation.py`, `memory/summaries.py`,
  `memory/jobs.py`.

**Условие:** разорвать цикл `crud ↔ memory_service` (см. §7.1). `SimpleBM25` не
зависит от ORM — переносится первым как чистый модуль.

---

### 4.5 `app/crud.py` — 1224 строки (44,2 КБ) — **приоритет 2**

Единый «бог БД» для 8+ доменов:

- Chats (`create_chat`…`clear_chat_memories`, 25–95)
- Characters (95–247)
- Messages (247–363)
- Memories + witness/важность/decay (363–825)
- Summaries (825–886)
- Presence (886–1004)
- Locations + Scene state (1004–1149)
- Rounds (1149–1224)

**Выделяемые модули** (`app/crud/` — тонкий слой доступа, по домену):

- `crud/chats.py`, `crud/characters.py`, `crud/messages.py`, `crud/memories.py`,
  `crud/summaries.py`, `crud/presence.py`, `crud/scene.py`, `crud/rounds.py`.

Файл-агрегатор `crud/__init__.py` реэкспортирует публичные функции, чтобы существующие
импорты (`from . import crud`) продолжали работать без правки всех потребителей на
первом шаге.

---

### 4.6 `app/database.py` — 630 строк (27,2 КБ)

`ensure_schema` (110–599) содержит ~490 строк DDL-схемы — это скрытая «миграция».
Выделить:

- `db/engine.py` — engine, pragma, `init_db`, сессии.
- `db/schema.py` — вся DDL из `ensure_schema` (алгоритм остаётся идемпотентным).
- `db/models.py` — переезд `models.py` рядом с БД (опционально).

### 4.7 `app/repetition_detector.py` — 808 строк (27,6 КБ)

Один файл = пакет эвристик:
- извлечение действий (`extract_actions`, `_match_actions_in_text`),
- текстовые/лексические скоринг-функции,
- cooldown, interaction-loop, progression/stagnation,
- итоговая сборка `analyze_response` + feedback-блоки.

**Выделяемые модули** (`app/repetition/`): `actions.py`, `scoring.py`,
`analyzer.py`, `feedback.py`. (Порядок ниже по приоритету — файл хорошо изолирован,
но велик.)

### 4.8 `app/context_builder.py` — 677 строк (26,6 КБ)

`ContextBuilder.build` (82–382) — сборка; `_select_retrieved`/`_trim_*` — отбор и
обрезка; `_load_presence_map` — обращение к БД.
**Выделяемые модули** (`app/context/`): `retrieval.py`, `assembly.py`,
`trimming.py`. Файл уже разбит на `_методы`, перенос механический.

### 4.9 `app/schemas.py` — 653 строки (21,3 КБ)

Все Pydantic-схемы в одном файле. **Выделяемые модули** (`app/schemas/`):
`chat.py`, `character.py`, `message.py`, `memory.py`, `relationship.py`,
`scene.py`, `context.py`, `job.py`. Схемы взаимно импортируют друг друга
(напр. `BuiltContext`, `IssueDelta`, `RelationshipDelta`) — сначала проверяем
ацикличность, затем реэкспорт через `schemas/__init__.py`.

### 4.10 `app/prompt_builder.py` — 629 строк (26,0 КБ)

Большой набор мелких `build_*`-функций (30+ функций). **Выделяемые модули**
(`app/prompt/`): `character.py` (карточка/личность/анти-мимикрия), `blocks.py`
(правила, память, диалог), `scene.py`, `extraction.py` (извлечение/суммаризация),
`relationships.py`. На первом шаге — реэкспорт из `prompt_builder.py` для совместимости.

### 4.11 `app/relationship_analyzer.py` — 619 строк (28,4 КБ)

`analyze_relationships` (single) + `analyze_batch_relationships` (batch) + парсинг.
**Выделяемые модули** (`app/relationships/analyzer/`): `single.py`, `batch.py`,
`parse.py`. Требует публичного интерфейса LLM (см. §4.2).

### 4.12 Маршрутизаторы (routers/)

- `routers/relationships.py` — 471 строка, содержит бизнес-логику поверх CRUD.
  Вынести «поведение» в сервисный слой, оставить в роутере HTTP-адаптацию.
- `routers/chat_engine.py` — 312 строк: SSE-обработка; оставить, вынести
  формирование событий/полезной нагрузки в `pipeline`.
- `routers/chats.py`, `characters.py`, `jobs.py` — нормального размера, не трогать.

---

## 5. Кандидаты на декомпозицию — Frontend

### 5.1 `frontend/src/components/characters/RelationshipPairDetail.vue` — 817 строк — **приоритет 3**

Смешивает: просмотр пары, историю, issues, траекторию, форму.
- Разбить на дочерние компоненты (`RelationshipHistory`, `IssueList`,
  `TrajectoryTimeline`, `RelationshipForm`) и composables
  (`useRelationshipPair`, `useIssueActions`).
- Несколько `<script setup>` блоков по доменам.

### 5.2 `frontend/src/components/layout/Sidebar.vue` — 737 строк — **приоритет 3**

Смешивает: список чатов, создание чата, переименование, удаление, поиск.
- Вынести `NewChatDialog.vue`, `ChatListItem.vue`, `RenameChatDialog.vue`,
  composable `useChatSidebar`.

### 5.3 `frontend/src/components/characters/RelationshipModal.vue` — 566 строк

Разбить на вкладки/подкомпоненты по тем же правилам, что и 5.1.

### 5.4 `frontend/src/mocks/data.ts` (691) и `mocks/service.ts` (588) — **приоритет 4**

Легаси-мок-слой для разработки. Когда API стабилизирован — удалить, а не
декомпозировать (проверить использование в сторах). Если оставлять — разбить по
доменам (`mocks/characters.ts`, `mocks/chats.ts`, …) и генерировать данные из
типов, а не дублировать.

### 5.5 Прочие

- `stores/messages.ts` (327), `stores/relationships.ts` (183) — нормального размера,
  следить за ростом; не трогать сейчас.
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

---

## 7. Сквозные проблемы, которые надо решить до/вместе с резкой

### 7.1 Циклические импорты

- `crud ↔ memory_service` (взаимный импорт). **План:** выделить чистые функции
  (BM25, скоринг) в `memory/retrieval.py` без ORM; «гибридный поиск» из `crud.py`
  перенести в `memory/`; `crud` оставить как низкоуровневый слой доступа, который
  НЕ импортирует сервисы.
- `relationship_service ↔ crud` (локальный импорт `crud` внутри функций).
  **План:** определить направление зависимости: сервис → crud (только). Память из
  событий создаётся через явный интерфейс `memory/` без импорта `crud` внутри.
- `task_queue ↔ memory_service` (диспетчер джобов → обработчики). **План:**
  ввести регистрацию обработчиков (паттерн `handler registry`) вместо прямого
  импорта обработчиков из `task_queue`.

### 7.2 Использование приватных функций

- `relationship_analyzer` импортирует `_invoke_llm`, `_extract_json_payload` из
  `ollama_client`. **План:** публичный фасад `llm/generation.py::invoke_json(...)`
  и перевод потребителей. После этого приватные функции можно скрыть.

### 7.3 Логика в роутерах

`routers/relationships.py` — бизнес-логика поверх CRUD. **План:** перенос в
сервисный слой (`relationships/`), роутер остаётся «тонким» (валидация + статусы).

### 7.4 Дублирование сборки контекста

Блоки отношений/поведения/issues собираются и в `relationship_service`, и в
`context_builder`, и в `prompt_builder`. **План:** единый слой `prompt/` —
генераторы строк, `context/` — композиция, сервисы — данные.

---

## 8. Целевая структура пакета `app/` (после декомпозиции)

```
app/
  main.py  config.py  ratelimit.py  generation_tracker.py
  llm/
    transport.py  generation.py  tasks.py
  pipeline/
    streaming.py  regeneration.py  relations.py  session.py
  memory/
    retrieval.py  extraction.py  consolidation.py  summaries.py  jobs.py
  relationships/
    crud.py  deltas.py  issues.py  decay.py  blocks.py  memory_feed.py  trajectory.py
    analyzer/  (single.py  batch.py  parse.py)
    interpreter.py
  context/
    builder.py  budget.py  state.py
  repetition/
    actions.py  scoring.py  analyzer.py  feedback.py
  prompt/
    character.py  blocks.py  scene.py  extraction.py  relationships.py
  perception/
    perception.py  witness.py
  db/
    engine.py  schema.py  sessions.py  models.py
  crud/
    chats.py  characters.py  messages.py  memories.py  summaries.py  presence.py  scene.py  rounds.py
  schemas/
    chat.py  character.py  message.py  memory.py  relationship.py  scene.py  context.py  job.py
  services/
    avatar.py  embeddings.py  tokens.py  task_queue.py
  routers/
    __init__.py  chat_engine.py  chats.py  characters.py  relationships.py  jobs.py
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
| 1 | Развязать циклы | §7.1: `memory/retrieval.py` (чистый BM25), фасад `llm` (7.2), registry джобов | `pytest` зелёный |
| 2 | `db/` | вынести `database.py::ensure_schema` DDL → `db/schema.py`, сессии → `db/engine.py` | `init_db` на пустой БД + `pytest` |
| 3 | `schemas/` | разбить `schemas.py` на пакет с реэкспортом | `pytest` + запуск API |
| 4 | `crud/` | разбить `crud.py` на доменные модули + реэкспорт через `__init__` | `pytest` |
| 5 | `llm/` | выделить `transport.py` из `ollama_client.py` | `pytest` |
| 6 | `pipeline/` | выделить `streaming.py` + `regeneration.py` из `chat_engine.py` (самый крупный шаг — на 2 под-этапа) | полный round-trip тест + `test_chat_engine.py`, `test_stream_disconnect.py` |
| 7 | `pipeline/relations.py` | перенести анализ отношений из `chat_engine.py` | `test_relationship_*`, `test_role_isolation.py` |
| 8 | `relationships/` | разбить `relationship_service.py` (crud → deltas → issues → decay → blocks → memory_feed) | `test_relationship_*` (14 файлов) |
| 9 | `memory/` | extraction/summaries/consolidation/jobs из `memory_service.py` | `test_memory_*`, `test_consolidation.py`, `test_task_queue.py` |
| 10 | `repetition/` | разбить `repetition_detector.py` | `test_repetition_detector.py`, `test_ollama_chat.py` |
| 11 | `context/`, `prompt/` | разбить `context_builder.py` и `prompt_builder.py` | `test_context_*`, `test_prompt_builder_golden` |
| 12 | Роутеры | вынести логику из `routers/relationships.py` в сервисный слой | `test_relationship_issues_endpoint.py` |
| 13 | Frontend Vue | §5.1–5.3 (поэлементно: Sidebar → RelationshipPairDetail → RelationshipModal) | `npm run build` (`vue-tsc`) |
| 14 | Legacy static | §6: удалить/заархивировать `app/static/app.js` | ручная проверка Vue-сборки через сервер |
| 15 | Чистка | удалить реэкспорт-фасады, если не нужны; актуализировать `docs/` | полный `pytest` + eval |

---

## 10. Риски и стратегия

| Риск | Смягчение |
|---|---|
| Регрессия streaming-пайплайна (SSE, отключение клиента, ретраи) | этап 6 на два под-этапа; прогон `test_stream_disconnect.py`, golden `tests/golden/*`, eval `tests/eval/` |
| Скрытая миграция схемы (`ensure_schema`) сломается при переносе | этап 2 на копии `ai_chat.db`; идемпотентность сохраняется, не меняем SQL |
| Реэкспорт-фасады замаскируют старые импорты | фасады временные; в этап 15 удаляются, контролируются `rg "from . import crud"` |
| Циклы импортов всплывут после переноса | перед каждым переносом — статический обход зависимостей; направление: роутер → сервис → crud/db |
| `SimpleBM25` извлечение из memory_service меняет численные результаты | перенос без изменений кода; golden-тесты по памяти |
| Legacy frontend и Vue конфликтуют в static | выключение legacy только после полного покрытия маршрутов Vue-сборкой |

---

## 11. Критерии завершения

1. Нет файлов > 600 строк в `app/` (кроме `prompts/ru.json` и static-ассетов).
2. Нет циклических импортов (`python -c "import app.main"` + статический анализ).
3. Приватные функции не пересекают границы модулей.
4. Роутеры не содержат бизнес-логики.
5. `pytest -q` и eval-набор зелёные до и после каждого этапа.
6. Vue-frontend собирается (`npm run build`), legacy `app/static/app.js` выведен
   из эксплуатации.

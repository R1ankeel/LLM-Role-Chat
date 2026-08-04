# Архитектура

## Обзор

Приложение построено как один FastAPI-сервис с тремя явно разделёнными зонами:

1. **API-слой** — роутеры (`app/routers/`): приём запросов, валидация, SSE-стриминг.
2. **Доменный слой** — движок чата (`app/chat_engine.py`), сервисы памяти (`app/memory_service.py`), отношений (`app/relationship_*.py`), контекста (`app/context_builder.py`).
3. **Слой данных** — `app/crud.py` (async-операции), `app/models.py` (ORM), `app/database.py` (движки + миграции).

Всё состояние живёт в SQLite (`ai_chat.db`). LLM-вызовы идут в локальный Ollama.
Фронтендов два, оба общаются с одним API (fetch + SSE):

- **Старый**: Vanilla JS SPA в `app/static/`, отдаётся самим FastAPI на `:8000` (не изменяется).
- **Новый**: Vue 3 + TypeScript + Vite в `frontend/`, dev на `:3000` с proxy `/api → :8000` (см. [README](README.md)).

```
┌──────────────────────────────┐
│  Браузер                     │
│  app/static SPA (:8000)      │  ← старый, не изменяется
│  frontend/ Vue SPA (:3000)   │  ← новый (Vite dev, proxy /api)
│  fetch / SSE                 │
└──────────────┬───────────────┘
               │ HTTP + SSE
┌──────────────▼───────────────┐
│  FastAPI (app/main.py)       │
│  routers/*.py                │
│  CORS, lifespan, воркеры     │
└──────┬───────────────┬───────┘
               │               │
┌──────▼─────────┐ ┌───▼──────────────┐
│  chat_engine   │ │  task_queue      │
│  memory_service│ │  memory jobs     │
│  relationships │ │  (embed/backfill │
│  ollama_client │ │   /consolidation)│
└──────┬─────────┘ └───┬──────────────┘
               │               │
               ├───────────────┼────────────────────────┐
               │               │                        │
┌──────▼───────────────▼─────┐  ┌───────────────▼──────────┐
│  crud.py / models.py       │  │  Ollama (localhost:11434)│
│  database.py (SQLite)      │  │  generate/chat/embed     │
└────────────────────────────┘  └──────────────────────────┘
```

## Новый frontend (`frontend/`)

Отдельное Vite-приложение (Vue 3 + Composition API + TypeScript + Pinia + Vue Router),
разрабатывается по плану [`Plans/frontend-app.md`](../Plans/frontend-app.md). Полностью изолировано
от `app/static/`; backend не меняется.

Слои (разделение ответственности):

1. **`types/`** — TS-интерфейсы 1:1 со схемами API (`chat`, `character`, `message`, `scene`, `sse`, `relationship`).
2. **`api/`** — единственный слой сетевых запросов (создан на Этапе 4): `client.ts` (`ApiError`,
   `request` с `query`, `parseRateLimitSeconds`), `sse.ts` (интерфейс `MessageStream` +
   `SseMessageStream`: `onToken/onMessage/onDone/onError/abort` через `fetch` + `getReader()`),
   домены `chats/characters/messages/scene/relationships`, фасад `index.ts` с переключателем
   `useMocks` (`VITE_USE_MOCKS`). Компоненты и store'ы вызывают только `api`.
3. **`mocks/`** — mock-данные и mock-сервис (`data.ts`, `service.ts`) с интерфейсом `Api` 1:1;
   mock-стрим имитирует токены через `setTimeout`. Включаются только через `VITE_USE_MOCKS=true`.
4. **`stores/`** — Pinia: `chats` (числовой `currentChatId`, «последний чат» в localStorage, модели),
   `messages` (реальный SSE-стрим, отрицательные temp-id, ошибки `rate-limit|conflict|generic`,
   восстановление генерации поллингом `generation-status`), `characters` (персонажи + выбранный +
   память/сводка + смена локации), `relationships` (граф, issues, outgoing/incoming с обогащением
   `open_issue_count` из графа, pair + timeline с пагинацией), `scene`, `ui` (модалка отношений).
   Гигантских store нет.
5. **`router/`** — `/` (redirect на последний чат из localStorage) и `/chat/:chatId`
   (валидация числового id, при 404 — редирект на `/`).
6. **`components/`** — презентационные компоненты: `layout/` (AppLayout, Sidebar, MainPanel, RightPanel),
   `chat/` (ChatHeader, MessageList/Item, SystemMessage, WorldEvent, GenerationIndicator, Composer, ChatView),
   `characters/` (CharacterList, CharacterDetails, RelationshipView, RelationshipGraph, RelationshipPairDetail,
   RelationshipModal), `scene/` (WorldStatePanel), `common/` (Avatar, Badge, Modal, EmptyState, ProgressBar).
7. **`styles/`** — дизайн-токены (CSS-переменные), base, компонентные классы.

Ключевые решения:

- **Реальный SSE (Этап 4):** `stores/messages.ts` состояния `idle → sending → streaming → idle`,
  `sendMessage`/`regenerateMessage` возвращают `MessageStream`; токены дозаписываются в streaming-сообщение
  с отрицательным temp-id (точечное обновление в ленте), на финальный `message`-event placeholder
  заменяется реальным; `stop()` = `abort()` + POST `/stop-generation`; 429 → отсчёт в Composer,
  409 → блокировка с объяснением, пагинация `GET /messages` — fetch-all страницами по 500 (backend не менялся).
  Два контрактных нюанса, учтённых при живой проверке:
  - `ChatDetail` приходит **плоским** (`extends ChatRead`, без ключа `chat`) — `currentChat = detail`;
  - backend первым SSE-событием шлёт **эхо сообщения игрока** — `onMessage` при `role==='user'`
    заменяет optimistic-копию реальным сообщением, чтобы не было дубликата.
- **Типы сообщений:** `character` (Avatar + accent + имя), `user` (свой стиль/выравнивание),
  `system` (центрированный блок — перемещения/смена сцены), `WorldEvent` (карточка с иконкой 🌍,
  не похожа на реплику). Лента `MessageList` объединяет сообщения и world-события по времени.
- **Аватары:** инициалы + детерминированный accent-цвет (`utils/color.ts`, палитра «приятных» тонов);
  компонент готов к `imageUrl` (TODO — поле появится в backend).
- **Composer:** авто-рост textarea, Enter=отправить / Shift+Enter=перенос, защита IME, Send↔Stop,
  disabled без выбранного чата.
- **Автоскролл** ленты — только если пользователь у нижней границы; иначе хинт «Новые сообщения».
  Индикатор генерации имеет зарезервированную высоту — без «прыжков» раскладки.

## Жизненный цикл одного раунда

Раунд начинается с `POST /api/chats/{chat_id}/message`.

### 1. Вход (router `app/routers/chat_engine.py`)

1. Валидация: непустой текст (400), чат существует (404).
2. Rate limit: `ratelimit.check_rate_limit(chat_id)` — 429, если с последнего завершённого раунда прошло < 5 секунд.
3. Конкурентность: `generation_tracker.is_gen_active(chat_id)` — 409, если генерация уже идёт.
4. Создаётся `asyncio.Queue`, запускается фоновая задача `_run_generation`, которая в собственной сессии БД выполняет `process_user_message_streaming` и складывает события в очередь.
5. Ответ — `StreamingResponse` (SSE), события поочерёдно вычитываются из очереди.

### 2. Ядро раунда (`process_user_message_streaming`, `app/chat_engine.py:285`)

1. Загрузка чата, настроек (`history_limit`, `thinking_mode`, `player_location`, `chat_locations`).
2. Окно истории: `context_history_load_cap` при включённом контекст-билдере, иначе `history_limit`.
3. Сохранение сообщения пользователя (это триггер раунда). `round_id = f"r{chat_id}-m{user_message.id}"` — стабильный идентификатор раунда.
4. Эхо сообщения пользователя в SSE.
5. Загрузка персонажей (player + NPC); если NPC нет — выход.
6. **Presence для сообщения пользователя**: для каждого персонажа вычисляется и сохраняется `MessagePresence` (`present|mentioned|absent|told`).
7. Загрузка состояния сцены (`SceneState`).
8. Подготовка компактного контекст-запроса для извлечения памяти; для каждого NPC — BM25/гибридный отбор релевантных воспоминаний.
9. Предварительная сборка блоков отношений: `relationships_blocks`, `drivers_blocks`, `open_issues_blocks`, `proactive_boosts`.
10. **Цикл генерации по NPC** (см. ниже).
11. Пост-раунд: пересчёт presence, извлечение сцены, отслеживание стагнации, фоновые задачи (отношения, память).

### 3. Цикл генерации по персонажам

Персонажи отвечают последовательно в порядке `order_index` (player → NPC₁ → NPC₂ → …). Каждый NPC видит предыдущие ответы NPC, которые он способен воспринять (`effective_prior_replies`) — это антимимикрия.

Для каждого текущего персонажа:

1. Имена остальных персонажей для изоляции роли.
2. Получение саммари + свежей presence-карты по всем сообщениям раунда.
3. **Эпистемическая маска**: вычисляется, чьё поведение персонаж наблюдал напрямую в этом раунде; если есть свидетельства — в промпт добавляется блок `<epistemic_mask>` (интерпретация без чисел).
4. **Токено-ориентированный контекст**: если `CONTEXT_ENABLED`, вызывается `ContextBuilder.build(...)` (см. [context_builder](#контекст-билдер)), иначе — legacy-сборка.
5. **LLM-вызов** `ollama_client.generate(...)`: потоковая генерация с retry/валидацией; события `token` ретранслируются в SSE.
6. Ошибка генерации (RuntimeError) не прерывает раунд: вместо ответа сохраняется строка-заглушка `*[Имя молчит, не в силах ответить]*`.
7. **Определение канала связи** по тексту ответа (`_detect_communication_channel`): magic/phone/radio/messenger → `visibility="targeted"` + целевые персонажи; иначе `direct`.
8. Сохранение реплики персонажа (`role="character"`, location = локация персонажа, visibility/channel/targets).
9. Расчёт presence для этой реплики (следующие NPC видят её только если могут воспринять).
10. Накопление `prior_replies` и `round_messages`.

### 4. Внутри `ollama_client.generate` (валидация и retry)

`generate` — асинхронный генератор, который:

1. Собирает stop-последовательности, температуру (с пер-персонажной jitter-регулировкой), witness-фильтрованную историю.
2. Выполняет до `max_role_isolation_retries + max_repetition_retries + 1` LLM-вызовов:
   - каждый ответ санитизируется (`sanitize_and_validate_response`): срез по чужому speaker-маркеру, детекция нарушения перспективы;
   - проверка заимствования лексики из чужих реплик (vocabulary control);
   - детекция повторов `repetition_detector.analyze_response`;
   - при нарушении — точечный retry с уточнённым промптом (строгая изоляция / фидбек о повторе).
3. При исчерпании бюджета изоляции и `FALLBACK_ON_ISOLATION_FAILURE` — fallback-промпт с температурой 0.6.
4. Стриминг токенов идёт по мере получения; финальный санитизированный текст отдаётся событием `response`.

### 4.1. WPE 3.0 Фаза 2: tool-calling `take_actions` (shadow)

При включённом флаге `WORLD_ENGINE_TOOLS_ENABLED` `_generate_once` использует
ветку tools/format вместо обычного вызова (подробности: `Plans/WPE.md` §8, §10):

- **Chat-путь**: в `/api/chat` уходит `tools: [take_actions]` (OpenAI-схема из
  `schemas.build_take_actions_tool`); `tool_calls` приходят в терминальном
  сообщении, **не рендерятся как текст** (тест #22) и парсятся в
  `schemas.TurnOutput` (`reply_target_character_ids` + `actions[]`).
- **Generate-путь**: `/api/generate` с `format: <JSON-Schema>`
  (`build_take_actions_json_schema`); выходной JSON парсится в `TurnOutput`.
- **Shadow**: действия извлекаются, логируются (`[WPE-P2] shadow …`), метрики
  копятся в `ollama_client.WPE_TOOLS_STATS` (`wpe_tools_stats_snapshot()`);
  **не применяются** — текст реплики остаётся прежним.
- **Фоллбэк** строго нативный (И14): tools → `format` (JSON-Schema); text-only
  фоллбэк **удалён в Фазе 8** — при недоступных tools/format генерация падает
  с `RuntimeError` (структурированные действия обязательны); возможности
  модели кэшируются один раз на имя модели (§12).
- **Откат**: выключение флага возвращает генерацию текст-only.
- Системный промпт получает инструкцию `build_take_actions_instruction` (по
  гейту флага) — текст реплики и действия в одном ответе.

### 4.2. WPE 3.0 Фаза 4: Cutover на `perceive()` + Recency Tail

При включённом `WORLD_ENGINE_PERCEPTION_ENABLED` presence в
`crud.compute_and_save_presence_for_message`/`compute_and_save_presence_for_round`
и в `witness_model.compute_mvp_presence` (когда передан `world_state`) решается
двухканальным `perceive()` (по `location_id` Фазы 1 / строкам legacy-bridge),
а результат схлопывается в legacy-лестницу через Renderer
`witness_model.perceive_to_presence`. Тот же `PerceptionResult` даёт гейт
адмиссибилити отношений через адаптер `chat_engine.evidence_mode_from_perception`
(Golden #14 — единый результат для генерации и отношений).

При включённом `WORLD_ENGINE_RECENCY_TAIL_ENABLED` P0-события этого персонажа
(адресация `target_character_ids` + будущие `remote_status=delivered`)
рендерятся `witness_model.build_character_recency_tail` → блок
`[СИСТЕМНОЕ ВМЕШАТЕЛЬСТВО: ...]` (`build_system_intervention_block`) в **самый
конец** user-сообщения, непосредственно перед generation cue — в обоих путях
(chat: `_build_generation_messages`; generate: `context_parts`), пересобирается
для каждого персонажа отдельно. В `context_builder` блок — часть фиксированных
инструкций, не усекается бюджетом (резерв `context_reserve_tokens`), и
дублируется в `BuiltContext.recency_tail_text`.

Откат обеих фаз — выключение флагов (legacy `can_character_perceive_event`
сохранён как fallback).

### 4.3. WPE 3.0 Фаза 5: Action Resolution + System Narrator (Ул.1)

При включённом `WORLD_ENGINE_ACTIONS_ENABLED` структурированные действия
(`schemas.TurnOutput.actions`, извлечённые только из native
tools/JSON-схемы — И4) применяются в round-пути `chat_engine.py`:

1. `ollama_client.generate()` пробрасывает в response-событие поля `turn`
   (`TurnOutput`) и `verdict` (вердикт Consistency Validator).
2. **Consistency Validator** (`app/action_resolution.classify_consistency`):
   `consistent` / `minor_ambiguity` (молчаливое действие) / `contradiction`.
   `contradiction` ретраится внутри `generate()` (≤
   `WPE_ACTION_CONSISTENCY_MAX_RETRIES`, фидбек в блоке
   `<action_consistency>`); молчаливое действие **не** ретраится (И16).
3. **Применение** (`crud.apply_character_actions`) — атомарно одной
   транзакцией: `move_to` резолвится в каноническую `Location`, обновляет
   `character.location` + `location_id` и создаёт immutable `WorldEvent(move)`
   (`location_from`/`location_to`/`round_id`); `send_message` валидирует
   адресатов и создаёт `WorldEvent(speech)` (Threads — Фаза 6). Порядок:
   move → зависящие от локации. Невалидные действия отклоняются без
   `WorldEvent` и не ломают валидные (#13).
4. **System Narrator** (И6, И16): для каждого применённого действия, **не
   отражённого в тексте реплики** (`action_resolution.reflected_action_indices`),
   движок создаёт служебное сообщение `role="system"` по шаблону из `WorldEvent`
   (`*[Система: <Имя> <действие>]*`), сразу после реплики; текст реплики не
   редактируется. При неразрешённом `contradiction` действия отклоняются и
   ремарка фиксирует решение движка.
5. **Regex-канал движения** (`detect_character_movement`) при включённом
   флаге — safety-net (И4): активен только при выключенном
   `WORLD_ENGINE_ACTIONS_ENABLED`.

Откат — выключение флага (движок возвращается к пост-раундовому regex-пути
движения).

### 4.4. WPE 3.0 Фаза 6: Threads/мессенджер + двухканальное частичное восприятие (Ул.2)

**Threads** (`WORLD_ENGINE_THREADS_ENABLED`): `create_message` для удалённого
канала (magic/phone/radio/messenger) вызывает
`crud.ensure_message_thread_delivery` в той же транзакции — создаётся/обновляется
`Thread` (`threads`) и `ThreadParticipantState` (`thread_participant_states`,
участники = автор + адресаты), адресатам проставляется
`last_delivered_message_id` (доставка). `send_message` из
`apply_character_actions` создаёт тред и участников (И14). `perceive()` читает
`world_state.thread_deliveries` (источник — `crud.thread_delivery_ids_for_message`):
адресат удалённого канала получает `visual=full/audio=full` +
`remote_status=delivered` **независимо от локации** (Golden #6, групповой тред —
#15). presence-пути (`compute_and_save_presence_for_message`/_for_round) строят
world-state по событию, включая доставки.

**Частичное восприятие** (`WORLD_ENGINE_PARTIAL_PERCEPTION_ENABLED`): `perceive()`
учитывает проницаемость рёбер `visual_permeability`/`audio_permeability`
(индекс `build_permeability_index`), громкость (стимулы `loud_sound`/`call`/
`shout` поднимают `muffled→full`) и стимул `invisible` (событие в одной локации
→ `visual=none`/`audio=full`, Golden #19; стекло — ребро `full/none`, #17; крик
из-за стены — `none/full`, #18). **Voice familiarity** (§4) — детерминированная
атрибуция: `witness_model.voice_familiarity` из `CharacterRelationship`
(голос знаком ↔ есть отношение наблюдателя к автору). Влияние на presence:
`audio=full` + знакомый голос → `mentioned`, незнакомый → `audible`;
`muffled` → атрибуция запрещена всегда.

**Renderer (И11)**: при включённых обоих флагах `ContextBuilder` строит
историю через `perceive()` + `render_perception_line` (канало-зависимые строки:
стекло — «действия видны, слов не слышно», незнакомый голос — «чей-то голос»),
не утекая содержание из `muffled`/`none`-каналов; иначе — legacy-лестница
`format_line_for_presence` (идентичное поведение Фаз 1–5). Откат — флаги
независимы: треды и частичное восприятие включаются/выключаются раздельно;
partial → бинарный full/none по каналам.

### 4.5. WPE 3.0 Фаза 7: Event Bus / Interrupts (Ул.5, §7, И17)

Цикл раунда вынесен из `chat_engine` в `app/round_engine.py` (правило §9):
`run_round` — единственная оркестрирующая функция. `EventBus` — очередь
приоритетов: разбуженные NPC идут впереди плановых, внутри приоритета FIFO
(защита от инверсии/зацикливания), плановый порядок — исходный `order_index`
(детерминизм). `chat_engine.process_user_message_streaming` делегирует цикл в
`run_round` (флаг on) / `run_round_fixed` (флаг off); пер-NPC шаг вынесен в
`_round_step(current_character, bus)` и сам будит NPC-адресатов своей реплики.

Буждение (триггер — адресация события `addressed=true`, И7):
- **игрок→NPC**: `target_character_ids` user-сообщения `seed`-ятся первым
  ходом раунда — адресат отвечает первым (Golden #2 → Ул.5);
- **NPC→NPC**: `target_character_ids` реплики (источник — `send_message` action
  И14, или regex-канал для отката) будят адресата немедленно — даже если он
  стоит позже по расписанию (Golden #21: «звонок будит NPC вне очереди»).

Инварианты (И17, §12): один NPC — максимум один ответ за раунд; повторные
буждения и буждения уже ответивших игнорируются (событие сохраняется в
истории); зацикливание невозможно — каждый `pop_next` помечает NPC
сгенерированным. Откат — `WORLD_ENGINE_EVENT_BUS_ENABLED` off → `run_round_fixed`
воспроизводит исходный фиксированный порядок без изменения поведения.

### 4.6. WPE 3.0 Фаза 8: Уборка (legacy-поля, text-only, regex)

Закрытие аудита legacy-полей (§6 v2) и удаление deprecated путей:

- **`Message.visibility`, `character.location`-строка — read-only legacy-bridge.**
  Источник — канонические поля: `Message.visibility` задаётся только при
  создании (no update-path); все write-path строки `character.location`
  (`crud.update_character_location`, `crud.update_character_locations_batch`)
  резолвят каноническую `Location` и пишут также `location_id` одной
  транзакцией.
- **Text-only путь генерации удалён (И14).** `_tool_mode_chain`/
  `_next_tool_mode` (`app/ollama_client.py`) больше не дают фоллбэк
  tools → format → text: при недоступных tools/format генерация падает с
  `RuntimeError`. Текст-only остаётся только для нетools-генерации
  (`preferred="text"`).
- **Regex-детекторы — legacy-safety-net, не источник истины.**
  `app/movement.py detect_character_movement` и
  `app/chat_engine._detect_communication_channel` не считывают действия из
  текста как истину; источник — `turn.actions` из tools/format. Regex
  срабатывает с deprecation-логом (`[WPE-P8]`), только когда действия не
  извлечены (actions off / откат).

### 5. Пост-раунд (в том же запросе)

1. **Presence round pass** — пересчёт presence для всех сообщений раунда с учётом финальных локаций.
2. **Извлечение сцены** (`extract_scene_state`): один LLM-вызов с structured output (`format` JSON-схема), определяет локации персонажей. `time_of_day` из ответа LLM игнорируется — движок никогда не меняет время суток автоматически, его устанавливает только пользователь через `PATCH /chats/{id}/scene`.
   - Локации принимаются только если: имя в списке разрешённых локаций И есть текстовое свидетельство перемещения (`_detect_movement_in_text`).
   - Перемещения объявляются системными сообщениями `role="system"` с `visibility="global"`.
3. **Стагнация**: каждый ответ NPC прогоняется через repetition-анализ; инкремент `round_count`, накопление `stagnation_rounds`. Время суток при этом не сдвигается автоматически (см. п. 2).
4. **Фоновая задача отношений** (если `relationship_analyzer_enabled`): `asyncio.create_task(_analyze_and_update_relationships(...))` — отдельная сессия БД.
5. **Фоновая задача памяти**: `asyncio.create_task(memory_service.process_post_round(...))`.

## Фоновые задачи

### `_memory_jobs_worker` (app/main.py:41)

Цикл с интервалом 10 секунд: вызывает `memory_job_queue.process_pending_jobs(job_types=["embed_memory", "backfill_embeddings"])`. Только embed-задачи диспетчеризуются автоматически; `post_round` задачи выполняются синхронно сразу после постановки в очередь.

### `_consolidation_scheduler` (app/main.py:62)

Каждые `consolidation_interval_hours` (по умолчанию 24 ч) ставит и запускает глобальную задачу консолидации памяти (`chat_id=0` = все чаты).

### Очередь задач (`app/task_queue.py`)

`MemoryJobQueue` — лёгкая async-очередь с персистентностью в таблице `memory_jobs`:

- `enqueue(job_type, chat_id, payload, max_attempts)` — создаёт запись `pending` с correlation_id.
- `run_job(job)` — семафор (до `task_queue_max_concurrent`), ретраи с экспоненциальной задержкой (`task_queue_retry_min_wait * multiplier^(attempt-1)`, capped `max_wait`); статусы: `pending → running → succeeded | failed → dead_letter`.
- Типы задач: `post_round`, `consolidation`, `embed_memory`, `backfill_embeddings` (диспетчеризация `_dispatch_job`).
- `process_pending_jobs(job_types)` — стартовое восстановление, лимит 100 задач.
- `retry_job(job_id)` / `cleanup_old_jobs(days)` / `get_job_stats(chat_id)`.

## Контекст-билдер

`app/context_builder.py` собирает контекст одного персонажа в токенный бюджет:

1. Кандидаты = окно истории + сообщения раунда; для каждого разрешается presence (или берётся из `presence_map`).
2. Сплит по frontier саммари: свежие линии и старые.
3. **Recent dialogue (P1)**: от новых к старым, лимит `max_replies_per_character` на чужого персонажа (свой не режется), гарантия P0 — самое свежее сообщение включается всегда.
4. **Retrieval (P3)**: только `present|told`, BM25-скоринг (`SimpleBM25`), жадная укладка в бюджет.
5. Фиксированные блоки: system, scene, instructions (вычитаются из бюджета с резервом).
6. **Summary (P2)**, **Memories (P2)** — обрезаются/выселяются при перерасходе.
7. Финальный проход: триминг в порядке приоритета (retrieved → memories → summary → recent).
8. Диагностика (`ContextDiagnostics`) и по-компонентный подсчёт токенов.

Scene-блок (`prompt_builder.build_scene_block`) принимает `character_appearances` (map имя → внешность, Этап C плана profile-avatar-appearance): в него добавляется строка `Внешность рядом стоящих: …` **только** для персонажей той же локации, что и текущий (co-present) — изоляция знаний сохраняется. Сама внешность персонажа попадает в его `<character>`-карту через `<appearance>`-тег. `format_character_descriptor` (трекер локаций/эстракция памяти) внешность не включает.

Бюджет считается в `context_budget.build_budget` (приоритет: reserve 15% → state P0 → summary P2 → memory P2 → retrieval P3 → recent-остаток). Динамический `num_ctx` на чат отслеживает `context_state.ContextState` (старт с `MIN_CTX`, только рост до `MAX_CTX` с safety factor 1.3).

## Поток восприятия (Perception → Witness)

1. Каждое сообщение = событие мира с полями `visibility`, `location`, `target_character_ids`, `channel`.
2. `perception.can_character_perceive_event` решает, что персонаж видит (см. [relations.md](relations.md) и [database.md](database.md) для схемы правил):
   - собственная реплика → `present`;
   - `global`/`public` → `present`;
   - `private`/`targeted` → `present` только для целей;
   - remote-каналы (magic/phone/radio/messenger) обходят изоляцию локаций;
   - `local` → `present` в той же локации, `mentioned` если имя упомянуто, иначе `absent`.
3. `witness_model` форматирует историю для персонажа: `present` → полный текст, `mentioned` → `[Тебя упомянули: …]`, `told` → `[Тебе рассказали: …]`, `absent` → пропуск.
4. Для памяти и саммари используются только `present|told` (`MEMORY_OBSERVABLE_PRESENCES`) — мягкие упоминания не становятся фактами.

## Поток памяти

1. **Извлечение** (post_round): для каждого персонажа — только наблюдаемый им текст → LLM извлекает 0–3 факта (`ExtractedFact` с `category`, `importance`, `witnessed`). При `sensors_memory_enabled` (Sprint 2, §5.1.3) SensorsService предлагает кандидатов `{facts: [{text, importance}]}` — движок прогоняет их через ту же валидацию и лимиты; Sensors память сам не пишет.
2. **Валидация** (`validate_extracted_facts`): длина, generic-паттерны, «чужие мысли», grounding (минимум 22% пересечения с контекстом), near-dup (Jaccard ≥ 0.75), лимит на раунд. При `memory_types_enabled` каждому факту присваивается `memory_type` (semantic/episodic/social/story): LLM-тип из промпта, при отсутствии — детерминированный fallback (`memory_service.classify_memory_type` по категории/тексту).
3. **Сохранение**: `crud.create_memory` с `content_hash` (SHA-256 нормализованного текста) — уникальность на уровне БД; пустой `memory_type` → default 'semantic'; лимит памяти на персонажа (`ensure_memory_limit`).
4. **Эмбеддинги**: enqueue `embed_memory` → `embedding_service` (Ollama `/api/embed`, bge-m3), хранение float32 BLOB.
5. **Поиск**: BM25 + вектор + RRF (`get_hybrid_memories_for_characters`), witness-фильтр кандидатов, boost прямых наблюдений.
6. **Decay**: снижение importance при неиспользовании > 7 дней.
7. **Саммари**: каждые `summary_interval_messages` сообщений, инкрементальное, на наблюдаемом тексте.
8. **Консолидация**: кластеризация по Jaccard ≥ 0.65, LLM-слияние кластера в один факт, удаление дубликатов.
9. **Эмоциональные якоря** (Sprint 2, §7, при `anchors_enabled`): из значимых RelationshipEvent (`_maybe_create_memory_from_event`) движок пишет `memory_anchors` (эмоция, валентность, интенсивность); активация top-K якорей в контекст — Sprint 7 (`crud.select_top_anchors`, importance × recency, дедуп по event_id).

## Отношения (кратко; подробно — [relations.md](relations.md))

- Направленные рёбра `source → target`, метрики (affection, trust, attraction, resentment, jealousy 0–100), тип из белого списка с графом переходов.
- Анализатор (LLM, batch или per-pair) оценивает дельты и open issues по фактам раунда; evidence-gating: `direct | observed | none`.
- Детерминированный интерпретатор превращает числа в семантические ярлыки для промпта (никаких чисел персонажу).
- Open issues — сюжетные крючки с салience-счётчиком и весовым proactive-бустом.

## Валидация и надёжность

- **Rate limit**: 1 сообщение / 5 сек на чат (считается от завершённой генерации).
- **Одна транзакция на раунд**: сообщения раунда коммитятся в конце запроса; ошибки валидации откатывают раунд.
- **Отвязка задач**: отношения и память — в отдельных сессиях, не блокируют ответ.
- **Retry-слои**: транспорт (3 попытки при таймаутах/сетевых ошибках) + генерация (изоляция/повторы).
- **Обработка обрыва клиента**: генерация продолжается, SSE-цикл ловит `CancelledError` и снимает rate limit.
- **`/stop-generation`**: отменяет активную задачу генерации (через `generation_tracker`).

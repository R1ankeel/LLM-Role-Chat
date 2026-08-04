# База данных

SQLite, файл `ai_chat.db` рядом с `main.py`. Два подключения:
- **sync** (`sqlite:///./ai_chat.db`) — миграции и фоновые задачи;
- **async** (`sqlite+aiosqlite:///./ai_chat.db`) — обработчики запросов.

При каждом старте включается `PRAGMA foreign_keys=ON` и `PRAGMA journal_mode=WAL`. Схема создаётся/мигрируется через `init_db()` → `ensure_schema(engine)`: `Base.metadata.create_all` + набор идемпотентных миграций (`ALTER TABLE ... ADD COLUMN` для новых колонок, `CREATE TABLE IF NOT EXISTS` для новых таблиц, бэкафилл-скрипты для старых данных).

## Таблицы

### `chats`
Чат-сессия. Каскадное удаление всего содержимого.

| колонка | тип | примечание |
|---|---|---|
| `id` | INTEGER PK | |
| `name` | TEXT(255) | |
| `general_prompt` | TEXT | системный промпт |
| `model_name` | TEXT(255) | по умолчанию `settings.default_model` |
| `max_history_length` | INTEGER | по умолчанию `settings.default_history_length` |
| `thinking_mode` | BOOLEAN | по умолчанию `settings.enable_thinking` |
| `player_location` | TEXT(255) | локация игрока |
| `locations` | TEXT | JSON-список локаций мира |
| `original_plot` | TEXT NULL | выделенный неизменяемый замысел (из `general_prompt`; Sprint 0, §16.1) |
| `story_prompt` | TEXT NULL | текущий story prompt (эволюционирующий; Sprint 8) |
| `story_enabled` | BOOLEAN (default 0) | включение динамического сюжета (Sprint 8) |
| `created_at` | DATETIME | |

### `characters`
Персонаж (NPC или игрок). `is_player=1` — игрок, `order_index=9999`.

| колонка | тип | примечание |
|---|---|---|
| `id` | INTEGER PK | |
| `chat_id` | FK → `chats.id` ON DELETE CASCADE | |
| `name` | TEXT(255) | |
| `personality`, `traits`, `speech_style`, `example_messages`, `boundaries`, `background`, `relationships`, `appearance` | TEXT | текстовая карточка персонажа (`appearance` — внешность, п.19–21 Profile) |
| `avatar_url` | TEXT(512) | относительный URL аватара (`/static/avatars/...`); пусто = placeholder |
| `location` | TEXT(255) | текущая локация (строковое имя; read-only legacy-bridge до Фазы 8 WPE 3.0) |
| `location_id` | INTEGER NULL | FK → `locations.id` ON DELETE SET NULL; каноническая локация (WPE 3.0 Фаза 1 — backfill из `location`; NULL = общая сцена / нерезолвлено) |
| `temperature` | REAL NULL | переопределение температуры |
| `order_index` | INTEGER | уникален в чате |
| `is_player` | BOOLEAN | |
| `created_at` | DATETIME | |

Индексы: `ix_characters_chat_order (chat_id, order_index)`.

### `messages`
Сообщение = world event. `character_id` NULL для user/system; при удалении персонажа его сообщения остаются (`ON DELETE SET NULL`).

| колонка | тип | примечание |
|---|---|---|
| `id` | INTEGER PK | |
| `chat_id` | FK → `chats.id` ON DELETE CASCADE | |
| `character_id` | FK → `characters.id` ON DELETE SET NULL | NULL для user/system |
| `role` | TEXT(50) | `user` / `character` / `system` |
| `content` | TEXT | |
| `visibility` | TEXT(20) | `private/local/targeted/public/global` |
| `location` | TEXT(255) | |
| `target_character_ids` | TEXT | JSON-список |
| `channel` | TEXT(20) | `direct/magic/phone/radio/messenger` |
| `timestamp` | DATETIME | |

Индексы: `ix_messages_chat_ts (chat_id, timestamp, id)`, `ix_messages_character_id (character_id)`.

### `world_events`
Неизменяемый (append-only) журнал world-событий (WPE 3.0). С Фазы 3 пишется
атомарно вместе с `Message` (`crud.create_message`, флаг
`WORLD_ENGINE_EVENTS_ENABLED`); строковая `location` — legacy-bridge до
перехода на `location_id`.

| колонка | тип | примечание |
|---|---|---|
| `id` | INTEGER PK | |
| `chat_id` | FK → `chats.id` ON DELETE CASCADE | |
| `character_id` | FK → `characters.id` ON DELETE SET NULL | автор; NULL для system/global |
| `message_id` | FK → `messages.id` ON DELETE SET NULL | привязка к речевому сообщению |
| `event_type` | TEXT(50) | `speech` / `move` / `system_narrator` / `system` |
| `location` | TEXT(255) | строковая локация события (legacy-bridge) |
| `location_id` | INTEGER NULL | FK → `locations.id` ON DELETE SET NULL; каноническая локация события (Sprint 0; NULL = общая сцена/нерезолвлено) |
| `location_from`, `location_to` | TEXT(255) | для `move` |
| `round_id` | TEXT(64) NULL | |
| `target_character_ids` | TEXT | JSON-список |
| `action` | TEXT NOT NULL DEFAULT '{}' | JSON `{"actor","action","target","object"}`; Sprint 1 (раундная event extraction) |
| `importance` | REAL NULL | 0..10; Sprint 1; движковые события не заполняют (NULL) |
| `story_salience` | REAL NULL | 0..1; Sprint 1 |
| `emotional_salience` | REAL NULL | 0..1; Sprint 1 |
| `created_at` | DATETIME | |

Индексы: `ix_world_events_chat_ts`, `ix_world_events_character_id`, `ix_world_events_round_id`, `ix_world_events_location_id`.

> **Sprint 1** (`Plans/update20.md §15`): колонки `action/importance/story_salience/
> emotional_salience` заполняются пост-раундной event extraction
> (`event_service.extract_round_events` → `crud.save_round_events`) только при
> `EVENT_EXTRACTION_ENABLED=true`. Движковые `speech`/`move` (dual-write из
> `crud.create_message`) salience/importance не заполняют — по этим полям
> (NOT NULL `importance`) определяется, что extraction для раунда уже записана
> (идемпотентность). Read-path новые поля пока не читает.

### `threads`
Тред/канал общения (WPE 3.0, Фаза 0). Заведён, **не пишется** до Фазы 6.

| колонка | тип | примечание |
|---|---|---|
| `id` | INTEGER PK | |
| `chat_id` | FK → `chats.id` ON DELETE CASCADE | |
| `name` | TEXT(255) | название треда |
| `channel` | TEXT(20) | `direct` / `magic` / `phone` / `radio` / `messenger` |
| `created_at`, `updated_at` | DATETIME | |

Индекс `ix_threads_chat_id`.

### `thread_participant_states`
Состояние участника треда (доставка/прочтение) — источник `remote_status=delivered` (WPE 3.0, Фаза 0).

| колонка | тип | примечание |
|---|---|---|
| `id` | INTEGER PK | |
| `thread_id` | FK → `threads.id` ON DELETE CASCADE | |
| `character_id` | FK → `characters.id` ON DELETE CASCADE | |
| `last_delivered_message_id`, `last_read_message_id` | INTEGER NULL | |
| `joined_at`, `updated_at` | DATETIME | |

UNIQUE `(thread_id, character_id)`. Индекс `ix_thread_participant_character`.

### `message_presence`
Witness-присутствие персонажа для сообщения (кто видел/слышал событие).

С Фазы 4 (WPE 3.0) при `WORLD_ENGINE_PERCEPTION_ENABLED` значения пишутся
через двухканальный `perceive()` и Renderer (`witness_model.perceive_to_presence`):
`present` (полный визуальный контакт), `mentioned` (крик/атрибуция по голосу),
`audible` (шум из-за стены/соседняя локация), `absent` (И11 — дальняя
локация не додумывается), `told`. Откат — выключить флаг (legacy
`can_character_perceive_event`).

| колонка | тип | примечание |
|---|---|---|
| `id` | INTEGER PK | |
| `message_id` | FK → `messages.id` ON DELETE CASCADE | |
| `character_id` | FK → `characters.id` ON DELETE CASCADE | |
| `presence` | TEXT(20) | `present` / `mentioned` / `audible` / `absent` / `told` |

UNIQUE `(message_id, character_id)`. Индекс `ix_presence_character_message`.

### `memories`
Долгосрочная память персонажа. Дубликаты исключаются по `content_hash` (SHA-256 нормализованного текста) в рамках персонажа.

| колонка | тип | примечание |
|---|---|---|
| `id` | INTEGER PK | |
| `chat_id` | FK → `chats.id` ON DELETE CASCADE | |
| `character_id` | FK → `characters.id` ON DELETE CASCADE | |
| `content` | TEXT | |
| `content_hash` | TEXT(64) | SHA-256 |
| `created_at` | DATETIME | |
| `importance` | REAL (default 0.5) | для BM25-ранжирования |
| `category` | TEXT(50) | `отношения/событие/локация/предмет/другое` |
| `last_accessed_at` | DATETIME NULL | для консолидации |
| `source_message_ids` | TEXT | JSON-список |
| `embedding` | BLOB | для векторного поиска (P3) |

UNIQUE `(character_id, content_hash)`. Индексы: `ix_memories_char_created`, `ix_memories_char_imp_created`, `ix_memories_char_last_accessed`.

### `character_summaries`
Персонаж-специфичное саммари сессии (уровень 3 памяти), один на персонажа.

| колонка | тип | примечание |
|---|---|---|
| `id` | INTEGER PK | |
| `chat_id` | FK ON DELETE CASCADE | |
| `character_id` | FK ON DELETE CASCADE | |
| `content` | TEXT | |
| `through_message_id` | INTEGER | последний учтённый message id |
| `updated_at` | DATETIME | |

UNIQUE `(character_id)`. Индекс `ix_summaries_chat_character`.

### `scene_states`
Глобальное состояние сцены (время, погода и т.д.), локации — по персонажам.

| колонка | тип | примечание |
|---|---|---|
| `chat_id` | INTEGER PK FK | |
| `time_of_day` | TEXT | |
| `character_locations` | TEXT | JSON: `{character_id: location}` |
| `custom_state` | TEXT | JSON: weather, mood, tension, plot_flags, ... |
| `updated_at` | DATETIME | |

Индекс `ix_scene_states_chat_id`.

### `memory_jobs`
Очередь/журнал задач обработки памяти (observability).

| колонка | тип | примечание |
|---|---|---|
| `id` | INTEGER PK | |
| `chat_id` | INTEGER | |
| `job_type` | TEXT(50) | `embed`, `backfill`, `consolidate`, `post_round` |
| `status` | TEXT(20) | `pending/running/succeeded/failed/dead_letter` |
| `payload`, `result`, `error_message` | TEXT NULL | |
| `attempt`, `max_attempts` | INTEGER | ретраи |
| `created_at`, `started_at`, `completed_at` | DATETIME | |
| `correlation_id` | TEXT(64) | |

Индекс `ix_memory_jobs_chat_status`, `correlation_id` индексируется.

### `character_relationships`
Направленное отношение `source → target` (только NPC→NPC и NPC→Player; Player→NPC не отслеживается).

| колонка | тип | примечание |
|---|---|---|
| `id` | INTEGER PK | |
| `chat_id` | FK ON DELETE CASCADE | |
| `source_character_id` | FK ON DELETE CASCADE | |
| `target_character_id` | FK ON DELETE CASCADE | |
| `relationship_type` | TEXT(50) | `нейтральное`, `друг`, `враг`, ... |
| `affection`, `trust` | INTEGER (default 50) | |
| `attraction`, `resentment`, `jealousy` | INTEGER (default 0) | |
| `description` | TEXT | текущее описание |
| `initial_description` | TEXT | исходное (для диффа) |
| `updated_at` | DATETIME | |

UNIQUE `(source_character_id, target_character_id)`. Индексы: `ix_rel_source_target`, `ix_rel_chat_source`.

### `relationship_events`
Журнал значимых изменений отношений.

| колонка | тип | примечание |
|---|---|---|
| `id` | INTEGER PK | |
| `relationship_id` | FK ON DELETE CASCADE | |
| `kind` | TEXT(20) | `llm` \| `decay` \| `manual` \| `archive` (Sprint 4: свёрнутые старые события) |
| `description`, `reason` | TEXT | |
| `delta_affection`, `delta_trust`, `delta_attraction`, `delta_resentment`, `delta_jealousy` | INTEGER | |
| `affection_after` … `jealousy_after` | INTEGER | снапшот состояния после события |
| `importance` | INTEGER | `0` для архивных строк |
| `source_message_ids` | TEXT JSON | привязка к сообщениям |
| `round_id` | TEXT(64) | привязка к раунду |
| `source_round_id` | TEXT(64) | привязка к раунду |
| `event_id` | INTEGER NULL | FK → `world_events.id` ON DELETE SET NULL; каузальное событие (Sprint 1, раундная extraction) |
| `timestamp` | DATETIME | |

Индексы: `ix_rel_events_rel_id`, `ix_rel_events_ts`, `ix_rel_events_event_id`.

> **Sprint 1**: `event_id` — привязка relationship-события к world-событию, которое
> его вызвало (пишется в `crud.save_round_events`, если у `ExtractedEvent` есть
> `importance`). Для decay/archive строк остаётся NULL.

### `relationship_issues`
Открытые сюжетные крючки (open issues) между парами. Поле `text` — данные сцены, а не инструкция для LLM (защита от prompt injection).

| колонка | тип | примечание |
|---|---|---|
| `id` | INTEGER PK | |
| `relationship_id` | FK ON DELETE CASCADE | |
| `issue_type` | TEXT(50) | |
| `text` | TEXT | ограничен по длине |
| `importance` | INTEGER (default 5) | |
| `state` | TEXT(20) | `open` / `resolved` |
| `created_round_id`, `resolved_round_id`, `last_mention_round_id` | TEXT(64) NULL | |
| `created_at`, `resolved_at` | DATETIME | |
| `rounds_since_last_mention` | INTEGER | счётчик салиентности (§7.4) |

Индексы: `ix_rel_issues_rel_state (relationship_id, state)`, `ix_rel_issues_state`.

## Таблицы состояния (Sprint 0 — заведены, не пишутся)

Заведены как фундамент state-driven архитектуры (`update20.md`, Sprint 0, п.3):
создаются идемпотентно, **read-path их не читает**, движок их не заполняет до
соответствующих спринтов. 10 таблиц из плана + `consolidation_state` (по §20/E).

### `character_states` (Sprint 3)
Одна строка на персонажа в чате: эмоции, стресс, физическое состояние, внимание, цели.

| колонка | тип | примечание |
|---|---|---|
| `id` | INTEGER PK | |
| `chat_id` | FK → `chats.id` ON DELETE CASCADE | |
| `character_id` | FK → `characters.id` ON DELETE CASCADE | UNIQUE |
| `emotional_state` | TEXT | JSON map emotion→intensity |
| `mood` | TEXT(50) NULL | |
| `stress` | REAL NULL | 0..1 |
| `physical_state` | TEXT | JSON |
| `attention` | TEXT NULL | текущий фокус |
| `current_focus_id` | FK → `characters.id` SET NULL | на кого смотрит |
| `active_goal` | TEXT NULL | |
| `personal_goals` | TEXT | JSON list |
| `updated_round_id` | TEXT(64) NULL | |
| `created_at`, `updated_at` | DATETIME | |

Индекс `ix_character_states_chat_id`.

### `beliefs` (Sprint 5)
Знания/убеждения персонажа (триплет subject/predicate/object).

| колонка | тип | примечание |
|---|---|---|
| `id` | INTEGER PK | |
| `chat_id` | FK ON DELETE CASCADE | |
| `character_id` | FK ON DELETE CASCADE | |
| `subject`, `predicate`, `object` | TEXT | «Борис предал Анну» |
| `source` | TEXT(30) | `direct_observation/heard/told_by/inference/rumor/memory` |
| `confidence` | REAL (default 0.5) | 0..1 |
| `type` | TEXT(20) | `fact/belief/suspicion` |
| `world_truth_ref` | FK → `world_events.id` SET NULL | если подтверждено миром |
| `created_at`, `updated_at` | DATETIME | |

Индекс `ix_beliefs_chat_id`.

### `story_states` (Sprint 8)
Original Plot (immutable) + Current Story State + Story Phase.

| колонка | тип | примечание |
|---|---|---|
| `id` | INTEGER PK | |
| `chat_id` | FK ON DELETE CASCADE | |
| `original_plot` | TEXT NULL | immutable |
| `current_state` | TEXT | JSON |
| `story_phase` | TEXT(100) NULL | |
| `updated_round_id` | TEXT(64) NULL | |
| `created_at`, `updated_at` | DATETIME | |

Индекс `ix_story_states_chat_id`.

### `story_threads` (Sprint 10)
Активные сюжетные линии.

| колонка | тип | примечание |
|---|---|---|
| `id` | INTEGER PK | |
| `chat_id` | FK ON DELETE CASCADE | |
| `title` | TEXT(255) | |
| `description` | TEXT NULL | |
| `status` | TEXT(20) | `active/paused/resolved` |
| `updated_round_id` | TEXT(64) NULL | |
| `created_at`, `updated_at` | DATETIME | |

Индекс `ix_story_threads_chat_id`.

### `story_events` (Sprint 8)
**Проекция** `world_events` для сюжета (canonical event source — §15.0).

| колонка | тип | примечание |
|---|---|---|
| `id` | INTEGER PK | |
| `chat_id` | FK ON DELETE CASCADE | |
| `event_id` | FK → `world_events.id` ON DELETE CASCADE | каноническое событие |
| `round_id` | TEXT(64) NULL | |
| `event_type` | TEXT(50) NULL | |
| `actors` | TEXT | JSON list |
| `location_id` | FK → `locations.id` SET NULL | |
| `cause` | TEXT NULL | |
| `consequences` | TEXT NULL | |
| `importance` | REAL NULL | |
| `created_at` | DATETIME | |

Индекс `ix_story_events_chat_id`.

### `event_links` (Sprint 1)
Причинно-следственные рёбра событий.

| колонка | тип | примечание |
|---|---|---|
| `id` | INTEGER PK | |
| `chat_id` | FK ON DELETE CASCADE | |
| `event_id` | FK → `world_events.id` ON DELETE CASCADE | следствие |
| `caused_by_event_id` | FK → `world_events.id` ON DELETE CASCADE NULL | причина |
| `kind` | TEXT(20) DEFAULT 'causes' | `causes` \| `consequence` \| `goal_step` \| `resolution` |
| `created_at` | DATETIME | |

Индексы: `ix_event_links_chat_id`, `ix_event_links_event_id`, `ix_event_links_caused_by`.

> **Sprint 1**: в раундной extraction рёбра пишутся из `ExtractedEvent.causes`
> (событие → события, которые его вызвали, `kind='causes'`).

### `memory_anchors` (Sprint 2/7)
Эмоциональные якоря направленного отношения `source→target`.

| колонка | тип | примечание |
|---|---|---|
| `id` | INTEGER PK | |
| `chat_id` | FK ON DELETE CASCADE | |
| `relationship_id` | FK ON DELETE CASCADE | |
| `event_id` | FK → `world_events.id` SET NULL | |
| `emotion` | TEXT(50) NULL | |
| `valence` | REAL NULL | -1..1 |
| `intensity` | REAL NULL | 0..1 |
| `importance` | REAL NULL | |
| `timestamp` | DATETIME NULL | |
| `created_at` | DATETIME | |

Индекс `ix_memory_anchors_chat_id`.

### `intents` (Sprint 10)
Intent NPC на ход.

| колонка | тип | примечание |
|---|---|---|
| `id` | INTEGER PK | |
| `chat_id` | FK ON DELETE CASCADE | |
| `character_id` | FK ON DELETE CASCADE | |
| `target_character_id` | FK → `characters.id` SET NULL | |
| `goal` | TEXT NULL | |
| `urgency` | REAL NULL | |
| `approach` | TEXT(50) NULL | |
| `emotion` | TEXT(50) NULL | |
| `risk` | REAL NULL | |
| `round_id` | TEXT(64) NULL | |
| `created_at` | DATETIME | |

Индекс `ix_intents_chat_id`.

### `npc_plans` (Sprint 10)
Долгоживущие маленькие планы NPC.

| колонка | тип | примечание |
|---|---|---|
| `id` | INTEGER PK | |
| `chat_id` | FK ON DELETE CASCADE | |
| `character_id` | FK ON DELETE CASCADE | |
| `plan_name` | TEXT(100) NULL | |
| `description` | TEXT NULL | |
| `status` | TEXT(20) | `active/paused/completed/abandoned` |
| `updated_round_id` | TEXT(64) NULL | |
| `created_at`, `updated_at` | DATETIME | |

Индекс `ix_npc_plans_chat_id`.

### `consolidation_state` (§20/E)
Состояние консолидации памяти (адаптивный таймер/водяные знаки).

| колонка | тип | примечание |
|---|---|---|
| `id` | INTEGER PK | |
| `chat_id` | FK ON DELETE CASCADE | |
| `character_id` | FK ON DELETE CASCADE | |
| `last_consolidated_round_id` | TEXT(64) NULL | |
| `next_consolidation_at` | DATETIME NULL | |
| `consolidation_count` | INTEGER (default 0) | |
| `created_at`, `updated_at` | DATETIME | |

UNIQUE `(character_id)`. Индекс `ix_consolidation_state_chat_id`.

## Миграции данных

`ensure_schema` идемпотентно выполняет при старте:

- **memories**: добавляет `content_hash`, `importance`, `category`, `last_accessed_at`, `source_message_ids`, `embedding`; бэкафиллит хэши и удаляет дубликаты.
- **characters**: добавляет карточные поля (`speech_style`, `example_messages`, `boundaries`, `background`, `relationships`, `appearance`, `avatar_url`, `temperature`, `location`, `is_player`).
- **chats**: `thinking_mode`, `player_location`, `locations`.
- **messages**: `visibility`, `location`, `target_character_ids`, `channel`.
- **scene_states**: `character_locations` (и legacy-поля `location`, `present_character_ids`).
- **relationships**: для каждого чата с текстом в `characters.relationships` создаёт рёбра к остальным персонажам; для чатов без player-персонажа создаёт его (`order_index=9999`) и рёбра NPC→Player; удаляет legacy-рёбра Player→NPC.
- **перевод legacy значений**: англ. типы отношений (`friend`→`друг` и т.д.) и категории памяти (`event`→`событие` и т.д.) → русские.
- **relationship_issues**: `rounds_since_last_mention`.
- **WPE 3.0 (Фаза 0)**: `characters.location_id` (nullable FK), таблицы `world_events`/`threads`/`thread_participant_states` (+ индексы). Идемпотентно; новые таблицы read-path не читает (откат тривиален).
- **WPE 3.0 (Фаза 1)**: backfill `characters.location_id` из строковой `location` — `crud.backfill_character_location_ids` (идемпотентно; «Общая сцена» → NULL, нерезолвленное имя → NULL + отчёт на ручной разбор), запуск `scripts/backfill_location_ids.py`. Сам backfill — обычное обновление данных, не изменение схемы.
- **Sprint 0 (§16.1)**: `chats.original_plot/story_prompt/story_enabled`; `world_events.location_id` (+ индекс); 11 таблиц состояния (`CREATE TABLE IF NOT EXISTS` + индексы, см. выше). Идемпотентно; новые таблицы read-path не читает.
- **Sprint 0 (backfill сюжета)**: `crud.backfill_plot_fields` — копирует `general_prompt` → `original_plot`/`story_prompt`, `story_enabled=false`, заполняет только пустые поля (идемпотентно, отчёт `PlotBackfillReport`); запуск `scripts/backfill_plot_fields.py`. Поведение не меняется (сюжет по-прежнему читается из `general_prompt`).
- **Sprint 0 (backfill локаций событий)**: `crud.backfill_event_location_ids` — из строковой `world_events.location` через `resolve_location_name` (+ shared-scene правило `perception.is_shared_scene`), идемпотентно, отчёт `EventLocationBackfillReport` (нерезолвленные → NULL + список); запуск `scripts/backfill_event_location_ids.py`. Сам backfill — обновление данных, не изменение схемы.
- **Sprint 1 (§15)**: `world_events.action` (TEXT NOT NULL DEFAULT '{}'), `world_events.importance`/`story_salience`/`emotional_salience` (REAL NULL), `relationship_events.event_id` (FK → `world_events.id` ON DELETE SET NULL, nullable, + индекс `ix_rel_events_event_id`). Идемпотентно (только если колонки/индекс отсутствуют); пишет только раундная extraction при `EVENT_EXTRACTION_ENABLED=true`, откат — флаг off. Backfill не требуется.

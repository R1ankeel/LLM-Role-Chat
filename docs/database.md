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
| `location` | TEXT(255) | текущая локация |
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

### `message_presence`
Witness-присутствие персонажа для сообщения (кто видел/слышал событие).

| колонка | тип | примечание |
|---|---|---|
| `id` | INTEGER PK | |
| `message_id` | FK → `messages.id` ON DELETE CASCADE | |
| `character_id` | FK → `characters.id` ON DELETE CASCADE | |
| `presence` | TEXT(20) | `present` / `mentioned` / `absent` / `told` |

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
| `timestamp` | DATETIME | |

Индексы: `ix_rel_events_rel_id`, `ix_rel_events_ts`.

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

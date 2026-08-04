# World Events (Sprint 1)

Структурированные world-события: `world_events` как канонический источник
истины + пост-раундный pipeline извлечения. Дизайн — `Plans/update20.md §15`;
статус Sprint 1 — там же (§15.x «Статус»).

## Канонический источник истины (§15.0)

`world_events` — единственный канонический журнал событий. Все потребители —
**проекции** через `event_id` (FK → `world_events.id`): `story_events`,
`memories`, `relationship_events`, `memory_anchors`, `beliefs.world_truth_ref`.
Правило: одна строка на событие, проекции пишутся в пост-раунд pipeline в
фиксированном порядке (world_events → memories/anchors → relationship_events →
story_events → beliefs).

## Схема (§15.1)

`world_events` расширена колонками Sprint 1:

- `action` — TEXT NOT NULL DEFAULT `'{}'`; JSON `{actor, action, target, object}`;
- `importance` — REAL NULL (0..10);
- `story_salience` — REAL NULL (0..1);
- `emotional_salience` — REAL NULL (0..1).

Новая таблица `event_links` (рёбра причинности):

| колонка | тип | примечание |
|---|---|---|
| `id` | INTEGER PK | |
| `chat_id` | FK ON DELETE CASCADE | |
| `event_id` | FK → `world_events.id` ON DELETE CASCADE | следствие |
| `caused_by_event_id` | FK → `world_events.id` ON DELETE CASCADE NULL | причина |
| `kind` | TEXT(20) DEFAULT 'causes' | `causes` \| `consequence` \| `goal_step` \| `resolution` |

## Event Extraction (§15.2)

Пост-раундное структурированное извлечение «что произошло в раунде» из
раундных сообщений (LLM с JSON-schema, по образцу `extract_scene_state`).

### Поток

```
RAW MESSAGES → event_service.extract_round_events → crud.save_round_events
              → world_events (+ action/importance/salience) + event_links
```

1. `app/event_service.py:extract_round_events(client, db, chat_id, round_messages, ...)`
   — строит историю (Игрок/Имя/Система), зовёт Sensors (если активна
   `sensors_event_enabled`) или LLM `ollama_client.extract_round_events`
   (JSON-schema → fallback без schema). Возвращает `EventExtractionResult`
   (`events` + `sensors_used`).
2. `app/crud.py:save_round_events(db, chat_id, events, round_id=None)` —
   резолв имён персонажей (casefold) → `character_id`, локаций →
   `resolve_location_name` → `location_id` (неизвестные → NULL без падения);
   кламп салиенсов в 0..1; событие с `importance < EVENT_MIN_IMPORTANCE`
   отбрасывается; рёбра `event_links` пишутся из `ExtractedEvent.causes`.
   Возвращает `EventExtractionReport` (written/skipped/sensors/error).

### Sensors hook (§5.1.3)

При `sensors_event_enabled` + заданной `SENSORS_MODEL` Sensors предлагает
`{event_type, source_character, targets, location, importance, ...}`. Движок
применяет правила: салиенс 0.5 детерминированно, лимит важности, запись через
`crud.save_round_events`. Sensors **не пишет в БД** — только предложение;
недоступен/`None` → детерминированный LLM-путь.

### Флаги (`app/config.py`, `.env`)

| переменная | default | смысл |
|---|---|---|
| `EVENT_EXTRACTION_ENABLED` | `false` | мастер-флаг раундной extraction |
| `EVENT_EXTRACTION_MODEL` | `""` | модель для extraction (пусто = основная) |
| `EVENT_MIN_IMPORTANCE` | `3.0` | события ниже не пишутся |
| `sensors_event_enabled` | `false` | Sensors-предложение classification |

При `EVENT_EXTRACTION_ENABLED=false` extraction не вызывается, pipeline-стадия
event extraction — no-op; read-path новые поля не читает (откат = флаг off).

## Post-Round Pipeline (§15.x, Sprint 1)

`app/post_round_pipeline.py:run_post_round_pipeline(...)` — оркестратор стадий:

```
presence → event extraction → memory → relationships → story
```

- каждая стадия — отдельная функция в try/except (падение одной не ломает раунд);
- memory/relationship планируются как background-задачи (`asyncio.create_task`),
  коллбеки инъекцируются из `chat_engine` (без циклической зависимости);
- story — каркас (наполнение Sprint 8–11);
- параметр `stages` позволяет выполнить подмножество стадий (для тестов).

## Идемпотентность

Extraction для раунда определяется через `world_events.importance IS NOT NULL`
(движковые `speech`/`move` из `crud.create_message` importance не заполняют).
Повторный вызов для раунда не пишет дубли.

## Тесты

- `tests/test_event_extraction.py` — 11 (extraction валидна, links, лимит
  важности, идемпотентность, откат по флагу, Sensors-hook + fallback).
- `tests/test_post_round_pipeline.py` — 7 (стадии, подмножество, изоляция
  ошибок, идемпотентность, background-планирование).
- `tests/test_sprint1_schema.py` — 4 (миграции + идемпотентность, без потерь).

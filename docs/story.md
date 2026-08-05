# Dynamic Story State (Sprint 8)

Original Plot + Current Story State + Story History + Phase (Plans/update20.md §16).
Сюжет — отдельная ось поверх World Events: что **важно для истории**, а не всё
подряд. Хранится в `story_states`, `story_events`, `story_threads`
(таблицы созданы в Sprint 0, здесь — write-path и рендер).

## Принципы

- **Original Plot неприкосновенен**: его может менять только пользователь
  (`PATCH /api/chats/{chat_id}/story`), LLM write-path его не трогает
  (пишутся только `story_events` + `current_story`);
- **Исходный `general_prompt` не меняется**: при включённом story сцена берётся
  из `chats.story_prompt` (helper `chat_engine._chat_plot_text`), legacy-путь
  идентичен;
- **Защита контекста**: в STORY-блок идут только активные потоки top-K
  (`STORY_THREADS_MAX`) — контекст не разрастается.

## Пайплайн (пост-раунд стадия `story`)

```
round world_events ──► write_story_events_from_round (порог важности)
        │              • важные пары и мира → story_events
        │              • идемпотентность по event_id
        ▼
update_story_state_from_round
        │              • summary из top-K важных событий
        │              • потоки: создание / рост importance / дедуп по имени
        │              • story_phase сохраняется (меняется только пользователем)
        ▼
   story_states + story_threads + story_events
```

`app/post_round_pipeline.py` стадия `story` (после beliefs), обе функции в
try/except — падение не роняет раунд. Гейт: `STORY_ENABLED` (глобальный флаг)
И `chats.story_enabled` (перчатовый тумблер).

## Пороги сюжетности

| событие | условие записи |
|---|---|
| `story_events` | важность ≥ `STORY_EVENT_MIN_IMPORTANCE` (4.0) |
| создание/обновление `story_threads` | важность ≥ `STORY_THREAD_MIN_IMPORTANCE` (6.0) |

- дедупликация потоков по имени (casefold); importance растёт (max), actors
  мержатся без дублей; прогress = 0..1 по выпадающим событиям.

## Блок STORY (context)

При включённых флагах `context_builder._build_story_block` читает активные
потоки (`crud.get_active_story_threads`) и рендерит `<story>`-блок
(`prompt_builder.build_story_block`): фаза + top-K линий с прогрессом.
`BuiltContext.story_text` → `ollama_client` → оба пути (`_build_generation_messages`
и legacy `context_parts`).

## API (только пользователь)

- `GET /api/chats/{chat_id}/story` — текущее состояние сюжета (state + события + потоки);
- `PATCH /api/chats/{chat_id}/story` — пользовательские правки:
  - `original_plot` (и в `chats`, и в `story_state`), `story_phase`,
    частичный merge `current_story`, `story_enabled` / `story_prompt`;
  - при включении с пустым `story_prompt` — посев из `general_prompt`/`original_plot`.

## Флаги

| переменная | default | смысл |
|---|---|---|
| `STORY_ENABLED` | `false` | писать story_events + рендер STORY |
| `STORY_THREADS_MAX` | `5` | cap активных потоков в контекст |
| `STORY_EVENT_MIN_IMPORTANCE` | `4.0` | порог записи story_events |
| `STORY_THREAD_MIN_IMPORTANCE` | `6.0` | порог создания/обновления потоков |
| `STORY_SUMMARY_MAX_EVENTS` | `20` | cap событий в summary |

## Тесты

`tests/test_story_state.py`: запись story_events (порог, идемпотентность,
выключенный флаг); state (summary/потоки/прогресс, дедуп, рост importance,
фаза сохраняется, chat-disabled через `_stage_story`); рендер STORY (пусто/top-K);
`story_text` в BuiltContext; `_chat_plot_text` (story_prompt vs general_prompt);
API GET/PATCH/merge/404.

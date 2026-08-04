# Sensors Model (аналитический слой)

Отдельная лёгкая LLM (≈9B) для быстрых фоновых аналитических задач. Sensors —
**НЕ источник истины** и **НЕ подменяет** основную модель генерации реплик.
Подробный дизайн — `Plans/update20.md §5.1`; статус Sprint 0 — там же.

## Принцип

```
Sensors Model ──► предложения (perception/event/emotion/memory/relationship)
        │
        ▼
Game Engine Rules ──► валидация / нормализация / запись
        │
        ▼
Main Model (генерация персонажа) — без изменений
```

Sensors **не может** самостоятельно: изменять БД / писать память, менять
отношения или настроение, перемещать персонажей, определять доступную
информацию (это решает `perceive()`), генерировать финальную реплику.
Она возвращает структурированное предложение; итог всегда решает движок.

## Конфигурация (`app/config.py`, `.env`)

| переменная | значение по умолчанию | смысл |
|---|---|---|
| `SENSORS_MODEL` | `""` | модель Sensors; пусто = слой выключен |
| `sensors_enabled` | `false` | мастер-флаг |
| `sensors_perception_enabled` | `false` | задача Perception |
| `sensors_event_enabled` | `false` | задача Event classification |
| `sensors_emotion_enabled` | `false` | задача Emotion/Mood |
| `sensors_memory_enabled` | `false` | задача Memory extraction |
| `sensors_relationship_enabled` | `false` | задача Relationship analysis |
| `sensors_timeout` | `60.0` | таймаут вызова (сек) |

Задача активна только когда: `SENSORS_MODEL` задана **и** `sensors_enabled=true`
**и** `sensors_<task>_enabled=true`. Не влияет на `DEFAULT_MODEL`/`chat.model_name`.

## Сервис (`app/sensors_service.py`)

- `SensorsService.is_enabled(task)` — активна ли задача.
- `build_prompt(task, minimal_context)` — короткий специализированный prompt.
- `invoke(client, task, **kwargs)` — вызов через существующий `app/ollama_client.py`
  (`_build_chat_payload` / `_build_generate_payload`), с `format`=JSON-схемой задачи.
- `validate(result, schema)` — JSON-schema валидация (`app/sensors/schemas.py`).
- `run(client, task, **kwargs)` — `build_prompt → invoke → parse → validate`, возвращает
  валидированный результат или `None`.

Синглтон: `from app.sensors_service import sensors_service`.

## Схемы задач (`app/sensors/schemas.py`)

`SENSOR_SCHEMAS` — dict `task → JSON-schema` для `perception`, `event`,
`emotion`, `memory`, `relationship`. `get_schema(task)` возвращает схему,
`validate_sensor_result(result, schema)` — валидированный результат или `None`.
Валидация: требуемые поля (None допустим только если схема допускает `null`),
типы, enum, min/max, обязательные свойства вложенных объектов.

## Graceful degradation

Любая ошибка (модель недоступна, timeout, ошибка HTTP, некорректный JSON,
отсутствие схемы) → `None`. Основной игровой цикл не падает; используется
детерминированный путь (legacy). При `SENSORS_MODEL=""` слой полностью выключен,
поведение равно текущему.

## Инфраструктура (Sprint 0)

Sprint 0 заводит только каркас (конфиг, сервис, схемы, тесты) под флагом off.
К процессам **не подключено**: ни perception, ни memory, ни relationships не
вызывают Sensors. Подключение — по спринтам (§5.1.3): event classification —
Sprint 1 ✅, memory extraction — Sprint 2 ✅, emotion/mood — Sprint 3 ✅.

## Подключение: Emotion/Mood (Sprint 3)

`app/character_state.py::update_states_from_round` — при активной задаче
(`sensors_service.is_enabled("emotion")`: `SENSORS_MODEL` + `sensors_enabled` +
`sensors_emotion_enabled`) SensorsService предлагает `{emotion, intensity,
confidence, mood_delta}` (схема `emotion`). Движок `emotion_engine` применяет
предложение **только в рамках caps и правил**:

- сдвиг интенсивности ограничен `SENSORS_EMOTION_INTENSITY_CAP` (default 0.3)
  за раунд и clamp [0,1]; emotion из стандартного вокабуляра `EMOTIONS`;
- Sensors **НЕ задаёт mood напрямую** — `mood` всегда выводит `derive_mood`
  из emotional_state + stress (детерминированно);
- Sensors недоступен / невалидный JSON / неизвестная эмоция → `None`,
  детерминированный путь (graceful degradation, §5.1.8).

## Подключение: Perception proposal (Sprint 4)

`app/crud.py::compute_and_save_presence_for_round` — при `attention_enabled` и
активной задаче `perception` (`sensors_service.is_enabled("perception")`:
`SENSORS_MODEL` + `sensors_enabled` + `sensors_perception_enabled`) SensorsService
предлагает `{potential_visual, potential_audio, addressed, notice,
significance}` (схема `perception`, §5.1.3). Движок:

- использует `significance` (0..1) как подсказку к attention score: подъём
  ограничен `SENSORS_PERCEPTION_SIGNIFICANCE_CAP` (default 0.15);
- Sensors **не определяет** окончательный набор информации — доступность
  решает только существующий `perceive()`/presence-лестница;
- Sensors **не принимает** решение о внимании (пороги остаются движком);
- Sensors недоступен / невалидный JSON / timeout → `None`, детерминированный
  путь (graceful degradation, §5.1.8).

Подробно про attention-слой — `docs/attention.md`.

## Подключение: Memory extraction (Sprint 2)

`app/memory_service.py::_extract_and_save_memories` — при `sensors_memory_enabled`
и `SENSORS_MODEL` SensorsService предлагает кандидатов
`{facts: [{text, importance}]}` (схема `memory`, `get_schema(task="memory")`).
Движок прогоняет их через существующую `validate_extracted_facts`
(witness-фильтр, near-dup, лимиты) и сохраняет как обычные факты; Sensors память
САМ НЕ пишет и не определяет типы (`memory_type` присваивает движок). Sensors
недоступен → детерминированный LLM-путь (`extract_memories_for_character`).

## Подключение: Event classification (Sprint 1)

Когда активна `sensors_event_enabled`, Sensors предлагает classification
(тип/участники/локация/importance), а движок применяет игровые правила:
салиенсы клампятся в 0..1, `importance < EVENT_MIN_IMPORTANCE` отбрасывается,
события и рёбра `event_links` пишет `crud.save_round_events`. Sensors **не
пишет в БД** — только предложение; движок решает записать или нет (fallback на
LLM-путь основной модели, если Sensors недоступен или вернул `None`).

## Тесты

`tests/test_sensors.py` (§5.1.10): чтение `SENSORS_MODEL` из `.env`, изоляция от
основной модели, Sensors не подменяет генерацию, корректный/некорректный JSON,
timeout/ошибка не роняют цикл, Sensors не пишет в БД, при off — legacy-поведение.

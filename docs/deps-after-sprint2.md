# Зависимости после спринта 2 (`config/` и `models/`)

> Дата: 2026-08-08
> Источник: статический анализ импортов (`app/`) после разбиения монолитов
> `config.py` и `models.py` на пакеты.
> Назначение: артефакт сравнения «до/после» (п. 7 gate). Baseline для моделей
> и конфига — снапшоты в `Plans/artifacts/` (gitignored).

## 1. `app/config.py` → пакет `app/config/`

Монолит `Settings` (~250 полей) разбит на доменные миксины + композиция.

| Модуль | Содержание |
|---|---|
| `core.py` | `SettingsBase` (pydantic `BaseSettings`): base/url/model/история, num_ctx-окно, бюджет контекста |
| `memory.py` | память/консолидация/embedding (BM25, rerank-веса, RRF) |
| `context.py` | контекст-бюджет (`context_v2_enabled` и пр.) |
| `relationships.py` | отношения/issues/decay |
| `repetition.py` | детектор повторов |
| `wpe.py` | WPE/мировые события |
| `story.py` | сюжет/консолидация |
| `sensors.py` | Sensors-модель |
| `task_queue.py` | очередь задач |
| `avatar.py` | аватары |

- **Композиция:** `class Settings(SettingsBase, MemorySettings, ...)` — без
  `Field`-конфликтов; миксины — простые классы (не наследники `SettingsBase`),
  чтобы не ломать MRO/порядок полей.
- **Синглтон:** `settings = Settings()` в `config/__init__.py`; доступ
  `settings.<attr>` и `from app.config import settings` сохранены 1:1.
- **Верификация:** API пакета (5 символов: `BaseSettings`, `Field`, `Settings`,
  `SettingsConfigDict`, `settings`) совпадает до/после; все **276 полей** и их
  значения совпадают (`settings-fields-before.txt`, `settings-values-before.json`).
- `config/lora.py` из §4.9 decomposition.md **не создан** — в исходном
  `config.py` нет LoRA-полей (пустой модуль не нужен).

## 2. `app/models.py` → пакет `app/models/`

27 ORM-классов разбиты на 12 доменных модулей.

| Модуль | Классы |
|---|---|
| `chat.py` | `Chat` |
| `character.py` | `Character`, `CharacterSummary` |
| `message.py` | `Message` |
| `memory.py` | `Memory`, `MemoryAnchor`, `MemoryJob` |
| `relationship.py` | `CharacterRelationship`, `RelationshipEvent`, `RelationshipIssue` (+ default-константы) |
| `presence.py` | `MessagePresence` |
| `scene.py` | `Location`, `SceneState` |
| `world.py` | `WorldEvent`, `Thread`, `ThreadParticipantState` |
| `story.py` | `StoryState`, `StoryThread`, `StoryEvent`, `EventLink` |
| `state.py` | `CharacterState`, `Belief`, `ConsolidationState` |
| `intent.py` | `Intent`, `NpcPlan` |
| `lora.py` | `LoRAAdapter`, `ChatLoRAAdapter` |

- `models/__init__.py` реэкспортирует весь публичный API (50 символов), включая
  импорты `Base`, `settings`, типы SQLAlchemy — чтобы `from app.models import X`
  работал как раньше.
- **`Base.metadata` не изменился**: 27 таблиц идентичны (`metadata-tables-before.txt`),
  class→table без расхождений (`models-classes-before.txt`), `configure_mappers` OK,
  `init_db` на чистой (копии) БД без ошибок.
- `world.py` добавлен для `WorldEvent`/`Thread`/`ThreadParticipantState`
  (в §4.8 не был назван явно).

## 3. Зависимости после спринта 2

```
все модули → app.config  (singleton settings, неизменный)
все модули → app.models  (реэкспортный фасад, неизменный API)
```

Ни одна внешняя сущность не обращается к полям/классам «через путь» —
только через `from app.config import settings` / `from app.models import X`
(проверка шага 3 §3 decomposition-sprints.md). Новых циклов импортов нет:
`python -c "import app.main"` работает.

## 4. Тестовая база после спринта 2

- `pytest -q`: **1301 passed, 41 failed** — набор упавших **идентичен**
  монолитному состоянию до резки (41 пред-существующий LLM/env-зависимый фейл).
- Обновлён `tests/test_sensors.py::test_sensors_model_not_used_outside_service`:
  проверка путей переведена на относительные (`config/sensors.py` в allowed-set),
  27 тестов файла проходят.
- Golden-снапшоты и eval-набор не затронуты (перенос 1:1, без изменения кода).

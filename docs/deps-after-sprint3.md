# Зависимости после спринта 3 (`db/` и `schemas/`)

> Дата: 2026-08-09
> Источник: статический анализ импортов (`app/`) после переноса DDL из
> `database.py` в `db/` и разбиения `schemas.py` на пакет `schemas/`.
> Назначение: артефакт сравнения «до/после» (п. 7 gate). Baseline для `schemas/`
> — `Plans/artifacts/schemas-api-before.txt` (gitignored).

## 1. `app/database.py` → пакет `app/db/`

Монолит 1379 строк разбит **без изменений SQL**:

| Файл | Содержание |
|---|---|
| `db/engine.py` | URL движков, `engine`/`async_engine`, PRAGMA-листенеры, `SessionLocal`/`AsyncSessionLocal`, `Base`, `init_db`, `get_async_db`/`get_db`/`get_session_factory`/`get_async_session_factory` |
| `db/schema.py` | `INDEXES`, `_normalize_memory_content`, `memory_content_hash`, `_backfill_memory_hashes`, `ensure_schema` (~1220 строк DDL) |
| `database.py` | тонкий реэкспорт-фасад (15 символов в `__all__`) |

Проверка переноса (программная, `git show HEAD:app/database.py` vs
`app/db/schema.py`): 23 triple-quoted SQL-блока и 94 `text(...)`-выражения
совпадают байт-в-байт; DDL-счётчики идентичны (22 `CREATE TABLE`,
38 `ALTER TABLE`, 48 `CREATE INDEX`, 1 `CREATE UNIQUE INDEX`).

Граф зависимостей — **однонаправленный, циклов нет**:

```
db/engine.py  →  db/schema.py        (init_db вызывает ensure_schema)
db/schema.py  →  (только sqlalchemy / stdlib)
database.py   →  db/engine, db/schema  (реэкспорт)
```

Потребители не менялись: `from app.database import Base/AsyncSessionLocal/
init_db/get_async_db/memory_content_hash/...` работает через фасад.

## 2. `app/schemas.py` → пакет `app/schemas/`

Монолит 1375 строк разбит на 13 доменных модулей (+ `__init__.py`):

| Модуль | Содержание |
|---|---|
| `chat.py` | `Chat*`, `ChatDetail`, `ClearHistoryRequest`, `UserMessage`, `Intervention*` |
| `character.py` | `Character*`, `InitialRelationship`, `Location*`, `CharacterLocationUpdate`, `CharacterSummaryRead` |
| `message.py` | `Role`/`PresenceType`/`EventVisibility`/`CommunicationChannel`, `_normalize_visibility`, `Message*` |
| `memory.py` | `MemoryCategory`/`MemoryType`, `normalize_category`/`normalize_memory_type`, `Memory*`, `ExtractedFact` |
| `relationship.py` | `IssueType`/`ISSUE_TYPES`, `IssueDelta`, `RelationshipDelta`, `CharacterRelationship*`, `RelationshipEventRead`, `RelationshipIssue*` |
| `scene.py` | `Scene*`, `EventAction`, `ExtractedEvent`, `EventExtraction*` |
| `context.py` | `ContextBudget`, `DroppedItem`, `ContextDiagnostics`, `BuiltContext` |
| `job.py` | `MemoryJobRead` |
| `story.py` | `StoryThreadRead`, `StoryEventRead`, `StoryState*`, `StoryStateResponse` |
| `belief.py` | `BeliefSource`, `BeliefType`, `BeliefRead` |
| `state.py` | `CharacterStateRead` |
| `lora.py` | `LoRAAdapterFormat`, `LoRAAdapter*`, `ChatLoRAConfig` |
| `perception.py` | `VisualLevel`/`AudioLevel`/`RemoteStatus`/`ActionType`, `PerceptionResult`, `Action`, `TurnOutput`, `build_take_actions_*` |

### Ацикличность (проверена до резки, этап §4.2)

Внутренние зависимости модулей — только «вниз» и «в сторону», циклов нет:

```
schemas/chat.py        →  schemas/character.py, schemas/message.py, app.config
schemas/message.py     →  app.perception, app.stimuli, app.config
schemas/perception.py  →  schemas/message.py, app.perception
schemas/memory.py      →  app.config
schemas/relationship.py→  app.config   (локальный импорт внутри validator)
schemas/state.py       →  app.emotion_engine (локальный импорт внутри validator)
прочие модули          →  (без зависимостей на app/ и на schemas/)
```

- Единственная «стрелка вверх» наружу пакета — `app.perception → app.schemas`
  (`from .schemas import PerceptionResult`), и она **локальная** (внутри функции
  `perception.py:633`), поэтому при импорте `schemas/message.py →
  app.perception` цикла не возникает: `app.perception` не импортирует
  `app.schemas` на верхнем уровне.
- Два относительных импорта обновлены на новый уровень пакета:
  `relationship.py`/`state.py`: `from ..config import settings` /
  `from ..emotion_engine import normalize_emotional_state` (было `from .`).

## 3. Публичный API `schemas/` (сверка до/после)

- `schemas/__init__.py` реэкспортирует **97 символов** (`__all__`),
  `Plans/artifacts/schemas-api-before.txt` == `schemas-api-after.txt`.
- 67 классов совпадают (`schemas-classes-before.txt` == `-after.txt`).
- Различие только в `dir(app.schemas)` — появляются имена 13 подмодулей
  (`belief`, `chat`, `character`, ...); это атрибуты пакета, не API.

## 4. Тестовая база после спринта 3

- `pytest -q`: **1301 passed, 41 failed** — набор упавших в ветке — **строгое
  подмножество** набора на `HEAD` (git worktree, до резки: 43 failed; 2 лишних
  в HEAD — флаки `test_chat_engine.py::test_memory_extraction_with_snapshots_after_session_closed`
  и `test_per_character_memory_extraction_called`). Новых регрессий нет.
- `init_db` на копии `ai_chat.db` (без WAL) — OK, повторный прогон идемпотентен;
  `python -c "import app.main"` OK; `python -m compileall app` OK.
- Сервер: `GET /api/health` → `{"status":"ok"}`; ручной раунд чата (SSE):
  `message` → 50+ `token` → `message` (ответ NPC) → `done`.

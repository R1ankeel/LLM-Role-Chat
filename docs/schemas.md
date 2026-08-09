# Pydantic-схемы (`app/schemas/`)

> Дата: 2026-08-09 (Sprint 3 декомпозиции)
> Монолит `app/schemas.py` (1375 строк, 63 Pydantic-класса + type-alias'ы и
> нормализаторы) разбит на пакет `app/schemas/` **без изменения поведения**.
> Публичный API пакета зафиксирован списком символов (`__all__`, 97 имён) и
> сверен до/после (`Plans/artifacts/schemas-api-before.txt` == `-after.txt`,
> gitignored).

## 1. Состав пакета

| Модуль | Содержание |
|---|---|
| `chat.py` | `ChatBase/Create/Update/Read`, `ChatDetail`, `ClearHistoryRequest`, `UserMessage`, `InterventionCreate/Read` |
| `character.py` | `CharacterBase/Create/Update/Read`, `InitialRelationship`, `LocationBase/Create/Update/Read`, `CharacterLocationUpdate`, `CharacterSummaryRead` |
| `message.py` | общие Literal (`Role`, `PresenceType`, `EventVisibility`, `CommunicationChannel`), `_normalize_visibility`, `MessageBase/Create/Read`, `MessagePresenceCreate` |
| `memory.py` | `MemoryCategory`/`MemoryType`, `normalize_category`/`normalize_memory_type`, `MemoryBase/Create/Update/Read`, `ExtractedFact` |
| `relationship.py` | `IssueType`/`ISSUE_TYPES`, `IssueDelta`, `RelationshipDelta`, `CharacterRelationshipRead/Update`, `RelationshipEventRead`, `RelationshipIssueRead/Resolve` |
| `scene.py` | `SceneCustomState`, `SceneStateBase/Read/Update`, `EventAction`, `ExtractedEvent`, `EventExtractionResult/Report` |
| `context.py` | `ContextBudget`, `DroppedItem`, `ContextDiagnostics`, `BuiltContext` |
| `job.py` | `MemoryJobRead` |
| `story.py` | `StoryThreadRead`, `StoryEventRead`, `StoryStateRead/Update/Response` |
| `belief.py` | `BeliefSource`/`BeliefType`, `BeliefRead` |
| `state.py` | `CharacterStateRead` |
| `lora.py` | `LoRAAdapterFormat`, `LoRAAdapterCreate/Update/Read`, `ChatLoRAConfig` |
| `perception.py` | `VisualLevel`/`AudioLevel`/`RemoteStatus`/`ActionType`, `PerceptionResult`, `Action`, `TurnOutput`, `build_take_actions_tool`/`build_take_actions_json_schema` |

`schemas/__init__.py` реэкспортирует все доменные символы + служебные имена,
которые были доступны в прежнем модуле (`settings`, `parse_target_ids`,
`parse_stimuli`, `datetime`, `BaseModel`, `Field` и т.д.), чтобы
`from app import schemas; schemas.X` работал как раньше.

## 2. Внутренние зависимости и ацикличность

Схемы взаимно ссылаются друг на друга (например `BuiltContext` → `ContextBudget`,
`RelationshipDelta` → `IssueDelta`, `ChatDetail` → `CharacterRead`+`MessageRead`).
Порядок модулей выбран так, чтобы между модулями пакета не было циклов:

```
schemas/chat.py        →  schemas/character.py, schemas/message.py, app.config
schemas/message.py     →  app.perception, app.stimuli, app.config
schemas/perception.py  →  schemas/message.py, app.perception
schemas/memory.py      →  app.config
schemas/relationship.py→  app.config        (локальный импорт в validator)
schemas/state.py       →  app.emotion_engine (локальный импорт в validator)
прочие модули          →  без зависимостей
```

- Общие Literal сообщений живут в `message.py` — на них ссылаются `chat.py`,
  `memory.py`, `perception.py`; обратных ссылок на `chat.py` нет.
- `app.perception → app.schemas` (`from .schemas import PerceptionResult`) —
  **локальный** импорт внутри функции (`perception.py:633`), поэтому цикл
  `schemas/message.py → app.perception` при импорте не возникает.
- Два относительных импорта, обновлённых при переносе:
  `schemas/relationship.py` и `schemas/state.py` используют `from ..config`
  / `from ..emotion_engine` (было `from .`, соответствовавшее пути `app/schemas.py`).

## 3. Реэкспорт и совместимость

Потребители не менялись:

- `from . import schemas` / `from app import schemas` → `schemas.X`;
- `from .schemas import ContextBudget` (`app/context_budget.py`),
  `from .schemas import IssueDelta, RelationshipDelta` (`relationship_analyzer.py`),
  `from .schemas import ISSUE_TYPES, IssueDelta, MemoryCreate, RelationshipDelta`
  (`relationship_service.py`), `from .schemas import PerceptionResult`
  (`perception.py`, локально).

Проверка: 97 символов `__all__` совпадают до/после; 67 классов совпадают
(`schemas-classes-before.txt` == `-after.txt`); в `dir(app.schemas)` дополнительно
появляются имена 13 подмодулей (атрибуты пакета, не API).

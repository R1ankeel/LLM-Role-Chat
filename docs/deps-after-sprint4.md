# Зависимости после спринта 4 (`crud/`)

> Дата: 2026-08-09
> Источник: статический анализ импортов (`app/`) после разбиения `crud.py`
> на пакет `crud/`. Назначение: артефакт сравнения «до/после» (п. 7 gate).
> Baseline API — `Plans/artifacts/crud-api-before.txt` → `crud-api-after.txt`
> (gitignored).

## 1. `app/crud.py` → пакет `app/crud/`

Монолит 4313 строк (157 определений) разбит на 16 доменных модулей +
реэкспортный `__init__.py`-фасад. Состав пакета — см. [crud.md](crud.md).

Сервисные импорты в пакете отсутствуют (проверка спринта 1 сохраняется):
в `crud/*.py` нет `memory_service`/`embedding_service`/`witness_model`/
`perception`/`wpe_shadow`/`sensors_service`/`belief_service` на верхнем уровне.

## 2. Граф зависимостей внутри пакета

Взаимные зависимости `chats ↔ characters ↔ locations` разорваны
function-level импортами (`# против цикла модулей (Sprint 4)`):

| Модуль | Импорт | Уровень |
|---|---|---|
| `chats.py` | `characters: _sync_player_character_location, get_characters_by_chat` | верхний |
| `characters.py` | `chats: get_chat` | внутри функций |
| `characters.py` | `locations: get_chat_locations, get_location, resolve_location_name` | верхний |
| `locations.py` | `chats: get_chat` | внутри функций |
| `locations.py` | `characters: get_characters_by_chat` | внутри функций |
| `locations.py` | `scene: get_scene_state` | внутри функций |

Верхнеуровневый граф пакета — **ациклический** (статический обход AST):

```
characters → locations, threads
chats      → characters
messages   → threads
rounds     → characters, locations
state      → rounds
прочие     → без внутренних зависимостей
```

`python -c "import app.main"` OK — новых циклов на уровне всего приложения нет.

## 3. Публичный API `crud/` (сверка до/после)

- Все **157 символов** монолита доступны из пакета
  (`crud-api-before.txt` → `crud-api-after.txt`, пересечение 100%).
- Фасад дополнительно реэкспортирует `settings` (`app.config`) и временный
  фасад `memory.retrieval` (5 имён rerank/retrieval, спринт 1) — прежнее
  поведение `from app import crud; crud.X`.

## 4. Тестовая база после спринта 4

- `pytest -q`: **1301 passed, 41 failed** — набор упавших **идентичен**
  baseline'у (41 пред-существующий LLM/env-зависимый фейл); новых регрессий нет.
- `python -m compileall app` OK.
- 2 флаки `test_world_engine_phase7.py::test_streaming_*` (порядок wake-up при
  включённом event bus) падают и на монолите `HEAD:app/crud.py` — вне спринта 4.
- Сервер: `GET /api/health` → `{"status":"ok"}`; ручной раунд чата (SSE) OK.

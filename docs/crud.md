# CRUD-слой (`app/crud/`)

> Дата: 2026-08-09 (Sprint 4 декомпозиции)
> Монолит `app/crud.py` (4313 строк, 157 определений) разбит на пакет
> `app/crud/` **без изменения поведения** (перенос тел 1:1). Публичный API
> пакета зафиксирован списком символов и сверен до/после
> (`Plans/artifacts/crud-api-before.txt` → `crud-api-after.txt`, gitignored).
> Прошёл gate спринта 4: `pytest -q` — 41 failed / 1301 passed, набор упавших
> **идентичен** монолитному baseline'у; новых регрессий нет.

## 1. Состав пакета

16 доменных модулей + реэкспортный `__init__.py` (фасад, снимается в спринте 10):

| Модуль | Строк | Содержание |
|---|---|---|
| `chats.py` | 111 | `create_chat`/`get_chat(s)`/`update_chat`/`delete_chat` + clear-операции (messages/memories/relationships/world_events/threads/memory_jobs) |
| `characters.py` | 447 | персонажи (вкл. player, sync локаций игрока/чата, `create/get/update/delete`), `get_characters_by_chat`, `apply_character_actions` + `ApplyActionsResult` |
| `messages.py` | 181 | сообщения + `_build_world_event`, пагинация, `count_messages_after` |
| `memories.py` | 438 | память: witness-фильтр, rerank-буст, anchors, consolidation state, `_CONSOLIDATION_INPUTS` |
| `summaries.py` | 59 | character summary |
| `presence.py` | 131 | presence/attention карты (`upsert_message_presence_batch`, `get_presence_map`, …) |
| `locations.py` | 410 | локации: CRUD + adjacency + backfill (`LocationBackfillReport`, `PlotBackfillReport`, `EventLocationBackfillReport`) |
| `threads.py` | 153 | messenger-нити и доставка сообщений |
| `scene.py` | 91 | scene state + присутствие |
| `rounds.py` | 204 | раунды: lookup, `save_round_events`, clamp-хелперы |
| `events.py` | 227 | world events / round events / event links |
| `story.py` | 278 | story state / story events / story threads |
| `state.py` | 230 | character state + beliefs |
| `intents.py` | 89 | NPC intents |
| `plans.py` | 96 | NPC plans |
| `lora.py` | 205 | LoRA adapters + chat lora config |

(строки — фактические на момент ревизии 2026-08-09)

## 2. Циклы модулей и function-level импорты

Взаимные зависимости `chats ↔ characters ↔ locations` на верхнем уровне
разорваны **импортами внутри функций-потребителей** (помечены
`# против цикла модулей (Sprint 4)`):

```
chats.py       →  from .characters import _sync_player_character_location, get_characters_by_chat   (верхний уровень)
characters.py  →  from .chats import get_chat                                                       (внутри функций)
characters.py  →  from .locations import get_chat_locations, get_location, resolve_location_name    (верхний уровень)
locations.py   →  from .chats import get_chat                                                       (внутри функций)
locations.py   →  from .characters import get_characters_by_chat                                    (внутри функций)
locations.py   →  from .scene import get_scene_state                                                (внутри функций)
```

Верхнеуровневый граф пакета — **ациклический** (проверено статически):

```
characters → locations, threads
chats      → characters
messages   → threads
rounds     → characters, locations
state      → rounds
прочие     → без внутренних зависимостей
```

## 3. Перенос и восстановленные декораторы

- Тела всех функций/классов перенесены 1:1, без правок логики; менялись только
  импорты и структура модулей.
- Механический перенос не переносил строки декораторов `@dataclass` — восстановлены
  вручную вместе с импортом `dataclass`: `ApplyActionsResult` (characters.py),
  `LocationBackfillReport` / `PlotBackfillReport` / `EventLocationBackfillReport`
  (locations.py). Без них `PlotBackfillReport() takes no arguments` (TypeError).

## 4. Реэкспорт и совместимость

`crud/__init__.py` — временный фасад: реэкспортирует **все 157 символов**
прежнего монолита (проверено: пересечение `crud-api-before.txt` →
`crud-api-after.txt` 100%), плюс `settings` (из `app.config`) и временный фасад
`memory.retrieval` (`RerankContext`, `RerankSignals`, `build_rerank_signals`,
`get_hybrid_memories_for_characters`, `get_relevant_memories_for_characters`).

Потребители не менялись: `from app import crud; crud.X` и
`from . import crud` работают как раньше. Снятие фасада — спринт 10 (этап 19).

## 5. Проверка (gate спринта 4)

- `pytest -q`: **1301 passed, 41 failed** — набор упавших **идентичен** baseline'у
  (41 пред-существующий LLM/env-зависимый фейл); новых регрессий нет.
  Покрытие crud-логики (backfill, `apply_character_actions`, presence, scene,
  threads) полностью зелёное.
- `python -c "import app.main"` OK; `python -m compileall app` OK; сервер
  стартует, ручной раунд чата (SSE) OK.
- Известные флаки вне спринта: `test_world_engine_phase7.py::test_streaming_call_wakes_npc_out_of_order`
  и `test_streaming_repeated_addressing_ignored` (порядок wake-up при
  `world_engine_event_bus_enabled=True`) падают и на монолите `HEAD:app/crud.py`.

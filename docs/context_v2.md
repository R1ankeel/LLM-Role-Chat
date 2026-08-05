# Context Builder v2 (Sprint 13)

Приоритизированная сборка контекста: новые блоки WORLD / WHAT YOU KNOW /
WHAT YOU PERCEIVE / YOUR STATE / RELATIONSHIP / ACTIVE GOAL / RELEVANT MEMORY /
STORY под флагом-канарейкой `context_v2_enabled` (Plans/update20.md §23).

Код: `app/context_builder.py` (сборка), `app/context_budget.py` (токен-подбюджеты),
`app/prompt_builder.py` (рендер блоков), `app/ollama_client.py` (доставка блоков
в промпт), `app/config.py` (настройки).

## Гейт (canary)

`CONTEXT_V2_ENABLED=false` по умолчанию. При выключенном флаге работают все
legacy-пути: `<scene>` рендерится как раньше, отношения — в system-промпте,
`<character_memories>` остаётся. При включённом флаге:

- `<scene>` → **WORLD** (то же содержимое, новый блок);
- отношения в system → **RELATIONSHIP** в отдельном user-блоке;
- `<character_memories>` → **RELEVANT MEMORY** (reranked, п.14);
- новые блоки **WHAT YOU PERCEIVE** и подбюджеты.

Дублирующие старые блоки не рендерятся — контент не задваивается.

## Блоки и приоритеты

Приоритет подбюджетов (§23), все — мягкие, `recent_min` — целевой floor:

```
reserve (никогда не заполняется)
  → state (P0, scene/мировое состояние)
  → perception/recent (P0 — floor recent_min резервируется ДО P1/P2,
       чтобы тугой бюджет не заморил историю)
  → intent/goal (P1)
  → relationship (P1)
  → story (P1)
  → summary (P2)
  → memories (P2)
  → beliefs / WHAT YOU KNOW (P2)
  → retrieved history (P3)
```

Реализация `build_budget` (`app/context_budget.py`): recent-потолок
`CONTEXT_RECENT_MAX_TOKENS` резервируется сразу после `state`, затем
подбюджеты v2-блоков (`world`, `perceive`, `goal`, `relationship`, `story`,
`knowledge`) и legacy (`summary`, `relevant_memory`, `retrieval`). Сумма всех
подбюджетов не превышает `available = total − reserve`.

## Рендер блоков (`app/prompt_builder.py`)

| Блок | Функция | Приоритет | Источник | При flag on |
|---|---|---|---|---|
| `<world>` | `build_world_block` | P0 | `scene_states` + локации + co-present | заменяет `<scene>` |
| `<what_you_perceive>` | `build_perceive_block` | P0 | perception-строки раунда (`presence != absent`) | новый |
| `<your_state>` | `build_your_state_block` | P0 | `character_states` (Sprint 3) | как раньше |
| `<relationship>` | `build_relationship_block` | P1 | `build_relationships_block` + anchors | из system → user-блок |
| `<active_goal>` | — | P1 | intent (Sprint 10) | как раньше |
| `<story>` | — | P1 | `story_states` (Sprint 8) | как раньше |
| `<what_you_know>` | — | P2 | beliefs (Sprint 5) | как раньше |
| `<relevant_memory>` | `build_relevant_memory_block` | P2 | reranked memories (п.14) | заменяет `<character_memories>` |
| `<crisis>` | — | P1 | crisis lines (Sprint 11) | как раньше |

WORLD-блок не содержит World Truth — только то, что персонаж видит вокруг.
Персонажу НЕ выдаётся никакая мировая истина (неизменное правило изоляции).

## Сборка (`app/context_builder.py`)

В методе `build` флаг `v2 = settings.context_v2_enabled` определяет ветку:

- `scene_block=""` + заполняется `world_block`;
- `system_prompt` получает `relationships_block=""` (отношения уходят в user-блок);
- `perceive_block` — из presence-лестницы раунда: только строки, чьи
  `message_id` в `round_messages` и `presence != "absent"` (персонаж не видит
  того, чего не воспринял);
- `relationship_block` — из переданного `relationships_block`;
- `mem_block`/`relevant_memory_block` — через `build_relevant_memory_block`;
- `BuiltContext` заполняет новые поля `world_text`, `perceive_text`,
  `relationship_text`, `relevant_memory_text`.

Токены: `total_tokens`/`component_tokens` включают новые блоки; диагностика
`_log_context` логирует их и `v2=%s`. Overflow-Диагностика и финальное
усечение тоже считают `world` (переименованный `scene`).

## Доставка в промпт (`app/ollama_client.py`)

Сигнатура `generate()` не меняется — новые v2-блоки выводятся внутри
`_generate_once` из `built_context`, когда `settings.context_v2_enabled`:

- `scene = built_context.world_text` при v2, иначе `scene_text`;
- `memories = built_context.relevant_memory_text` при v2, иначе legacy;
- локальные `perceive_block`/`relationship_user_block` из `built_context`;
- `_build_generation_messages` получил параметры `perceive_block`,
  `relationship_block` (рендерятся после `scene_block`/перед `crisis_block`);
- legacy-конкатенация тоже их добавляет.

## Debug-контур (§29.1)

Новый read-only `app/routers/debug.py` (GET-только, отдаётся при
`DEBUG_ENABLED=true`, иначе 404):

| Endpoint | Отдаёт |
|---|---|
| `GET /chats/{id}/debug/state` | сводка: story_state, character_states, beliefs, intents, активные story_threads |
| `GET /chats/{id}/debug/beliefs?character_id=` | beliefs персонажа (тип, confidence, world_truth_ref) |
| `GET /chats/{id}/debug/threads?status=` | story_threads (active/archived) |
| `GET /chats/{id}/debug/events?limit=` | world_events + event_links (причинность) |
| `GET /chats/{id}/debug/anchors?relationship_id=` | memory_anchors |
| `GET /chats/{id}/debug/pipeline` | последний пост-раунд pipeline-отчёт (in-memory) |
| `GET /debug/{chat_id}` | HTML-страница `app/static/debug.html` |

CRUD-хелперы добавлены в `app/crud.py` (`get_world_events_for_chat`,
`get_event_links_for_events`, `get_story_threads_for_chat`,
`get_story_threads_by_status`) — никакой новой БД. Последний pipeline-отчёт
хранится в памяти (`remember_pipeline_report`, пишется из `chat_engine` после
`run_post_round_pipeline`), не персистится.

## Настройки (.env)

| Переменная | По умолчанию | Смысл |
|---|---|---|
| `CONTEXT_V2_ENABLED` | `false` | canary: вкл v2-сборку |
| `CONTEXT_V2_WORLD_BUDGET` | `3000` | подбюджет WORLD (P0) |
| `CONTEXT_V2_PERCEIVE_BUDGET` | `2500` | подбюджет WHAT YOU PERCEIVE (P0) |
| `CONTEXT_V2_RELATIONSHIP_BUDGET` | `2000` | подбюджет RELATIONSHIP (P1) |
| `CONTEXT_V2_GOAL_BUDGET` | `800` | подбюджет ACTIVE GOAL (P1) |
| `CONTEXT_V2_STORY_BUDGET` | `2000` | подбюджет STORY (P1) |
| `CONTEXT_V2_KNOWLEDGE_BUDGET` | `1500` | подбюджет WHAT YOU KNOW (P2) |
| `CONTEXT_V2_MEMORY_BUDGET` | `4000` | подбюджет RELEVANT MEMORY (P2) |
| `DEBUG_ENABLED` | `false` | вкл debug-контур §29.1 |

Legacy-настройки бюджета (`MAX_CONTEXT_TOKENS`, `CONTEXT_RECENT_*`,
`CONTEXT_STATE_BUDGET`, `CONTEXT_RESERVE_TOKENS` и т.п.) не меняются и
используются v2-веткой как общий пул + часть подбюджетов.

## Риски

- **Перерасход токенов** — новые блоки в отдельных подбюджетах; `max_context`
  НЕ увеличивается (явный запрет §23).
- **Изменение промпта** — canary: legacy-путь не тронут при off (проверено
  golden/интеграционными тестами).
- **World Truth** — WORLD и PERCEIVE строятся только из персонального
  восприятия; никакой мировой истины персонажу (правило изоляции R1).

## Тесты

`tests/test_context_v2.py` (13):

- подбюджеты v2 присутствуют и в пределах `total − reserve`; legacy-бюджет
  имеет нулевые v2-подбюджеты;
- v2 build: WORLD/PERCEIVE/RELATIONSHIP заполнены, `scene_text` пуст, в system
  отношений 0;
- RELEVANT MEMORY заполнен из `create_memory`;
- perceive только из round-строк (не из старой истории);
- flag off → legacy (`scene_text`, отношения в system, пустые v2-поля);
- усечение RELEVANT MEMORY под `context_v2_memory_budget`;
- golden-проверки `<world>`, `<what_you_perceive>`, `<relationship>`,
  `<relevant_memory>`;
- `test_v2_blocks_empty_when_flag_off`.

`tests/test_debug_router.py` (5): 404 при `debug_enabled=false`, форма
state-сводки, 404 для несуществующего чата, все вьюхи отвечают,
pipeline-хранилище (remember → read).

Полный прогон: **1125 passed / 25 failed** (25 пред-существующих падений — на
baseline те же; новых регрессий нет; 18 новых тестов проходят).

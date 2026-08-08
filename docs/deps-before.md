# Зависимости до спринта 1 (baseline)

> Дата: 2026-08-08
> Источник: статический анализ импортов (`app/` и `app/routers/`) на чистом
> дереве (последний коммит `80b8750 world fixes`).
> Назначение: артефакт сравнения «до/после» каждого спринта (п. 7 gate).

## 1. Карта слоёв (целевая)

```
router → pipeline (chat_engine) → services → crud → db
обособлены: prompt, context, memory, relationships, perception
```

## 2. Фактическое состояние на старте спринта 1

### 2.1 Цикл `crud ↔ сервисы` (главный предмет развязки)

`crud.py` (4365 строк) импортирует сервисный слой **и на верхнем уровне, и
локально** — обратная стрелка от `crud` к сервисам нарушает целевое направление:

| Импорт в `crud.py` | Тип | Используется в |
|---|---|---|
| `embedding_service` | верхнеуровневый | `get_relevant_memories_for_characters`, `get_hybrid_memories_for_characters`, `_apply_rerank`, `build_rerank_signals` |
| `memory_service` | верхнеуровневый | `get_relevant_memories_for_characters`, `get_hybrid_memories_for_characters`, `_apply_rerank`, `build_rerank_signals` |
| `perception` | верхнеуровневый | `create_message` (serialize_target_ids), location-хелперы, presence-функции |
| `witness_model` | верхнеуровневый | `filter_memories_by_witness`, `compute_and_save_presence_*` |
| `wpe_shadow` | локальный (стр. 459) | `create_message` → `run_shadow_perception` |
| `attention` | локальный (стр. 1609) | `_attention_score_for` |
| `sensors_service` | локальный (стр. 1861) | `compute_and_save_presence_for_message/round` |
| `belief_service` | локальный (стр. 3354) | `upsert_belief` → `merge_confidence` |

**Потребители сервисных функций `crud`:** `chat_engine.py`
(`build_rerank_signals`, `get_hybrid_memories_for_characters`,
`get_relevant_memories_for_characters`, `compute_and_save_presence_for_message`,
`create_message`), `post_round_pipeline.py`
(`compute_and_save_presence_for_round`), `context_builder.py` (использует
`memory_service` напрямую), тесты `test_embeddings.py`, `test_hybrid_rerank.py`.

### 2.2 `task_queue ↔ memory_service` (предмет шага 5)

`task_queue.py` импортирует `memory_service` на верхнем уровне и `_dispatch_job`
напрямую вызывает `_process_post_round_job`/`_process_consolidation_job`/
`_process_embed_memory_job`/`_process_backfill_embeddings_job` (приватные
обработчики). Тесты `test_task_queue.py` уже ожидают сигнатуру
`run_job(job, handler)` — расхождение кода и тестов (9 фейлов в baseline).

### 2.3 `wpe_shadow ↔ crud` (предмет шага 6)

`crud.create_message` вызывает `wpe_shadow.run_shadow_perception`; `wpe_shadow`
импортирует `crud` локально — двунаправленная связь.

### 2.4 Приватные LLM-функции (предмет шага 7)

`relationship_analyzer.py` импортирует приватные `_invoke_llm` и
`_extract_json_payload` из `ollama_client`. `sensors_service.py` использует
`_build_chat_payload`/`_build_generate_payload`/`llm_request` (приватные/низкие).
Оба — кандидаты на публичный фасад `llm/generation.py::invoke_json`.

### 2.5 `relationship_service ↔ crud` (предмет шага 4)

Направление уже «сервис → crud», но импорты локальные внутри функций
(стр. 596, 618, 698, 744, 1427, 1683, 1780) — требуется вынос на верхний
уровень и явный интерфейс `memory/` для создания памяти из событий.

## 3. Обособленные группы (не в целевом «стволе»)

- `prompt_builder.py` → только `role_isolation`
- `context_builder.py` → `crud`, `memory_service`, `perception`, `witness_model`
  (потребитель сервисов — не источник цикла)
- `perception.py` → `stimuli`, `config`
- `witness_model.py` → `perception`, `prompt_builder`, `stimuli`, `config`
- `relationship_service.py` → `crud`, `models`, `schemas`, `prompt_builder`
- `memory_service.py` → `crud`, `embedding_service`, `models`, `ollama_client`,
  `schemas`, `task_queue`, `witness_model` (загружен, но не источник цикла
  с `crud`; цикл создаёт именно `crud` → `memory_service`)

## 4. Циклические/нарушенные связи (подлежат устранению в спринте 1)

1. `crud → memory_service` и `crud → embedding_service` (верхнеуровневые).
2. `crud → perception / witness_model / attention / sensors_service /
   belief_service` (верхнеуровневые + локальные).
3. `crud → wpe_shadow → crud` (двунаправленная).
4. `task_queue → memory_service._process_*` (диспетчер знает обработчики).
5. `relationship_analyzer → ollama_client._invoke_llm/_extract_json_payload`
   (приватные через границу).
6. `memory_service → task_queue` (в обработчиках) + `task_queue → memory_service`
   (в диспетчере) — требуется развязать через handler-registry.

## 5. Тестовая база (baseline)

- `pytest -q`: **1293 passed, 49 failed** (~12:58).
- Список фейлов: `C:\Users\user\AppData\Local\Temp\opencode\sprint1-baseline-failures.txt`
- Golden-снапшоты `tests/golden/*.json` (SHA-256):
  - `role_isolation_snapshots.json` → `48061D57D825F830B6EFF256EFD3E88201790BC8300C2A367A846F3291A08BB7`
  - `snapshots.json` → `771AC28B872340E8955D40E14FB801B563C2AEFCA668133CC7EC01674BC902DB`
  - `snapshots_iso.json` → `9704B46CD2DEA4D2BC427B9038E91D19DD66783CF475BC0A27053ED6D565B6E8`
  - `test_constants.json` → `4C37AAECB8FC9D68218A401B0FCD37BF8F49C4119567108924402D672CF479D4`
- Eval-набор `tests/eval/`: **не прогоняется до конца в mock-режиме без
  запущенного ollama** — сенсоры/отношения делают реальные сетевые вызовы
  (`getaddrinfo failed`). Зафиксирован как ограничение окружения, не регресс.
- Golden-тесты `test_prompt_builder_golden.py`, `test_role_isolation_golden.py`
  проходят на baseline (входят в 1293 passed).

## 6. Ключевые потребители публичного API (для сверки после переноса)

| Символ | Потребители |
|---|---|
| `crud.build_rerank_signals` | `chat_engine.py:823,2855`; `test_hybrid_rerank.py` |
| `crud.get_hybrid_memories_for_characters` | `chat_engine.py:834,2866`; `test_embeddings.py`; `test_hybrid_rerank.py` |
| `crud.get_relevant_memories_for_characters` | `chat_engine.py:843,2877`; `test_hybrid_rerank.py` |
| `crud.compute_and_save_presence_for_message` | `chat_engine.py:759,1424,3215` |
| `crud.compute_and_save_presence_for_round` | `post_round_pipeline.py:55` |
| `crud.create_message` | `chat_engine.py` (7 точек) |
| `memory_service.SimpleBM25/RerankSignals/RerankContext/rerank_memories` | `context_builder.py:148,459,461,745` |
| `memory_service.rerank_weights` | `test_hybrid_rerank.py` |

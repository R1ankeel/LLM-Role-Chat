# Зависимости после спринта 1 (развязка циклов)

> Дата: 2026-08-08
> Источник: статический анализ импортов (`app/` и `app/routers/`) после
> коммитов `9ee9bba`, `2416bff`.
> Назначение: артефакт сравнения «до/после» (п. 7 gate) — фиксирует, что
> убрано из `crud.py` и какие слои стали односторонними. Baseline — в
> [deps-before.md](deps-before.md).

## 1. Что изменилось по сравнению с baseline

| # | Предмет (§2 decomposition-sprints.md) | Было (deps-before) | Стало |
|---|---|---|---|
| 1 | `crud ↔ memory_service` | `crud` импортировал `memory_service`, `embedding_service` | BM25/rerank/гибридный поиск перенесены в `app/memory/retrieval.py`; из `crud.py` верхнеуровневые импорты удалены; для потребителей оставлен фасад-реэкспорт `crud.get_*` |
| 2 | `crud` → WPE/сервисы | локальные импорты `perception`, `witness_model`, `attention`, `wpe_shadow`, `sensors_service`, `belief_service` | из `crud.py` удалены; presence/attention пересчитываются в `post_round_pipeline.py`; чистые хелперы локаций — в `app/perception_utils.py` |
| 3 | `crud ↔ wpe_shadow` | двунаправленная (`crud.create_message` → `wpe_shadow`) | shadow-триггер перенесён в `chat_engine._create_message_with_shadow` → `wpe_shadow.maybe_run_shadow_perception`; направление сервис → crud |
| 4 | `task_queue ↔ memory_service` | диспетчер вызывал `memory_service._process_*` напрямую | handler-registry (`register_handler`/`get_handler`); `task_queue` не импортирует `memory_service` |
| 5 | private LLM-функции | `relationship_analyzer`, `sensors_service` использовали `_invoke_llm`/`_build_*` | публичный фасад `llm/generation.py::invoke_json`/`extract_json_payload` |
| 6 | `relationship_service ↔ crud` | локальные импорты `crud` внутри функций | импорты на верхнем уровне; память из событий — через `memory/create.py::create_memory` |
| 7 | `belief_service ↔ crud` | `crud` вызывал `belief_service.merge_confidence` | `merge_confidence` перенесена в `crud`; направление сервис → crud |

## 2. Текущее направление зависимостей

```
router → chat_engine → services → crud → models/database
                     (post_round_pipeline, memory_service,
                      relationship_service, belief_service)
обособлены: perception (→ perception_utils, stimuli), witness_model,
            prompt_builder, memory (→ crud), llm (→ ollama_client)
```

### 2.1 `crud.py` после развязки

Импортирует только `models`, `schemas`, `perception_utils`, `config`,
`database`, `lora_validation`, `memory.retrieval` (фасад для потребителей).
**Сервисных импортов нет** — проверено `rg`.

### 2.2 Новые модули спринта 1

- `app/perception_utils.py` — чистые хелперы локаций/адресатов (без DB/LLM);
  `perception.py` реэкспортирует их (публичный API модуля не изменился).
- `app/memory/retrieval.py` — `SimpleBM25`, `rerank_*`, гибридный поиск (RRF);
  работает поверх `crud` (направление memory → crud).
- `app/memory/create.py` — публичный интерфейс `create_memory` для сервисов.
- `app/llm/generation.py` — фасад `invoke_json`/`extract_json_payload`
  (закрывает приватные функции `ollama_client`).

### 2.3 `post_round_pipeline.py` теперь владелец presence/attention

Перенесены из `crud.py`: `compute_and_save_presence_for_message`,
`compute_and_save_presence_for_round`, `_attention_score_for`,
`_chat_world_state_for_characters`. Направление: pipeline → crud.

### 2.4 `task_queue.py` после handler-registry

`run_job(job, handler=None)` — обработчик берётся из registry по
`job.job_type` либо передаётся явно (так и делают тесты). Цикл
`task_queue ↔ memory_service` разорван.

## 3. Разрешённые остатки (сознательно сохранены до спринта 10)

- Фасад-реэкспорты `crud.get_relevant_memories_for_characters` и др. (для
  `chat_engine`/тестов) — удаляются на этапе 19.
- `memory_service.py` продолжает импортировать `task_queue` (обработчики
  регистрируются именно там) — это легальное направление сервис → task_queue.

## 4. Тестовая база после спринта 1

- `pytest -q`: **1301 passed, 41 failed** (после перевода `test_task_queue.py`,
  `test_attention.py` и др. на async-сессии). Набор упавших — те же
  LLM/env-зависимые фейлы, что и на baseline (см. §5 deps-before).
- Golden-снапшоты и eval-набор не затронуты переносом (код не менялся по логике).

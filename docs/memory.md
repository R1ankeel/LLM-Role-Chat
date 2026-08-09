# Слой памяти (`app/memory/`)

> Дата: 2026-08-10 (Sprint 6, Milestone 6C декомпозиции)
> Монолит `app/memory_service.py` (2191 строка) разрезан на пакет `app/memory/`
> **без изменения поведения** (перенос тел 1:1 по диапазонам §4.5; менялись
> только импорты). Прошёл gate 6C: прогон memory-подмножества тестов — 16 failed /
> 90 passed, набор упавших **идентичен** baseline'у до переноса; новых регрессий нет.

## 1. Состав пакета

9 доменных модулей. `__init__.py` — **намеренно пуст** (только docstring, см. §3):

| Модуль | Строк | Содержание |
|---|---|---|
| `retrieval.py` | 618 | гибридный поиск (Sprint 1): BM25, RRF-объединение, `rerank_memories` (оси lexical/semantic/emotional/story/relationship/recency/salience), `get_observable_context`-зависимые выборки |
| `create.py` | 25 | интерфейс создания памяти (Sprint 1) |
| `validation.py` | 387 | `classify_memory_type`, `validate_extracted_facts`, `fact_grounding_overlap`, `jaccard_similarity`, константы паттернов (generic/false-me/other-mind/story) |
| `witness.py` | 189 | witness-слой: `get_observable_context_for_character`, фильтрация текста памяти по presence, `_sensors_proposal_to_facts` |
| `extraction.py` | 208 | `_extract_and_save_memories` (извлечение фактов после раунда + сохранение, дедуп по валидации) |
| `summaries.py` | 123 | `_maybe_update_summaries` (обновление саммари сессий) |
| `consolidation.py` | 231 | кластеризация/слияние памяти: `_cluster_memories_by_similarity`, `_consolidate_character_memories`, `consolidate_memories_job` |
| `adaptive.py` | 588 | score-based адаптивная консолидация: `compute_consolidation_score`, `evaluate_consolidation`, `consolidate_chat_adaptive`, `schedule_adaptive_consolidation` (детали — `adaptive_consolidation.md`) |
| `jobs.py` | 279 | handler'ы фоновых задач: `process_post_round`, `_process_consolidation_job`, `_process_embed_memory_job`, `_process_backfill_embeddings_job` (+ регистрация в `task_queue`) |

(строки — фактические на момент ревизии 2026-08-10)

`app/memory_service.py` — тонкий фасад (100 строк): docstring + реэкспорт
публичного API из `memory/*` + legacy-имена для patch-контрактов
(`settings`, `AsyncSessionLocal`, `ollama_client`, `task_queue`). Потребители
`from . import memory_service` и `from .memory_service import X` не менялись.

## 2. Зависимости внутри пакета

Межмодульные ссылки (ацикличны, направление — от «слоя задач» к «листьям»):

```
jobs        → extraction, summaries, consolidation, adaptive
adaptive    → consolidation
consolidation → validation
extraction  → validation, witness
summaries   → witness
validation  → retrieval            (токенизация/совпадение имён)
witness     → без внутрипакетных    (внешний witness_model)
retrieval   → без внутрипакетных
create      → без внутрипакетных
```

Внешние зависимости пакета (наружу из `app.memory`):

```
jobs        → embedding_service, models, task_queue, database(AsyncSessionLocal), config
adaptive    → crud, embedding_service, models, ollama_client, config
consolidation→ models, ollama_client, config
extraction  → crud, ollama_client, schemas, task_queue, database, config
summaries   → crud, ollama_client, database, config
retrieval   → crud, embedding_service, models, config
create      → crud, schemas
validation  → schemas, config
witness     → schemas, witness_model
```

Направление сохраняется: `memory` — сервисный слой; его вызывают
`chat_engine`/`pipeline`, роутеры, `task_queue`; обратных ссылок на них нет.
Исключение — Sprint 1 цикл `crud ↔ memory.retrieval` (см. §3).

## 3. Почему `__init__.py` пуст (Sprint 1 цикл)

`app/crud/__init__.py:13` импортирует `memory.retrieval` (временный фасад,
Sprint 1, §7.1), а `memory/retrieval.py` делает `from .. import crud` (доступ к
атрибутам — только в момент вызова). Цикл «выживает» именно за счёт того, что
`app.memory/__init__` ничего не реэкспортирует.

Реэкспорт через `__init__` тянет `jobs.py`, который **на верхнем уровне**
вызывает `task_queue.register_handler(...)` — в момент, когда `app.crud`
ещё находится в partial-init и `app.task_queue` не закончил инициализацию
(`task_queue` → `from . import crud`), получается `AttributeError`/`ImportError`
(проверено на ревизии 2026-08-10, реверт в `__init__`).

Поэтому публичный API `memory_service` **зафиксирован фасадом**
`app/memory_service.py` (явный список реэкспорта — контракт) и прямыми
импортами из подмодулей (`memory.retrieval`/`memory.create` — потребители
`crud`, роутеры). Снятие фасада и фиксация API в `__init__` — спринт 10
(этап 21 декомпозиции), вместе с `ollama_client.py`/`relationship_service.py`.

## 4. Проверка (gate 6C)

- Прогон memory-подмножества (`test_memory_*`, `test_consolidation.py`,
  `test_adaptive_consolidation.py`, `test_task_queue.py`, `test_hybrid_rerank.py`,
  `test_witness_filter.py`, `test_memory_service.py`, `test_chat_engine.py`):
  **16 failed / 90 passed** — набор упавших **идентичен** baseline'у на коммите
  `ad16356` (до переноса 6C); новых регрессий нет.
- Patch-таргеты тестов переведены с фасада на модули пакета:
  `app.memory_service.{SessionLocal,AsyncSessionLocal}` →
  `app.memory.{extraction,summaries}.AsyncSessionLocal`;
  `app.memory_service.{enqueue_consolidation_job,consolidate_memories_job}` →
  `app.memory.jobs.*`.
- `python -c "import app.main"` OK; `python -m compileall app` OK; сервер
  стартует, ручной раунд чата (SSE) OK.

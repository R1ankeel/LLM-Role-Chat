# Зависимости после спринта 6 (6A: `pipeline/relations.py`; 6B: `relationships/`; 6C: `memory/`)

> Дата: 2026-08-10
> Источник: статический анализ импортов (`app/`) после спринта 6 декомпозиции.
> Три независимых переноса, каждый закрыт собственным gate (6A → 6B → 6C):
> анализ отношений ушёл из `chat_engine.py` в `pipeline/`, сервис отношений и
> сервис памяти разрезаны на доменные пакеты **без изменения поведения**.
> Состав пакетов — см. [relations.md](relations.md) (система отношений),
> [memory.md](memory.md), [retrieval.md](retrieval.md).

## 1. Что сделано в спринте 6

- **6A — `app/pipeline/relations.py` (999 строк).** Из `chat_engine.py`
  перенесены `_analyze_and_update_relationships`,
  `_run_sensors_relationship_proposal`, `_run_per_pair_analysis` и все хелперы
  анализа (evidence/constrain/mentions/scene-summary/belief/hearsay).
  `chat_engine.py` стал фасадом (74 строки): реэкспорт API из
  `pipeline/{relations,streaming,session,story,lora,regeneration}.py`.
- **6B — `app/relationship_service.py` → `app/relationships/`.** 8 доменных
  модулей (`crud`, `validation`, `deltas`, `blocks`, `issues`, `decay`,
  `memory_feed`, `trajectory`) + `__init__.py` (135 строк) с зафиксированным
  публичным API. `relationship_service.py` — тонкий фасад (114 строк).
- **6C — `app/memory_service.py` → `app/memory/`.** 7 новых доменных модулей
  (`validation`, `witness`, `extraction`, `summaries`, `consolidation`,
  `adaptive`, `jobs`) + существовавшие со Sprint 1 `retrieval`/`create`.
  `memory_service.py` — тонкий фасад (100 строк); `memory/__init__.py` пуст
  намеренно (Sprint 1 цикл `crud ↔ memory.retrieval`, см. [memory.md](memory.md) §3).

## 2. Граф внутри пакетов (после спринта 6)

`pipeline/` — ацикличен, `relations.py` листовой по отношению к остальным
(подтягивается из `story.py`/`streaming.py` ленивыми импортами):

```
streaming → session, regeneration, relations(лениво)
story     → relations(лениво: _build_pair_relationship_context, _evidence_mode)
relations → без импортов других модулей pipeline/
```

`relationships/` — ацикличен, направление сервис → crud одностороннее:

```
blocks     → crud, issues
deltas     → crud, validation, issues, memory_feed
issues     → crud, memory_feed
decay      → app.crud
trajectory → deltas
memory_feed→ app.memory.create
```

`memory/` — ацикличен, направление от слоя задач к листьям:

```
jobs        → extraction, summaries, consolidation, adaptive
adaptive    → consolidation
consolidation → validation
extraction  → validation, witness
summaries   → witness
validation  → retrieval
```

## 3. Направления между пакетами

Ключевые однонаправленные связи после спринта 6 (без новых циклов):

```
pipeline (streaming/story) → memory (extraction)   # извлечение памяти из раунда
relationships → memory (memory_feed → memory.create)  # память из событий отношений
relationships → crud      # чтение/запись отношений
pipeline (relations) → relationships  # применение дельт (через relationship_service/фасад)
memory → crud            # чтение/запись памяти
```

Известный до-существующий цикл (Sprint 1, не новый): `crud → memory.retrieval`
и `memory.retrieval → crud` — «выживает» за счёт пустого `memory/__init__.py`
и отложенного доступа к атрибутам `crud` в `retrieval.py` (см. [memory.md](memory.md) §3).

Фасады монолитов (удаляются в спринте 10, этапы 19–21): `app/chat_engine.py`,
`app/relationship_service.py`, `app/memory_service.py`, `app/ollama_client.py`.

## 4. Публичные API пакетов

- `pipeline/` — `app/pipeline/__init__.py` (импорты подмодулей + `__all__`,
  зафиксирован в спринте 5B).
- `relationships/` — `__all__` в `app/relationships/__init__.py` (136 строк),
  включая private-символы для patch-контрактов тестов.
- `memory/` — контракт в фасаде `app/memory_service.py` (реэкспорт); прямой
  доступ к `memory.retrieval`/`memory.create` сохранён (потребители `crud`,
  роутеры).

## 5. Тестовая база после спринта 6

- 6A/6B: полный прогон `test_relationship_*`, `test_relationship_issues*.py`,
  `test_relationship_service.py`, `test_relationship_context.py` — зелёные.
- 6C: memory-подмножество — **16 failed / 90 passed**, набор упавших
  **идентичен** baseline'у (коммит `ad16356`) — пред-существующие
  LLM/env-зависимые фейлы; новых регрессий нет.
- `python -c "import app.main"` OK; `python -m compileall app` OK; ручной раунд
  чата (SSE) OK.

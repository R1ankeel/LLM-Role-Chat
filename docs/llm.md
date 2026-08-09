# LLM-слой (`app/llm/`)

> Дата: 2026-08-09 (Sprint 5, Milestone 5A декомпозиции)
> Монолит `app/ollama_client.py` (3032 строки) разрезан на пакет `app/llm/`
> **без изменения поведения** (перенос тел 1:1 по диапазонам §4.3). Публичный
> API пакета зафиксирован списком символов и сверен до/после
> (`Plans/artifacts/ollama-client-api-before.txt` →
> `llm-api-after.txt`, gitignored). Прошёл gate 5A: `pytest -q` — 41 failed /
> 1301 passed, набор упавших **идентичен** монолитному baseline'у; новых
> регрессий нет.

## 1. Состав пакета

7 доменных модулей + реэкспортный `__init__.py` (фасад, снимается в спринте 10):

| Модуль | Строк | Содержание |
|---|---|---|
| `lock.py` | 28 | глобальная сериализация Ollama-запросов: `_llm_locks`, `_llm_lock_for` |
| `transport.py` | 548 | HTTP-транспорт: `_call_ollama`/`_call_ollama_chat`, `_stream_*`, `_read_ollama_error`, `llm_request`, `_ConfigProxy`, legacy-константы (ретраи, таймауты, флаги) |
| `prompting.py` | 364 | форматирование истории, payload-билдеры: `_build_generate_payload`, `_build_chat_payload`, `_build_generation_messages`, `format_history_for_character` |
| `generation.py` | 1344 | `_invoke_llm`, `_generate_once`, `generate`, vocabulary borrowing, публичный JSON-фасад `invoke_json`/`extract_json_payload` |
| `tasks.py` | 645 | извлечение памяти, суммаризация, scene-state, event extraction (`extract_memories_for_character`, `summarize_for_character`, `extract_scene_state`, `extract_round_events`) |
| `wpe.py` | 187 | tool-calling: `_tool_mode_chain`, `_next_tool_mode`, `_parse_tool_calls`, `_parse_turn_output_json`, `WPE_TOOLS_STATS` (shadow-метрики Фазы 2) |
| `models.py` | 160 | `list_models`/`create_model`/`delete_model`/`upload_adapter_file`/`check_capabilities` |

(строки — фактические на момент ревизии 2026-08-09)

`app/ollama_client.py` — тонкий фасад (13 строк): `from .llm import *` +
двумя явными импортами `invoke_json`/`extract_json_payload`. Потребители
(`from . import ollama_client` и `from .ollama_client import X`) не менялись.

## 2. Зависимости внутри пакета

Между модулями `llm/*` **нет верхнеуровневых импортов друг друга** — все
межмодульные символы подтягиваются через `__init__.py`-фасад или локальными
импортами внутри функций (например, `from ..lora_manager import RuntimeCapabilities`
в `models.py:144` — локально, против цикла). Граф пакета — **ациклический**
(проверено статически; `python -c "import app.main"` OK).

Внешние зависимости пакета (наружу из `app.llm`):

```
generation → config, context_budget_manager, prompt_builder,
             repetition_detector, role_isolation, witness_model
prompting  → config, prompt_builder, repetition_detector, schemas,
             token_counter, witness_model
tasks      → config, prompt_builder
models     → config, lora_manager (только локальный импорт)
transport  → config
wpe        → config
lock       → без внешних зависимостей
```

Направление сохраняется: `llm` — нижний LLM-слой; сервисы
(`memory_service`, `event_service`, `chat_engine`, `lora_manager`,
`plot/*`) вызывают его, но не наоборот.

## 3. Публичный API (сверка до/после)

- `ollama-client-api-before.txt` (144 символа, `dir()` монолита) →
  `llm-api-after.txt` (93 символа, `dir()` фасада без `__dunder__`).
- Разница — за счёт имён 7 подмодулей пакета + stdlib-импорты (`asyncio`,
  `httpx`, `json`, `re`, `time`, `logging`, `WeakKeyDictionary` и т.п.),
  которые больше не живут в фасаде, и внутренних хелперов (`build_*`-билдеры
  промптов, `filter_history_for_character*`, `sanitize_and_validate_response`,
  `analyze_response`, `merge_char_locations`, `get_token_counter` и др.),
  переехавших в модули `llm/*`.
- **Проверено программно:** ни один внешний потребитель не ссылается на
  отсутствующие в фасаде символы (regex-скан по `app/` вне `app/llm`:
  0 обращений к 53 «недостающим» именам). Все символы, реально используемые
  наружу (`generate`, `llm_request`, `extract_memories_for_character`,
  `summarize_for_character`, `extract_round_events`, `extract_scene_state`,
  `check_capabilities`, `list_models`, `upload_adapter_file`, `create_model`,
  `_build_chat_payload`/`_build_generate_payload` для `plot/*`), доступны через
  фасад — регрессий нет.

## 4. Известные остаточные private-импорты

`plot/crisis_engine.py:359,377` и `plot/story_consolidation.py:428,445`
по-прежнему дёргают `ollama_client._build_chat_payload`/`_build_generate_payload`
(локальный импорт против цикла). Это **до-существующее** использование
приватных символов через фасад — закрыто фасадом (символы в `__all__`), снятие —
спринт 10 (этап 19), как и удаление самого `app/ollama_client.py`.

## 5. Проверка (gate 5A)

- Потребители фасада: `test_vocabulary_borrowing.py`,
  `test_llm_serialization.py`, `test_sensors.py`, WPE-тесты,
  `test_relationship_*` — зелёные (выборочный прогон: 204 passed).
- `pytest -q`: **1301 passed, 41 failed** — набор упавших **идентичен**
  baseline'у (41 пред-существующий LLM/env-зависимый фейл); новых регрессий нет.
- `python -m compileall app` OK; `python -c "import app.main"` OK; сервер
  стартует, ручной раунд чата (SSE) OK.
- Золотые снапшоты `tests/golden/*` не затронуты (логика не менялась).

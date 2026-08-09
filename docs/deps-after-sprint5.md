# Зависимости после спринта 5 (Milestone 5A: `llm/`)

> Дата: 2026-08-09
> Источник: статический анализ импортов (`app/`) после разрезания
> `ollama_client.py` на пакет `app/llm/`. Назначение: артефакт сравнения
> «до/после» (п. 7 gate). Baseline API —
> `Plans/artifacts/ollama-client-api-before.txt` → `llm-api-after.txt`
> (gitignored).

## 1. `app/ollama_client.py` → пакет `app/llm/`

Монолит 3032 строк разрезан на 7 доменных модулей + реэкспортный
`__init__.py`-фасад. Состав пакета — см. [llm.md](llm.md).
`app/ollama_client.py` стал тонкой обёрткой (`from .llm import *`).

## 2. Граф зависимостей внутри пакета

Верхнеуровневых импортов между модулями `llm/*` **нет** (весь межмодульный
обмен — через `__init__.py` или локальные импорты):

```
lock, transport, prompting, generation, tasks, wpe, models
    →  друг друга не импортируют на верхнем уровне
```

Единственный локальный импорт против цикла — `models.py:144`
`from ..lora_manager import RuntimeCapabilities` (внутри `check_capabilities`).

Граф пакета — **ациклический**; `python -c "import app.main"` OK — новых
циклов на уровне всего приложения нет.

Внешние зависимости (наружу из `app.llm`): `config`, `prompt_builder`,
`schemas`, `token_counter`, `repetition_detector`, `role_isolation`,
`witness_model`, `context_budget_manager`, `lora_manager` (локально). Направление
`llm` ← сервисы сохраняется; обратных ссылок на `chat_engine`/`memory_service`
из `llm/*` нет.

## 3. Публичный API `llm/` (сверка до/после)

- `ollama-client-api-before.txt`: 144 символа (`dir()` монолита);
  `llm-api-after.txt`: 93 символа (`dir()` фасада без dunders).
- Разница — имена 7 подмодулей, stdlib-импорты и внутренние хелперы,
  переехавшие в `llm/*`.
- **Программная проверка по `app/` (вне `app/llm`): 0 обращений к символам,
  отсутствующим в фасаде** — все внешне используемые имена покрыты
  (`generate`, `llm_request`, `extract_memories_for_character`,
  `summarize_for_character`, `extract_scene_state`, `extract_round_events`,
  `check_capabilities`, `list_models`, `upload_adapter_file`, `create_model`,
  `delete_model`, `_build_chat_payload`, `_build_generate_payload`).

## 4. Тестовая база после спринта 5A

- Потребители фасада (`test_vocabulary_borrowing.py`,
  `test_llm_serialization.py`, `test_sensors.py`, `test_relationship_*`):
  зелёные (204 passed).
- `pytest -q`: **1301 passed, 41 failed** — набор упавших **идентичен**
  baseline'у (41 пред-существующий LLM/env-зависимый фейл); новых регрессий нет.
- `python -m compileall app` OK.
- Сервер: `GET /api/health` → `{"status":"ok"}`; ручной раунд чата (SSE) OK.
- Известные флаки вне спринта: `test_world_engine_phase7.py::test_streaming_*`
  (порядок wake-up при включённом event bus) падают и на монолите.

# Аудит тестов `ai-roleplay-chat`

**Дата:** 03.08.2026 · **Платформа:** Windows 11 · **Python:** 3.13.7 · **pytest:** 9.1.1 · **pytest-asyncio:** 1.4.0 (`asyncio_mode=auto`)

**Статус:** 🟡 Требует исправлений — 28/598 pytest-тестов падают, но **все 28 — баги в самих тестах**, а не в production-коде. При этом есть критические warnings в production и проблемы изоляции тестовой инфраструктуры.

---

## 1. Результаты полного прогона

| Набор | Команда | Результат |
|---|---|---|
| Весь pytest-набор (unit + integration + golden) | `python -m pytest -v` (из `ai-roleplay-chat`) | **598 тестов: 570 passed, 28 failed, 0 skipped, 0 xfailed**, EXITCODE=1 |
| Golden-тесты (входят в 598) | `pytest tests/golden/` | **32 теста — все PASSED** |
| Eval harness (mock) | `python -m tests.eval.run_eval --mode mock` | **6 сценариев: 5 passed, 1 failed** (`isolation_basic`) |
| Eval harness (real) | `python -m tests.eval.run_eval --mode real` | Не запускался (nightly, недетерминирован, требует 20–60 мин) |
| Frontend | `npm run build` (vue-tsc + vite) | **EXITCODE=0, сборка успешна** (unit-тестов frontend нет) |

Сводка: **всего 604 теста/сценария, 575 passed, 29 failed.**

---

## 2. Провалившиеся тесты

### 2.1 Ошибки в самих тестах (test bugs) — 28

Все 28 pytest-провалов вызваны некорректным тестовым кодом, а не production-кодом. Классификация по первопричине:

**A. Пропущенный `await` (11 тестов)** — async CRUD-функции (`crud.create_character`, `crud.create_message`, `crud.get_characters_by_chat`, хелпер `create_characters`) вызываются без `await`. Ошибки `TypeError: 'coroutine' object is not iterable/subscriptable`, `AttributeError: 'coroutine' object has no attribute 'id'`. Production-код корректен.
- `tests/test_memory_service.py`: `test_summary_not_triggered_below_threshold`, `test_summary_triggered_at_threshold`, `test_summary_watermark_advances`, `test_extract_and_save_stores_importance_category`, `test_eviction_prefers_low_importance` (5)
- `tests/test_memory_perception.py`: `test_cross_location_memory_extraction_skip`, `test_first_person_pronoun_no_leak_to_remote`, `test_information_transfer_after_telling`, `test_memory_isolation_between_characters`, `test_memory_retrieval_in_prompt_block`, `test_bad_llm_fact_for_non_witness_grounding` (6)

**B. Устаревший контракт API (8 тестов)** — тесты написаны под старые сигнатуры, которых больше нет в production.
- `tests/test_stream_disconnect.py` (2): патчат `app.routers.chat_engine.SessionLocal` — атрибут не существует (роутер использует `AsyncSessionLocal`/`get_async_db`) → `AttributeError`.
- `tests/test_task_queue.py` (6): `run_job(job, handler)` — а в `app/task_queue.py` сигнатура `run_job(job)` (диспетчеризация по mapping'у) → `TypeError`. Плюс `patch("app.database.get_session_factory")` неэффективен: `task_queue` импортирует `AsyncSessionLocal` на уровне модуля, и jobs уходят в реальную `ai_chat.db`.

**C. Тест написан под не-потоковый API Ollama (2 теста)** — мок подменяет `client.post`, но production использует `client.stream` (`app/ollama_client.py:1054`). Мок «мёртв», идёт реальный сетевой вызов на `http://test/api/chat` → `getaddrinfo failed`.
- `tests/test_repetition_detector.py`: `test_generate_repetition_retry_with_feedback`, `test_repetition_retry_limit`

**D. Старые ожидания относительно конфигурации из `.env` (3 теста)** — тесты предполагают малый `MIN_CTX` (формула `int(prompt*1.3)+500`), но `.env` задаёт `MIN_CTX_TOKENS=32778` → `assert 32778 == (X*1.3+500)`. Production `ContextState.apply_prompt` работает по докам.
- `tests/test_context_state.py`: `test_ctx_is_monotonic_never_shrinks`, `test_ctx_only_grows_when_prompt_outgrows_current`, `test_chats_are_isolated`

**E. Ошибка в названии логгера (1 тест)** — `caplog.at_level(..., logger="token_counter")`, а реальный логгер — `"app.token_counter"`, INFO не попадает в захват.
- `tests/test_token_counter.py::test_token_counter_mode_reported`

**F. Ошибка типа сессии для async-движка (2 теста)** — `sessionmaker(bind=db_engine)` (sync) поверх async-движка, затем `db.query(...)` → `AsyncContextNotStarted`.
- `tests/test_task_queue.py`: `test_process_post_round_uses_task_queue`, `test_datetime_serialization_in_payload`

### 2.2 Ошибки в production-коде, из-за которых падают тесты — **0**

Ни один из 28 провалов не указывает на неисправность production-логики. Однако см. раздел 4.1 «Критические warnings» — там есть дефекты production, пока не приводящие к падению тестов.

---

## 3. Критические ложные провалы (false failures)

Эти провалы **не означают, что production-функциональность сломана** — они означают, что тесты не могут даже выполниться:

- **11 тестов (категория A)** — проваливаются на первом же `create_characters`/`create_message` без `await`. Сообщения об ошибках выглядят «страшно» (`AttributeError`, `TypeError: cannot unpack non-iterable coroutine`), но отражают только ошибку в тесте. Проверяемая логика (summary, memory extraction, eviction, location-isolation) в итоге **не тестируется вообще**.
- **2 теста repetition detector (категория C)** — падают с `getaddrinfo failed` на попытке реального сетевого вызова, хотя должны были работать в полной изоляции через мок.
- **2 теста stream_disconnect (категория B)** — падают до вызова проверяемой логики из-за несуществующего атрибута для патча. Сама фича «generation переживает отключение клиента» не проверяется.

Итог: **15 тестов из 28 — критические ложные провалы**, не связанные с поведением системы.

---

## 4. Warnings

Базовый прогон: **1943 warnings** (много дубликатов `PytestUnraisableExceptionWarning` на тест). Прогон с `-W error::RuntimeWarning` подтвердил, что failed-набор не меняется (те же 28).

### 4.1 Критические warnings (production-код)

1. **`DeprecationWarning: datetime.utcnow()` — 16 мест в production**, 3 места в тестах.
   - `app/crud.py` (556, 585, 675, 793, 859, 1314), `app/task_queue.py` (138, 156, 175, 286), `app/relationship_service.py` (225, 373, 807, 1140), `app/memory_service.py` (1041), `app/chat_engine.py` (1094).
   - `datetime.utcnow()` объявлен deprecated (Python 3.12) и запланирован к удалению. Требуется `datetime.now(timezone.utc)`.
2. **`RuntimeWarning: coroutine 'process_post_round' was never awaited`** и **`'coroutine _analyze_and_update_relationships' was never awaited`** — фоновые задачи создаются через `asyncio.create_task(...)` в `app/chat_engine.py` (986, 1043) и `app/memory_service.py` (901) без удержания ссылок. В тестах это `PytestUnraisableExceptionWarning`; на продакшене означает, что задачи не отслеживаются и могут быть молчаливо отброшены при преждевременном закрытии генератора SSE (строки 986–1051 не выполняются, если клиент отключился на `yield`). Риск **молчаливой потери обработки памяти/отношений** после поста-round.
3. **`RuntimeWarning: coroutine 'AsyncConnection.close' / 'AsyncSession.close' was never awaited`** — в тестах `test_task_queue` (незакрытые сессии реальной БД), следствие проблемы изоляции (см. раздел 5).

### 4.2 Некритические warnings

- **`StarletteDeprecationWarning` (httpx2)** — `fastapi.testclient` на старом httpx-бэкенде; требуется `httpx>=0.28` (настроено), влияет на все API-тесты через `TestClient`.
- **`RuntimeWarning: coroutine never awaited`** для `create_characters` (8), `process_post_round` (5), `_analyze_and_update_relationships` (5), `create_message` (2), `create_character` (1), `get_characters_by_chat` (1) — исчезнут после исправления тестов (добавление `await`).

---

## 5. Проблемы тестовой инфраструктуры

1. **Eval mock-режим НЕ изолирован от реальной БД.** `tests/eval/mock_llm.py` подменяет только генерацию основного ответа. Подсистемы, вызываемые в post-round (relationship analysis, scene-state extraction), обращаются к реальной `ai_chat.db` через `AsyncSessionLocal` → `FOREIGN KEY constraint failed` в логах; scene-state extraction дёргает реальный Ollama (`getaddrinfo failed` на `http://mock-ollama`). Кроме того, eval **пишет тестовые данные в реальную production-БД** (`ai_chat.db`, job_ids выросли до 2444+) — риск загрязнения данных.
2. **`tests/test_task_queue.py` и `tests/test_embeddings.py` не изолированы** — job-хендлеры открывают `AsyncSessionLocal()` (реальный файл `ai_chat.db`), а не in-memory движок фикстур. Результат: `test_embed_memory_job` получает статус `failed` (память не найдена в реальной БД), task-queue тесты мутируют реальную таблицу jobs.
3. **Тесты зависят от `.env`** (неявно через `settings`): `MIN_CTX_TOKENS=32778`, `CTX_BUFFER_TOKENS=500` ломают `test_context_state`. `.env` не управляется фикстурами — тест-сюита не детерминирована от окружения.
4. **`.env` содержит сомнительные значения конфигурации:** `DEFAULT_MODEL=VladimirGav/gemma4-26b-16GB-VRAM-Uncensored:latest` (с **ведущим пробелом** — потенциально ломает запросы), `MIN_CTX_TOKENS=32778` (в 4× больше дефолта и сводит на нет смысл динамического старта с малого контекста), `MAX_CTX=64000` превышает `MAX_CONTEXT_TOKENS=16000`.
5. **Eval-сценарий `isolation_basic.yaml` самопротиворечив:** в turn 3 `must_contain: ["mercy"]` и одновременно `must_not_contain: ["me"]`, а `me` — подстрока `mercy`. Проверка невыполнима → сценарий падает всегда.
6. **Frontend: unit-тестов нет** (в `package.json` только `dev`/`build`/`preview`; vitest/jest отсутствуют). CI-покрытие UI отсутствует.
7. **CI запускает Python 3.11, локально 3.13.7** — версия не зафиксирована жёстко; поведение (warnings, `datetime.utcnow`) различается.

---

## 6. Итоговая оценка состояния проекта

**Вердикт: 🟡 проект в целом рабочий, но статус «stable» пока преждевременен.**

Что работает:
- **570/598 pytest** + **32/32 golden** + **5/6 eval mock** + frontend build — прошли.
- **Production-код:** ни один провал теста не указывает на дефект production-логики; ключевые подсистемы (context_state, memory CRUD, pruning, consolidation, location isolation, golden-промпты) подтверждены.

Что мешает считать стабильным:
- **28 тестов не могут выполняться** из-за багов в тестах — это снижает надёжность test-сигнала и доверие к 570 зелёным (часть проверяемой логики фактически не покрыта).
- **Критические production-warnings:** `datetime.utcnow()` (16 мест), фоновые задачи без удержания ссылок (риск молчаливой потери post-round обработки при отключении клиента).
- **Тестовая инфраструктура не герметична:** eval и job-тесты пишут в реальную `ai_chat.db`; тесты не изолированы от `.env`.
- **Достоверность тестовых результатов:** средняя. 15 провалов — ложные (тесты падают до проверки логики).

---

## 7. План устранения

### 7.1 Исправление тестов (приоритет — 28 провалов)
1. **Добавить `await`** для всех async-вызовов в `test_memory_service.py` (5) и `test_memory_perception.py` (6): `create_characters(...)`, `create_message(...)`, `get_characters_by_chat(...)`.
2. **`test_task_queue.py`:** привести вызовы к новой сигнатуре `run_job(job)` (без `handler`); заменить `sessionmaker(bind=async_engine)` на async-сессии (или `AsyncSession`-фикстуру); заменить неэффективный `patch("app.database.get_session_factory")` на патч `app.task_queue.AsyncSessionLocal` (или внедрение фабрики в `TaskQueue`).
3. **`test_stream_disconnect.py`:** заменить патч `SessionLocal` на актуальный механизм БД роутера (`AsyncSessionLocal`/`get_async_db`).
4. **`test_repetition_detector.py`:** мокать `client.stream` вместо `client.post` (например, `AsyncMock` с итеративным возвратом).
5. **`test_context_state.py`:** параметризовать ожидаемые `MIN_CTX`/`BUF` через `settings` (или изолировать тесты от `.env` фикстурой-переопределением), чтобы формулы совпадали с реальной конфигурацией.
6. **`test_token_counter.py`:** исправить имя логгера на `"app.token_counter"`.

### 7.2 Изоляция тестов от реальной БД и `.env`
- Ввести фикстуры, подменяющие `AsyncSessionLocal` для job-хендлеров и eval harness (in-memory engine), либо внедрение фабрики сессий как зависимость (`TaskQueue(engine_factory=...)`).
- Eval mock-режим: полностью изолировать (mock для scene-state/relationship/embedding; фиктивная БД; не трогать `ai_chat.db`).
- Тесты, зависящие от `.env`, должны явно контролировать конфигурацию (fixture `monkeypatch` на `settings`).

### 7.3 Исправление production-warnings (критично)
- Заменить `datetime.utcnow()` → `datetime.now(timezone.utc)` во всех 16 точках (`app/crud.py`, `app/task_queue.py`, `app/relationship_service.py`, `app/memory_service.py`, `app/chat_engine.py`) + в 3 тестах.
- Удерживать ссылки на фоновые задачи (`asyncio.create_task` → `set`-коллекция `self._bg_tasks` + `done_callback`-очистка) в `app/chat_engine.py` (986, 1043) и `app/memory_service.py` (901), либо явно `await` при гарантированном полном потреблении. Проверить, что post-round выполняется при преждевременном отключении SSE-клиента.
- Закрывать async-сессии в тестах (`AsyncSession.close` awaited).

### 7.4 Инфраструктура
- Исправить `isolation_basic.yaml` (убрать конфликт `mercy`/`me` или изменить `must_contain`).
- Почистить `.env`: убрать ведущий пробел в `DEFAULT_MODEL`; пересмотреть `MIN_CTX_TOKENS=32778` и `MAX_CTX=64000` относительно `MAX_CONTEXT_TOKENS=16000`.
- Зафиксировать версию Python в CI (`.python-version`/actions) на 3.13, совпадающую с локальной.
- Опционально: добавить unit-тесты frontend (vitest) для core composables/stores.

---

## 8. Рекомендуемый порядок исправлений

1. **Добавить `await` (категория A, 11 тестов)** — самый дешёвый фикс, сразу закрывает 11 провалов. ✅ `docs/tests-fix.md` уже содержит описание.
2. **`test_task_queue.py` (8 тестов)** — привести к новой сигнатуре + изолировать БД (заодно закрывает инфраструктурную проблему п.5.2).
3. **`test_repetition_detector.py` (2 теста)** — мок `client.stream`.
4. **`test_stream_disconnect.py` (2 теста)** — актуальный патч БД.
5. **`test_context_state.py` (3 теста)** — параметризация через `settings`.
6. **`test_token_counter.py` (1 тест)** — имя логгера.
7. **`datetime.utcnow()` → `datetime.now(timezone.utc)`** по production.
8. **Удержание ссылок на фоновые задачи** + проверка post-round при disconnect.
9. **Изоляция eval harness** (mock всего, фиктивная БД) + фикс `isolation_basic.yaml`.
10. **Чистка `.env`** и фиксация версии Python в CI.

---

## Приложение. Какие тесты добавить/усилить

- **Тесты post-round при отключении клиента** — сейчас `test_stream_disconnect` сломан (ложный провал); нужен рабочий тест, что generation, сохранение сообщений, memory и relationship-обработка завершаются после раннего закрытия SSE.
- **Тест на удержание фоновых задач** — проверка, что `_bg_tasks` не разрастается и задачи завершаются.
- **Интеграционный тест token_counter через реальный интерфейс** (не только `logger`-захват).
- **Изоляционные тесты для eval-мока** — mock не должен дёргать Ollama/реальную БД (regression-guard).
- **Frontend unit-тесты** (vitest) для хотя бы `useChat`/session store.
- **Golden-тесты:** расширить покрытие новых промптовых фич (после изменения `context_state`/`MIN_CTX`).
- **CI-гейт:** прогон полного набора (`pytest tests/` без `--ignore`) после исправления провалов, чтобы 28+1 (eval) провалов не вернулись.

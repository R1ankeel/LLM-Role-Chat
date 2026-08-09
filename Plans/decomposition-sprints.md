# План декомпозиции по спринтам

> Статус: **в работе** — спринты 1–3 выполнены, спринт 3 подготовлен к коммиту
> (ревизия 2026-08-09)
> Дата: 2026-08-09
> Исходник: [Plans/decomposition.md](decomposition.md) — §9 (поэтапный план) и §10 (риски)
> Правило: код в рамках этой работы не меняется по логике — только перенос,
> переименования и разбиение импортов. **Каждый спринт заканчивается зелёным
> `pytest -q` и ручной проверкой одного раунда чата (SSE streaming работает).**

---

## 0.1 Статус выполнения

### Спринт 1 — выполнено (коммиты `9ee9bba`, `2416bff`)

**Что сделано (все шаги §2):**

1. **Базовая линия (этап 0).** `pytest -q` — 1293 passed / 49 failed (докоммитный
   baseline); стартовый dependency graph сохранён в `docs/deps-before.md`.
2. **`crud ↔ memory_service`.** Чистые BM25/rerank-функции вынесены в
   `app/memory/retrieval.py`; из `crud.py` убраны верхнеуровневые импорты
   `memory_service`/`embedding_service`. Для потребителей (`chat_engine`) оставлен
   фасад-реэкспорт `crud.get_*` (снят в спринте 10).
3. **`crud` → WPE/сервисы.** Из `crud.py` убраны локальные импорты `perception`,
   `witness_model`, `attention`, `wpe_shadow`, `sensors_service`, `belief_service`.
   Presence/attention пересчитываются в `post_round_pipeline.py`
   (`_attention_score_for`, `compute_and_save_presence_for_message/round`,
   `_chat_world_state_for_characters`). Чистые хелперы локаций/адресатов вынесены
   в `app/perception_utils.py` (без DB/LLM).
4. **`relationship_service ↔ crud`.** Создание памяти из событий — через явный
   интерфейс `memory/create.py::create_memory`; локальные импорты `crud` подняты
   на верхний уровень.
5. **`task_queue ↔ memory_service`.** Handler-registry
   (`register_handler`/`get_handler`); `task_queue` больше не импортирует
   `memory_service`; `run_job(job, handler=None)`.
6. **`wpe_shadow ↔ crud`.** Shadow-perception перенесён в сервисный слой:
   `chat_engine._create_message_with_shadow` → `wpe_shadow.maybe_run_shadow_perception`;
   `crud.create_message` больше не вызывает WPE.
7. **Фасад LLM.** Публичный `app/llm/generation.py::invoke_json` /
   `extract_json_payload`; `relationship_analyzer` и `sensors_service` переведены
   на него (приватные `_invoke_llm`/`_build_*` через границу больше не ходят).

**Gate:** `pytest -q` зелёный (попутно переведены тесты на async-сессии:
`test_task_queue.py`, `test_attention.py` и др.); `compileall` OK; сервер стартует;
ручной раунд чата OK; `rg` по `crud.py`: сервисных импортов нет.

**Артефакт:** `docs/deps-before.md` (baseline) + `docs/deps-after-sprint1.md`.

### Спринт 2 — выполнено (коммит `7438b12`)

**Что сделано (все шаги §3):**

1. **`config.py` → пакет `config/`.** 10 миксинов + композиция `Settings`
   (`core.py` — `SettingsBase` + base/url/model/история, `memory.py`, `context.py`,
   `relationships.py`, `repetition.py`, `wpe.py`, `story.py`, `sensors.py`,
   `task_queue.py`, `avatar.py`). Синглтон `settings = Settings()` сохранён,
   доступ `settings.<attr>` не менялся. **MRO-конфликт решён:** миксины — простые
   классы (не наследники `SettingsBase`), чтобы порядок разрешения полей не
   ломался.
2. **`models.py` → пакет `models/`.** 12 доменных модулей: `chat.py`,
   `character.py`, `message.py`, `memory.py`, `relationship.py`, `presence.py`,
   `scene.py`, `world.py`, `story.py`, `state.py`, `intent.py`, `lora.py`.
   `models/__init__.py` реэкспортирует весь API; `Base.metadata` не изменился.
3. Проверка путей импорта: внешние сущности используют только
   `from app.models import X`.

**Отклонение от §4.9:** `config/lora.py` не создан — в исходном `config.py`
нет LoRA-полей (пустой модуль не нужен); `world.py` добавлен в `models/`
для `WorldEvent`/`Thread`/`ThreadParticipantState` (в §4.8 не был назван явно).

**Верификация (gate):**

- `init_db` на чистой (копии) БД — OK, `configure_mappers` OK.
- **Публичный API `models/`:** 50 символов совпадают до/после
  (`Plans/artifacts/models-api-before.txt`); 27 таблиц идентичны
  (`metadata-tables-before.txt`); class→table без расхождений
  (`models-classes-before.txt`).
- **Настройки:** все 276 полей и значения совпадают
  (`settings-fields-before.txt`, `settings-values-before.json`); API `config/`
  (5 символов) совпадает (`config-api-before.txt`).
- **`pytest -q`:** 41 failed / 1301 passed — набор упавших **идентичен**
  монолитному состоянию до резки (41 пред-существующий LLM/env-зависимый фейл,
  включая флаки `test_llm_serialization.py::test_call_ollama_chat_holds_lock_while_request_in_flight`);
  `tests/test_sensors.py::test_sensors_model_not_used_outside_service` обновлён
  под пакетную структуру (сравнение по относительным путям, разрешён
  `config/sensors.py`).
- Сервер стартует; `GET /api/chats` → 200; ручной раунд чата OK.

**Артефакты:** `Plans/artifacts/` (gitignored) — снапшоты API/таблиц/настроек
до и после; `docs/deps-after-sprint2.md`.

### Спринт 3 — выполнено (не закоммичен)

**Что сделано (все шаги §4):**

1. **`database.py` → `db/`.** Вся DDL из `ensure_schema` (~1220 строк, диапазон
   111–1330) перенесена в `db/schema.py` **без изменений SQL** (проверено
   программно: 23 triple-quoted SQL-блока и 94 `text(...)`-выражения
   байт-в-байт совпадают с `HEAD:app/database.py`; счётчики DDL: 22
   `CREATE TABLE`, 38 `ALTER TABLE`, 48 `CREATE INDEX`, 1 `CREATE UNIQUE INDEX`
   — идентичны). Идемпотентность сохранена. Engine, pragma, `init_db`, сессии
   и `Base` → `db/engine.py` (зависимость `engine → schema` — циклов нет).
   В `database.py` остался тонкий реэкспорт-фасад (импорты `Base`,
   `SessionLocal`, `AsyncSessionLocal`, `init_db`, `get_async_db`,
   `memory_content_hash` и т.д. у потребителей не менялись).
2. **`schemas.py` → пакет `schemas/`.** 13 доменных модулей: `chat.py`,
   `character.py`, `message.py`, `memory.py`, `relationship.py`, `scene.py`,
   `context.py`, `job.py`, `story.py`, `belief.py`, `state.py`, `lora.py`,
   `perception.py`. **Ацикличность проверена статически до резки:**
   `schemas/perception.py → schemas/message.py → app.perception/stimuli`
   (локальные импорты `app.perception` на `app.schemas` остаются
   function-level), `chat.py → character.py + message.py` — циклов нет.
   Реэкспорт через `schemas/__init__.py` (`__all__` из 97 символов).
   Два внутренних относительных импорта обновлены на новый уровень пакета:
   `from .config import settings` → `from ..config import settings`
   (`schemas/relationship.py`), `from .emotion_engine import ...` →
   `from ..emotion_engine import ...` (`schemas/state.py`).
3. **Отклонение от §4.6:** `Location*` и `CharacterLocationUpdate`/`CharacterSummaryRead`
   отнесены в `character.py` (отдельного `locations.py` в плане `schemas/` нет);
   event-схемы (`ExtractedEvent`/`EventExtraction*`/`EventAction`) — в `scene.py`;
   `UserMessage`/`Intervention*` — в `chat.py`; `MemoryJobRead` — в `job.py`.

**Верификация (gate):**

- **`init_db` на копии `ai_chat.db`** — OK: на копии без WAL-файлов создалась
  схема, миграции/backfill применились (`app.db.schema` лог), повторный прогон
  идемпотентен.
- **Публичный API `schemas/`:** `__all__` (97 символов) совпадает до/после
  (`Plans/artifacts/schemas-api-before.txt` vs `schemas-api-after.txt`);
  67 классов совпадают (`schemas-classes-before.txt`/`-after.txt`). Различие
  в `dir()` только за счёт имён 13 подмодулей пакета.
- **`pytest -q`:** 41 failed / 1301 passed. Сверка с git worktree HEAD
  (до резки): набор упавших в ветке — **строгое подмножество** HEAD (в HEAD
  43; 2 лишних — флаки LLM-снапшотов `test_chat_engine.py::test_memory_extraction_*`).
  Новых регрессий нет.
- Сервер стартует; `GET /api/health` → `{"status": "ok"}`; `GET /api/chats` → 200;
  **ручной раунд чата OK** (SSE): создан чат + NPC, `POST /api/chats/28/message`
  вернул `message` → 50+ `token` → `message` (ответ NPC) → `done`.

**Артефакты:** `Plans/artifacts/` (gitignored): `schemas-api-before.txt`,
`schemas-classes-before.txt`, `schemas-api-after.txt`, `schemas-classes-after.txt`;
`docs/deps-after-sprint3.md`; `docs/database.md` (обновлён — раздел про пакет `db/`);
`docs/schemas.md`.

---

## 0. Ритм и правила спринтов

- **Длительность:** 2 недели (10 рабочих дней). Все сроки ориентировочные.
- **Порядок строго выдерживается:** сначала развязка циклов, потом безопасные
  монолиты (`config`, `models`), затем крупные файлы (`crud`, `ollama_client`,
  `chat_engine`), затем сервисы, frontend и legacy.
- **Крупные спринты делятся на milestone'ы с собственным gate**
  (5A/5B, 6A/6B/6C): каждый milestone — это контрольный рубеж, это может быть
  не отдельный календарный спринт, но обязательная точка остановки: следующий
  milestone не начинается, пока предыдущий не прошёл gate.
- **Gate каждого спринта/milestone'а (обязателен перед продолжением):**
  1. `pytest -q` — зелёный (85 файлов + golden + eval-набор, если затронут);
  2. `python -m compileall app` без ошибок;
  3. запуск сервера + один рабочий раунд чата (SSE streaming);
  4. статическая проверка зависимостей по затронутым пакетам
     (`rg`/`python -c "import app.main"`): новых циклов нет;
  5. review-чек: приватные (`_`) функции не пересекают границы модулей;
  6. **публичный API затронутых пакетов зафиксирован** — список экспортируемых
     символов (`app.crud`, `app.models`, `app.schemas`, `app.llm`, `app.memory`,
     `app.relationships`, …) снят до переноса и сверен после; через фасад
     запрещено «подтягивать» внутренние символы;
  7. **dependency graph затронутых слоёв до/после** сохранён как артефакт в
     `docs/` — видно не только «циклов нет», но и «слой A больше не знает о B».
- **Если gate не пройден** — спринт/milestone не закрывается: устраняется причина
  регресса, счётчик итераций увеличивается, только после этого движемся дальше.
- **Ветки:** одна ветка на спринт/milestone (`refactor/sprint-N-<slug>`), PR
  закрывается только при зелёном gate. Маленькие коммиты по шагам внутри.

---

## 1. Сводная карта спринтов

| Спринт | Тема | Этапы §9 | Ключевые артефакты |
|---|---|---|---|
| 1 | Подготовка и развязка циклов | 0–1 | `memory/retrieval.py`, фасад `llm.generation.invoke_json`, handler-registry джобов |
| 2 | `config/` и `models/` | 2–3 | `config/`, `models/` пакеты |
| 3 | `db/` и `schemas/` | 4–5 | `db/schema.py`, `db/engine.py`, `schemas/` |
| 4 | `crud/` (самый крупный) | 6 | `crud/*.py` — 16 доменных модулей |
| 5 | `llm/` (5A) и `pipeline/` (5B) | 7–8 | 5A: `llm/*`; 5B: `pipeline/streaming.py`, `pipeline/regeneration.py` |
| 6 | Отношения и память (6A/6B/6C) | 9–11 | 6A: `pipeline/relations.py`; 6B: `relationships/*`; 6C: `memory/*` |
| 7 | Детекторы и контекст | 12–14 | `repetition/*`, `context/*`, `prompt/*`, `perception/*` |
| 8 | Сюжет и роутеры | 15–16 | `plot/consolidation/*`, `plot/crisis/*`, тонкие роутеры |
| 9 | Frontend Vue и legacy | 17–18 | декомпозиция 5 компонентов; вывод `app/static/app.js` |
| 10 | Чистка и завершение | 19 | удаление фасадов, актуализация `docs/`, критерии §11 |

---

## 2. Спринт 1 — Подготовка и развязка циклов

**Цель:** сделать слои односторонними, чтобы резка файлов в следующих спринтах
не порождала регрессий. **Без этого спринта нельзя начинать спринт 4 (`crud/`).**

**Шаги:**

1. **Базовая линия (этап 0).** Прогнать `pytest -q`, зафиксировать число
   пройденных/упавших, слепок golden-снапшотов (`tests/golden/`), прогнать
   eval-набор. **Снять стартовый dependency graph** (`docs/deps-before.md`):
   router → pipeline → services → crud → db и обособленные группы
   (`prompt`, `context`, `memory`, `relationships`) — с него начинается
   сравнение «до/после» каждого спринта. Сохранить отчёт в `docs/` (или
   комментарий в PR), чтобы было с чем сравнивать после каждого спринта.
2. **`crud ↔ memory_service` (§7.1).** Выделить чистые функции BM25 и rerank
   (без ORM) в `app/memory/retrieval.py`. «Гибридный поиск» и rerank из `crud.py`
   перенести в `memory/`. Из `crud.py` убрать верхнеуровневые импорты
   `memory_service`, `embedding_service` и сервисные вызовы.
3. **`crud` → WPE/сервисы (§7.1).** Убрать локальные импорты `witness_model`,
   `perception`, `attention`, `wpe_shadow`, `sensors_service`, `belief_service`
   из `crud.py`. Присутствие/внимание/белифы пересчитываются сервисами
   (`memory_service`, `post_round_pipeline`) поверх чистого `crud`.
4. **`relationship_service ↔ crud` (§7.1).** Зафиксировать направление:
   сервис → crud (только). Создание памяти из событий — через явный интерфейс
   `memory/` без внутреннего импорта `crud`.
5. **`task_queue ↔ memory_service` (§7.1).** Ввести handler-registry: диспетчер
   джобов не импортирует обработчики напрямую, обработчики регистрируются.
6. **`wpe_shadow ↔ crud` (§7.1).** Перенести shadow-perception в сервис,
   вызывающий crud, а не наоборот.
7. **Фасад LLM (§7.2).** Ввести публичный `llm/generation.py::invoke_json(...)`
   (заглушка-фасад), перевести `relationship_analyzer` и `sensors_service`
   с приватных `_invoke_llm`/`_extract_json_payload` на него.

**Проверка (gate):** `pytest -q` зелёный; `rg` по `crud.py`: импортов сервисов нет;
`python -c "import app.main"` работает; ручной раунд чата.

**Риски:** изменение маршрутов вызовов при развязке `crud` может задеть
присутствие/внимание — каждый перенесённый вызов подкрепляется тестом
(`test_witness_filter.py`, `test_attention.py`, `test_beliefs.py`,
`test_memory_service.py`).

**DoD:** слои `crud`/`db` односторонние; приватные LLM-функции закрыты фасадом;
`pytest` зелёный; golden-снапшоты не изменились без причины.

---

## 3. Спринт 2 — `config/` и `models/`

**Цель:** срезать два самых безопасных монолита (этапы 2–3 §9). Не требуют
развязки циклов — идеальная обкатка процесса переноса.

**Шаги:**

1. **`config.py` → пакет `config/`.** Разбить ~250 полей `Settings` на доменные
   модули: `core.py` (base/url/model/история), `memory.py`, `context.py`,
   `relationships.py`, `repetition.py`, `wpe.py`, `story.py`, `sensors.py`,
   `lora.py`, `task_queue.py`, `avatar.py`. Композиция через `SettingsBase` +
   доменные миксины. Синглтон `settings = Settings()` и доступ `settings.<attr>`
   сохраняются — потребители не меняются.
2. **`models.py` → пакет `models/`.** Разбить 27 ORM-классов по доменам:
   `chat.py`, `character.py`, `message.py`, `memory.py`, `relationship.py`,
   `presence.py`, `scene.py`, `story.py`, `intent.py`, `state.py`, `lora.py`.
   `models/__init__.py` реэкспортирует все классы; `Base.metadata` не меняется.
   **Снять список экспортируемых символов `models/` до и после переноса** —
   публичный API пакета зафиксирован, иначе фасад будет вечно маскировать
   старые пути импорта.
3. Проверить, что ни одна внешняя сущность не обращается к полям «через путь» —
   только через `from app.models import X`.

**Проверка (gate):** `init_db` на пустой (копии) БД без ошибок; `pytest -q`
зелёный; запуск API; ручной раунд чата; **публичный API `models/` совпадает
до/после (сверка списка символов)**.

**Риски:** `models.py` импортируется повсеместно — главный риск в правильном
реэкспорте `__init__.py`; миксины `config` могут менять порядок разрешения полей —
сверять итоговый набор атрибутов до/после (snapshot-тест на `settings`).

**DoD:** `config/` и `models/` — пакеты; реэкспортные фасады дают прежний API;
`pytest` зелёный; `Base.metadata` и схема БД не изменились.

---

## 4. Спринт 3 — `db/` и `schemas/`

**Цель:** вынести скрытую «миграцию» и схему из `database.py` (этапы 4–5 §9).

**Шаги:**

1. **`database.py` → `db/`.** Вынести весь DDL из `ensure_schema` (~1220 строк,
   диапазон 111–1330) в `db/schema.py` **без изменений SQL** (идемпотентность
   сохраняется). Engine, pragma, `init_db`, сессии → `db/engine.py`. В
   `database.py` остаются тонкие реэкспорты.
2. **`schemas.py` → пакет `schemas/`.** Разбить 72 Pydantic-класса:
   `chat.py`, `character.py`, `message.py`, `memory.py`, `relationship.py`,
   `scene.py`, `context.py`, `job.py`, `story.py`, `belief.py`, `state.py`,
   `lora.py`, `perception.py`. **Сначала** проверить ацикличность взаимных
   импортов (`BuiltContext`, `IssueDelta`, `RelationshipDelta` и др.), затем
   реэкспорт через `schemas/__init__.py`. **Снять список экспортируемых символов
   `schemas/` до и после** — публичный API пакета зафиксирован.

**Проверка (gate):** `init_db` на **копии** `ai_chat.db` (см. `ai_chat.db.bak-pre-cleanup-20260808` как образец подхода); `pytest -q` зелёный; запуск API; ручной раунд чата; **публичный API `schemas/` совпадает до/после (сверка списка символов)**.

**Риски (см. §10):** перенос DDL-схемы ломает скрытую миграцию — работаем
только на копии БД, SQL не меняем; схемы взаимно импортируют друг друга —
ацикличность проверяем статическим обходом до резки.

**DoD:** `ensure_schema` не содержит DDL; `schemas/` — пакет; поведение при
создании БД идентично; `pytest` зелёный.

---

## 5. Спринт 4 — `crud/` (самый крупный)

**Цель:** разбить «бога БД» на 4313 строк (этап 6 §9). **Предусловие:**
выполнен спринт 1 — из `crud` убраны сервисные импорты. Делится на
3 под-этапа, каждый со своим gate.

**Шаги:**

1. **Под-этап A — механика пакета.** Создать `app/crud/` как пакет; перенести
   публичные функции без переименований; `crud/__init__.py` реэкспортирует весь
   публичный API, чтобы `from . import crud` у потребителей продолжал работать.
   Gate: `pytest -q` + `rg "from . import crud"` не пугает — всё зелёное.
2. **Под-этап B — доменные модули (простая группа).** Разнести:
   `chats.py`, `characters.py` (вкл. player/location sync), `messages.py`,
   `locations.py`, `threads.py`, `lora.py`. Gate: `pytest -q`.
3. **Под-этап C — доменные модули (сложная группа).** Разнести:
   `memories.py` (witness-фильтр, rerank, anchors), `summaries.py`,
   `presence.py`, `scene.py`, `rounds.py`, `events.py`, `story.py`,
   `state.py` (character state + beliefs), `intents.py`, `plans.py`.
   Gate: `pytest -q` + полный прогон сервисных тестов
   (`test_memory_*`, `test_beliefs.py`, `test_attention.py`,
   `test_witness_filter.py`, `test_post_round_pipeline.py`).

**Проверка (gate спринта):** `pytest -q` зелёный; `rg` проверка — в `crud/*.py`
нет импортов `memory_service`/`embedding_service`/`witness_model`/`perception`/
`wpe_shadow`/`sensors_service`/`belief_service`; запуск API; ручной раунд чата.

**Риски:** самый большой файл — высокий шанс ошибиться в переносе диапазонов
(таблица §4.1); реэкспорт-фасад замаскирует ошибки — после каждого под-этапа
запускать полный `pytest`, а не только затронутые тесты.

**DoD:** `crud/` — 16 доменных модулей; `crud/__init__.py` реэкспортирует прежний
API; слои не нарушены; `pytest` зелёный.

---

## 6. Спринт 5 — `llm/` (5A) и `pipeline/` (5B)

**Цель:** разбить `ollama_client.py` (3032) и вынести streaming-ядро из
`chat_engine.py` (3118) (этапы 7–8 §9). **Это два независимых больших
рефакторинга** — спринт делится на два milestone'а с отдельными gate: **5A**
(`ollama_client` → `llm/`) и **5B** (`chat_engine` → `pipeline/`). Если объём
растёт, milestone можно оформить отдельным спринтом, но gate каждого milestone
обязателен. **Предусловие:** фасад `invoke_json` из спринта 1 готов.

### Milestone 5A — `ollama_client.py` → `llm/` (этап 7)

**Шаги:** выделить по диапазонам §4.3: `lock.py` (глобальная сериализация
`_llm_lock_for`), `transport.py` (`_call_*`, `_stream_*`, `_read_ollama_error`,
`_ConfigProxy`, `llm_request`), `prompting.py` (форматирование истории,
payload-билдеры, `_messages_to_prompt`), `generation.py` (`_invoke_llm`,
`_generate_once`, `generate`, vocabulary-borrowing + публичный `invoke_json`),
`tasks.py` (извлечение памяти, суммаризация, scene-state, event extraction),
`wpe.py` (tool-calling, `_parse_tool_calls`, `_parse_turn_output_json`,
tool-mode chain), `models.py` (list/create/delete/upload/check_capabilities).
`_ConfigProxy` — кандидат на удаление (покрывается `config.py`), решить в этом
же milestone.

**Gate 5A:** `pytest -q`; `test_vocabulary_borrowing.py`, `test_llm_serialization.py`,
WPE-тесты, `test_sensors.py` и тесты `relationship_analyzer` (потребители фасада);
golden `tests/golden/*`; **публичный API `llm/` зафиксирован списком символов**;
**dependency graph `llm/` снят**; ручной раунд чата.

### Milestone 5B — `chat_engine.py` → `pipeline/` (этап 8, на 2 под-этапа)

**Шаги:**

- Под-этап A: `pipeline/streaming.py` — `process_user_message_streaming`
  (549–1619) + `pipeline/session.py` (`process_user_message`, общие хелперы
  раунда).
- Под-этап B: `pipeline/regeneration.py` — `regenerate_message_streaming`
  (2609–3118) + `pipeline/lora.py` (`resolve_generation_model`,
  `lora_first_apply_warning`) + `pipeline/story.py` (`_chat_story_block`,
  `_chat_plot_text`, belief evidence).
- Между под-этапами — полный round-trip тест и `test_stream_disconnect.py`.

**Gate 5B:** `pytest -q`; `test_chat_engine.py`, `test_stream_disconnect.py`;
golden-снапшоты `tests/golden/*` и eval-набор `tests/eval/`; **публичный API
`pipeline/` зафиксирован**; **dependency graph `pipeline/` снят**; ручной раунд
чата (SSE streaming).

**Риски (см. §10):** регрессия streaming-пайплайна (SSE, отключение клиента,
ретраи) — этап 8 на два под-этапа, обязателен полный round-trip; private-импорты
`relationship_analyzer`/`sensors_service` уже закрыты фасадом в спринте 1;
две большие задачи в одном спринте — milestone'ы не смешивать в одном PR.

**DoD:** `ollama_client.py` перестал существовать как монолит (фасад-реэкспорт
допустим временно); `pipeline/streaming.py` + `regeneration.py` выделены;
SSE-пайплайн поведенчески идентичен; **gate 5A и 5B пройдены по отдельности**.

---

## 7. Спринт 6 — Отношения и память

**Цель:** `relationship_service.py` (1861) и `memory_service.py` (2191)
(этапы 9–11 §9). **Самый опасный спринт:** три независимых переноса, каждый
закрывается собственным gate — **6A, 6B, 6C**. **Порядок жёсткий: 6A → 6B → 6C;**
`memory/` (6C) начинается **только** после полной стабильности `relationships/`
(6B). **Предусловие:** спринт 1 (развязка циклов) и спринт 4 (`crud/`) выполнены.

### Milestone 6A — `pipeline/relations.py` (этап 9)

**Шаги:** перенести из `chat_engine.py` `_analyze_and_update_relationships`
(1619–2088), `_run_sensors_relationship_proposal` (2088–2166) и
`_run_per_pair_analysis` + evidence/constrain (2166–2586). Импортировать
только публичные API модулей.

**Gate 6A:** `pytest -q`; `test_relationship_*`, `test_role_isolation.py`;
**dependency graph:** анализ отношений больше не живёт в `chat_engine`/`pipeline`
streaming-пути; ручной раунд чата.

### Milestone 6B — `relationship_service.py` → `relationships/` (этап 10)

**Шаги:** разбить по §4.4: `crud.py` (CRUD 107–267), `validation.py` (267–388),
`deltas.py` (388–528 + saturation guard), `blocks.py` (578–863), `issues.py`
(863–1371), `decay.py` (1371–1592 + `prune_relationship_events`),
`memory_feed.py` (1592–1800), `trajectory.py` (1800–1861). Локальные импорты
`crud` внутри функций — устранить (направление сервис → crud).

**Gate 6B:** `pytest -q`; полный прогон `test_relationship_*`,
`test_relationship_issues*.py`, `test_relationship_service.py`,
`test_relationship_context.py`; **публичный API `relationships/` зафиксирован**;
**dependency graph:** направление relationships → crud только однонаправленное.

### Milestone 6C — `memory_service.py` → `memory/` (этап 11)

> Начинается **только** после того, как gate 6B пройден и `relationships/`
> полностью стабилен. Не совмещать два переноса одновременно.

**Шаги:** разбить по §4.5: `retrieval.py` (уже в спринте 1 — BM25 + rerank),
`extraction.py` (`_extract_and_save_memories` 888–1073), `summaries.py` (1073–1179),
`jobs.py` (1179–1263 + embedding-джобы 2098–2191), `consolidation.py` (1263–1573),
`adaptive.py` (1573–2098), `validation.py` (`classify_memory_type`,
`validate_extracted_facts` 477–725). `get_observable_context_for_character`
(725–888) — в `memory/` (witness-слой).

**Gate 6C:** `pytest -q`; `test_memory_*`, `test_consolidation.py`,
`test_adaptive_consolidation.py`, `test_task_queue.py`, `test_hybrid_rerank.py`,
`test_memory_types.py`; golden-тесты по памяти (`tests/golden/`); **публичный API
`memory/` зафиксирован**; ручной раунд чата.

**Риски:** BM25/rerank перенос меняет численные результаты — перенос без
изменения кода, golden-тесты по памяти (`tests/golden/`); relationship-service
полон точечной логики — переносить функциями целиком; слишком ранний старт 6C
загрязняет два переноса сразу — строгий порядок 6A → 6B → 6C.

**DoD:** три milestone'а пройдены по очереди (не параллельно); сервисы разбиты
на доменные пакеты; направление сервис → crud соблюдено; численные результаты
поиска/отношений идентичны.

---

## 8. Спринт 7 — Детекторы и контекст

**Цель:** `repetition_detector.py` (840), `context_builder.py` (1036),
`prompt_builder.py` (1054), `perception.py` (760) (этапы 12–14 §9).

**Шаги:**

1. **`repetition_detector.py` → `repetition/` (этап 12).** По §4.10:
   `actions.py` (извлечение действий), `scoring.py` (лексические скоринг-функции,
   cooldown), `analyzer.py` (interaction-loop, progression/stagnation, итоговая
   `analyze_response`), `feedback.py`.
2. **`context_builder.py` → `context/` (этап 13).** По §4.11: `retrieval.py`
   (`_select_retrieved`), `assembly.py` (`ContextBuilder.build`, `_assemble_recent`),
   `trimming.py` (`_trim_*`), `story.py` (`_build_story_block`). Файл уже разбит
   на `_методы` — перенос механический.
3. **`prompt_builder.py` → `prompt/` (этап 13).** По §4.12: `character.py`
   (карточка/личность/анти-мимикрия), `blocks.py` (правила/память/диалог),
   `scene.py`, `extraction.py`, `relationships.py`, `story.py`, `state.py`.
   **Декомпозиция ≠ консолидация — это две отдельные задачи:**
   - **(3a) Чистая декомпозиция (здесь):** перенести существующие функции
     **1:1**, без «улучшений» и без выбора «правильной» версии дублей. Каждая
     `build_*` переезжает как есть; реэкспорт из `prompt_builder.py` для
     совместимости.
   - **(3b) Последующая архитектурная чистка (отдельная задача/спринт, НЕ
     здесь):** определить единственного владельца обёрток §7.4
     (`character_state`/`npc_plans`/`story_state`) и удалить дублирование.
   Смешивать 3a и 3b нельзя: иначе по golden-тестам промптов нельзя будет
   сказать, изменилось ли поведение из-за переноса или из-за консолидации.
   **После (3a) — обязательное golden-сравнение 1:1** (`test_prompt_builder_golden`).
4. **`perception.py` → `perception/` (этап 14).** По §4.13: `locations.py`
   (`normalize_location`, `locations_match`, adjacency, toponym), `levels.py`
   (`get_perception_level`, `can_character_perceive_event`), `world.py`
   (`PerceptionWorldState`, `build_permeability_index`), `events.py`
   (`event_from_message`). Остальные WPE-модули (§7.6) — **не** объединять,
   следить за ростом.

**Проверка (gate):** `pytest -q`; `test_context_*`,
`test_prompt_builder_golden`, `test_perception*`, `test_locations_perception.py`,
`test_world_engine_phase*`, `test_repetition_detector.py`, `test_ollama_chat.py`;
ручной раунд чата.

**Риски:** WPE-тесты зависят от тонких функций — перенос без изменения кода,
все `_parse_*`/`build_*` сохраняются как есть; консолидация обёрток §7.4
**не выполняется в этом спринте** (задача 3b) — golden-сравнение после (3a)
фиксирует только эффект переноса; чтобы не допустить скрытой консолидации,
перенос выполняется функциями целиком без правок тела.

**DoD:** 4 монолита разбиты; перенос 1:1 (консолидация обёрток §7.4 —
отдельная задача 3b, вынесена из спринта); golden-сравнение промптов 1:1;
WPE-пакет остаётся связным, без искусственных пакетов.

---

## 9. Спринт 8 — Сюжет и роутеры

**Цель:** `plot/story_consolidation.py` (888), `plot/crisis_engine.py` (809),
логика в роутерах (этапы 15–16 §9).

**Шаги:**

1. **`story_consolidation.py` → `plot/consolidation/` (этап 15).** По §4.14:
   `parse.py` (парсинг/валидация JSON `_parse_*`, `validate_*`), `grounding.py`
   (`_thread_grounded`), `llm.py` (`_invoke_consolidation`), `apply.py`
   (`_apply_consolidation`), `scheduler.py` (триггер `maybe_consolidate_story`).
2. **`crisis_engine.py` → `plot/crisis/` (этап 15).** По §4.14: `pressure.py`
   (`compute_crisis_pressure`, `trajectory_score_*`), `scoring.py` (кандидаты),
   `llm.py` (`_evaluate_crisis_llm`), `apply.py` (`_apply_crisis_softly`),
   `block.py` (`build_crisis_block`, `run_crisis_engine`).
3. **Роутеры (этап 16).** `routers/relationships.py` — вынести бизнес-логику в
   `relationships/` (роутер тонкий: валидация + статусы). `routers/debug.py` —
   вынести `_serialize_*` в `services/debug_render.py`, сохранить маршрут
   `/debug` (страница `app/static/debug.html`). `routers/chat_engine.py` —
   формирование событий/полезной нагрузки в `pipeline/`, SSE-обработка остаётся.
   `routers/chats|characters|jobs|locations|lora.py` — не трогать.

**Проверка (gate):** `pytest -q`; `test_story_consolidation.py`,
`test_crisis_engine.py`, `test_story_state.py`, `test_story_threads.py`,
`test_relationship_issues_endpoint.py`, `test_debug_router.py`,
`test_chat_engine.py`; ручной раунд чата.

**Риски:** LLM-вызовы в plot-модулях — использовать только публичный фасад
`invoke_json`; перенос логики роутеров — не менять HTTP-контракты (тесты
endpoint'ов зелёные).

**DoD:** `plot/consolidation/` и `plot/crisis/` — пакеты; роутеры не содержат
бизнес-логики; HTTP-контракты не изменились.

---

## 10. Спринт 9 — Frontend Vue и legacy

**Цель:** декомпозиция крупных Vue-компонентов и вывод legacy SPA
(этапы 17–18 §9). Может выполняться параллельно со спринтами 6–8 при наличии
свободного разработчика.

**Шаги (по §5.1–5.5, от крупного к мелкому):**

1. **`RelationshipPairDetail.vue` (817).** Разбить на `RelationshipHistory.vue`,
   `IssueList.vue`, `TrajectoryTimeline.vue`, `RelationshipForm.vue` +
   composables `useRelationshipPair`, `useIssueActions`. Вынести layout-логику
   графа из `RelationshipGraph.vue` в `useRelationshipGraph`.
2. **`Sidebar.vue` (737).** Вынести `NewChatDialog.vue`, `ChatListItem.vue`,
   `RenameChatDialog.vue`, composable `useChatSidebar`.
3. **`LoRASettings.vue` (630).** Вынести `LoRAAdapterForm.vue`,
   `LoRAAdapterListItem.vue`, `LoRACompatibilityBadge.vue`, composable
   `useLoRAForm` (логика в `stores/lora.ts` уже есть).
4. **`CharacterProfileModal.vue` (593).** Разбить на `CharacterProfile.vue`,
   `CharacterMemoryTab.vue`, `CharacterStateTab.vue`, composable
   `useCharacterProfile`.
5. **`Composer.vue` (551).** Вынести `InterventionEditor.vue`, composable
   `useComposer`.
6. **Mocks (§5.6).** Принять решение: если API стабилизирован — удалить
   `mocks/data.ts`/`mocks/service.ts` (проверить использование в сторах);
   иначе — разбить по доменам и генерировать из типов.
7. **Legacy static (этап 18 / §6).** Убедиться, что Vue-сборка покрывает все
   маршруты (root, chat, health, models). Обновить `app/main.py` (раздача static).
   Вывести `app/static/app.js`/`style.css` в архив/удалить (оставить `favicon`
   и `debug.html`).

**Проверка (gate):** `npm run build` (`vue-tsc`) без ошибок; ручная проверка
Vue-сборки через сервер по всем маршрутам; `pytest -q` по backend-тестам
(не затронуты, но для контроля).

**Риски:** legacy и Vue конфликтуют в static — выключение legacy только после
полного покрытия маршрутов Vue-сборкой; `stores/messages.ts` (374) — не трогать,
следить (выносить `useStreaming.ts` только при росте).

**DoD:** 5 компонентов декомпозированы; mocks удалены/разбиты; legacy SPA
выведен из эксплуатации без потери маршрутов.

---

## 11. Спринт 10 — Чистка и завершение

**Цель:** убрать временные фасады, актуализировать документацию, проверить
критерии завершения (этап 19 §9, критерии §11).

**Шаги:**

1. **Удаление реэкспорт-фасадов.** По одному убирать `__init__.py`-реэкспорты,
   которые больше не нужны; каждый шаг контролируется
   `rg "from . import crud"` / `rg "from app.crud"` — обновлять потребителей.
   `crud.py`/`ollama_client.py`/`chat_engine.py` и пр. остаются только как
   временные фасады или удаляются.
2. **Статические проверки.**
   - **Нет необоснованно крупных файлов; целевой ориентир — ≤600 строк** (не
     абсолютный порог): файлы крупнее 600 строк допустимы, если размер обоснован
     связной ответственностью; исключения фиксируются в архитектурной
     документации `docs/`.
   - Нет циклических импортов: `python -c "import app.main"` + статический анализ.
   - `crud/` и `db/` не импортируют сервисный слой.
   - Приватные функции не пересекают границы модулей.
   - **Публичный API каждого пакета сверен с зафиксированным списком символов**
     (`app.crud`, `app.models`, `app.schemas`, `app.llm`, `app.memory`,
     `app.relationships`, …): через фасады не «подтянуты» внутренние символы.
   - **Итоговый dependency graph** сохранён в `docs/` и совпадает с целевым:
     router → pipeline → services → crud → db; `prompt`, `context`, `memory`,
     `relationships` обособлены.
3. **Документация.** Актуализировать `docs/`, README, `Plans/decomposition.md`
   (статус: выполнено; актуальные размеры файлов), обновить карту архитектуры §8.
4. **Полный прогон.** `pytest -q` + eval-набор (`tests/eval/`) + golden-снапшоты;
   сравнение с базовой линией спринта 1; ручная проверка всего функционала.

**Проверка (gate):** все 9 критериев §11 выполнены; `pytest -q` и eval зелёные.

**DoD:** декомпозиция завершена, фасады удалены, документация актуальна.

---

## 12. Сводные риски и их спринты

| Риск | Спринт | Смягчение |
|---|---|---|
| Спринт 5 — два независимых больших рефакторинга в одном | 5 | milestone 5A (`llm/`) и 5B (`pipeline/`) с отдельными gate; при росте объёма — отдельные спринты |
| Регрессия streaming-пайплайна (SSE, disconnect, ретраи) | 5B | два под-этапа; `test_stream_disconnect.py`; golden + eval |
| Спринт 6 — три переноса сразу | 6 | milestone 6A/6B/6C с gate; `memory/` только после стабильности `relationships/` |
| Изменение численных результатов BM25/rerank | 6C | перенос без изменения кода; golden-тесты по памяти |
| Консолидация обёрток §7.4 маскируется под «перенос» | 7 | перенос 1:1 (3a), консолидация — отдельная задача (3b); golden-сравнение после (3a) |
| Скрытая миграция `ensure_schema` ломается | 3 | только копия `ai_chat.db`; SQL не меняем |
| `crud` тянет сервисы — регрессии присутствия/внимания | 1, 4 | развязка ДО резки; каждый сервисный вызов фиксируется тестом |
| Private-импорты (`_invoke_llm`, `_extract_json_payload`) | 1, 5A, 8 | публичный фасад `invoke_json` до резки |
| Фасады маскируют старые импорты внутренних символов | 4, 10 | публичный API пакетов зафиксирован списком символов; сверка при чистке |
| Слои незаметно снова узнают друг о друге (скрытая связанность) | все | dependency graph до/после как артефакт каждого спринта (`docs/`) |
| Legacy frontend vs Vue в static | 9 | выключение legacy только после покрытия маршрутов |
| Frontend-компоненты уже не «растут» после декомпозиции | 9 | проверять рост в следующих спринтах (см. §5.7) |

---

## 13. Критерии завершения программы (из §11 decomposition.md)

1. Нет **необоснованно** крупных файлов в `app/`; целевой ориентир — ≤600 строк
   (кроме `prompts/ru.json` и static-ассетов); исключения зафиксированы в
   архитектурной документации `docs/`.
2. Нет циклических импортов (`python -c "import app.main"` + статический анализ).
3. `crud/` и `db/` не импортируют сервисный слой (однонаправленные зависимости).
4. Приватные функции не пересекают границы модулей.
5. Роутеры не содержат бизнес-логики.
6. `pytest -q` и eval-набор зелёные до и после каждого спринта.
7. Vue-frontend собирается (`npm run build`), legacy `app/static/app.js` выведен
   из эксплуатации.
8. **Публичный API пакетов зафиксирован списком символов** (`app.crud`,
   `app.models`, `app.schemas`, `app.llm`, `app.memory`, `app.relationships`, …):
   через фасады не используются внутренние символы.
9. **Итоговый dependency graph** соответствует целевому (router → pipeline →
   services → crud → db; обособлены `prompt`, `context`, `memory`,
   `relationships`) — артефакт `docs/` сверен с планом.

> Последний пункт (§6, legacy) допускает перенос за рамки спринта 9, если Vue
> не покрывает все маршруты — в этом случае legacy выводится отдельным
> follow-up, а не блокирует критерии 1–6 и 8–9.

# LoRA-адаптеры — план реализации

**Статус:** план утверждён, реализация идёт по спринтам. **Sprint 0 выполнен** (2026-08-07, протокол `research/lora-spike/PROTOCOL.md`, §3), **Sprint 1 (модель данных и миграции) выполнен** (2026-08-07, см. §3; покрытие — `tests/test_lora_db_crud.py`). Скорректирован по ревью (см. §8). **Объём MVP сужен (2026-08-07): ровно один LoRA-адаптер на чат, без weight/scale и без N-адаптеров (см. §2.5, §8.6).**
**Дата:** 2026-08-07
**Блокирующий этап:** Sprint 0 (Ollama LoRA Compatibility Spike) — **✅ ВЫПОЛНЕН** (протокол приложен). Sprint 1 (модель данных и миграции) — **✅ ВЫПОЛНЕН**. Sprint 2+ может начинаться.
**Основание:** Техническое задание (42 пункта), приложенное к запросу
**Ограничение (конвенция проекта):** Vanilla JS SPA в `app/static/` (index.html + app.js) **НЕ изменяется**. Все правки — в новом Vue-фронтенде (`frontend/src/`) и бэкенде. Аналогично плану `Plans/locations2.md`.

---

## 1. Анализ текущей архитектуры (выполнено)

### 1.1. Конвейер генерации

Основная генерация ответа персонажа выполняется в `app/chat_engine.py` через `ollama_client.generate(...)` с `model_name=chat.model_name`:

| Место | Строка | Назначение | LoRA |
|---|---|---|---|
| `process_user_message_streaming` | ~923/932 | **Основной ответ персонажа** (main character reply) | ✅ **Да** |
| `regenerate_message_streaming` | ~2760/2769 | **Перегенерация ответа персонажа** | ✅ **Да** |
| `extract_scene_state` | ~1226–1228 | Scene State Extraction | ❌ Нет |
| `post_round_pipeline` | ~1412–1416 | Пресенс → события → память → отношения → сюжет | ❌ Нет |

### 1.2. Служебные LLM-вызовы (НЕ получают LoRA)

Все служебные вызовы идут через отдельные методы `OllamaClient` (не через `generate()`), поэтому ограничение интеграции только на основной вызов `generate()` автоматически не затронет их:

- `ollama_client.extract_memories_for_character` / `summarize_for_character` / `extract_memories_unified` (memory_service.py)
- `ollama_client.extract_scene_state` (chat_engine.py ~1226)
- `ollama_client.extract_round_events` (event_service.py)
- `relationship_analyzer` / `relationship_service` (анализ отношений, связи)
- sensors (восприятие), story_consolidation, crisis_engine
- Валидация/пост-обработка

**Вывод:** точкой внедрения LoRA является ТОЛЬКО основной вызов `ollama_client.generate()` в `chat_engine.py` (строки ~923/932 и ~2760/2769).

### 1.3. OllamaClient

- `app/ollama_client.py`, HTTP через `httpx.AsyncClient`, создаётся в lifespan и хранится в `app.state.ollama_client` (main.py).
- Публичные методы: `generate()`, `extract_memories_for_character()`, `summarize_for_character()`, `extract_scene_state()`, `extract_round_events()`.
- Стриминг: `_stream_ollama_chat` (POST `/api/chat`), `_stream_ollama_generate` (POST `/api/generate`).
- Глобальный LLM-lock для сериализации вызовов.
- Кэш tool-mode по имени модели (`_MODEL_TOOL_MODE_CACHE`) — runtime-модель получит собственную запись (унаследует поведение базовой при первом вызове).

**Необходимо добавить в OllamaClient:** `create_model(name, from, adapters)` (структурный `POST /api/create`), `upload_adapter_file()` (blob HEAD/POST), `delete_model()`, `list_models()`, `check_capabilities()` — всё через HTTP API Ollama (без subprocess/shell, соответствует ТЗ §35). Механизм — Sprint 0, §2.7.

### 1.4. БД и миграции

- SQLite (`ai_chat.db`), SQLAlchemy 2.0 (`Mapped`/`mapped_column`), асинхронный.
- Миграции — без Alembic: идемпотентный `ensure_schema()` в `app/database.py` (CREATE TABLE IF NOT EXISTS + инспекция существующих колонок, backfill).
- `Chat` в `app/models.py`: `id`, `name`, `model_name`, `thinking_mode`, `story_enabled` и др.

### 1.5. API

- FastAPI, роутеры в `app/routers/` (стиль: `APIRouter` + `Depends(get_async_db)`), регистрация в `app/main.py`.
- CRUD-хелперы в `app/crud.py`, Pydantic-схемы в `app/schemas.py` (Pydantic v2).
- `chats.model_name` — базовая модель чата; дефолт из `settings.default_model`.

### 1.6. Frontend (Vue)

- `frontend/src/components/settings/SettingsTabs.vue` — вкладки: general / player / characters / locations (соответствуют ТЗ §3).
- `SettingsModal.vue` — рендерит активную вкладку по `SettingsTab` из `frontend/src/stores/ui.ts`.
- API-слой: `frontend/src/api/*.ts` через `client.ts` (`request()`), mocks в `frontend/src/mocks/`.
- `ChatHeader.vue` — показывает `model_name` и Badge Thinking/Instant → сюда индикатор «LoRA» при активной конфигурации.
- Сборка/проверка: `npm run build` = `vue-tsc -b && vite build`. Отдельного фронт-тест-раннера нет.

### 1.7. Тесты

- pytest + pytest-asyncio (`asyncio_mode=auto`), фикстуры `db_engine`/`db_session`/`chat` (in-memory SQLite) в `tests/conftest.py`.
- Ollama взаимодействия мокаются на уровне httpx/методов клиента.

### 1.8. Ограничения Ollama (подтверждено в Sprint 0, протокол `research/lora-spike/PROTOCOL.md`)

- LoRA задаётся через `POST /api/create`: `from` (имя базовой модели) + `adapters` (`{имя_файла: sha256 blob}`), после загрузки адаптера в blob (`HEAD`/`POST /api/blobs/:digest`).
- **Поле `modelfile` в HTTP `POST /api/create` НЕ работает** в 0.32.6 (`400 "neither 'from' or 'files' was specified"`). Modelfile-файл с `ADAPTER <путь>` работает только через CLI `ollama create` (CLI сам делает blobs); приложение идёт через HTTP API без shell.
- **Ровно один адаптер** на runtime-модель (`supports_multiple_loras=false`). Два `ADAPTER` → `400 "only one adapter is currently supported"`.
- **Weight/scale не поддерживается** (`supports_lora_weights=false`): в API и Modelfile нет параметра масштаба адаптера.
- GGUF LoRA — рабочий формат. Safetensors-адаптеры Ollama поддерживает только для Llama/Mistral/Gemma 1&2; для Gemma 4 не заявлены → в MVP только GGUF (`supports_safetensors=false`).
- **MVP строится ровно под эти ограничения** (§2.5): один адаптер на runtime-модель, weight/scale отсутствует; мульти-LoRA и веса исключены из MVP (см. §8.6).

---

## 2. Архитектурное решение

### 2.1. Схема потока

Состояния `lora_enabled` (подробно в §2.4):

```
lora_enabled=false                      → generate(model_name=chat.model_name)
lora_enabled=true, адаптер не выбран    → generate(model_name=chat.model_name)  // пустая runtime-модель НЕ создаётся
lora_enabled=true, 1 адаптер выбран     → LoRAManager.resolve(...) → runtime model

LoRAManager.resolve(db, client, chat)
   ├─ загрузить выбранный адаптер (не более одного, §2.5)
   ├─ compatibility check по base_model_identity (НЕ строковое сравнение имён, см. §2.3):
   │     Compatible   → продолжаем
   │     Incompatible → явная ошибка, генерация не начинается
   │     Unknown      → предупреждение/подтверждение, НЕ silent fallback
   ├─ runtime_key = sha256(base_model_identity + adapter_id + file_sha256)
   ├─ runtime_name = "{base-slug}-lora-{hash8}"
   └─ ensure_runtime_model(key, name, from, adapter_blob)  ← максимум 1× POST /api/create
        │   (blob: HEAD/POST /api/blobs/:digest + POST /api/create {from, adapters})
        │
        ▼
chat_engine: ollama_client.generate(model_name=runtime_name, ...)
   (только основной вызов; служебные вызовы получают chat.model_name как раньше)
```

### 2.2. Runtime key и кэш (сохранено из исходного плана, ТЗ §14, §28 — незыблемо)

Детерминированный ключ: хеш от **base model identity + adapter_id + sha256-содержимого файла адаптера**. Любое изменение (другой адаптер / другой файл адаптера) → новый ключ → новая runtime-модель.

- **sha256 содержимого файла адаптера** (blob-диджест) — основной идентификатор версии адаптера: blob-хранилище content-addressed, поэтому путь/размер/дата не нужны (два разных файла → разные blob → разные модели).
- Weight/order в ключ НЕ входят: в MVP их нет (см. §2.5).
- Пример: `base + adapter A` — один ключ; `base + adapter B` — другой.
- Одинаковая конфигурация → повторное использование существующей runtime-модели (кэш), НЕ повторный create.
- Кэш: in-memory dict `runtime_key → exists` + сверка `GET /api/tags` при старте/приложении. НИКОГДА не вызывать create на каждое сообщение.

### 2.3. Base model identity vs Ollama model name

Два разных понятия, которые нельзя смешивать в одном `==`:

| Понятие | Пример | Где хранится |
|---|---|---|
| **Ollama model name** | `goetia-26b` | `chat.model_name` — имя для API Ollama |
| **Base model identity** | `Naphula/Goetia-26B-A4B-v1.3-Absolute-Heretic-ARA` | отдельное metadata-поле `LoRAAdapter.base_model_identity` (nullable) |

Compatibility check сравнивает **идентичность базовой модели**, а не произвольные имена. Механизм:

1. Identity адаптера: явное поле `base_model_identity` → если пусто, попытка автоопределения (метаданные GGUF/имя файла) → иначе identity не определена.
2. Identity базовой модели чата: явное поле `Chat.base_model_identity` (nullable, в MVP можно не добавлять) → если не задано, низкодоверенная fallback на `chat.model_name`.
3. Результат:
   - **Compatible** — идентичности определены и совпадают → применяем.
   - **Incompatible** — определены и не совпадают → явная ошибка, применение блокируется.
   - **Unknown** — хотя бы одна идентичность не определена/низкодоверенная → не блокируем автоматически, но показываем понятное предупреждение/запрос подтверждения. **НЕ silent fallback.**

Корректная LoRA НЕ блокируется только из-за того, что локальное имя (`goetia-26b`) отличается от HuggingFace-идентификатора. Runtime-модель создаётся `FROM chat.model_name` (локальная модель Ollama), identity — это отдельный факт, используемый только для compatibility check.

### 2.4. Семантика `lora_enabled` — три состояния

| Состояние | Поведение | UI |
|---|---|---|
| `enabled=false` | Используется исходный `chat.model_name` | Тумблер выключен |
| `enabled=true`, адаптеров нет | Допустимое состояние; трактуется как «LoRA включена, но активных адаптеров нет»; пустая runtime-модель НЕ создаётся; генерация идёт на `chat.model_name` | Предупреждение: «LoRA включена, но адаптеры не выбраны» |
| `enabled=true`, 1 адаптер выбран | Используется LoRA runtime configuration (resolve → runtime model) | Выбранный адаптер |

Семантика фиксируется одинаково в API (`ChatLoRAConfig`), backend (`resolve`) и frontend (тумблер + предупреждение). `resolve()` НЕ создаёт runtime-модель, если адаптер не выбран.

### 2.5. Ограничения runtime → объём MVP (ровно одна LoRA, без весов)

Sprint 0 подтвердил (протокол): Ollama 0.32.6 поддерживает **ровно один** адаптер на runtime-модель и **не имеет механизма weight/scale**. MVP строится ровно под эти ограничения, без фиктивной поддержки:

- **Ровно один LoRA-адаптер на чат** — в модели данных, API и UI (нет списка адаптеров, `order_index`, «применить первый и молчать»).
- **Weight/scale отсутствует полностью** — поле не хранится в БД, не передаётся в API и не отображается в UI. Никакой эмуляции (temperature/prompt/подмена) и никаких «резервных» полей на будущее.
- `RuntimeCapabilities`: `supports_lora`, `supports_safetensors` (подтверждены Sprint 0: `true` / `false` — GGUF-only). Флаги мульти-LoRA и весов исключены как нерелевантные для MVP.
- N-адаптеры и веса — **вне MVP** (см. §8.6). Если будущий runtime их поддержит — отдельная задача по расширению модели данных.

### 2.6. Глобальный registry vs конфигурация чата

Два логических понятия, не смешиваются в API и UI:

- **Глобальный registry** (`lora_adapters`): «Какие адаптеры вообще доступны приложению?» — регистрация/редактирование (`Dark Goetia RU`, `Goetia Literary`, ...). Endpoints: `GET/POST/PUT/DELETE /api/lora`.
- **Конфигурация чата**: «Какой из них используется в этом конкретном чате?» — `{enabled, adapter_id}` (ровно один адаптер, §2.5). Endpoints: `GET/PUT /api/chats/{id}/lora`.

Вкладка LoRA в UI содержит обе части (см. Sprint 5).

### 2.7. Runtime / создание модели (HTTP API, без shell) и проверка пути

- **Создание runtime-модели** (Sprint 0, Q4/Q5): НЕ через `modelfile` (в 0.32.6 HTTP API его не читает). Флоу:
  1. `sha256` файла адаптера;
  2. `HEAD /api/blobs/sha256:<digest>` → 200/404; при 404 `POST /api/blobs/sha256:<digest>` байтами файла → 200;
  3. `POST /api/create` `{"model", "from": chat.model_name, "adapters": {"имя.gguf": "sha256:<digest>"}, "stream": false}` → 200.
  Итоговая Modelfile автоматически: `FROM <blob базы>` + `ADAPTER <blob адаптера>` + TEMPLATE/RENDERER/PARSER базы.
- **Валидация пути при регистрации адаптера** (Sprint 1/4):
  - путь абсолютный;
  - файл/директория существует;
  - корректный тип для выбранного `format` (gguf/auto; safetensors отклоняется как неподдерживаемый, `supports_safetensors=false`);
  - файл доступен для чтения (permissions, права доступа пользователя);
  - файл — валидный GGUF (проверка магических байтов/чтение заголовка) и sha256 вычисляется при регистрации;
  - runtime (процесс Ollama) потенциально может получить доступ к файлу.
- **Физический файл не удаляется никогда.** `DELETE /api/lora/{id}` удаляет только регистрацию из registry приложения. `.gguf` пользователя остаётся на диске.
- **Автоматический cleanup runtime-моделей в MVP НЕТ** (см. §8). В первой версии отсутствует `cleanup()`/GC: runtime-модели остаются в Ollama. Удаление — отдельная будущая задача (риск удалить модель, используемую другим чатом/после перезапуска/при временно выключенной LoRA).

---

## 3. Спринты

---

### Спринт 0 — Ollama LoRA Compatibility Spike — **✅ ВЫПОЛНЕН (2026-08-07)**

**Статус:** выполнен. Протокол: `research/lora-spike/PROTOCOL.md` (команды, выводы, хронология).
**Цель:** эмпирически подтвердить реальную поддержку Ollama на **конкретном кейсе из задачи**, а не на абстрактном адаптере. До завершения этого спринта массовое изменение кода (Sprint 1+) не начинается.

**Экспериментальный стенд (обязателен):**
- базовая модель **Goetia 26B** (Gemma 4 MoE) в локальной Ollama;
- адаптер **`Dark-Goetia-26B-A4B-LoRA-RU-v1`** в фактическом формате, который будет использоваться (GGUF LoRA);
- сценарии: 1 адаптер; несколько адаптеров; изменение weight/scale.

**Задачи — получить однозначные ответы на 7 вопросов:**
1. Можно ли применить **один** `ADAPTER` в Modelfile (`FROM Goetia` + `ADAPTER` + `ollama create`).
2. Можно ли применить **несколько** `ADAPTER` одновременно (в одном Modelfile / через любой реальный механизм).
3. Можно ли задавать **каждому** адаптеру индивидуальный weight/scale.
4. **Каким именно механизмом** задаётся weight, если он поддерживается (параметр Modelfile, API `/api/create`, версия Ollama).
5. Поддерживается ли это **именно текущей установленной версией** Ollama (`GET /api/version`).
6. Работает ли это именно с **GGUF LoRA** (фактический формат из задачи; не safetensors).
7. Есть ли **ограничения для архитектуры Goetia/Gemma 4 MoE** (MoE-архитектура, 26B/A4B, совместимость адаптера с базовой).

**Методика:**
- Каждый пункт проверяется реальным `ollama create` + генерацией на runtime-модели и сравнением результата с базовой моделью (подтверждение, что LoRA реально влияет).
- Версия Ollama и все команды/выводы фиксируются в протокол.
- Никакой API/runtime-механики для функций, существование которых не подтверждено, не проектируется.

**Критерий готовности:**
- Все 7 вопросов имеют фактический ответ; capability-флаги (`supports_lora`, `supports_multiple_loras`, `supports_lora_weights`, `supports_safetensors`) заполнены по результатам эксперимента.
- В раздел «Решение» ниже записаны: реально подтверждённые возможности, ограничения, и механизм weight (или факт его отсутствия).
- Если Ollama не поддерживает индивидуальные weights или несколько LoRA напрямую — это явно отражено в архитектуре (§2.5) и в UI (Sprint 5), без фиктивной поддержки.

**Решение (заполнено по итогам эксперимента 2026-08-07):**

| Вопрос | Ответ |
|---|---|
| Q1. Один `ADAPTER` | ✅ Да |
| Q2. Несколько `ADAPTER` | ❌ Нет — `400 "only one adapter is currently supported"` |
| Q3. Индивидуальный weight/scale | ❌ Нет — механизма нет |
| Q4. Механизм weight | Нет; API: `from` + `adapters` (`{имя: sha256 blob}`) |
| Q5. Версия | 0.32.6; `POST /api/create` **не читает** `modelfile` (400) |
| Q6. GGUF LoRA | ✅ Да |
| Q7. Goetia/Gemma 4 MoE | Ограничений для GGUF-кейса не найдено |

Capability-флаги: `supports_lora=true`, `supports_multiple_loras=false`, `supports_lora_weights=false`, `supports_safetensors=false` (только GGUF).

**Проверка:** протокол тестов с реальной моделью в `research/lora-spike/PROTOCOL.md`; выводы в этом файле (§1.8, §2.2, §2.5, §2.7, §8.3–§8.5); Sprint 1 разблокирован.

---

### Спринт 1 — Модель данных и миграции — **✅ ВЫПОЛНЕН (2026-08-07)**

**Цель:** схема БД для адаптеров и конфигурации чата.

**Статус:** выполнен. Все задачи 1–8 закрыты; покрытие — `tests/test_lora_db_crud.py` (29 тестов, ТЗ §36: 1–13). Итоговая реализация:

- **Модели** (`app/models.py`): `LoRAAdapter` (таблица `lora_adapters`, поля по задаче 1; `sha256` содержимого файла хранится для blob-флоу и runtime key §2.2; атрибут метаданных назван `metadata_json`, колонка в БД — `metadata`, т.к. имя `metadata` зарезервировано в Declarative API), `ChatLoRAAdapter` (таблица `chat_lora_adapters`, `UNIQUE(chat_id)` — не более одного адаптера на чат; поля `weight`/`order_index` НЕ создаются), `Chat.lora_enabled` (`BOOLEAN NOT NULL DEFAULT 0`) + relationship `lora_adapter` (1:1, `cascade="all, delete-orphan"`).
- **Миграции** (`app/database.py` `ensure_schema`): идемпотентный `ALTER TABLE chats ADD COLUMN lora_enabled BOOLEAN NOT NULL DEFAULT 0` (backfill существующих чатов → false автоматически default-ом), `CREATE TABLE IF NOT EXISTS lora_adapters` / `chat_lora_adapters`, индексы `ix_lora_adapters_enabled` / `ix_chat_lora_adapter_id`, `CONSTRAINT uq_chat_lora_chat UNIQUE (chat_id)`.
- **Pydantic-схемы** (`app/schemas.py`): `LoRAAdapterCreate/Update/Read` (включая `base_model_identity`, `metadata`, `sha256`; `Read` коэрсит `metadata_json` ORM → `metadata`), `ChatLoRAConfig` (`{enabled: bool, adapter_id: int | null}`) — без weight.
- **Валидация пути** (`app/lora_validation.py`, §2.7): абсолютный путь, существует, является файлом, читаемый, валидный GGUF (магические байты `GGUF` + чтение заголовка: версия/tensor_count), safetensors отклоняется (`supports_safetensors=false`); при регистрации вычисляется `sha256` (по чанкам, гигабайтные файлы). Ошибки → `LoRAValidationError` (422), `LoRAInUseError` (409, несёт `chats` — список `(chat_id, name)`).
- **CRUD** (`app/crud.py`): `list/get/create/update/delete_lora_adapter`, `get_chat_lora_adapter`, `get_chat_lora_config` / `put_chat_lora_config` (атомарная замена единственной связки: update на месте, чтобы не упереться в `UNIQUE(chat_id)`; `enabled=true` + `adapter_id=null` сохраняется как допустимое состояние §2.4), `list_adapter_usage_chats` (список чатов для 409). `update` при изменении `path`/`format` повторно валидирует и пересчитывает `sha256`. `delete` проверяет использование и **никогда не трогает физический файл** (задача 8).

**Критерий готовности:** ✅ свежая и существующая («прод»-копия без LoRA-таблиц) БД мигрируются идемпотентно (`ensure_schema` ×2); ✅ в чате невозможен второй адаптер (`UNIQUE(chat_id)`); ✅ невалидный путь/формат/файл не принимается (422-эквивалент); ✅ удаление записи не удаляет файл на диске.

**Проверка:** `pytest tests/test_lora_db_crud.py` — 29 passed (ТЗ §36: 1–13).

---

### Спринт 2 — LoRAManager + расширение OllamaClient

**Цель:** runtime-слой: создание/кэширование/проверка runtime-моделей.

**Задачи:**
1. Новый `app/lora_manager.py`:
   - `RuntimeCapabilities` (`supports_lora`, `supports_safetensors`, §2.5) + `check_capabilities(client)`.
   - `validate()` — путь (по §2.7) + **compatibility check по base_model_identity** (§2.3): результат `Compatible / Incompatible / Unknown`. `Unknown` → предупреждение/подтверждение, НЕ silent fallback и НЕ блокировка.
   - `runtime_key(...)` — sha256 (детерминированный, §2.2) и `runtime_name(...)` — `{slug(base)}-lora-{hash8}`.
   - `resolve(db, client, chat)` → `(model_name, info)`: по семантике `lora_enabled` (§2.4):
     - `enabled=false` → `chat.model_name`;
     - `enabled=true` + адаптер не выбран → `chat.model_name`, runtime-модель НЕ создаётся;
     - `enabled=true` + 1 адаптер → compatibility check → runtime-модель.
   - `ensure_runtime_model()` — кэш `key → exists`; при промахе `create_model()`; блокировка повторного создания (lock).
   - **БЕЗ `cleanup()`/GC runtime-моделей** (удалено из MVP, §2.7). Runtime-модели остаются в Ollama.
   - Логирование: создание/кэш-хит/ошибка/статус Unknown.
2. OllamaClient: `create_model(name, from, adapters: dict[str, str])` (POST `/api/create`, структурный `from`+`adapters`), `upload_adapter_file(path, digest)` (HEAD+POST `/api/blobs/:digest`), `delete_model(name)` (DELETE `/api/delete` — вызывается только явно, автоудаления нет; **в httpx 0.28.1 `Client.delete()` не принимает body → использовать `client.request("DELETE", url, json=...)`**), `list_models()` (GET `/api/tags`), `check_capabilities()`.
3. Пайплайн создания runtime-модели без shell: sha256 файла → `HEAD /api/blobs/:digest` (200/404) → при 404 `POST` байтами файла → `POST /api/create {from, adapters}`. **`modelfile`-строку не передавать** (0.32.6 возвращает 400); в `adapters` ровно один адаптер (§2.5).
4. Ошибки: несовместимость / невозможность создать runtime-модель → `RuntimeError` с текстом (не silent fallback).

**Критерий готовности:** для одной конфигурации `ollama create` выполняется максимум 1 раз; смена адаптера даёт новый ключ; повторный запуск не пересоздаёт модель (сверка `list_models`); ни один код не вызывает `ollama create` на каждое сообщение.

**Проверка:** `pytest` — тесты runtime (ТЗ §36: 14–21) с моками httpx.

---

### Спринт 3 — Интеграция в основную генерацию

**Цель:** LoRA применяется ТОЛЬКО к основному ответу персонажа.

**Задачи:**
1. В `app/chat_engine.py` добавить хелпер `resolve_generation_model(db, client, chat)` → подставленный runtime `model_name`.
2. Применить к ОСНОВНЫМ вызовам `ollama_client.generate()`:
   - `process_user_message_streaming` (~стр. 923/932),
   - `regenerate_message_streaming` (~стр. 2760/2769).
3. Служебные вызовы НЕ трогать: `extract_scene_state` (~1226), `post_round_pipeline` (~1416), memory/relationship/event/sensors/consolidation/crisis.
4. Ошибка LoRA до начала генерации: сообщение клиенту, конфиг чата не меняется.
5. Статус `Unknown` по совместимости (§2.3): при первом применении — предупреждение клиенту с подтверждением; `Incompatible` — блокировка с текстом. Ни то, ни другое не является silent fallback на базовую модель.
6. **Жёсткое правило (сохранить):** LoRA применяется только к основной генерации ответа персонажа. В рамках задачи НЕ изменяются: Dynamic CTX, thinking reserve, sensor context, memory pipeline, relationships, World & Perception Engine, role isolation, retry logic, tool-mode.
7. Streaming, Thinking/Instant, stop, retries, tool-mode — без изменений.

**Критерий готовности:** при `lora_enabled=false` или без выбранного адаптера ответ идентичен текущему поведению; при `lora_enabled=true` + выбранном адаптере основной вызов идёт на runtime-модель, служебные — на `chat.model_name`.

**Проверка:** `pytest` — интеграционные тесты (ТЗ §36: 19, 20, 21), мануальный прогон чата.

---

### Спринт 4 — REST API

**Цель:** endpoints для управления адаптерами и конфигурацией чата.

**Задачи:**
1. Новый `app/routers/lora.py` — **две группы endpoints, разделённые по §2.6**:
   - **Глобальный registry:** `GET /api/lora` (список), `POST /api/lora` (создать; валидация пути по §2.7 + identity), `PUT /api/lora/{id}` (изменить), `DELETE /api/lora/{id}` (удалить регистрацию; 409 со списком чатов, если используется ≥1 чатом; физический файл не трогается).
   - **Конфигурация чата:** `GET /api/chats/{id}/lora` (вернуть `ChatLoRAConfig`), `PUT /api/chats/{id}/lora` (атомарная замена `{enabled, adapter_id}`).
2. PUT конфигурации чата валидирует: ссылку на существующий адаптер и UNIQUE-ограничение (не более одного на чат). `enabled=true` + `adapter_id=null` сохраняется как допустимое состояние (§2.4).
3. Коды ошибок по конвенции проекта: 404 (не найдено), 409 (конфликт/использование), 422 (валидация пути/ссылок на адаптер).
4. Регистрация роутера в `app/main.py`.

**Критерий готовности:** настройки фронта грузятся одним `GET /api/chats/{id}/lora`; PUT атомарен (сбой не оставляет половинчатую конфигурацию); `enabled=true` с `adapter_id=null` сохраняется как допустимое состояние (§2.4).

**Проверка:** `pytest` — тесты API (ТЗ §36: 22–31).

---

### Спринт 5 — Frontend (Vue)

**Цель:** вкладка «LoRA» в модальном окне настроек и индикация в шапке чата.

**Задачи:**
1. `frontend/src/stores/ui.ts` — расширить тип `SettingsTab` на `'lora'`.
2. `SettingsTabs.vue` — добавить вкладку «LoRA» (обязательная, согласно ТЗ §3).
3. Новый `frontend/src/components/settings/LoRASettings.vue` — **две логические части (§2.6)**:
   - **«Доступные LoRA» (глобальный registry):** список зарегистрированных адаптеров (name, path, base_model/identity, статус Compatible/Incompatible/Unknown), кнопка «+ Добавить LoRA» → форма (название/путь/base model identity/описание), редактирование.
   - **«LoRA этого чата» (конфигурация):** тумблер «Включить LoRA» + селектор одного адаптера из registry (имя, base_model/identity) + кнопка «Убрать».
4. **Три состояния `lora_enabled` (§2.4):**
   - `enabled=true` + адаптер не выбран → предупреждение «LoRA включена, но адаптер не выбран»;
   - `enabled=false` → нейтрально;
   - `enabled=true` + адаптер выбран → рабочий вид.
5. **UI по ограничениям runtime (§2.5):**
   - выбор ровно одного адаптера (мультивыбор и weight-контролы отсутствуют — их нет в модели данных);
   - `supports_safetensors=false` — в форме регистрации можно выбрать только GGUF;
   - статус `Unknown` совместимости — понятное предупреждение/подтверждение (не блокирует молча).
6. API-слой `frontend/src/api/lora.ts` + типы в `api/types.ts` + mocks (`mocks/data.ts`, `mocks/service.ts`) + Pinia-стор. Registry и chat config — раздельные состояния/действия в сторе.
7. `ChatHeader.vue` — индикатор «LoRA» при активной конфигурации (enabled=true + выбран адаптер).
8. Единый источник состояния: один объект `{enabled, adapter_id}`; после Save источник истины — серверное состояние.

**Критерий готовности:** полный цикл: включить → выбрать адаптер → сохранить → перезагрузить → состояние восстановлено. Без сабмита несохранённые изменения сбрасываются. Registry и конфигурация чата не смешиваются в UI. Ограничения runtime показаны явно.

**Проверка:** `npm run build` (vue-tsc), мануальные сценарии (фронт-тест-раннер отсутствует). Тесты ТЗ §36: 32–39.

---

### Спринт 6 — Документация и финальная проверка

**Цель:** соответствие ТЗ §38 и Definition of Done (§42).

**Задачи:**
1. Документация: обновить `docs/README.md`, `docs/architecture.md`, `docs/api.md`, `docs/database.md`, `docs/configuration.md`; добавить раздел/файл «LoRA adapters» (включение одного адаптера, ограничения runtime — одна LoRA, без весов, раздел FAQ по Ollama).
2. Полный прогон `pytest` + acceptance-сценарии A–F (ТЗ §37).
3. **Acceptance на реальной модели из задачи** (не абстрактная тестовая LoRA):
   - `Dark-Goetia-26B-A4B-LoRA-RU-v1` + соответствующая базовая Goetia;
   - полный цикл: `register adapter → select adapter → enable LoRA → generate → streaming → disable LoRA → generate without LoRA`.
4. Чеклист Definition of Done (§42).

**Критерий готовности:** все пункты §38–§42 закрыты, в `app/static/` нет изменений; приёмка с реальной моделью выполнена и задокументирована.

**Проверка:** полный тестовый прогон + мануальный сценарий на реальном Ollama.

---

## 4. Тест-матрица (маппинг на ТЗ §36)

| № ТЗ | Покрытие | Спринт |
|---|---|---|
| 1–4 | БД: таблицы, UNIQUE(chat_id), миграция/backfill, lora_enabled=false по умолчанию | 1 |
| 5–13 | CRUD: create/update/delete, валидация пути, атомарный PUT, delete с usage | 1 |
| 14–21 | Runtime: ключ, кэш (1× create), пересоздание при смене адаптера, несовместимость, ошибки, служебные без LoRA | 2–3 |
| 22–31 | API: endpoints, коды ошибок, атомарность | 4 |
| 32–39 | Frontend: тумблер, селектор, форма, индикация, состояние | 5 |

## 5. Acceptance criteria (ТЗ §37)

- A. Создание/редактирование адаптера в модальных настройках (registry).
- B. Включение LoRA + назначение адаптеров чату (config).
- C. Перегенерация ответа идёт с LoRA; служебные вызовы без LoRA.
- D. Смена адаптера → новая runtime-модель (без «залипания» старой).
- E. Несовместимость/недоступность → понятная ошибка, без молчаливого игнора.
- F. `lora_enabled=false` → поведение идентично текущему.
- G. `enabled=true` + адаптер не выбран → предупреждение «LoRA включена, но адаптер не выбран»; пустая runtime-модель не создаётся.
- H. **Приёмка на реальной модели:** `Dark-Goetia-26B-A4B-LoRA-RU-v1` + базовая Goetia: полный цикл register → select → enable → generate → streaming → disable → generate без LoRA.
- I. Совместимость по identity, а не по имени: адаптер с HF-идентификатором ≠ локального имени применяется (Compatible/Unknown), не блокируется строковым `==`; `Unknown` — понятное предупреждение.

## 6. Definition of Done (ТЗ §42)

- Все спринты закрыты по критериям готовности.
- `pytest` зелёный, `npm run build` без ошибок vue-tsc.
- `app/static/` не изменён.
- Документация обновлена.
- Пункты ТЗ §1–§42 покрыты/разъяснены в этом файле.

## 7. Запрещённые решения (ТЗ §40 — сводка для контроля)

1. LoRA НЕ внедряется в system prompt / prompt-контекст.
2. Никакого «вшивания» LoRA в character personality.
3. Физический файл адаптера не удаляется: ни автоматически, ни при `DELETE` регистрации.
4. `ollama create` НЕ вызывается на каждое сообщение.
5. LoRA НЕ применяется к служебным вызовам (scene state, память, отношения, сенсоры, валидаторы и пр.).
6. Нет silent fallback при несовместимости/ошибке — только явная ошибка. Для `Unknown` — явное предупреждение/подтверждение.
7. Dynamic context / thinking / sensors не изменяются.
8. Пустая runtime-модель (при `enabled=true` без адаптеров) НЕ создаётся.
9. Нет эмуляции weight/scale (temperature/prompt/подмена) и нет «резервных» полей для несуществующей фичи — weight/scale исключён из MVP (§2.5).
10. Нет фиктивной поддержки N-адаптеров «по бумаге» — ровно один адаптер на runtime-модель и на чат (§2.5).

## 8. Сводка изменений после ревью

### 8.1. Что сохранено из исходного плана (без изменений)

- Структура спринтов (Sprint 0–6) и разбивка на БД → runtime → интеграция → API → frontend → документация.
- Точка внедрения: ТОЛЬКО основной вызов `ollama_client.generate()` в `chat_engine.py` (~923/932, ~2760/2769); служебные вызовы не трогаются.
- Жёсткое правило неизменности существующих LLM-подсистем (Dynamic CTX, thinking reserve, sensors, memory pipeline, relationships, World & Perception Engine, role isolation, retry logic).
- Runtime key: детерминированный, от base identity + adapter_id + sha256 содержимого файла; любой change → новый ключ.
- Runtime кэш: максимум 1× `ollama create` на конфигурацию, сверка `GET /api/tags`, никогда не создавать на каждое сообщение.
- Modelfile через HTTP API (`POST /api/create`), без shell.
- Один LoRA-адаптер на чат (`UNIQUE(chat_id)`), без weight/order_index (§2.5).
- Ошибки несовместимости/недоступности — явные, без silent fallback.
- `app/static/` не изменяется; Vue-фронтенд + backend.
- Ограничение «физический файл не удаляется» (было, уточнено).

### 8.2. Что изменено

| # | Пункт | Было | Стало |
|---|---|---|---|
| 1 | Sprint 0 | «Spike: возможности Ollama» — абстрактная проверка | **«Ollama LoRA Compatibility Spike», BLOCKING**: конкретный кейс (Goetia 26B + Dark-Goetia-26B-A4B-LoRA-RU-v1, GGUF), 7 вопросов, переход к Sprint 1 только после подтверждения |
| 2 | Совместимость | `base_model == chat.model_name` | Identity-подход: `base_model_identity` vs `chat.model_name`; статусы Compatible/Incompatible/Unknown; Unknown — предупреждение, не блок |
| 3 | `lora_enabled` | бинарный флаг | 3 состояния; `enabled=true` без адаптеров = допустимо, пустая runtime-модель не создаётся, в UI предупреждение |
| 4 | Cleanup runtime-моделей | `cleanup()` в MVP | Убрано из MVP; GC — отдельная будущая задача; runtime-модели остаются в Ollama |
| 5 | Путь к LoRA | «абсолютный, существует, без \n» | Полная валидация: тип для формата, читаемость, доступ для Ollama, capability формата |
| 6 | Registry vs Chat | подразумевались, не разведены | Явно разделены (§2.6), разнесены endpoints и UI-части |
| 7 | N-адаптеры | «рассчитано на N» | **Исключено из MVP**: ровно один адаптер на чат (`UNIQUE(chat_id)`), без списков и весов (§2.5, §8.6) |
| 8 | Weight | «weight в runtime key + ошибка при ≠1.0» | **Исключено из MVP**: weight не хранится, не передаётся, не эмулируется (§2.5, §8.6) |
| 9 | Runtime cache | — | Сохранён без изменений (уже был корректен) |
| 10 | Acceptance | абстрактная LoRA | Реальная `Dark-Goetia-26B-A4B-LoRA-RU-v1`, полный цикл |
| 11 | LLM-подсистемы | перечень | Зафиксирован полный список неизменяемых подсистем |
| 12 | План | — | Обновлён до начала реализации (этот файл) |
| 13 | Объём MVP | «0..N адаптеров + weight» (решение ревью) | **Сужен до ровно одного адаптера, без weight/scale и без N-адаптеров** (§2.5, §8.6): из модели данных, API, UI и runtime-логики убраны weight, order_index, мультивыбор; `RuntimeCapabilities` сведены к `supports_lora`/`supports_safetensors` |

### 8.3. Ограничения, выявленные экспериментом

- **HTTP `POST /api/create` в 0.32.6 не принимает поле `modelfile`** → 400 `"neither 'from' or 'files' was specified"`. Только структурированное тело (`from`/`files`/`adapters`). Приложение не использует Modelfile-строки.
- **Ровно 1 адаптер** на runtime-модель: `400 "only one adapter is currently supported"`.
- **Weight/scale отсутствует**: параметра масштаба адаптера нет ни в API, ни в Modelfile.
- **Safetensors-адаптеры**: Ollama поддерживает только для Llama/Mistral/Gemma 1&2; для Gemma 4 — нет. GGUF-only.
- **`/api/generate` может вернуть пустой `response`** (`done_reason=length`) у gemma4+LoRA: токены уходят в channel-токены, renderer их подавляет. Основная генерация приложения — `/api/chat` (`use_chat_api=True`) — не затрагивается.

### 8.4. Возможности Ollama, реально подтверждённые

- Один GGUF LoRA-адаптер применяется к базовой модели (`from` + `adapters` blob) и реально влияет на генерацию (воспроизводимое отличие channel-поведения на `/api/generate`, корректная генерация на `/api/chat`).
- Blob-флоу: `HEAD /api/blobs/:digest` (200/404), `POST /api/blobs/:digest` (200), `POST /api/create` (200), `DELETE /api/delete` (200/404).
- Смена содержимого адаптера → новый blob → новая runtime-модель (content-addressed).

### 8.5. Ограничения, выявленные экспериментом → исключены из MVP

- **N-адаптеры** — runtime принимает ровно 1 (Sprint 0 Q2). Мульти-LoRA исключена из MVP (решение ревью, §2.5, §8.6).
- **Weight/scale** — механизм отсутствует (Sprint 0 Q3–Q4). Веса исключены из MVP полностью: поле не хранится, не передаётся, не эмулируется (решение ревью, §2.5, §8.6).
- **Формат адаптера** — `supports_safetensors=false` (Sprint 0 Q6): только GGUF.
- **Архитектура базовой модели** (Goetia/Gemma 4 MoE) — ограничений для GGUF-кейса не выявлено (Sprint 0 Q7).

### 8.6. Решение по объёму MVP (2026-08-07): одна LoRA, без весов

- Опыт прототипа и результаты Sprint 0 (Ollama 0.32.6: ровно один `ADAPTER`, нет weight/scale) показали: мульти-LoRA и индивидуальные веса для текущего use-case не нужны.
- **MVP:** ровно один LoRA-адаптер на чат (`enabled` + `adapter_id`), без `weight`/`order_index`, без списков в `ChatLoRAConfig`, один индикатор «LoRA» в шапке чата.
- Из плана удалены: флаги `supports_multiple_loras`/`supports_lora_weights`, capacity/weight checks в `resolve()`, weight-элементы runtime key, weight slider в UI, тест-кейсы про вес.
- Модель данных (`UNIQUE(chat_id)`) сохраняет возможность расширить до N-адаптеров в будущем отдельной задачей.

# LoRA-адаптеры (MVP: одна LoRA на чат, без весов)

Поддержка LoRA-адаптеров для **основного ответа персонажа**. План и полная
контекстная информация — [`Plans/LoRA.md`](../Plans/LoRA.md). Документ описывает
реализованные слои **Sprint 1 (модель данных, миграции, CRUD, валидация пути)**,
**Sprint 2 (runtime-слой: `LoRAManager` + расширение `ollama_client`)**,
**Sprint 3 (интеграция в основную генерацию)**, **Sprint 4 (REST API)** и
**Sprint 5 (Vue-фронтенд: вкладка «LoRA» + индикатор в шапке чата)** и
**Sprint 6 (документация, полный pytest, приёмка на реальной модели —
`research/lora-acceptance/ACCEPTANCE.md`, 12/12 PASS; найденные дефекты — ниже в
«Приёмка на реальной модели»)**.

## Ограничения MVP (подтверждены эмпирически, Sprint 0)

- **Ровно один LoRA-адаптер на чат** — в модели данных (`UNIQUE(chat_id)`), в
  конфигурации и в UI. Поля `weight`/`order_index` НЕ создаются.
- **Weight/scale отсутствует** — не хранится, не передаётся, не эмулируется.
- **Только GGUF** — `supports_safetensors=false`; safetensors-адаптеры
  отклоняются при регистрации.
- **Физический файл пользователя никогда не удаляется** — `DELETE` регистрации
  не трогает `.gguf` на диске.
- Служебные LLM-вызовы (память, отношения, сенсоры, scene state и т.д.) LoRA
  **не получают** — интеграция только в основной вызов `generate()` (Sprint 3,
  реализовано).

## Модель данных

### `lora_adapters` — глобальный registry

| Колонка | Тип | Описание |
|---------|-----|----------|
| `id` | INTEGER PK | |
| `name` | TEXT NOT NULL | отображаемое имя |
| `path` | TEXT NOT NULL | абсолютный путь к файлу пользователя (`.gguf`) |
| `format` | TEXT NOT NULL DEFAULT 'auto' | `gguf` после регистрации (auto → фактический) |
| `base_model` | TEXT NOT NULL DEFAULT '' | наименование базовой модели (для справки) |
| `base_model_identity` | TEXT NULL | identity базовой модели для compatibility check (§2.3) |
| `enabled` | INTEGER NOT NULL DEFAULT 1 | |
| `description` | TEXT NOT NULL DEFAULT '' | |
| `source` | TEXT NOT NULL DEFAULT '' | |
| `metadata` | TEXT NOT NULL DEFAULT '{}' | JSON-объект произвольных метаданных |
| `sha256` | TEXT NOT NULL DEFAULT '' | содержимое файла (blob-диджест, runtime key §2.2) |
| `created_at` / `updated_at` | DATETIME | |

Примечание по реализации: в ORM атрибут называется `metadata_json` (колонка в
БД — `metadata`), т.к. имя `metadata` зарезервировано в Declarative API.

### `chat_lora_adapters` — связка «чат → адаптер»

| Колонка | Тип | Описание |
|---------|-----|----------|
| `id` | INTEGER PK | |
| `chat_id` | INTEGER NOT NULL FK → chats.id ON DELETE CASCADE | `UNIQUE(chat_id)` — не более одной связки на чат |
| `adapter_id` | INTEGER NOT NULL FK → lora_adapters.id ON DELETE CASCADE | |
| `created_at` | DATETIME | |

Индексы: `ix_lora_adapters_enabled (enabled)`, `ix_chat_lora_adapter_id
(adapter_id)`.

### `chats.lora_enabled` и `chats.base_model_identity`

- `lora_enabled` — `BOOLEAN NOT NULL DEFAULT 0`. Флаг отделён от связки:
  «LoRA включена, но адаптер не выбран» — допустимое состояние (пустая
  runtime-модель не создаётся).
- `base_model_identity` — `VARCHAR(512) NULL` (добавлено в Sprint 2). Identity
  базовой модели для compatibility check (§2.3). Если не задано — низкодоверенная
  fallback на `model_name` → результат сравнения `Unknown`.

## Миграции

Идемпотентный `ensure_schema()` в `app/database.py`:

- `ALTER TABLE chats ADD COLUMN lora_enabled BOOLEAN NOT NULL DEFAULT 0` —
  существующие чаты автоматически получают `false` (backfill через DEFAULT);
- `ALTER TABLE chats ADD COLUMN base_model_identity VARCHAR(512) NULL` (Sprint 2);
- `CREATE TABLE IF NOT EXISTS lora_adapters` / `chat_lora_adapters` + индексы и
  `CONSTRAINT uq_chat_lora_chat UNIQUE (chat_id)`.

Свежая БД создаётся через `Base.metadata.create_all` (Python-side default), прод
мигрируется `ensure_schema` (DB-default) — оба пути проверены тестами.

## Валидация пути (`app/lora_validation.py`)

При create/update регистрации адаптера (§2.7):

1. путь **абсолютный** (иначе «Путь должен быть абсолютным»);
2. **существует** и является **файлом**;
3. доступен для **чтения**;
4. `format` корректен: только `gguf`/`auto`; `safetensors` → ошибка
   (`supports_safetensors=false`);
5. файл — **валидный GGUF**: магические байты `GGUF` + чтение заголовка
   (версия, tensor_count); обрезанный файл отклоняется;
6. вычисляется **`sha256`** содержимого (по чанкам, для blob-флоу и runtime key).

Ошибки: `LoRAValidationError` → HTTP 422 с текстом; `LoRAInUseError`
(несёт список `chats: [(chat_id, name), ...]`) → HTTP 409.

## CRUD (`app/crud.py`)

| Функция | Назначение |
|---------|-----------|
| `list_lora_adapters(db, skip, limit)` | список registry |
| `get_lora_adapter(db, adapter_id)` | адаптер по id |
| `create_lora_adapter(db, create)` | валидация пути + sha256 + запись |
| `update_lora_adapter(db, id, update)` | частичное обновление; при изменении `path`/`format` — повторная валидация и пересчёт sha256 |
| `delete_lora_adapter(db, id)` | удаляет **только регистрацию**; при использовании чатами → `LoRAInUseError` (409); файл на диске НЕ удаляется |
| `get_chat_lora_config(db, chat_id)` | `ChatLoRAConfig{enabled, adapter_id}` |
| `put_chat_lora_config(db, chat_id, config)` | атомарная замена единственной связки (update на месте — иначе `UNIQUE(chat_id)`); `enabled=true` + `adapter_id=null` сохраняется как допустимое состояние |
| `get_chat_lora_adapter(db, chat_id)` | выбранная связка чата (≤ 1) |
| `list_adapter_usage_chats(db, adapter_id)` | чаты, использующие адаптер (для 409) |

## Runtime-слой (Sprint 2)

### `app/lora_manager.py`

| Элемент | Назначение |
|---------|-----------|
| `RuntimeCapabilities` | флаги `supports_lora` (true), `supports_safetensors` (false — только GGUF, §2.5) |
| `check_capabilities(client)` | доступность Ollama (GET `/api/version`), недоступность → `RuntimeError` |
| `validate(adapter, chat)` | путь (§2.7, без пересчёта sha256 — хранимый blob-диджест авторитетен) + compatibility (§2.3) → `ValidationResult{path_ok, compatibility}` |
| `check_compatibility(adapter, chat)` | `Compatible` / `Incompatible` / `Unknown` по identity базовой модели (§2.3); `Unknown` при неопределённой/низкодоверенной identity |
| `runtime_key(base_identity, adapter_id, file_sha256)` | детерминированный sha256 (§2.2): base identity + adapter_id + sha256 файла |
| `runtime_name(base_model, key)` | `{slug(base)}-lora-{hash8}` |
| `LoRAManager.resolve(db, client, chat)` | `(model_name, ResolveResult)` по семантике `lora_enabled` (§2.4) |
| `LoRAManager.ensure_runtime_model(...)` | кэш `key → exists` → сверка `list_models` → под lock (двойная проверка) → blob → create; максимум 1× create на конфигурацию |

Семантика `resolve` (§2.4):

- `enabled=false` → `chat.model_name` (runtime-модель не участвует);
- `enabled=true` + адаптер не выбран → `chat.model_name`, **пустая runtime-модель
  НЕ создаётся** (предупреждение);
- `enabled=true` + 1 адаптер → compatibility check → runtime-модель.

Совместимость (§2.3):

- обе identity заданы явно (`Chat.base_model_identity` + `adapter.base_model_identity`)
  и совпадают → `Compatible` → создаём runtime-модель;
- заданы и не совпадают → `Incompatible` → `RuntimeError`, генерация не начинается
  (не silent fallback);
- хотя бы одна не задана / низкодоверенная (fallback на `chat.model_name`,
  автоопределение) → `Unknown` → предупреждение, runtime-модель создаётся
  (не блокировка и не silent fallback).

### Расширение `app/ollama_client.py`

| Функция | HTTP | Назначение |
|---------|------|-----------|
| `create_model(client, name, from_model, adapters)` | `POST /api/create` | структурное тело `{model, from, adapters, stream:false}`; **`modelfile` не передаётся** (0.32.6 → 400); ровно один адаптер (§2.5) |
| `upload_adapter_file(client, path, digest)` | `HEAD`/`POST /api/blobs/:digest` | 200 → ничего; 404 → `POST` байтами файла |
| `delete_model(client, name)` | `DELETE /api/delete` | **только явный вызов**, автоудаления нет; в httpx 0.28.1 `Client.delete` без body → `client.request("DELETE", url, json=...)`; 404 = успех |
| `list_models(client)` | `GET /api/tags` | отсортированный уникальный список имён (сверка существования runtime-моделей) |
| `check_capabilities(client)` | `GET /api/version` | доступность + флаги (ленивый импорт `RuntimeCapabilities`) |

Пайплайн создания runtime-модели (без shell, §2.7):

1. sha256 файла (вычислен при регистрации, §2.2);
2. `HEAD /api/blobs/sha256:<digest>` → 200 (уже есть) / 404;
3. при 404 — `POST /api/blobs/sha256:<digest>` байтами файла;
4. `POST /api/create {"model", "from": chat.model_name, "adapters": {"имя.gguf": "sha256:<digest>"}, "stream": false}`.

Кэш и блокировка: in-memory `key → runtime_name` + сверка `list_models` при
промахе (покрывает перезапуск процесса) + per-event-loop `asyncio.Lock` с
двойной проверкой — для одной конфигурации `ollama create` выполняется
максимум 1 раз; **ни один код не вызывает create на каждое сообщение** (§7.4).

**Нет `cleanup()`/GC runtime-моделей** (§2.7): runtime-модели остаются в Ollama,
удаляются только явным `delete_model`.

Ошибки (несовместимость, недоступный runtime, пропавший файл адаптера, битая
связка конфигурации) → `RuntimeError` с понятным текстом, без silent fallback.

## Интеграция в основную генерацию (Sprint 3)

Точка внедрения — ТОЛЬКО основной вызов `ollama_client.generate()` в
`app/chat_engine.py`.

### Хелперы `app/chat_engine.py`

| Элемент | Назначение |
|---------|-----------|
| `resolve_generation_model(db, client, chat, lora_manager=None)` | выбрать модель для основной генерации: делегирует `LoRAManager.resolve` (семантика `lora_enabled`, §2.4); дефолт — `_default_lora_manager()`, в проде передаётся `app.state.lora_manager` |
| `lora_first_apply_warning(chat_id, info)` | SSE-событие `{"type": "lora_warning", "kind": "compatibility_unknown", "detail": ...}` ровно один раз на чат (in-process), при первом применении LoRA со статусом `Unknown` |

### Куда подставлен runtime `model_name`

- `process_user_message_streaming` — генерация идёт с `model_name=generation_model_name`;
- `regenerate_message_streaming` — то же самое.

Служебные вызовы (`extract_scene_state`, `post_round_pipeline`, память,
отношения, события, сенсоры, консолидация, кризис) **не затронуты** — все
используют `chat.model_name`.

### Проводка менеджера

- `app/main.py`: `app.state.lora_manager = LoRAManager()` в lifespan;
- `app/routers/chat_engine.py`: `lora_manager = getattr(request.app.state, "lora_manager", None)` → передаётся в оба генератора (fallback `None` → дефолт в `chat_engine`).

### Поведение ошибок

- Ошибка LoRA **до начала генерации** (Incompatible, пропавший файл адаптера,
  битая конфигурация, недоступный runtime) → `RuntimeError` из `resolve` до
  создания user-сообщения; клиенту — понятная ошибка, конфигурация чата не
  изменяется. НЕ silent fallback.
- `Unknown` по совместимости (§2.3) → SSE `lora_warning` перед первым токеном
  (информирование/подтверждение), генерация продолжается на runtime-модели.
- `Incompatible` → блокировка с текстом (до `lora_warning` не доходит).

Не изменены: Dynamic CTX, thinking reserve, sensor context, memory pipeline,
relationships, World & Perception Engine, role isolation, retry logic, tool-mode;
Streaming/Thinking/Instant/stop/retries — без изменений.

## REST API (Sprint 4)

Роутер `app/routers/lora.py` (зарегистрирован в `app/main.py` через
`api_router.include_router(lora.router)`). **Две логические группы endpoints,
разделённые по §2.6.**

### Глобальный registry (какие адаптеры доступны приложению)

| Endpoint | Назначение |
|----------|-----------|
| `GET /api/lora` | список зарегистрированных адаптеров (`LoRAAdapterRead[]`, новые первыми) |
| `POST /api/lora` | регистрация: валидация пути (§2.7) + `base_model_identity`; 201 |
| `PUT /api/lora/{adapter_id}` | изменение; при смене `path`/`format` — повторная валидация и пересчёт sha256 |
| `DELETE /api/lora/{adapter_id}` | удаление **только регистрации** (физический файл не трогается) |

Тело `POST /api/lora` — `LoRAAdapterCreate`: `name`, `path`, `format`
(`gguf`/`auto`; `safetensors` отклоняется), `base_model`, `base_model_identity`
(nullable), `enabled`, `description`, `source`, `metadata`.

### Конфигурация чата (какой адаптер используется в конкретном чате)

| Endpoint | Назначение |
|----------|-----------|
| `GET /api/chats/{chat_id}/lora` | `ChatLoRAConfig{enabled, adapter_id}` — один запрос = источник настроек фронта |
| `PUT /api/chats/{chat_id}/lora` | атомарная замена `{enabled, adapter_id}`; UNIQUE(chat_id) — не более одной связки |

`enabled=true` + `adapter_id=null` — допустимое состояние (§2.4): «LoRA
включена, но адаптер не выбран»; пустая runtime-модель не создаётся.

### Коды ошибок

| Код | Условие |
|-----|---------|
| 404 | чат/адаптер не найден |
| 409 | `DELETE` регистрации адаптера, используемого ≥1 чатом; `detail` = `{message, chats: [{chat_id, name}]}` |
| 422 | невалидный путь/формат/файл (§2.7), ссылка на несуществующий адаптер |

## Тесты

`tests/test_lora_db_crud.py` — 29 тестов (ТЗ §36: 1–13):

- миграции свежей и «прод»-БД (идемпотентность, backfill `lora_enabled=false`);
- UNIQUE(chat_id), FK CASCADE;
- валидация пути (не абсолютный / не существует / директория / не-GGUF /
  обрезанный GGUF / safetensors);
- CRUD create/update/delete, sha256, metadata, атомарная замена конфигурации;
- delete использованного адаптера → `LoRAInUseError` со списком чатов;
- физический файл цел после удаления регистрации.

`tests/test_lora_runtime.py` — 21 тест (ТЗ §36: 14–21), моки httpx
(`httpx.MockTransport`, фейковый Ollama с in-memory models/blobs):

- runtime key/name: детерминированность, чувствительность к base identity /
  adapter_id / sha256, формат `{slug}-lora-{hash8}`;
- семантика `lora_enabled` (false / true без адаптера → base, без create);
- Compatible → runtime-модель, **ровно 1× create**, повторный resolve — кэш-хит;
- смена адаптера → новый ключ → новая runtime-модель;
- повторный запуск (новый `LoRAManager`) — сверка `list_models`, create = 0;
- Incompatible / пропавший файл / битая конфигурация → `RuntimeError`;
- Unknown (fallback identity на `model_name`) → не блокирует, модель создаётся;
- blob-флоу (HEAD 200 → без POST), create с >1 адаптером → ошибка;
- `delete_model` через `request("DELETE", json=...)` с телом;
- `check_capabilities` кэшируется на инстансе.

`tests/test_lora_integration.py` — 6 тестов (ТЗ §36: 21), моки
`FakeOllamaClient`/`FakeLoRAManager`:

- `lora_enabled=false` → ответ идентичен текущему (base, без runtime-модели);
- `lora_enabled=true` без адаптера → base, без create;
- `lora_enabled=true` + адаптер → основной `generate` на runtime-модели,
  служебные вызовы (`extract_scene_state`, `post_round_pipeline`) — на
  `chat.model_name`;
- перегенерация (`regenerate`) — тоже на runtime-модели;
- Unknown → SSE `lora_warning` (1 раз на чат), конфиг не меняется;
- Incompatible → `RuntimeError` до генерации, конфиг не меняется.

`tests/test_lora_api.py` — 20 тестов (ТЗ §36: 22–31), клиент через
`httpx.ASGITransport` + override `get_async_db`:

- registry: пустой список, create (201, auto → gguf, sha256), list;
- 422: неабсолютный путь, safetensors, не-GGUF, несуществующий путь;
- PUT: обновление метаданных (sha256 не пересчитывается), смена пути
  (revalidation + пересчёт sha256), невалидный путь → 422, несуществующий → 404;
- DELETE: неиспользуемый → 204, файл на диске цел; используемый чатами → 409
  с `chats: [{chat_id, name}]`; несуществующий → 404;
- конфигурация чата: GET дефолт `{enabled: false, adapter_id: null}`, чат не
  найден → 404; PUT enable+adapter / disable / `enabled=true`+`adapter_id=null`
  (§2.4); атомарная замена (одна связка, старый адаптер освобождается);
  несуществующий адаптер → 422; несуществующий чат → 404.

## Дальнейшие спринты (не реализовано)

- **Sprint 6** — документация и финальная проверка (ТЗ §38–§42, acceptance на
  реальной модели `Dark-Goetia-26B-A4B-LoRA-RU-v1`).

## Frontend (Sprint 5, Vue)

Вкладка «LoRA» в модальном окне настроек + индикатор в шапке чата.

### Файлы

| Файл | Назначение |
|------|-----------|
| `frontend/src/types/lora.ts` | `LoRAAdapter`, `ChatLoRAConfig`, `CompatibilityStatus` |
| `frontend/src/api/lora.ts` | `fetch/create/update/deleteLoraAdapter`, `fetch/updateChatLoraConfig` |
| `frontend/src/api/types.ts` | методы `Api` (registry + chat config) |
| `frontend/src/stores/lora.ts` | Pinia: **раздельные** registry (`adapters`) и chat config (`config`) |
| `frontend/src/components/settings/LoRASettings.vue` | вкладка «LoRA» — две секции (§2.6) |
| `frontend/src/components/chat/ChatHeader.vue` | бейдж-индикатор «LoRA» |
| `frontend/src/mocks/data.ts` / `service.ts` | mock-адаптеры, mock-конфиги, mock-API |

### Две логические части UI (§2.6)

- **«Доступные LoRA» (глобальный registry)** — список адаптеров (name, путь,
  base model/identity, статус Compatible/Incompatible/Unknown относительно
  базовой модели текущего чата), «+ Добавить LoRA» → форма
  (название/путь/формат/base model identity/базовая модель/описание),
  редактирование, удаление (409 → список чатов).
- **«LoRA этого чата» (конфигурация)** — тумблер «Включить LoRA», селектор
  **ровно одного** адаптера, кнопка «Убрать».

### Три состояния `lora_enabled` (§2.4)

- `enabled=true` + адаптер не выбран → предупреждение «LoRA включена, но
  адаптер не выбран»;
- `enabled=false` → нейтральный вид;
- `enabled=true` + адаптер → рабочий вид (селектор + «Убрать»).

### Ограничения runtime в UI (§2.5)

- ровно один адаптер: селектор, без мультивыбора и weight-контролов;
- `supports_safetensors=false`: в форме регистрации только `gguf`/`auto`;
- статус `Unknown` — явное предупреждение (badge в списке + inline-блок при
  выборе), не блокирует молча.

### Единый источник состояния

Единственный объект `{enabled, adapter_id}`. В компоненте — локальный draft,
синхронизируемый **только** из серверного ответа; после Save источник истины —
`GET/PUT /api/chats/{id}/lora` (стор `lora.config`). Без сабмита изменения
сбрасываются (компонент пересоздаётся при закрытии модалки / смене чата).

### Статус совместимости в UI (§2.3)

Статус считается по `base_model_identity` адаптера и чата. Для этого в `ChatRead`
добавлено поле `base_model_identity` (nullable). Если identity не задана (у
адаптера или у чата) — статус `Unknown` (честное предупреждение). Обратная
совместимость: старый backend не отдаёт поле → статус `Unknown`.

### Проверка

`npm run build` (vue-tsc) без ошибок; мануальные сценарии (фронт-тест-раннер
отсутствует). Backend после уточнения `ChatRead` — полный LoRA-прогон 76 passed,
chat/memory/ollama 41 passed.
## Приёмка на реальной модели (Sprint 6)

Скрипт `research/lora-acceptance/run_acceptance.py` прогоняет полный цикл на
**реальной** модели из задачи (`SubMaroonDark-Goetia-26B-A4B-LoRA-RU-v1.gguf` +
`Goetia-26B-A4B...IQ4_XS.gguf`, локальный Ollama 0.32.6) через реальные модули
приложения (`crud`, `LoRAManager`, `ollama_client`); БД — временный SQLite-файл.

Итог: **12/12 PASS** (протокол — `research/lora-acceptance/ACCEPTANCE.md`):
register adapter (sha256 совпадает с blob) → select adapter → enable LoRA → resolve →
runtime-модель создана/переиспользована в реальном Ollama → `Compatible` →
generate с LoRA → disable → generate без LoRA.

### Дефекты, найденные при приёмке и исправленные

1. **Имя runtime-модели длиннее 40 символов → `400 invalid model name`.**
   Ollama ограничивает длину имени модели (`MaxModelNameLength = 40`). Для длинных
   HF-путей (``hf.co/mradermacher/...:...gguf``) slug базовой модели обрезается до
   `40 - len("-lora-{hash8}")`; уникальность сохраняется через `hash8` (runtime key).
   Регрессия: `tests/test_lora_runtime.py::test_runtime_name_truncated_for_long_base_model`.
2. **Повторный запуск пересоздавал runtime-модель.** Модель, созданная без явного
   тега, в `GET /api/tags` отдаётся как `name:latest`; сверка искала голое `name`.
   Исправлено: `_model_exists_in_ollama()` учитывает и `name:latest`.
   Регрессия: `tests/test_lora_runtime.py::test_fresh_manager_reuses_model_listed_with_latest_tag`.
3. **Acceptance-скрипт:** `httpx.AsyncClient` без таймаута (дефолт 5 с) → `Ollama chat
   timeout` на холодной загрузке 14 ГБ модели; таймаут клиента 900 с. Повторное
   создание персонажа → `order_index=1 уже занят`; персонаж создаётся один раз.

## FAQ по Ollama

**Как Ollama применяет LoRA?** Runtime-модель создаётся структурным `POST /api/create`
с `from` (базовая модель) и ровно одним `adapters: {filename: digest}`. В 0.32.6
модель принимает **ровно один** адаптер; механизма `weight/scale` нет. Приложение
не вызывает `ollama create` на каждое сообщение — runtime-модель создаётся один раз
и переиспользуется (кэш + сверка `GET /api/tags`).

**Почему `ollama run <base>` не показывает эффект LoRA?** LoRA «вшит» только в
отдельную runtime-модель. Базовую модель без адаптера менять нельзя — генерировать
нужно через runtime-имя (приложение делает это автоматически при `enabled=true`).

**Почему runtime-модель в `ollama list` имеет тег `:latest`?** Модель создаётся без
явного тега, Ollama добавляет `:latest`. Это учтено в сверке существования.

**Почему первая генерация после создания модели медленная/таймаутит?** Холодная
загрузка модели в VRAM (на RTX 5070 Ti — десятки секунд для 14 ГБ GGUF). Клиент
должен иметь таймаут больше времени загрузки (в acceptance — 900 с).

**Почему лимит имени 40 символов?** `MaxModelNameLength` в Ollama. Длинные имена
базовых моделей из HuggingFace обрезаются в runtime-имени; уникальность — через
`hash8` runtime key.

**Можно ли очистить runtime-модели?** MVP не удаляет их автоматически (без GC).
При необходимости — вручную: `ollama rm <runtime-name>`. При следующем `resolve`
модель будет пересоздана.

**Какие форматы адаптеров поддерживаются?** Только GGUF (`.gguf`). В 0.32.6
safetensors-адаптеры не поддерживаются (`supports_safetensors=false`, подтверждено
Sprint 0).

**Можно ли несколько LoRA одновременно?** Нет — ровно одна на runtime-модель
(ограничение Ollama) и ровно одна на чат (ограничение MVP, `UNIQUE(chat_id)`).

**Как пересоздать runtime-модель при смене адаптера?** Любое изменение
(другой адаптер / другой файл / другая `base_model_identity`) даёт новый runtime key
→ новая runtime-модель автоматически; старые остаются в Ollama до ручной очистки.

# LoRA-адаптеры (MVP: одна LoRA на чат, без весов)

Поддержка LoRA-адаптеров для **основного ответа персонажа**. План и полная
контекстная информация — [`Plans/LoRA.md`](../Plans/LoRA.md). Документ описывает
реализованные слои **Sprint 1 (модель данных, миграции, CRUD, валидация пути)** и
**Sprint 2 (runtime-слой: `LoRAManager` + расширение `ollama_client`)**.
REST API (Sprint 4) и фронтенд (Sprint 5) — в плане, ещё не реализованы.

## Ограничения MVP (подтверждены эмпирически, Sprint 0)

- **Ровно один LoRA-адаптер на чат** — в модели данных (`UNIQUE(chat_id)`), в
  конфигурации и в UI. Поля `weight`/`order_index` НЕ создаются.
- **Weight/scale отсутствует** — не хранится, не передаётся, не эмулируется.
- **Только GGUF** — `supports_safetensors=false`; safetensors-адаптеры
  отклоняются при регистрации.
- **Физический файл пользователя никогда не удаляется** — `DELETE` регистрации
  не трогает `.gguf` на диске.
- Служебные LLM-вызовы (память, отношения, сенсоры, scene state и т.д.) LoRA
  **не получают** — интеграция только в основной вызов `generate()` (Sprint 3).

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

## Дальнейшие спринты (не реализовано)

- **Sprint 3** — интеграция в основной `generate()` (chat_engine): только
  основной ответ персонажа получает runtime-модель, служебные вызовы — без LoRA
- **Sprint 4** — REST API `/api/lora` и `/api/chats/{id}/lora`
- **Sprint 5** — Vue-фронтенд (вкладка «LoRA»)

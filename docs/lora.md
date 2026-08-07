# LoRA-адаптеры (MVP: одна LoRA на чат, без весов)

Поддержка LoRA-адаптеров для **основного ответа персонажа**. План и полная
контекстная информация — [`Plans/LoRA.md`](../Plans/LoRA.md). Документ описывает
реализованный слой **Sprint 1 (модель данных, миграции, CRUD, валидация пути)**.
Runtime-слой (Sprint 2) и REST API (Sprint 4) — в плане, ещё не реализованы.

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

### `chats.lora_enabled`

`BOOLEAN NOT NULL DEFAULT 0`. Флаг отделён от связки: «LoRA включена, но
адаптер не выбран» — допустимое состояние (пустая runtime-модель не создаётся).

## Миграции

Идемпотентный `ensure_schema()` в `app/database.py`:

- `ALTER TABLE chats ADD COLUMN lora_enabled BOOLEAN NOT NULL DEFAULT 0` —
  существующие чаты автоматически получают `false` (backfill через DEFAULT);
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

## Тесты

`tests/test_lora_db_crud.py` — 29 тестов (ТЗ §36: 1–13):

- миграции свежей и «прод»-БД (идемпотентность, backfill `lora_enabled=false`);
- UNIQUE(chat_id), FK CASCADE;
- валидация пути (не абсолютный / не существует / директория / не-GGUF /
  обрезанный GGUF / safetensors);
- CRUD create/update/delete, sha256, metadata, атомарная замена конфигурации;
- delete использованного адаптера → `LoRAInUseError` со списком чатов;
- физический файл цел после удаления регистрации.

## Дальнейшие спринты (не реализовано)

- **Sprint 2** — `LoRAManager` + `OllamaClient.create_model/upload_adapter_file/...`
- **Sprint 3** — интеграция в основной `generate()` (chat_engine)
- **Sprint 4** — REST API `/api/lora` и `/api/chats/{id}/lora`
- **Sprint 5** — Vue-фронтенд (вкладка «LoRA»)

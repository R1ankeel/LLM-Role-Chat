# Локации 2.0

Локации — самостоятельная сущность чата с CRUD API и UI. Реализовано в рамках
«Локации 2.0 и проверка изоляции NPC» (см. `Plans/locations2.md`, спринты 1–3,
5).

## Модель и совместимость

До «Локаций 2.0» локации хранились только строками:

- `chats.locations` — JSON-массив названий (для движка);
- `characters.location` — строка-имя;
- `messages.location` — строка;
- `scene_states.character_locations` — JSON `{id|name: название}`;
- `chats.player_location` — строка.

Теперь добавлена таблица `locations` — **источник истины** для CRUD и описаний:

| колонка | тип | примечание |
|---|---|---|
| `id` | INTEGER PK | |
| `chat_id` | FK → `chats.id` ON DELETE CASCADE | |
| `name` | TEXT(255) NOT NULL | уникально в пределах чата |
| `description` | TEXT NOT NULL DEFAULT '' | |
| `created_at` | DATETIME | |
| `updated_at` | DATETIME | |

- `UniqueConstraint(chat_id, name)` (`uq_location_chat_name`) + индекс `ix_locations_chat_id (chat_id)`.
- `chats.locations` **не удаляется** — остаётся кэшем названий для движка и
  автоматически синхронизируется при каждой CRUD-операции (§14).
- `location_id` в `characters` **не вводится**: `characters.location` остаётся
  строковым именем (perception/сцена/сообщения сравнивают по имени).
- Сравнение имён локаций идемпотентно через `perception.locations_match`
  (case-insensitive, если `settings.normalize_locations=True`).

## Миграция / backfill

При старте `ensure_schema` создаёт таблицу `locations` и идемпотентно
заполняет её из существующих `chats.locations` (`INSERT OR IGNORE`,
`description = ''`). Повторный запуск не дублирует строки.

## CRUD API

Все пути под префиксом `/api` (роутер `app/routers/locations.py`).

### GET `/api/chats/{chat_id}/locations`
Список локаций чата (сортировка по `name`). `404` — чат не найден.

### POST `/api/chats/{chat_id}/locations`
Создать локацию. Тело: `{ "name": "...", "description": "..." }`.
- `201` — создано.
- `409` — имя уже существует (в т.ч. с другим регистром).
- `422` — пустое имя.
Синхронизирует `chats.locations`.

### PUT `/api/chats/{chat_id}/locations/{location_id}`
Изменить `name` / `description`. При переименовании **синхронно обновляются**
строковые ссылки во всех местах, чтобы не было битых ссылок:
- `characters.location` (включая игрока);
- `messages.location`;
- `scene_states.character_locations` (значения);
- `chats.locations` (кэш).

`404` — локация не найдена / не принадлежит чату; `409` — имя занято; `422` —
пустое имя.

### DELETE `/api/chats/{chat_id}/locations/{location_id}`
Удалить локацию.
- `204` — удалено.
- `404` — не найдена / не принадлежит чату.
- `409` — на локацию ссылаются персонажи; в `detail` возвращается
  `{ "message": "...", "characters": ["Имя1", ...] }` — список персонажей,
  использующих локацию. Удаление блокируется, локация не удаляется молча.

## UI вкладки «Локации» (спринт 3)

В новом Vue-фронтенде вкладка «Локации» (`LocationSettings.vue`) полностью
управляет сущностью:

- список локаций грузится через `GET /locations` при смене чата;
- кнопка «+» открывает инлайн-форму «Название / Описание»;
- карточки «Изменить» / «Удалить»;
- удаление подтверждается `confirm`; при `409` показывается тост со списком
  ссылающихся персонажей (из `detail.characters`);
- тосты через `ui.toast`, сетевые ошибки через `ApiError`/`ApiError.detailData`;
- в мок-режиме (`useMocks`) данные хранятся в `mockLocations` и синхронизируют
  `chats.locations` (JSON-кэш названий).

## Точная изоляция NPC: `compute_is_isolated` (спринт 5)

`is_isolated` решает только один вопрос (Планы §5-B): «есть ли рядом с NPC
кто-либо, с кем он может непосредственно взаимодействовать?» — и **не является**
универсальным фильтром истории (это делает perception / `can_character_perceive_event`).

Хелпер `perception.compute_is_isolated(char_loc, other_char_locs, player_loc)`:

- NPC изолирован **только если** в его локации нет ни игрока, ни других NPC.
- Сравнение локаций — через `perception.locations_match` (case-insensitive
  при `settings.normalize_locations=True`).
- Пустая локация (`""`) = общая сцена → **не** изолирует.

```python
perception.compute_is_isolated("living_room", ["living_room"], "kitchen")  # False — Борис рядом
perception.compute_is_isolated("living_room", ["kitchen"], "kitchen")      # True  — рядом никого
perception.compute_is_isolated("", [], "")                                  # False — общая сцена
```

Применяется во всех **4 местах** расчёта изоляции в `chat_engine.py` через
хелпер `_character_is_isolated`:

- обычная генерация — `context_builder.build(... is_isolated=...)` и
  `ollama_client.generate(... is_isolated=...)`;
- регенерация (`regenerate_message_streaming`) — те же две точки.

Эффект: Анна и Борис в гостиной при игроке на кухне **не изолированы** —
полноценно взаимодействуют друг с другом (тест 5 §22).

Role isolation / foreign speaker protection **не затронуты** (§12): маркер
`изоляция`, stop sequences, `sanitize_and_validate_response` и чужие speaker
markers по-прежнему обрабатываются независимо от `is_isolated`.

## Дальнейшие спринты

- Спринты 4, 6–7 — персональная фильтрация истории, описание локации в scene
  block, память, диагностика, полная регрессия.

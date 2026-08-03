# Локации 2.0

Локации — самостоятельная сущность чата с CRUD API и UI. Реализовано в рамках
«Локации 2.0 и проверка изоляции NPC» (см. `Plans/locations2.md`, спринты 1–3,
5–6).

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

## Описание локации в scene block (спринт 6)

`Location.description` попадает в scene block персонажа под его локацией (§18):

```
Твоя локация: Гостиная

Большая светлая гостиная с диваном, телевизором и выходом на кухню.
```

- Без описания — только «Твоя локация: Гостиная».
- Описание — это **окружение**, а не «истина о происходящем»: не отдельная
  сюжетная информация, не «правда» о событиях.
- Описание другой локации в контекст персонажа **не** утекает.
- Реализация: `prompt_builder.build_scene_block(..., location_descriptions=...)`,
  параметр `location_descriptions: dict[str, str]` (имя локации → описание).
  Передаётся из `crud.get_chat_locations` через оба пути генерации
  (`process_user_message_streaming` и `regenerate_message_streaming`) в
  `ContextBuilder.build` и `ollama_client.generate`/`_generate_once`.

## Память и attribution (спринт 6, §20)

Проверен `filter_history_for_memory_extraction`: строки форматируются с
префиксом говорящего («Анна: Я ненавижу кофе.»), поэтому даже когда Борис
**слышит** реплику Анны в одной комнате, владелец факта сохранён — извлечение
приписывает факт Анне. **Доступность события ≠ владелец факта.**

Memory architecture не менялась: фильтрация по presence (`present`/`told`)
и attribution на уровне форматирования строк работают корректно (тест 8 §22).

## Диагностическое логирование (спринт 6, §21)

За настройкой `settings.generation_debug` (`GENERATION_DEBUG`, по умолчанию
`false` — выключено на production). В цикле генерации NPC (обычная генерация
и регенерация) логируется (`logger.debug`, логгер `app.chat_engine`):

```
NPC=<имя> Location=<локация> PlayerLocation=<локация игрока>
Visible characters=[...]
Hidden characters=[...]
Visible messages=<N>
Filtered messages=<M>
```

- **Visible characters** — NPC и игрок в той же локации, что и текущий персонаж;
- **Hidden characters** — NPC в других локациях;
- **Visible messages** — сообщения, прошедшие perception-фильтр (presence ≠ absent);
- **Filtered messages** — сообщения, скрытые фильтром (presence = absent).

Помогает ответить на вопросы: «почему NPC не видит другого NPC» и «почему
сообщение из другой локации попало в контекст». Реализовано в
`chat_engine._log_generation_diagnostics`.

## Персональный view истории и `effective_prior_replies` (спринт 4, §2–§4, §8–§11)

### Принцип

Общая история чата — единая. Для каждого NPC из неё строится **персональное
представление** через единый «фильтр восприятия» (`can_character_perceive_event` +
`locations_match` + `REMOTE_CHANNELS`):

- **Одна локация** → NPC видят реплики друг друга как `present` (общий
  локальный контекст, §8). «Другой NPC в моей сцене» ≠ «чужой контекст».
- **Разные локации** → события другой локации не попадают в view без
  допустимого remote-канала (§9).
- Перемещение (§19): view пересчитывается автоматически при построении
  контекста по текущей локации персонажа — история не переписывается,
  меняется только результат фильтрации.

### Исправление `effective_prior_replies` (§10)

Битый lookup `presence_map.get(character_id, "present")` (где `presence_map`
индексирован по `message_id`) убран. Вместо него:

- `process_user_message_streaming` накапливает **события** ответов текущего
  раунда (`prior_reply_events`), а для каждого следующего NPC доступность
  каждого ответа определяется тем же механизмом, что и для обычной истории:
  `can_character_perceive_event(viewer, event)`.
- Включено только то, что персонаж мог воспринять полностью (presence
  `present`/`told`); `absent`/`mentioned` скрываются.
- `regenerate_message_streaming` использует тот же фильтр для ответов раунда.
- Единое правило для истории, `prior_replies` и sequential generation (§11):
  никаких отдельных несовместимых фильтров.

### Тесты (§22)

| # | проверка | тест |
|---|---|---|
| 1 | A+B одна локация → B видит ответ A (в `prior_replies`) | `test_locations_perception.py::test_same_location_sees_alt_reply_prior_reply` |
| 2 | A+B+C одна локация → все видят допустимые ответы | `test_three_same_location_all_see_valid` |
| 3 | C в другой локации не видит события/ответы A+B | `test_cross_location_hidden_from_prior_replies` |
| 4 | Перемещение A пересчитывает view | `test_move_recalculates_perception` |
| 5 | `is_isolated` точная | `test_chat_engine.py::test_compute_is_isolated_engine_applied` |
| 7 | Sequential generation | `test_perception.py::test_sequential_generation_respects_locations` |
| 8 | Memory attribution | `test_memory_perception.py::test_memory_attribution_speaker_preserved_same_room` |
| 9 | Remote-канал через локации | `test_remote_channel_bridges_locations` |
| 10 | `compute_is_isolated` + CRUD | `test_perception.py` / `test_locations_api.py` |

Ручные сценарии §23 (1–5) автоматизированы в `tests/test_manual_scenarios.py`
(4 passed): общая гостиная + изолированный NPC на кухне (1–2), сцена NPC
продолжается без игрока (3), удалённый канал в другую локацию (4), speaker
isolation (5).

Speaker isolation (§12, тест 6 §22) не затронут: NPC одной локации видят друг
друга, но каждый отвечает только от своего имени; роль `role_isolation`,
stop sequences и `sanitize_and_validate_response` работают независимо.

## Регрессия (спринт 7)

Полный прогон `pytest`: **570 passed, 28 failed**. Набор из 28 падений —
**pre-existing** и идентичен спринтам 1–2–4–5–6 (task_queue `MemoryJobQueue`,
context_state, embeddings, memory_service, memory_perception, repetition,
stream_disconnect, token_counter); в изменённых для локаций файлах новых
падений нет. Старый Vanilla JS SPA (`app/static/`) не изменялся.

Ручные сценарии §23 (1–5) переведены в автотесты `tests/test_manual_scenarios.py`
(общая гостиная + изолированный NPC на кухне; сцена NPC продолжается без
игрока; удалённый канал в другую локацию; speaker isolation) — 4 passed,
прогоняют реальный путь генерации с фейковым LLM.

Критически важные инварианты (§24) подтверждены автоматическими тестами
(см. таблицу выше): одна локация → общий контекст; разные локации → разные
view; общая история хранится одна; `is_isolated` ≠ фильтр истории;
`player_location` не единственный источник локальной сцены; remote-канал
может сделать событие доступным независимо от локации.

## Уровни восприятия и соседство (Isolation FIS, Спринт 2)

### Уровни восприятия

Введено разделение VISIBLE / AUDIBLE / MENTIONED / ABSENT
(`perception.PerceptionLevel`), отображаемое в presence:

| Уровень | Presence | Что получает персонаж |
|---|---|---|
| VISIBLE | `present` | Полное событие |
| AUDIBLE | `audible` (новое) | Только слышимое (без визуальных деталей и мыслей автора) |
| MENTIONED | `mentioned` | Непосредственное обращение (стимул `address`/`call` на зрителя) |
| ABSENT | `absent` | Ничего |

Центральная функция `perception.get_perception_level(...)`:

- одна локация → `visible` (вне зависимости от стимулов);
- `address`/`call` на зрителя из **соседней** локации → `mentioned`; из
  далёкой недостижимой локации → `absent` (ТЗ §14);
- громкий стимул (`knock`/`shout`/`loud_sound`/`call`) из соседней → `audible`;
- тихое событие из соседней без стимула → `absent` (ТЗ §7: слышны только
  достаточно громкие звуки).

Простое упоминание имени в повествовании («Вчера Антон ходил в магазин»)
**больше не даёт** `mentioned` (ТЗ §6) — правило «имя в тексте → mentioned»
убрано из LOCAL-ветки `can_character_perceive_event`.

`can_character_perceive_event` принимает опциональный `adjacency_index` и
переводит уровень в presence через `_LEVEL_TO_PRESENCE`. Ветки
OWN_MESSAGE / GLOBAL / PUBLIC / private / targeted / REMOTE_CHANNELS
сохранены без изменений.

### Соседство локаций

Новая колонка `locations.adjacent_to` — JSON-массив имён соседних локаций:

```json
{ "name": "Кухня", "adjacent_to": ["Гостиная", "Коридор"] }
```

- `perception.build_adjacency_index(locations)` строит нормализованный
  симметричный индекс `{имя: {соседи}}` (связь A→B автоматически даёт B→A).
- `perception.are_locations_adjacent(a, b, index)` — проверка соседства по
  явным связям. Без связей локации НЕ соседние (консервативно).
- `crud.get_adjacency_index(db, chat_id)` — индекс для чата из таблицы.
- При переименовании локации ссылки в `adjacent_to` других локаций
  обновляются синхронно.
- Опциональный эвристический fallback (общий первый топоним, напр.
  «Квартира Ольги» / «Квартира Бориса») включается флагом
  `ADJACENCY_FALLBACK_ENABLED` (по умолчанию `False`).

### Стимулы

Стимулы — **метаданные события**, не отдельная сущность БД. Хранятся в
`messages.stimuli` (JSON). Модуль `app/stimuli.py`:

- `Stimulus(type, target_character, audibility)` — `knock | call | shout | address | loud_sound`;
- `extract_stimuli(text, character_names)` — regex-эвристики (заменяемы на LLM без изменения остального кода);
- `build_audible_line(event)` — рендер AUDIBLE **без утечки визуальных деталей**:
  стук → «Ты слышишь стук в дверь из соседней локации.», крик → «…крик…», зов,
  громкий звук, голос; легаси-сообщения без стимулов → generic «Из соседней
  локации доносится звук.» — полный контент никогда не возвращается;
- `parse_stimuli`/`serialize_stimuli` — JSON-сериализация.

### Рендер в истории

`witness_model.format_line_for_presence`:

- `audible` → `[Ты слышишь: {snippet}]`, где `snippet` — строка от `build_audible_line`;
- `mentioned` с address/call-стимулом на зрителя → `{Автор} обращается к тебе: «…»`;
  иначе — прежний `[Тебя упомянули: {snippet}]`;
- `present`/`told`/`absent` — без изменений.

`MEMORY_OBSERVABLE_PRESENCES = {present, told}` — `audible`/`mentioned`
не попадают в извлечение памяти/сводки (частичная инфа не выдаётся как полная).

### Активация на этом этапе

`audible`/`mentioned`-уровни и соседство работают в perception-функциях и
при передаче `adjacency_index` (в т.ч. через `compute_mvp_presence` и
`filter_history_*`). С Спринта 3 `extract_stimuli` вызывается при создании
user- и character-сообщений (в `chat_engine.process_user_message_streaming`
и `regenerate_message_streaming`), стимулы сохраняются в `messages.stimuli`
и читаются perception-слоем через `event_from_message`.
Передача `adjacency_index` в рантайм-контекст генерации (audible-реплики в
`_effective_prior_replies`, подключение соседства в `chat_engine`) — Спринт 4.

### Тесты (§18 items 4-10)

| # | проверка | тест |
|---|---|---|
| 4 | Та же локация → VISIBLE | `tests/test_perception_levels.py::test_same_location_visible*` |
| 5 | Соседняя + стук → AUDIBLE | `test_adjacent_knock_audible` / `test_can_perceive_adjacent_knock_audible_presence` |
| 6 | Соседняя + крик → AUDIBLE | `test_adjacent_shout_audible` |
| 7 | Обращение по имени → MENTIONED | `test_address_by_name_mentioned` / `test_call_by_name_mentioned` |
| 8 | Простое упоминание → НЕ MENTIONED | `test_simple_mention_not_mentioned` / `test_local_branch_name_mention_no_longer_mentioned` |
| 9 | Далёкая несвязанная → ABSENT | `test_far_location_absent` / `test_far_unrelated_location_absent_even_when_addressed` |
| 10 | AUDIBLE без визуальных деталей | `test_audible_line_does_not_reveal_visual_details` / `test_format_audible_line_uses_template` |

Интеграция CRUD: `test_adjacency_crud_and_perception_integration`,
`test_rename_updates_adjacency_references`.

### Тесты (§18 items 17-20, стимулы)

| # | проверка | тест |
|---|---|---|
| 17 | «стучу в дверь» → stimulus knock | `tests/test_stimuli.py::test_knock_stimulus` |
| 18 | «Ольга, ты дома?» → stimulus address | `test_address_stimulus` |
| 19 | Стимул не создаёт отдельное сообщение | `test_stimuli_do_not_create_extra_messages` |
| 20 | Стимул доступен perception | `test_stimulus_reaches_perception` |

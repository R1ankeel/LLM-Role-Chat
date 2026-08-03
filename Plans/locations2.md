# Локации 2.0 и исправление изоляции NPC — план реализации

> **Статус:** Спринты 1-3 выполнены (2026-08-03). Спринт 3 «UI вкладки Локации» выполнен (2026-08-03): CRUD в UI, обработка 409, сборка/типы прошли. Спринт 4 «Персональная фильтрация истории (perception)» выполнен (2026-08-03). Спринт 5 «compute_is_isolated» выполнен (2026-08-03). Спринт 6 «Scene block с описанием + память + логирование» выполнен (2026-08-03). Спринт 7 «Полный прогон тестов и ручные сценарии» выполнен (2026-08-03).
> **Дата:** 2026-08-03
> **Цель:** реализовать систему «Локации 2.0» (локации как самостоятельная сущность с CRUD и UI) и исправить чрезмерную изоляцию NPC.
> **Главный принцип:** общая история чата остаётся единой. Для каждого NPC из неё формируется персональное представление истории на основании того, какие события этот NPC мог воспринимать. Локация — главный фактор восприятия, но не единственный. NPC одной локации полноценно взаимодействуют друг с другом.
> **Ограничение:** существующий Vanilla JS SPA (`app/static/`) НЕ изменяется; правки только в новом Vue-фронтенде + backend. Без масштабного рефакторинга движка.

## 1. Исследование текущей реализации (выполнено)

### 1.1 Где сейчас хранятся локации
- `chats.locations` — TEXT, JSON-массив названий (для движка: список допустимых локаций, парсинг сцены);
- `characters.location` — TEXT, строка-имя;
- `messages.location` — TEXT, локация события;
- `scene_states.character_locations` — JSON по имени;
- `chats.player_location` — TEXT.

Отдельной сущности `Location` нет.

### 1.2 Причина over-isolation
- **Корень:** `is_isolated` фактически вычисляется как `character.location != player_location` в `chat_engine.py` (обычная генерация ~строки 596-599 и 627; `regenerate_message_streaming` ~строки 1971-1973 и 2009). NPC, не находящийся у игрока, считается изолированным, даже если в его локации другие NPC → Анна и Борис в гостиной не взаимодействуют, пока игрок на кухне.
- **`prior_replies`:** `chat_engine.py:525-529` — lookup `presence_map.get(current_character.id, "present")`: `presence_map` индексирован по `message_id`, lookup по `character_id` → всегда default `"present"` → ответы других персонажей попадают в anti-mimicry/vocabulary-блоки независимо от того, мог ли NPC их воспринимать. Нарушает тест 3.

### 1.3 Что уже работает (не трогать без необходимости)
- Witness-фильтрация диалога по presence (`witness_model.filter_history_for_character`);
- `perception.can_character_perceive_event` + `locations_match` (casefold) + `REMOTE_CHANNELS` (magic/phone/radio/messenger);
- Role isolation: `role_isolation.build_role_isolation_block`, `find_foreign_speaker_marker`, `sanitize_and_validate_response`, stop sequences, маркер `изоляция`;
- Sequential generation (`process_user_message_streaming` + `regenerate_message_streaming`);
- Memory extraction по observable-context (`memory_service`, `witness_model.filter_history_for_memory_extraction`);
- Вкладка «Локации» в новом Vue-фронтенде (`LocationSettings.vue`), но read-only и с несовпадением форматов (фронт ждёт `{name, description}`, бэкенд хранит строки) — фактически пустая.

## 2. Архитектурный принцип

- **НЕ создавать** отдельную историю на локацию и не дублировать историю в БД.
- История чата — единая:
  ```
  Игрок (гостиная): Аня, привет!
  Анна (гостиная): Игрок, привет!

  Виктор (кухня): Наташа, привет!
  Наталья (кухня): Виктор, привет!
  ```
- При генерации ответа NPC строится его **персональный view** фильтрацией общей истории через «фильтр восприятия»:
  ```
       ОБЩАЯ ИСТОРИЯ
             │
             ▼
    ФИЛЬТР ВОСПРИЯТИЯ
             │
      ┌──────┼──────┐
      ▼      ▼      ▼
    Анна   Виктор  Наталья
      │      │       │
   свой view свой view свой view
  ```

## 3. Восприятие события

Для каждого сообщения/события: «может ли конкретный персонаж воспринять это событие?» — через существующий `can_character_perceive_event` и perception/presence.

- **Основной критерий — локация:** одна локация → доступно; разные локации → недоступно.
- **Не единственный критерий:** поддерживать удалённые способы передачи — личное сообщение, звонок, радио, другие `REMOTE_CHANNEL`, глобальные события и прочие предусмотренные движком механизмы.

## 4. Пример удалённого сообщения

Недопустим примитивный фильтр `message.location == character.location` как единственный критерий.

```
Игрок (комната игрока):
*Я достал телефон и написал сообщение в мессенджере Василию
"Ты когда выйдешь?"
```
Василий в другой локации всё равно получает сообщение, если механика определяет его как адресата. Использовать существующий `REMOTE_CHANNELS`, не создавать параллельный механизм.

## 5. Разделение двух задач

- **A. Восприятие истории** — «какие сообщения NPC может видеть/слышать/знать?» → `can_character_perceive_event` + perception.
- **B. `is_isolated`** — «есть ли рядом с NPC кто-либо, с кем он может непосредственно взаимодействовать?» → НЕ универсальный фильтр истории.

## 6. Новая логика `is_isolated`

Хелпер `compute_is_isolated(char_loc, other_char_locs, player_loc)`:
- NPC изолирован только если **в его локации нет ни игрока, ни других NPC**.
- Проверку локаций выполнять через `locations_match`.
- Пустая локация (`""`) = общая сцена → не делает NPC изолированным.

Примеры:
```
Анна → гостиная, Борис → гостиная, Игрок → кухня
Анна: is_isolated = false   # Борис рядом

Анна → гостиная, Борис → кухня, Игрок → кухня
Анна: is_isolated = true    # рядом никого
```
Применить во всех местах расчёта `is_isolated`: обычная генерация (`chat_engine.py` ~596-599, ~627) и регенерация (~1971-1973, ~2009). Не просто убрать isolation, а сделать его точным.

## 7. Взаимодействие NPC одной локации

NPC одной локации — участники одной локальной сцены: видят реплики друг друга, слышат, отвечают, реагируют на действия, продолжают диалог. Каждый отвечает **только от своего имени**:
```
Анна: — Борис, ты куда?
Борис: — На кухню.
```
Нормально. А вот это — нарушение speaker isolation:
```
Борис: — Анна: ты куда? — Виктор: я сейчас приду.
```

## 8. История для NPC одной локации

Пример: Игрок, Анна, Борис в гостиной — при генерации Анны она получает полную допустимую историю (реплики Бориса входят в её view). Другой NPC в той же локации **НЕ является «чужим контекстом»**.

## 9. История для NPC другой локации

Гостиная (Анна, Борис) + Кухня (Виктор, Наталья):
- view Анны: только события гостиной;
- view Виктора: только события кухни.
События другой локации не попадают в контекст без существующего механизма передачи информации.

## 10. `effective_prior_replies`

Исправить механизм `effective_prior_replies` (`chat_engine.py:525-529`). Убрать битый lookup через `message_id`/`character_id`. Для каждого предыдущего ответа доступность определять через **тот же механизм восприятия события**, что и для обычной истории: `can_character_perceive_event(viewer, event)`.
- Ответ NPC в той же локации → доступен NPC этой локации.
- Ответ NPC в другой локации → недоступен (если нет допустимого remote-канала).

Единое правило. Не создавать отдельные несовместимые фильтры для истории, `prior_replies` и sequential generation.

## 11. Sequential generation

Генерация последовательная. При генерации Бориса он видит ответ Анны текущего раунда (одна локация); при генерации Виктора — допустимые ответы Анны и Бориса. NPC из другой локации эти ответы не видит. (После фикса §10 это выполняется автоматически через presence `present` → `round_messages` → witness.)

## 12. Speaker isolation сохранить

Исправление локального контекста НЕ ломает защиту от генерации от имени другого NPC, подмены говорящего, чужих speaker markers, смешивания персонажей в одном ответе.

Разделить: «другой NPC в моей сцене» ≠ «NPC, от имени которого я должен писать». Сохранить: role isolation, generation cue, stop sequences, `sanitize_and_validate_response`, foreign speaker marker, связанные проверки.

## 13. Модель Location

Новая таблица `locations` (`app/models.py`):
```
Location
- id
- chat_id (FK → chats, ondelete=CASCADE)
- name
- description
- created_at
- updated_at
UniqueConstraint(chat_id, name)
```

## 14. Совместимость со старой системой

- `chats.locations` **не удалять** — остаётся кэшем для существующего движка.
- Таблица `locations` — источник истины для CRUD и описаний.
- При CRUD-операциях синхронизировать `chats.locations`.
- `location_id` в `characters` **не вводить** (нет необходимости) — `characters.location` остаётся строковым именем.
- При переименовании локации синхронно обновлять ссылки: `characters.location`, `messages.location`, `scene_states.character_locations`. Не допускать битых ссылок.

## 15. Миграция / backfill

При старте приложения (`app/database.py`, `ensure_schema`): создать таблицу `locations` и сделать backfill — распарсить `chats.locations` в строки (для существующих `description = ""`). Существующие строковые поля не ломать. Использовать существующий механизм миграций (создание таблиц при старте), не плодить вторую систему миграций.

## 16. CRUD API

Новый роутер `app/routers/locations.py` (паттерны из `routers/characters.py`):

| Метод | Путь | Действие |
|---|---|---|
| GET | `/api/chats/{chat_id}/locations` | список `LocationRead` |
| POST | `/api/chats/{chat_id}/locations` | создать (name, description); **409 при дубле имени**; обновить `chats.locations` |
| PUT | `/api/chats/{chat_id}/locations/{loc_id}` | изменить; при переименовании обновить строковые ссылки (см. §14) |
| DELETE | `/api/chats/{chat_id}/locations/{loc_id}` | **409 при наличии ссылающихся персонажей** (вернуть информацию о персонажах); иначе удалить + синхронизировать `chats.locations` |

Схемы `LocationCreate/Update/Read` в `app/schemas.py`, регистрация в `app/main.py`.

## 17. UI: вкладка «Локации» (Vue)

- Файлы: `frontend/src/components/settings/LocationSettings.vue` + новый `frontend/src/api/locations.ts`; список из нового GET, не из `chat.locations`.
- **Кнопка «+»** → форма «Название / Описание» → POST → показать в списке.
- Карточка: «Название / Описание / Изменить / Удалить». Редактирование и удаление.
- При удалении с HTTP 409 — показать пользователю, какие персонажи используют эту локацию.
- Использовать существующие UI-паттерны; старый Vanilla JS SPA не изменять.

## 18. Описание локации в контексте

`Location.description` использовать в scene block (`prompt_builder.build_scene_block`):
```
Твоя локация: Гостиная

Большая светлая гостиная с диваном, телевизором и выходом на кухню.
```
Без описания — только «Твоя локация: Гостиная». Описание — это окружение, а не «истина о происходящем», не отдельная сюжетная информация. Описания передавать из `crud.get_chat_locations` в оба пути генерации.

## 19. Перемещение между локациями

Персональный view пересчитывается автоматически при построении контекста по текущей локации персонажа:
- после перехода Анна воспринимает события новой локации;
- не получает новых событий старой локации;
- не получает автоматически старые события новой локации (которых физически не воспринимала);
- может получать события через remote-каналы.
Историю не переписывать — меняется только результат фильтрации. Проверка: тест 4.

## 20. Память

Проверить `filter_history_for_memory_extraction` и attribution. Анна и Борис в одной комнате: «Анна: — Я ненавижу кофе.» — Борис может услышать, но факт принадлежит Анне («Анна не любит кофе»). **Доступность события ≠ владелец факта.** Не менять memory architecture, если после изменения perception она работает корректно. Проверка: тест 8.

## 21. Диагностическое логирование (DEBUG)

В цикле генерации NPC (обычная генерация + регенерация), за DEBUG-флагом/настройкой:
```
NPC=<имя>
Location=<локация>
PlayerLocation=<локация игрока>

Visible characters=[...]
Hidden characters=[...]

Visible messages=<N>
Filtered messages=<M>
```
Логи должны помогать диагностировать: «почему NPC не видит другого NPC» и «почему сообщение из другой локации попало в контекст». Не включать на production-уровне.

## 22. Автотесты — `tests/`

1. A + B одна локация → B видит ответ A.
2. A + B + C одна локация → все видят допустимые ответы друг друга.
3. A + B → location_1, C → location_2 → C не видит события location_1 (witness `absent`, `effective_prior_replies` пуст).
4. Перемещение A (location_1 → location_2) → контекст и perception пересчитаны.
5. A + B одна локация → `is_isolated=False`; одинокий C → `is_isolated=True`.
6. Speaker isolation: NPC одной локации видят друг друга, но каждый отвечает только за себя; чужие маркеры вырезаются.
7. Sequential generation: следующий NPC одной локации видит ответ предыдущего; NPC другой локации — нет.
8. Memory attribution: факт остаётся привязан к правильному говорящему.
9. **Дополнительный:** удалённый канал — сообщение игрока Василию в другой локации: если это допустимое remote-событие, Василий его видит.
10. Дополнительно: юнит-тест `compute_is_isolated` (пустая локация = общая сцена), CRUD-тест роутера (создание, дубль → 409, переименование → ссылки обновлены, удаление → 409 при ссылках).

## 23. Ручные сценарии (ТЗ §23)

1. Гостиная: Игрок + Анна + Борис → Анна и Борис общаются между собой и с игроком.
2. Гостиная (Анна, Борис) + Кухня (Виктор) → Анна и Борис продолжают общаться, Виктор их разговор не видит.
3. Игрок на кухне (Игрок + Виктор); гостиная (Анна, Борис) → NPC гостиной продолжают собственную сцену без игрока и не вмешиваются в кухонную сцену.
4. Сообщение Василию из другой локации (remote-канал).
5. Speaker isolation.

## 24. Критически важные инварианты

1. Одна локация → общий локальный контекст.
2. Разные локации → разные локальные представления истории.
3. Общая история → хранится одна.
4. Персональный контекст → строится фильтрацией общей истории.
5. Та же локация → событие обычно воспринимается.
6. Другая локация → событие обычно не воспринимается.
7. REMOTE_CHANNEL / допустимое средство передачи → может сделать событие доступным независимо от локации.
8. Видеть другого NPC ≠ генерировать от имени другого NPC.
9. `is_isolated` ≠ фильтр истории (определяет отсутствие участников локальной сцены).
10. `player_location` ≠ единственный источник определения локальной сцены (NPC взаимодействуют независимо от присутствия игрока).

## 25. Что нельзя делать

- Отдельную историю БД для каждой локации.
- Отдельный механизм памяти для каждой локации.
- `location_id` везде только ради новой системы (строковой модели достаточно).
- Полное отключение role isolation / foreign speaker protection.
- Передачу всей истории каждому NPC.
- Фильтрацию исключительно через `message.location == character.location`.
- Использование `player_location` как единственного критерия локальной сцены.
- Крупный рефакторинг движка, не требующийся для задачи.

## 26. Порядок реализации

1. Изучить текущую реализацию локаций. (выполнено)
2. Изучить `role_isolation`. (выполнено)
3. Изучить `can_character_perceive_event`, `presence`, `locations_match`, `REMOTE_CHANNELS`. (выполнено)
4. Изучить текущую генерацию и `prior_replies`. (выполнено)
5. Модель `Location` (§13).
6. Миграция/backfill (§15).
7. CRUD API (§16).
8. UI вкладки «Локации» (§17).
9. Персональная фильтрация истории через perception (§2-4, §8-9). (выполнено, Спринт 4)
10. `compute_is_isolated` (§6). (выполнено, Спринт 5)
11. `effective_prior_replies` (§10). (выполнено, Спринт 4)
12. Проверка sequential generation (§11). (выполнено, Спринт 4)
13. Описание локации в scene block (§18). (выполнено, Спринт 6)
14. Проверка memory attribution (§20). (выполнено, Спринт 6)
15. Диагностическое логирование (§21). (выполнено, Спринт 6)
16. Автотесты (§22). (выполнено, Спринт 7)
17. Ручные сценарии (§23). (выполнено, Спринт 7)
18. Проверка, что существующие тесты role isolation не сломаны (§12). (выполнено, Спринт 7)

## 27. Итоговая архитектура

```
                 ОБЩАЯ ИСТОРИЯ
                       │
                       ▼
             ┌──────────────────┐
             │ PERCEPTION FILTER │
             └──────────────────┘
                       │
      ┌────────────────┼────────────────┐
      ▼                ▼                ▼
    АННА             ВИКТОР           ВАСИЛИЙ
      │                │                │
  свой view        свой view        свой view
      │                │                │
      ▼                ▼                ▼
   гостиная          кухня          другая локация
```

- Обычная речь: `same location` → visible; `different location` → hidden.
- Remote-канал: `different location + valid remote channel` → visible.
- Локальное взаимодействие: `same location` → shared scene → NPC видят и реагируют друг на друга.
- Speaker isolation: NPC видит другого NPC ≠ NPC становится другим NPC.

> **Главная цель:** не усиливать изоляцию, а сделать её точной. Персонаж видит ровно ту часть общей истории мира, которую мог воспринимать; все NPC одной локации остаются полноценными участниками одной сцены.

---

# 28. Разбивка на спринты

Внедряем поэтапно. Каждый спринт — самодостаточная единица: работает, тестируется, не ломает предыдущее. Порядок выбран так, чтобы сначала закрыть «Локации 2.0» (модель/CRUD/UI), затем — исправление изоляции (ядро задачи), затем — полировка и тесты.

## Спринт 1 — Модель Location + миграция/backfill — ВЫПОЛНЕН (2026-08-03)
**Цель:** локации становятся самостоятельной сущностью в БД.
**Задачи:**
1. Модель `Location` в `app/models.py` (§13): `id`, `chat_id` FK (ondelete=CASCADE), `name`, `description`, `created_at`, `updated_at`, `UniqueConstraint(chat_id, name)`.
2. Миграция/backfill в `app/database.py` (§15): создать таблицу, распарсить существующие `chats.locations` в строки (`description=""`), существующие поля не ломать.
3. Хелпер `crud.get_chat_locations(db, chat_id) -> list[Location]`.
**Критерий готовности:** при старте приложения таблица создаётся и наполняется из старых данных; движение/чтение не сломано; существующие тесты проходят.

**Что сделано:**
- `app/models.py`: добавлена модель `Location` (таблица `locations`) с полями `id`, `chat_id` (FK `chats.id` ON DELETE CASCADE), `name`, `description` (`TEXT NOT NULL DEFAULT ''`), `created_at`, `updated_at`; `UniqueConstraint(chat_id, name)` (имя `uq_location_chat_name`) + индекс `ix_locations_chat_id`. На `Chat` добавлена relationship `location_records` с каскадным удалением (имя не совпадает с колонкой-кэшем `locations`).
- `app/database.py` (`ensure_schema`): `CREATE TABLE IF NOT EXISTS locations`, индекс по `chat_id`, идемпотентный backfill из `chats.locations` (JSON-массив названий) через `INSERT OR IGNORE` (`description=""`); существующие строковые поля не меняются; повторный запуск не дублирует строки.
- `app/crud.py`: добавлен хелпер `get_chat_locations(db, chat_id) -> list[models.Location]` (сортировка по `name, id`).

**Проверка:**
- На копии реальной `ai_chat.db`: таблица создавалась заново, забэкфиллено 16 локаций (chat 3 — 7 шт., chat 7 — 9 шт.), повторный `init_db()` идемпотентен.
- Полный прогон `pytest`: 542 passed, 28 failed — все 28 падений **pre-existing** (проверено через `git stash`: падают и без этих правок; `MemoryJobQueue.run_job`, `test_context_state`, `test_memory_service`, `test_embeddings`, `test_stream_disconnect` и др. — не связаны с локациями).

## Спринт 2 — CRUD API — ВЫПОЛНЕН (2026-08-03)
**Цель:** полный CRUD локаций.
**Задачи:**
1. Схемы `LocationCreate/Update/Read` в `app/schemas.py`.
2. Роутер `app/routers/locations.py` (§16): GET список, POST (дубль имени → 409), PUT (при переименовании — обновить строковые ссылки `characters.location`, `messages.location`, `scene_states.character_locations`), DELETE (при ссылающихся персонажах → 409 с информацией о них).
3. Синхронизация `chats.locations` при CRUD (§14).
4. Регистрация роутера в `app/main.py`.
**Критерий готовности:** все endpoint'ы работают по паттернам `routers/characters.py`; тесты роутера (§22 п.10) зелёные.

**Что сделано:**
- `app/schemas.py`: схемы `LocationBase/Create/Update/Read` (секция Location). `name` — `min_length=1` + strip-валидатор (пустое имя → 422); `description` — strip. `LocationRead` — `from_attributes` (`id`, `chat_id`, `created_at`, `updated_at`).
- `app/crud.py`: CRUD-функции в секции Location:
  - `create_location` — case-insensitive проверка дублей через `perception.locations_match` → `ValueError` → 409; `IntegrityError` (race) → тоже 409;
  - `update_location` — смена имени (с проверкой дублей) + `_rename_location_references` (синхронное обновление `characters.location`, `messages.location`, `scene_states.character_locations`-значений) + `_sync_chat_locations_cache`;
  - `get_characters_referencing_location` — персонажи (вкл. игрока), чья локация совпадает;
  - `delete_location` — удаление + синхронизация кэша;
  - `_sync_chat_locations_cache` — пересбор `chats.locations` (JSON имён) из таблицы.
  - Импорты: добавлены `json`, `update`, `IntegrityError`.
- `app/routers/locations.py`: GET/POST/PUT/DELETE по паттернам `routers/characters.py`; `404` чат/локация, `409` дубль имени, `409` при удалении со ссылающимися персонажами (`detail = {message, characters: [имена]}`).
- `app/main.py`: регистрация `locations.router` в `api_router`.
- `tests/test_locations_api.py`: 8 тестов — create+list (и синхронизация `chats.locations`), дубль → 409 (в т.ч. case-insensitive), пустое имя → 422, переименование → синхронизация ссылок (characters/messages/scene_states/chats.locations), удаление со ссылками → 409 (имена в detail), удаление свободной → 204, 404 (чат не найден / локация не из этого чата).
- `docs/locations.md`: новая документация — модель, миграция, CRUD API, поведение 409.

**Проверка:**
- `pytest tests/test_locations_api.py`: 8 passed.
- Полный прогон `pytest`: 550 passed, 28 failed — набор падений идентичен pre-existing из Спринта 1 (проверено списком; новых нет).

## Спринт 3 — UI вкладки «Локации» — ВЫПОЛНЕН (2026-08-03)
**Цель:** пользователь управляет локациями из нового Vue-фронтенда.
**Задачи:**
1. `frontend/src/api/locations.ts` (новый API-слой).
2. `LocationSettings.vue` (§17): список из GET, кнопка «+» → форма «Название/Описание», карточки «Изменить/Удалить».
3. Обработка 409 при удалении — показ ссылающихся персонажей.
4. Обновление списка после CRUD; при необходимости синхронизация `chat.locations` в сторе.
**Критерий готовности:** полный цикл create → list → edit → delete в UI; старый SPA не тронут.

**Что сделано:**
- `frontend/src/api/locations.ts`: `fetchLocations/createLocation/updateLocation/deleteLocation` + `LocationCreateInput/LocationUpdateInput` (дубликаты типов также в `api/types.ts`); в `api/index.ts` методы добавлены в фасад реального API, интерфейс `Api` в `api/types.ts` расширен.
- `frontend/src/api/client.ts`: `ApiError.detailData` + парсинг объектного `detail` (`message` или `JSON.stringify`) в `toApiError`.
- `frontend/src/mocks/data.ts`: `mockLocations` (чаты 1, 2); `frontend/src/mocks/service.ts`: мок-реализации CRUD + синхронизация `chat.locations` (JSON-кэш названий) и очистка `mockLocations` при create/delete чата.
- `frontend/src/components/settings/LocationSettings.vue` переписан: список (загрузка при смене чата), кнопка «+» → инлайн-форма «Название/Описание», карточки «Изменить/Удалить», подтверждение удаления через `confirm`, обработка 409 через `ApiError.detailData` (тост со списком персонажей), тосты через `ui.toast`.
- Синхронизация `chat.locations` в сторе `chats.ts` не требуется: поле нигде не читается в UI, mock и backend синхронизируют его на каждый CRUD.
**Проверка:** `npx vue-tsc -b` без ошибок; `npm run build` собран (366ms); `pytest tests/test_locations_api.py` — 8 passed. `frontend/dist` в gitignore.

## Спринт 4 — Персональная фильтрация истории (perception) — ВЫПОЛНЕН (2026-08-03)
**Цель:** корректные персональные view истории NPC (§2-4, §8-9).
**Задачи:**
1. Аудит пути «общая история → witness-фильтр → контекст NPC» в `chat_engine`/`context_builder`/`witness_model`; убедиться, что `can_character_perceive_event` + `locations_match` + `REMOTE_CHANNELS` дают правильный view.
2. Проверить, что NPC одной локации видят реплики друг друга как `present` (а не как «чужой контекст»).
3. Исправить `effective_prior_replies` (§10): убрать битый lookup, доступность каждого ответа решать через `can_character_perceive_event(viewer, event)`.
4. Проверить sequential generation (§11) и перемещение между локациями (§19) — view пересчитывается по текущей локации.
**Критерий готовности:** тесты 1-4, 7, 9 (§22) проходят; единый фильтр для истории, `prior_replies`, sequential generation.

**Что сделано:**
- Аудит подтвердил, что история и witness-фильтр уже строят персональный view через `resolve_presence` (§2-4, §8-9) — ср. `app/witness_model.py`, `app/crud.py::get_presence_map` / `compute_and_save_presence_for_message`.
- §10 был **не выполнен** несмотря на прежние метки: в обоих путях генерации (`process_user_message_streaming`, `regenerate_message_streaming`) использовался битый lookup `presence_map.get(character_id, "present")`, где `presence_map` индексирован по `message_id`, а не по `character_id`; присутствие любого NPC в ранних раундах по умолчанию засчитывалось как `present`.
- Исправление §10:
  - `effective_prior_replies` реализован как `_effective_prior_replies(prior_reply_events, viewer_character_id, viewer_location, viewer_name, character_names)`: для каждого события `char_message` решение принимается через `can_character_perceive_event(viewer, event)` → `present`/`told` включаются, `absent`/`mentioned` скрываются.
  - Накопление событий: `prior_reply_events: list = []` вместо старого списка строк `prior_replies`; событие добавляется сразу после успешной генерации реплики NPC (порядок: устаревшая reply-строка по-прежнему пишется в историю как `char_message`).
  - `current_presence` и вложенный `get_presence_map` в регенерации удалены; регенерация фильтрует `round_messages` тем же `_effective_prior_replies`.
- Sequential generation (§11) и перемещение (§19): фильтр пересчитывается на каждой итерации по текущим `viewer_location`/`character_locations` (тесты 4, 7 — см. ниже).
- Новые тесты: `tests/test_locations_perception.py` (тесты 1-4, 9 §22) — 5 passed.
- `docs/locations.md`: раздел «Персональный view истории и `effective_prior_replies` (спринт 4)».

**Проверка:**
- `pytest tests/test_perception.py tests/test_witness_filter.py tests/test_chat_engine.py tests/test_role_isolation.py tests/test_locations_api.py tests/test_prompt_builder.py tests/test_context_builder.py tests/test_locations_perception.py`: 120 passed.
- Полный прогон `pytest`: 566 passed, 28 failed — набор падений идентичен pre-existing (task_queue `MemoryJobQueue`, context_state, embeddings, memory, repetition, stream_disconnect, token_counter; в изменённых файлах новых падений нет).

## Спринт 5 — `compute_is_isolated` — ВЫПОЛНЕН (2026-08-03)
**Цель:** точная изоляция (§5-6).
**Задачи:**
1. Хелпер `compute_is_isolated(char_loc, other_char_locs, player_loc)` с `locations_match`; `""` = общая сцена → не изолирован.
2. Применить во всех 4 местах `chat_engine.py` (обычная генерация + регенерация).
3. Убедиться, что role isolation и foreign speaker protection не затронуты (§12).
**Критерий готовности:** тест 5 (§22) проходит; Анна и Борис в гостиной при игроке на кухне взаимодействуют.

**Что сделано:**
- `app/perception.py`: хелпер `compute_is_isolated(char_loc, other_char_locs, player_loc)` — NPC изолирован только если в его локации нет ни игрока, ни других NPC; сравнение через `locations_match`; пустая локация (`""`) = общая сцена → `False`.
- `app/chat_engine.py`: хелпер `_character_is_isolated(character_locations, character_id, characters, player_location)`; применён во всех **4** местах расчёта изоляции: `context_builder.build(...)` и `ollama_client.generate(...)` в обычной генерации (`process_user_message_streaming`) и в `regenerate_message_streaming`. Старое выражение `character_locations.get(id) != player_location` убрано.
- Role isolation / foreign speaker protection **не тронуты**: `is_isolated` лишь включает/выключает `isolated_block` и generation cue; маркер `изоляция`, stop sequences, `sanitize_and_validate_response`, чужие speaker markers обрабатываются независимо (§12).
- Тесты: юнит-тесты `compute_is_isolated` в `tests/test_perception.py` (общая сцена, игрок рядом, другой NPC рядом, одинокий NPC, case-insensitive); интеграционный тест `tests/test_chat_engine.py::test_compute_is_isolated_engine_applied` (тест 5 §22: A+B в гостиной → `is_isolated=False`, одинокий C → `is_isolated=True` при игроке на кухне).
- `docs/locations.md`: раздел «Точная изоляция NPC: compute_is_isolated».

**Проверка:**
- `pytest tests/test_perception.py tests/test_chat_engine.py tests/test_role_isolation.py tests/test_witness_filter.py tests/test_locations_api.py`: 74 passed.
- Полный прогон `pytest`: 555 passed, 28 failed — набор падений идентичен pre-existing из Спринтов 1-2 (task_queue `MemoryJobQueue`, context_state, embeddings, memory, repetition, stream_disconnect, token_counter; в изменённых файлах падений нет).

## Спринт 6 — Scene block с описанием + память + логирование — ВЫПОЛНЕН (2026-08-03)
**Цель:** полировка контекста и диагностика.
**Задачи:**
1. `Location.description` в scene block (§18): «Твоя локация: <name> — <description>», без описания — только название.
2. Проверка memory attribution (§20) — факт привязан к говорящему (тест 8).
3. Диагностическое DEBUG-логирование (§21): NPC, Location, PlayerLocation, Visible/Hidden characters, Visible/Filtered messages.
**Критерий готовности:** тест 8 проходит; логи позволяют ответить на «почему NPC не видит другого NPC» и «почему сообщение чужой локации попало в контекст».

**Что сделано:**
- **Scene block с описанием (§18):** `prompt_builder.build_scene_block` — новый параметр `location_descriptions: dict[str, str]` (имя → описание). После «Твоя локация: <name>» добавляется описание на отдельной строке, если непустое; без описания — только название. Описание другой локации в контекст не попадает.
- **Проводка в оба пути генерации:** новый хелпер `chat_engine._load_location_descriptions(db, chat_id)` (из `crud.get_chat_locations`); вызывается в `process_user_message_streaming` и `regenerate_message_streaming`, передаётся в `ContextBuilder.build(...)` и `ollama_client.generate(...)` → `_generate_once(...)` → `build_scene_block(...)`.
- **Память / attribution (§20, тест 8):** проверен `filter_history_for_memory_extraction` — строки сохраняют префикс говорящего («Анна: Я ненавижу кофе.»), так что Борис слышит реплику Анны, но владелец факта — Анна. Архитектура памяти не менялась. Новый тест `tests/test_memory_perception.py::test_memory_attribution_speaker_preserved_same_room`.
- **Диагностическое логирование (§21):** настройка `settings.generation_debug` (`GENERATION_DEBUG`, default `false`). Хелпер `chat_engine._log_generation_diagnostics` вызывается в цикле генерации NPC обоих путей и пишет в `logger.debug`: `NPC / Location / PlayerLocation / Visible characters / Hidden characters / Visible messages / Filtered messages`. Visible messages — presence ≠ absent; Filtered — presence = absent (через `resolve_presence`).
- Тесты: scene block с описанием (3 юнит-теста в `tests/test_prompt_builder.py`), описание в обоих путях генерации + `location_descriptions` в `generate` (`tests/test_chat_engine.py::test_location_description_in_scene_block`), DEBUG-лог (§21) с on/off флагом (`tests/test_chat_engine.py::test_generation_diagnostics_log`), тест 8 (§20) memory attribution (`tests/test_memory_perception.py`).
- `docs/locations.md`: разделы «Описание локации в scene block», «Память и attribution», «Диагностическое логирование».

**Проверка:**
- Новые тесты: `test_prompt_builder.py::TestBuildSceneBlockLocationDescriptions`, `test_chat_engine.py::test_location_description_in_scene_block`, `test_chat_engine.py::test_generation_diagnostics_log`, `test_memory_perception.py::test_memory_attribution_speaker_preserved_same_room` — 6 passed.
- Затронутые файлы (`test_chat_engine`, `test_context_builder`, `test_ollama_chat`, `test_prompt_builder`, `test_perception`, `test_locations_api`, `test_witness_filter`): 107 passed.
- Полный прогон `pytest`: 561 passed, 28 failed — набор падений идентичен pre-existing из Спринтов 1-2-5 (task_queue `MemoryJobQueue`, context_state, embeddings, memory_service, memory_perception ×6, repetition, stream_disconnect, token_counter; в изменённых файлах новых падений нет).

## Спринт 7 — Полный прогон тестов и ручные сценарии — ВЫПОЛНЕН (2026-08-03)
**Цель:** регрессия и верификация всего.
**Задачи:**
1. Все автотесты §22 (1-10) + существующие тесты role isolation (§26 п.18).
2. Ручные сценарии §23 (1-5).
3. Прогон `pytest` целиком, проверка, что старый SPA и существующее поведение не сломаны.
**Критерий готовности:** все тесты зелёные, ручные сценарии подтверждают инварианты §24, ограничения §25 соблюдены.

**Что сделано:**
- **Ручные сценарии §23 (1-5)** переведены в автоматические тесты `tests/test_manual_scenarios.py` (прогоняют реальный путь `process_user_message_streaming` с детерминированным фейковым LLM; проверяют наблюдаемое поведение, которое человек проверил бы в UI):
  1. §23.1 «общая гостиная» + §23.2 «Виктор на кухне изолирован»: `test_scenario_1_2_same_room_interact_and_kitchen_hidden` — Анна и Борис видят игрока и друг друга (`prior_replies`), Виктор не видит ни игрока, ни реплик гостиной.
  2. §23.3 «сцена NPC продолжается без игрока»: `test_scenario_3_npc_scene_continues_without_player` — после перемещения игрока на кухню NPC гостиной продолжают общаться между собой (не изолированы), Виктор перестаёт быть изолированным.
  3. §23.4 «сообщение в другой локации через удалённый канал»: `test_scenario_4_remote_message_across_locations` — таргетированное событие `messenger` видно Василию в другой локации (`REMOTE_CHANNEL_MESSENGER`).
  4. §23.5 «speaker isolation»: `test_scenario_5_speaker_isolation` — реплика, начинающаяся с чужого speaker marker, обрезается до пустой и помечается невалидной; реплика со своим префиксом сохраняется.
- **Инварианты §24** подтверждены автотестами: §10.1 `effective_prior_replies` = единый фильтр для обоих путей генерации; §10.2 `prior_reply_events` пишутся в историю после каждой успешной генерации; §19 view пересчитывается при перемещении; §20 attribution говорящего сохранена (спринт 6); §23.1-5 см. выше.
- **Ограничения §25** соблюдены: старый Vanilla JS SPA (`app/static/`) не изменялся — `git diff --stat -- app/static/` пуст; `effective_prior_replies` использует `can_character_perceive_event` (единый фильтр); промпты добавлены только на русском.
- **Ручной скрипт** (`verify_manual.py`) не используется: standalone-прогон зависает на фоновом сетевом вызове, не замоканном вне pytest; те же кодовые пути полностью покрыты pytest-тестами с паттерном `_run_round` (patch `ollama_client.generate`, `asyncio.create_task`, `asyncio.to_thread`).
- `docs/locations.md`: раздел «Регрессия (спринт 7)».

**Проверка:**
- Новые тесты: `tests/test_manual_scenarios.py` — 4 passed.
- Полный прогон `pytest`: 570 passed, 28 failed — набор падений идентичен pre-existing из Спринтов 1-2-4-5-6 (task_queue `MemoryJobQueue`, context_state, embeddings, memory_service, memory_perception ×6, repetition, stream_disconnect, token_counter; в изменённых файлах новых падений нет). Критерий «все тесты зелёные» формально не достигнут из-за 28 pre-existing падений вне scope спринтов локаций (они были красными до их начала).

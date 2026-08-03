# Isolation FIS — ослабление изоляции ролевого движка: восприятие, движение, стимулы

> **Статус:** План (не реализован). Составлен на основе ревизии кода 2026-08-03.
> **Дата:** 2026-08-03
> **Цель:** исправить чрезмерную изоляцию персонажей. Персонаж должен быть ограничен только тем, **что он может знать и какие действия может авторизованно описывать**, но не искусственно ограничен в собственном поведении (движение, обращение, инициатива, уход из локации).
> **Главный принцип:** разделение МИР → ВОСПРИЯТИЕ → РЕШЕНИЕ ПЕРСОНАЖА → ДЕЙСТВИЕ → ИЗМЕНЕНИЕ МИРА. Восприятие ≠ обязательная реакция.
> **Инвариант:** авторство. Персонаж управляет только собой; запрет на управление чужим персонажем НЕ является запретом на взаимодействие с ним.
> **Ограничение:** не ломаем модель сообщений, память, отношения, локации, API и обратную совместимость данных. Существующую защиту авторства (stop sequences, sanitize, hard/soft violations) сохраняем.

---

## 0. Текущее состояние (результат ревизии)

### 0.1 Карта проблемных мест

| Файл | Строки | Проблема по ТЗ |
|---|---|---|
| `app/role_isolation.py` | `build_isolated_generation_cue` (110-122) | Жёсткий поведенческий запрет: «НЕ покидай свою локацию, НЕ иди к игроку, НЕ обращайся к нему, не двигайся в локацию других» (§5). |
| `app/role_isolation.py` | `build_generation_cue` (83-92), `build_generation_cue_for_chat` (95-107) | Жёсткий объём: «минимум 3-5 абзацев, ~150-250 слов», «Не отвечай коротко — это не смс» (§15). |
| `app/role_isolation.py` | `build_role_isolation_block` (28-66) | Работает только на авторство (хорошо), но без явного раздела «ВЗАИМОДЕЙСТВИЕ» из ТЗ §4 — персонажу не разрешено явно двигаться/обращаться/идти к другому. |
| `app/prompt_builder.py` | `build_isolated_block` (558-560) → шаблон `isolated` | Шаблон: «НЕ обращайся к игроку» — поведенческий запрет (§5). |
| `app/prompts/ru.json` | `rules` п.5, `reinforcement`, `negative` п.9, `generation_cue.chat`, `isolated` | Правило «Если ты в другой локации, ты НЕ видишь и НЕ слышишь…» противоречит AUDIBLE/соседству; жёсткий объём 150–250 слов; запрет обращения к игроку. |
| `app/perception.py` | `can_character_perceive_event` (137-205), `_name_mentioned` (208-214) | Нет уровней VISIBLE/AUDIBLE/MENTIONED/ABSENT. Соседние локации → всегда `absent` (кроме имени в тексте → `mentioned`). Простое упоминание имени делает `mentioned` (против ТЗ §6). |
| `app/perception.py` | — | Нет `are_locations_adjacent()`. |
| `app/witness_model.py` | `Presence` (19), `format_line_for_presence` (140-172) | Нет присутствия `audible`; нет рендера «слышимого» без утечки визуальных деталей. |
| `app/chat_engine.py` | `_detect_movement_in_text` (314-386), блок scene extraction (867-958) | Движение детектируется LLM-экстракцией сцены **в конце раунда, после генерации всех NPC** (§12 нарушен). Fallback в `_detect_movement_in_text` (382-384) может срабатывать на «глагол движения + название» без подтверждения совершённости (§9). |
| `app/chat_engine.py` | `process_user_message_streaming` (647-852), `regenerate_message_streaming` (~2050-2243) | `is_isolated` используется только для выбора cue/блока — после правки блоков поведенческого запрета больше нет. Обновление локации из текста не происходит до генерации следующего NPC. |
| `app/ollama_client.py` | 906, 911-916, 940-943 | Выбор `build_isolated_generation_cue` при `is_isolated` → убрать. |
| `app/context_builder.py` | 513-536 | `if is_isolated: build_isolated_block()` + выбор cue → перевести на единый cue, изолированный блок сделать информационным. |

### 0.2 Что уже работает и НЕ трогаем
- `role_isolation`: `build_stop_sequences`, `sanitize_character_response`, `find_foreign_speaker_marker`, `is_response_valid`, `contains_perspective_violation` (hard/soft), `sanitize_and_validate_response` — защита авторства остаётся как есть.
- Witness-фильтрация по presence (`witness_model`), `perception.can_character_perceive_event`, `REMOTE_CHANNELS`, `locations_match` (casefold), `compute_is_isolated` (§6 в locations2.md).
- Таблица `locations`, CRUD локаций, кэш `chats.locations`, бэктфилл в `ensure_schema`.
- Механизм миграций `ensure_schema` (`ALTER TABLE ... ADD COLUMN` с проверкой инспектором) — используем для новых колонок.
- Модель сообщений/памяти/отношений, API, stop sequences — без изменения.

### 0.3 Что важно для тестов
- `tests/golden/snapshots_iso.json` и `snapshots.json` содержат точные снимки `build_generation_cue*`, `build_role_isolation_block`, правил. Любая правка этих функций/шаблонов требует **обновления снапшотов**.
- `tests/test_role_isolation.py`, `tests/test_perception.py`, `tests/test_locations_perception.py`, `tests/golden/test_role_isolation_golden.py` — придётся точечно обновить (проверки «изолированный не должен двигаться» отсутствуют, но текст cue завязан на снапшоты).
- Набор запускается `pytest` из корня `ai-roleplay-chat` (см. `pytest.ini`).

---

## 1. Архитектурные решения

### 1.1 Уровни восприятия
Вводим уровень как отдельное понятие, затем отображаем в присутствие (presence), которое уже персистится в `message_presence`.

```
PerceptionLevel = Literal["visible", "audible", "mentioned", "absent"]
Presence       = Literal["present", "mentioned", "audible", "absent", "told"]   # + "audible"
```

| Уровень | Presence | Что получает персонаж |
|---|---|---|
| VISIBLE | `present` | Полное событие |
| AUDIBLE | `audible` (новое) | Только слышимое (без визуальных деталей и мыслей автора) |
| MENTIONED | `mentioned` | Непосредственное обращение (по стимулу `address`/`call`) |
| ABSENT | `absent` | Ничего |

Правила MENTIONED (ТЗ §6): простое упоминание имени в повествовании («Вчера Антон ходил в магазин») → НЕ `mentioned`. Только стимул `address`/`call`, нацеленный на персонажа → `mentioned`, при условии физической достижимости (та же локация, соседняя локация, или удалённый канал с таргетингом).

`told` остаётся для пересказа (не входит в ТЗ, не трогаем).

### 1.2 Соседство локаций — `are_locations_adjacent`
- Не использовать `startswith("Квартира Ольги")` как основной механизм.
- Вводим новую функцию `are_locations_adjacent(a, b, adjacency_index)`.
- `adjacency_index: dict[str, set[str]]` (нормализованные имена → множество соседей), строится один раз на чат из новой опциональной колонки `locations.adjacent_to` (JSON-массив имён).
- На первом этапе соседство — это явные связи, задаваемые в CRUD локаций. Если связи не заданы → локации не соседние (консервативно, без регрессий для существующих чатов).
- Опциональный эвристический fallback (общий топоним-префикс, например «Квартира Ольги» / «Квартира Бориса» → соседние) включается отдельным флагом `adjacency_fallback_enabled` (по умолчанию `False`), чтобы заменить его позже полноценной связью.

### 1.3 Стимулы
- Новый модуль `app/stimuli.py`:
  ```python
  @dataclass
  class Stimulus:
      type: str            # knock | call | shout | address | loud_sound
      target_character: str | None
      audibility: str      # high | medium | low

  def extract_stimuli(text: str, character_names: list[str], viewer_name: str | None = None) -> list[Stimulus]
  def has_stimulus(stimuli: list[Stimulus], type_: str) -> bool
  def build_audible_line(event) -> str          # рендер AUDIBLE без утечки визуала
  ```
- Стимулы — **метаданные исходного события**, не отдельное сообщение БД. Храним в новой колонке `messages.stimuli` (JSON, default `"[]"`).
- Эвристики (regex) изолированы внутри `extract_stimuli` — позже заменяемы на LLM-извлечение без изменения остального кода.
  - knock: `стуч|стук|постучал|барабан` + дверь/окно/соседний;
  - call: `зову|зовёт|позвал|оклика` ;
  - shout: `крич|орать|воп|заорал` ;
  - loud_sound: `грохот|шум|громк|звон|треск|хлоп|дверь захлопнулась` ;
  - address: звательная конструкция «Имя, ...» или «Имя!»/«Имя?» в начале реплики; разрешение target по `character_names` (включая уменьшительные формы, если есть в БД; на первом этапе — точное совпадение имени и имени в именительном падеже).
- `audibility`: `high` для shout/knock/call/loud_sound, `medium` для address, `low` для тихого («шепчу»).

### 1.4 Влияние стимулов на perception
- В `get_perception_level`: событие содержит `address`/`call`-стимул на зрителя → уровень `mentioned` (если достижимо: та же локация, соседняя, или `REMOTE_CHANNEL` с таргетингом).
- Событие с `knock`/`shout`/`loud_sound`/`call` из соседней локации → `audible`.
- Событие из той же локации → `visible` (вне зависимости от стимулов).
- Далёкая несвязанная локация → `absent` даже при обращении по имени (ТЗ §14: «при условии что обращение физически достижимо»).

### 1.5 Движение (переписывание)
Новая функция в новом модуле `app/movement.py`:
```python
def detect_character_movement(
    text: str,
    character_name: str,
    known_locations: list[str],        # из locations + текущие локации персонажей
    character_locations: dict[int, str],  # id -> loc (для «подошёл к Ольге»)
    character_names: dict[int, str],
) -> str | None
```
Возвращает **название новой локации** (совершившийся переход) или `None`.

Правила (§9-§11):
- Только совершившийся переход: глаголы прибытия (`вошёл`, `вошла`, `зашла`, `зашли`, `вхожу в`, `пришёл в`, `направился в/на`, `пошёл в/на`, `вышел в` и т.п.) + **явное название локации из known_locations**.
- Исключаем намерение/будущее/отрицание/воспоминание: `хочу пойти`, `собираюсь`, `мог(ла) бы`, `пойду` (план), `не пошёл`, `не пойду`, `вспоминаю, как ходил`, `вчера ходила`, `пошёл бы`.
- Матчинг названий локаций через существующий `_loc_keys`-подход (склонения), но только для разрешения **явного** названия; локация должна быть в `known_locations`.
- «Вышел из комнаты» без явной целевой локации → `None` (не додумываем «Коридор»).
- «Подошёл к Ольге» / «пошёл к Ольге» без признака прибытия → `None`. «Зашла к Ольге» (прибытие) → локация Ольги, если она известна и подтверждается.
- Пустая локация `""` = общая сцена — переход не триггерится.

### 1.6 Тайминг обновления мира (§12)
Порядок внутри цикла генерации NPC (`process_user_message_streaming`), сразу после получения `response_text` и **до** генерации следующего NPC:

```
текст персонажа (response_text)
   ↓
detect_character_movement(...)
   ↓
определение новой локации
   ↓
валидация (локация в known_locations)
   ↓
crud.update_character_location / batch + character_locations[cid] = new
   ↓
сообщение персонажа сохраняется с location = новое значение
   ↓
присутствие сообщения считается с учётом новой локации
```

- Системное сообщение «*Имя переместился в X*» (visibility=global) создаём так же, как сейчас в блоке scene extraction, чтобы перемещение было видно в истории.
- Блок LLM-экстракции сцены (конец раунда) остаётся как вторичная сверка для локаций, **не** меняет уже подтверждённое детерминированное перемещение (детерминированная правка имеет приоритет; при расхождении — не перезаписывать локацию, уже обновлённую детектором в этом раунде).
- То же применить в `regenerate_message_streaming` для нового текста ответа.

### 1.7 `is_isolated`
- `compute_is_isolated` (наличие кого-либо рядом) сохраняется как вычисление уровня информации.
- `is_isolated` НЕ означает запрета двигаться/говорить/взаимодействовать. Он влияет только на текст блока `isolated` (информационный: «рядом никого; ты можешь слышать звуки из соседних локаций») и больше не выбирает отдельный generation cue.
- `build_isolated_generation_cue` **удаляется** полностью; все вызовы переводятся на единый cue.

### 1.8 Generation cue (§15, §16)
Единый cue (chat и legacy) без жёсткого объёма:

```
Если произошло значимое событие или персонажу обратились:
    сначала отреагируй на это.
Если ситуация не требует длинного ответа:
    ответ может быть коротким.
Если значимого события нет:
    персонаж может самостоятельно развивать свою сцену.
Не растягивай ответ искусственно ради объёма.
```

Приоритет контекста: текущий стимул → реакция персонажа → при необходимости развитие собственной сцены. Не жертвовать реакцией на актуальное событие ради описательного текста.

### 1.9 Изоляция (авторство) — блок по ТЗ §4
`build_role_isolation_block` переписывается концептуально по образцу ТЗ: «Ты управляешь ТОЛЬКО своим персонажем» + раздел АВТОРСТВО (запреты) + раздел ВЗАИМОДЕЙСТВИЕ (свобода: реагировать, обращаться, слышать, начинать разговор, двигаться, покидать локацию, входить, следовать). Параметр `strict` сохраняется (ретрай-предупреждение).

---

## 2. Изменения по файлам

### 2.1 `app/perception.py`
- Добавить `PerceptionLevel` и `Audibility`.
- Расширить `Presence` в witness_model (см. 2.3).
- `are_locations_adjacent(a, b, adjacency_index: Mapping[str, set[str]] | None = None) -> bool` + `build_adjacency_index(locations: list[Any]) -> dict[str, set[str]]` (из `adjacent_to`).
- `get_perception_level(*, viewer_location, event_location, event_text, viewer_name, adjacency_index=None, stimuli=None, targets=None, channel=None, visibility=...) -> tuple[PerceptionLevel, str]` — отдельная функция, не смешивает причины.
- `can_character_perceive_event` переписать на использование `get_perception_level` (сохранив ветки OWN_MESSAGE, GLOBAL, PUBLIC, private/targeted, REMOTE_CHANNELS), возвращая `(presence, reason)`.
- Убрать правило «упоминание имени → mentioned» из LOCAL-ветки; заменить на стимулы (`address`/`call`). `_name_mentioned` оставить только для REMOTE-ветки без таргета (или заменить на стимулы — решить на этапе Спринт 3).
- `event_from_message` дополнить полем `stimuli` (parse JSON из `_get_attr(message, "stimuli")`).
- Сохранить `compute_is_isolated` без изменений.

### 2.2 `app/stimuli.py` (новый)
- `Stimulus` dataclass, `extract_stimuli`, `has_stimulus`, `build_audible_line`, `serialize_stimuli` / `parse_stimuli` (JSON).
- `build_audible_line(event)`: по стимулам формирует строку вида:
  - knock → «Ты слышишь стук в дверь из соседней локации.»
  - shout → «Ты слышишь крик из соседней локации.»
  - call → «Из соседней локации доносится зов.»
  - loud_sound → «Ты слышишь громкий звук из соседней локации.»
  - address → «Из соседней локации доносится голос: «…»» (только цитата прямой речи, если она есть; иначе без содержимого).
  - Без стимулов → generic «Из соседней локации доносится звук.» — НИКОГДА не возвращает полный контент.

### 2.3 `app/witness_model.py`
- `Presence` → `Literal["present", "mentioned", "audible", "absent", "told"]`.
- `MEMORY_OBSERVABLE_PRESENCES` — оставить `{"present", "told"}` (audible/mentioned НЕ дают полной наблюдаемости для памяти/суммаризации — соответствует ТЗ §8).
- `format_line_for_presence`:
  - `audible` → `build_audible_line(message)` + шаблон `witness.audible` (новый ключ в ru.json).
  - `mentioned` → если стимул `address`/`call` на зрителя → «{Автор} обращается к тебе: «…»» (новая форма); иначе оставить `[Тебя упомянули: {snippet}]`.
  - `present`/`told`/`absent` — без изменений.
- `compute_mvp_presence` — через обновлённый `can_character_perceive_event` (получает `stimuli` из события).

### 2.4 `app/models.py`
- `Message`: добавить `stimuli: Mapped[str] = mapped_column(Text, default="[]", nullable=False)`.
- `Location`: добавить `adjacent_to: Mapped[str] = mapped_column(Text, default="[]", nullable=False)` (JSON-массив имён).

### 2.5 `app/schemas.py`
- `LocationBase`/`LocationUpdate`/`LocationRead`: поле `adjacent_to: list[str] = []` (serialize как JSON).
- `MessageCreate`/`MessageRead`: поле `stimuli: list[dict] | list[StimulusDict] = []` (serialize как JSON). `MessageCreate.orm_kwargs` — сериализация `stimuli`.
- `Stimulus` может быть Pydantic-моделью (schema) или dataclass + dict; для хранения использовать JSON-строку.

### 2.6 `app/database.py` (ensure_schema)
- Миграция: `ALTER TABLE messages ADD COLUMN stimuli TEXT NOT NULL DEFAULT '[]'` (проверка через инспектор).
- Миграция: `ALTER TABLE locations ADD COLUMN adjacent_to TEXT NOT NULL DEFAULT '[]'` (проверка через инспектор).
- Всё по существующему паттерну `if col not in columns: conn.execute(...)`.

### 2.7 `app/crud.py`
- `compute_and_save_presence_for_message` / `compute_and_save_presence_for_round`: присутствие уже хранится как строка — код не меняется, но возвращает теперь и `audible`.
- `get_chat_locations` уже есть. Добавить `get_adjacency_index(db, chat_id) -> dict[str, set[str]]` (читает `locations.adjacent_to`).
- `create_location`/`update_location` — поддержать `adjacent_to` (передача из схемы в ORM).
- `update_character_locations_batch` — уже есть; для детерминированного движения можно использовать его или точечный `update_character_location`.

### 2.8 `app/chat_engine.py`
- Заменить вызовы `build_isolated_generation_cue` (нет — они в ollama_client/context_builder).
- В `process_user_message_streaming` внутри цикла NPC, после `response_text`:
  1. `new_loc = detect_character_movement(response_text, current_character.name, known_locations, character_locations, character_names)`.
  2. Если `new_loc` и отличается → обновить БД (`update_character_locations_batch` или `update_character_location`), `character_locations[cid] = new_loc`, создать системное сообщение о перемещении (глобальное), обновить `round_messages`/`context_messages`.
  3. `char_location` для сообщения персонажа = актуальная (возможно новая) локация.
  4. `compute_and_save_presence_for_message` — с новой локацией.
- `_effective_prior_replies`: включать `audible`-реплики в виде слышимой строки (через `format_line_for_presence`), `mentioned` — в виде обращения, `absent` — исключать.
- Блок scene extraction (конец раунда): локации, уже подтверждённые детектором в этом раунде, не перезаписывать; использовать `detect_character_movement` как валидатор вместо `_detect_movement_in_text` (или удалить старую функцию и заменить).
- `regenerate_message_streaming` — та же обработка движения по новому тексту ответа.
- `_detect_movement_in_text` → заменить вызовы на `detect_character_movement`; функцию оставить только если где-то ещё используется как валидатор, иначе удалить.

### 2.9 `app/context_builder.py`
- Убрать импорт и использование `build_isolated_generation_cue`; единый cue (`build_generation_cue`/`build_generation_cue_for_chat`).
- `build_isolated_block()` — оставить вызов только для информационного блока (после правки шаблона).
- `_RETRIEVED_PRESENCES = frozenset({"present", "told"})` — оставить (audible/mentioned в BM25-ретриве не попадают, чтобы не утекать частичную инфу как полную).

### 2.10 `app/ollama_client.py`
- Убрать `build_isolated_generation_cue` из импортов и обоих выборов cue (911-916, 940-943). `isolated_block` остаётся как `build_isolated_block() if is_isolated else ""`.

### 2.11 `app/role_isolation.py`
- `build_role_isolation_block` — переписать по ТЗ §4 (авторство + свобода взаимодействия). `strict` сохранить.
- `build_isolated_generation_cue` — удалить.
- `build_generation_cue` / `build_generation_cue_for_chat` — переписать по §15/§16 (без «150-250 слов», без «Не отвечай коротко — это не смс»).

### 2.12 `app/prompts/ru.json`
- `rules` п.5: заменить на «Если ты в другой локации, ты не видишь, что там происходит. Ты можешь слышать только достаточно громкие звуки из соседних локаций.».
- `reinforcement`: убрать «минимум 3-5 абзацев, ~150-250 слов»; локационная часть — как правила п.5.
- `negative` п.9: заменить на §15 (короткий ответ допустим; не растягивай ради объёма; реагируй на актуальное событие).
- `generation_cue.chat`: переписать по §15/§16.
- `isolated`: переписать на информационный блок (рядом никого; можно слышать соседние локации; никаких запретов на движение/обращение).
- `witness.audible`: добавить шаблон по умолчанию (например «[Ты слышишь: {snippet}]», где snippet — аудио-строка от `build_audible_line`).
- `isolation.*` шаблоны — обновить под ТЗ §4.

### 2.13 `app/routers/locations.py`
- PUT/POST локаций уже прокидывают schema; добавить `adjacent_to` в валидацию/ответ (схема сама обработает).

---

## 3. Спринты

### Спринт 1 — Ослабление изоляции и generation cue (§4, §5, §15, §16)
- [x] `role_isolation.py`: переписать `build_role_isolation_block`; удалить `build_isolated_generation_cue`; переписать `build_generation_cue` и `build_generation_cue_for_chat`.
- [x] `prompt_builder.py`: `build_isolated_block` — сменить источник шаблона (содержимое в ru.json).
- [x] `ru.json`: `rules` п.5, `reinforcement`, `negative` п.9, `generation_cue.chat`, `isolated`, `isolation.*`.
- [x] `context_builder.py` + `ollama_client.py`: убрать `build_isolated_generation_cue`; единый cue.
- [x] Обновить golden-снапшоты (`snapshots_iso.json`, `snapshots.json`) и `tests/golden/test_role_isolation_golden.py` под новые тексты.
- [x] Тесты: 1-3, 21-24 из §18.
- [x] Прогон `pytest tests/test_role_isolation.py tests/golden/`.

### Спринт 2 — Уровни восприятия + соседство (§6, §7, §8)
- [ ] `perception.py`: `PerceptionLevel`, `get_perception_level`, `are_locations_adjacent`, `build_adjacency_index`, переработка `can_character_perceive_event`, `event_from_message` + stimuli.
- [ ] `models.py` + `schemas.py` + `database.ensure_schema`: `messages.stimuli`, `locations.adjacent_to`.
- [ ] `crud.py`: `get_adjacency_index`, поддержка `adjacent_to` в CRUD локаций.
- [ ] `witness_model.py`: `Presence += "audible"`, `format_line_for_presence` (audible/mentioned-address), `compute_mvp_presence`.
- [ ] `routers/locations.py`: `adjacent_to` в API.
- [ ] Тесты: 4-10 из §18.
- [ ] Прогон `pytest tests/test_perception.py tests/test_locations_perception.py tests/test_witness_filter.py`.

### Спринт 3 — Стимулы (§13, §14)
- [ ] `app/stimuli.py`: `Stimulus`, `extract_stimuli`, `has_stimulus`, `build_audible_line`, сериализация.
- [ ] `chat_engine.py`: `extract_stimuli` при создании сообщений (user + character), сохранение в `messages.stimuli`.
- [ ] `perception.py`: `get_perception_level` использует стимулы для MENTIONED; убрать «имя в тексте → mentioned» из LOCAL-ветки.
- [ ] Тесты: 17-20 из §18.
- [ ] Прогон `pytest tests/test_perception.py`.

### Спринт 4 — Движение и обновление мира (§9-§12)
- [ ] `app/movement.py`: `detect_character_movement`.
- [ ] `chat_engine.py`: вызов детектора в цикле NPC до генерации следующего; обновление БД + in-memory локаций; `message.location` и присутствие с новой локацией; системное сообщение о перемещении; сверка с scene extraction; `regenerate_message_streaming`.
- [ ] Убрать/заменить `_detect_movement_in_text`.
- [ ] Тесты: 11-16 из §18 (16 — интеграционный через `process_user_message_streaming`).
- [ ] Прогон `pytest tests/test_chat_engine.py tests/test_locations_perception.py`.

### Спринт 5 — Интеграция, обязательная проверка, отчёт (§19)
- [ ] Полный прогон `pytest`.
- [ ] Проверка всех вызовов `build_isolated_generation_cue` (не осталось).
- [ ] Проверка, что `is_isolated` больше нигде не запрещает движение/взаимодействие.
- [ ] Ручные сценарии (`tests/test_manual_scenarios.py`) + smoke по `main.py`.
- [ ] Заполнить итоговый отчёт (раздел 6).

---

## 4. Тесты по ТЗ §18

Где размещаем:

| # | Случай | Файл |
|---|---|---|
| 1 | Персонаж может уйти из своей локации (в cue/isolated-блоке нет запрета) | `tests/test_role_isolation.py` (новый класс) |
| 2 | Персонаж может обратиться к другому персонажу | `tests/test_role_isolation.py` |
| 3 | Персонаж не пишет действия другого (hard-violation сохраняется) | `tests/test_role_isolation.py` (уже есть, дополнить) |
| 4 | Та же локация → VISIBLE | `tests/test_perception.py` |
| 5 | Соседняя + стук → AUDIBLE | `tests/test_perception.py` (или `test_perception_levels.py`) |
| 6 | Соседняя + крик → AUDIBLE | там же |
| 7 | Обращение по имени → MENTIONED | там же |
| 8 | Простое упоминание имени → НЕ MENTIONED | там же |
| 9 | Далёкая несвязанная локация → ABSENT | там же |
| 10 | AUDIBLE не раскрывает визуальные детали | там же (`build_audible_line` / `format_line_for_presence`) |
| 11 | «Я вошёл в кухню» → location=кухня | `tests/test_movement_detection.py` (новый) |
| 12 | «Я вышел из комнаты» (нет цели) → без изменений | там же |
| 13 | «Я хочу пойти в кухню» → без изменений | там же |
| 14 | «Я не пошёл в кухню» → без изменений | там же |
| 15 | «Я вспоминаю, как ходил в магазин» → без изменений | там же |
| 16 | Перемещение обновляет БД до генерации следующего NPC | интеграционный в `tests/test_locations_perception.py` или `test_movement_detection.py` (через `process_user_message_streaming` + fake_generate) |
| 17 | «стучу в дверь» → stimulus knock | `tests/test_stimuli.py` (новый) |
| 18 | «Ольга, ты дома?» → stimulus address | там же |
| 19 | Стимул не создаёт отдельное сообщение в БД | там же (проверка количества `messages`) |
| 20 | Стимул доступен perception-системе | `tests/test_stimuli.py` + `tests/test_perception.py` |
| 21 | Ответ на обращение может быть коротким | `tests/test_role_isolation.py` (cue) |
| 22 | Нет обязательных 150-250 слов | там же (в тексте cue/negative нет «150-250»/«3-5 абзацев») |
| 23 | Персонаж может начать действие сам | там же (cue разрешает инициативу) |
| 24 | Персонаж может проигнорировать стимул | там же (в cue/isolated нет «обязан реагировать») |

Правила тестирования уровня восприятия:
- `get_perception_level`/`can_character_perceive_event` тестируем с явным `adjacency_index` (напр. `{"комната": {"коридор"}}`).
- Для теста 5/6 событие должно нести stimulus knock/shout (через `event_from_message` с полем `stimuli` или `Stimulus` объектом).
- Для теста 8 «Вчера Антон ходил в магазин» — стимул address отсутствует → уровень НЕ mentioned (absent для далёкой локации, или visible/absent в зависимости от локации; главное — не mentioned).

---

## 5. Чек-лист обязательной проверки (ТЗ §19)

- [x] 1. `grep build_isolated_generation_cue` → нет совпадений в `app/`.
- [x] 2. Проверено, что cue больше не создаёт поведенческий запрет (движение/обращение/локация).
- [x] 3. `grep is_isolated` → все места: `is_isolated` влияет только на информационный блок, не на запреты.
- [x] 4. Удалены ограничения «не покидай локацию», «не иди к игроку», «не обращайся», «играй здесь и сейчас».
- [ ] 5. События из соседних локаций не исчезают полностью (audible-строка попадает в контекст).
- [ ] 6. AUDIBLE не раскрывает визуальные детали/мысли (тест 10).
- [ ] 7. Движение действительно обновляет БД (`characters.location`).
- [ ] 8. Обновлённая локация используется следующим NPC в том же раунде (тест 16).
- [ ] 9. Стимулы не дублируют сообщения (тест 19).
- [ ] 10. Полный прогон `pytest` — зелёный.
- [x] Golden-снапшоты обновлены и проходят.

---

## 6. Итоговый отчёт (шаблон)

По завершении каждого спринта/задачи заполнить:

1. Какие файлы изменены.
2. Что изменено в каждом файле.
3. Какие тесты добавлены/изменены.
4. Результат существующих тестов.
5. Результат новых тестов.
6. Какие ограничения текущей реализации остались.
7. Какие изменения были сознательно НЕ внесены и почему.

---

## 7. Ограничения текущей реализации (ожидаемые)

- Соседство локаций на первом этапе — только явные связи (`locations.adjacent_to`); без связей — локации не соседние (консервативно).
- `extract_stimuli` — regex/эвристики; возможны ложные срабатывания (в т.ч. на уменьшительные имена). Изолированы в модуле для замены на LLM.
- AUDIBLE-рендер для событий без стимулов (легаси-сообщения) — generic-строка без содержимого (без утечки, но и без полезной информации).
- audиble/mentioned-события не попадают в BM25-ретрив старой истории (`_RETRIEVED_PRESENCES`) — частичная инфа не выдаётся как полная.
- Персонаж, изолированный в «далёкой» локации, всё ещё не видит чужих сцен — это корректно по восприятию, а не по поведению.
- Перемещение персонажа, написанное через LLM-экстракцию сцены без подтверждения детектором движения, может не примениться — по ТЗ это правильно (не додумываем локацию).

## 8. Что сознательно НЕ делаем

- Не вводим отдельную таблицу «событий»/«стимулов» как сущностей БД — стимулы остаются метаданными сообщения.
- Не добавляем колонку «иерархия локаций» (`parent_id`) в v1 — заменяемая абстракция `are_locations_adjacent` + `adjacent_to` покрывает требование без ломки модели.
- Не переписываем систему памяти/отношений/эпистемических масок.
- Не меняем stop sequences и sanitize-логику авторства (противоречия с новой логикой нет).
- Не делаем «обязательную реакцию на стимул» — персонаж волен игнорировать стимул (соответствует §2 и тесту 24).
- Не удаляем «не выдумывай действия других» — это защита авторства, остаётся.

---

## 9. Итоговый отчёт — Спринт 1 (заполнено 2026-08-03)

### 1. Какие файлы изменены
- `app/role_isolation.py`
- `app/prompt_builder.py`
- `app/context_builder.py`
- `app/ollama_client.py`
- `app/prompts/ru.json`
- `tests/golden/snapshots_iso.json`
- `tests/golden/snapshots.json`
- `tests/golden/test_constants.json`
- `tests/golden/test_role_isolation_golden.py`
- `tests/golden/test_prompt_builder_golden.py`
- `tests/test_role_isolation.py`
- `tests/test_prompt_builder.py`
- `tests/test_ollama_chat.py`

### 2. Что изменено в каждом файле
- `role_isolation.py`: переписан `build_role_isolation_block` по ТЗ §4 (АВТОРСТВО + ВЗАИМОДЕЙСТВИЕ со свободой движения/обращения); удалён `build_isolated_generation_cue`; переписаны `build_generation_cue` и `build_generation_cue_for_chat` по §15-§16 (реакция → развитие сцены, короткий ответ допустим, без жёсткого объёма 150-250 слов).
- `prompt_builder.py`: `build_isolated_block` теперь читает шаблон `isolated` из ru.json и носит информационный характер (только восприятие, без запретов).
- `context_builder.py`: убран импорт и выбор `build_isolated_generation_cue`; единый cue (`build_generation_cue`/`build_generation_cue_for_chat`).
- `ollama_client.py`: убран импорт и оба тернарника с `build_isolated_generation_cue`; единый cue; `isolated_block` остаётся информационным.
- `ru.json`: `rules` п.5 (слышно громкие звуки из соседних), `negative` п.9 (реакция-в-приоритете, короткий ответ допустим), `reinforcement` (без жёсткого объёма), `generation_cue.chat` (по §15-§16), `isolation.*` (АВТОРСТВО/ВЗАИМОДЕЙСТВИЕ), `isolated` (информационный, без запретов).
- Golden-снапшоты и тестовые константы перегенерированы под новые тексты.
- Unit-тесты обновлены под новую формулировку блока и cue.

### 3. Какие тесты добавлены/изменены
- Добавлен класс `TestIsolationBehaviorFreedom` в `tests/test_role_isolation.py` — тесты §18: 1 (можно уйти из локации), 2 (можно обратиться), 3 (за других писать нельзя — hard-violation), 21 (короткий ответ допустим), 22 (нет обязательных 150-250 слов / 3-5 абзацев), 23 (самостоятельная инициатива), 24 (можно игнорировать стимул).
- Обновлены golden-тесты: `test_isolation_block_different_names`, `test_system_prompt_section_order`, `test_reinforcement_block_is_short`.
- Перегенерированы снапшоты `snapshots_iso.json`, `snapshots.json`, `test_constants.json`.

### 4. Результат существующих тестов
`pytest tests/test_role_isolation.py tests/test_prompt_builder.py tests/test_context_builder.py tests/test_ollama_chat.py tests/golden/` → **155 passed**.

### 5. Результат новых тестов
`TestIsolationBehaviorFreedom` — 7 passed.

### 6. Ограничения текущей реализации
- Полный прогон `pytest` не полностью зелёный: 29 падений предсуществующие и НЕ связаны с этим спринтом (API-дрейф `MemoryJobQueue.run_job`, `SessionLocal` в `chat_engine`, `ContextState`/token-counter/embeddings-логика). Их устранение — отдельная задача вне Спринта 1.
- `isolation.header`/`allowed`/`forbidden` в ru.json теперь не используются напрямую `build_role_isolation_block` (текст зашит в функции по ТЗ §4); шаблоны обновлены для консистентности, но остаются запасными.

### 7. Что сознательно НЕ внесено
- Не менялись `stop sequences` и sanitize-логика авторства.
- Не вводилась «обязательная реакция на стимул».
- Запрет «не выдумывай действия других» сохранён как защита авторства.
- Спринты 2-5 (восприятие, стимулы, движение) не начинали — по плану.

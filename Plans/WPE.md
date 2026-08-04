# World & Perception Engine 3.0 — план внедрения (v3)

> Дата: 2026-08-03 · Статус: **Фаза 8 реализована (08-04)**.
> Статус фазы 8 (Уборка и документация): аудит legacy-полей (§6 v2) закрыт —
> `Message.visibility` и `character.location`-строка только read-only
> legacy-bridge (все write-path `update_character_location`/
> `update_character_locations_batch` пишут также `location_id`); deprecated
> text-only путь генерации удалён — при недоступных tools/format генерация
> падает с ошибкой (RuntimeError, И14), regex-детекторы
> (`detect_character_movement`, `_detect_communication_channel`) помечены и
> работают только как legacy-safety-net (не источник истины); обновлены
> `docs/architecture.md`, `docs/configuration.md`, `README.md`; финальный прогон
> golden-набора §11: **771 passed, 28 pre-existing падений (набор идентичен
> фазе 7)** — барьер пройден.
> Статус фазы 7 (Event Bus / Interrupts, Ул.5, §7, И17): цикл раунда вынесен
> в `app/round_engine.py` (единственная оркестрирующая функция `run_round`,
> правило §9); очередь приоритетов `EventBus` — разбуженные NPC впереди
> плановых, внутри приоритета FIFO, плановый порядок — исходный `order_index`;
> буждение по адресации: игрок→NPC из `target_character_ids` user-сообщения
> (первый ход), NPC→NPC из `target_character_ids` реплики (`addressed=true`,
> И7); один ответ на NPC за раунд, повторные буждения и буждения уже
> ответивших игнорируются (И17). `chat_engine.process_user_message_streaming`
> делегирует цикл в `run_round`/`run_round_fixed`; флаг
> `WORLD_ENGINE_EVENT_BUS_ENABLED` (default False) — откат: исходный
> фиксированный порядок без изменения поведения. Golden #21 (звонок будит NPC
> вне очереди), #2→Ул.5 (игрок→NPC отвечает первым), «один ответ за раунд»,
> «нет зацикливания» покрыты `tests/test_world_engine_phase7.py` (13 тестов).
> Статус фазы 6 (Threads/мессенджер + двухканальное частичное восприятие):
> `Thread` + `ThreadParticipantState` пишутся в проде — `create_message`
> по удалённому каналу (magic/phone/radio/messenger) создаёт/обновляет тред и
> участников, адресату проставляется `remote_status=delivered` независимо от
> локации (Golden #6); групповой тред — участники автор+адресаты, доставка
> только адресатам (Golden #15); `send_message` из `apply_character_actions`
> создаёт тред (И14); `perceive()` отдаёт full/full+delivered адресату
> удалённого канала из `world_state.thread_deliveries`. Частичное восприятие:
> ребро `visual_permeability`/`audio_permeability` + громкость (muffled→full от
> loud_sound), невидимость (`invisible`-стимул → visual=none/audio=full при
> `WORLD_ENGINE_PARTIAL_PERCEPTION_ENABLED`, Golden #17/#18/#19); voice
> familiarity — детерминированная атрибуция из `CharacterRelationship`
> (знакомый голос → «голос <имя>», незнакомый → «чей-то голос», muffled →
> атрибуция запрещена); Renderer `ContextBuilder` использует
> `render_perception_line` при включённых обоих флагах (И11, без утечки
> семантики). Флаги раздельные (`WORLD_ENGINE_THREADS_ENABLED`,
> `WORLD_ENGINE_PARTIAL_PERCEPTION_ENABLED`); откат — partial → бинарный
> full/none по каналам, треды выключаются отдельно. Golden #6/#15/#17/#18/#19
> + voice familiarity покрыты `tests/test_world_engine_phase6.py` (14 тестов).
> Статус фазы 5 (Action Resolution): протокол §5 исполнен целиком —
> действия из native tools (`turn.actions`) применяются атомарно
> (`crud.apply_character_actions`): `move_to` обновляет `location`+`location_id`
> и создаёт immutable `WorldEvent(move)` одной транзакцией, `send_message`
> валидирует адресатов и создаёт `WorldEvent(speech)`; Consistency Validator
> (`app/action_resolution.classify_consistency`) — три класса: `consistent` /
> `minor_ambiguity` (молчаливое действие → System Narrator **без ретрая**, И16) /
> `contradiction` (ретрай ≤1 внутри `generate()` с фидбеком, затем отклонение +
> ремарка); `generate()` отдаёт `turn`+`verdict` в response-событии;
> `detect_character_movement`/regex-канал понижен до safety-net (И4) при
> включённом `WORLD_ENGINE_ACTIONS_ENABLED`. Откат — флаг off возвращает
> пост-раундовый regex-путь. Golden #4/#5/#12/#13/#16/#22 покрыты
> (`tests/test_world_engine_phase5.py`, 27 тестов).
> Статус фазы 4 (Cutover): `perceive()` подключён к production-путям под
> флагом `WORLD_ENGINE_PERCEPTION_ENABLED` — presence в
> `crud.compute_and_save_presence_for_message`/`_for_round` и
> `witness_model.compute_mvp_presence` пишутся через двухканальный движок,
> схлопывание в legacy-лестницу — Renderer `perceive_to_presence` (Golden #14
> идентичности, гейт адмиссибилити `evidence_mode_from_perception`);
> Recency Tail (И15, флаг `WORLD_ENGINE_RECENCY_TAIL_ENABLED`) —
> `build_system_intervention_block`/`build_character_recency_tail` в самый
> конец user-сообщения перед generation cue в chat- и generate-путях,
> `BuiltContext.recency_tail_text` исключён из усечения бюджетом; явная
> адресация — P0-приоритет в `context_builder` (Golden #2). Откат — выключить
> оба флага; legacy `can_character_perceive_event` сохранён как fallback.
> Статус фазы 3: `WorldEvent` dual-write атомарно с `Message`
> (`crud.create_message`, флаг `WORLD_ENGINE_EVENTS_ENABLED`), двухканальный
> `perceive()` в shadow (`app/wpe_shadow.py`): расхождения со старым
> `can_character_perceive_event` классифицируются по 4 категориям v2 + И13
> (`GLASS`/`SHOUT_THROUGH_WALL`/`INVISIBLE`/`WALL`), логируются (`[WPE-P3]`),
> в контекст не идут; метрики `WPE_SHADOW_STATS`; откат — выключение флага.
> Статус фазы 2: tool-calling `take_actions` в shadow (`WORLD_ENGINE_TOOLS_ENABLED`),
> `TurnOutput` + фоллбэк tools→format→text-only, метрики `WPE_TOOLS_STATS`.
> (Фаза 8: deprecated text-only фоллбэк удалён — только tools→format, И14.)
> Статус фазы 1: канонические локации в read-path — backfill
> `characters.location_id` (`crud.backfill_character_location_ids`,
> `scripts/backfill_location_ids.py`), `perceive()` сравнивает `location_id`
> (флаг `WORLD_ENGINE_LOCATIONS_ENABLED`, по умолчанию off; откат — строковое
> сравнение), строковый код сравнения локаций помечен legacy-bridge.
> Статус фазы 0: фундамент внедрён без изменения поведения — таблицы
> `WorldEvent`/`Thread`/`ThreadParticipantState`, `Character.location_id`
> (nullable), резолвер и двухканальный `perceive()` (покрыты юнит-тестами на
> реальных локациях из текущих чатов), контракт `Action[]` + tool/JSON-Schema,
> флаги `WORLD_ENGINE_* = false`. См. §10.
> Документ развивает `Plans/WorldPerceptionEngine.md` (v2). v2 остаётся базой
> (инварианты И1–И12, Address Resolution, модель данных) — этот план
> **переопределяет** те его места, которых касаются пять новых улучшений,
> и добавляет недостающие фазы. Где проще переработать модуль с нуля, чем
> достраивать legacy — это сделано явным правилом (§9), а не "когда-нибудь".
>
> Пять улучшений, внесённых в v2:
> 1. **System Narrator** — вместо ретраев Consistency Validator при
>    "молчаливом" действии система сама вставляет техническую ремарку в чат.
> 2. **Двухканальные сенсоры** — `PerceptionResult` разделён на
>    `visual_level` и `audio_level` вместо единой шкалы `physical_level`.
> 3. **Recency Tail** — приоритетные события (`addressed=true`,
>    `remote_status=delivered`) рендерятся в самый конец промпта, перед
>    генерацией ответа.
> 4. **Native Tool Calling** — текст + действия в одном LLM-вызове только
>    через нативные tools / structured outputs; парсинг JSON из сырого
>    текста запрещён.
> 5. **Event Bus / Interrupts** — `addressed=true` будит целевого NPC и
>    вставляет его в очередь генерации вне расписания.

---

## 0. Как пять улучшений меняют v2

| # | Улучшение | Что в v2 меняется | Затронутые модули |
|---|---|---|---|
| 1 | System Narrator | §5.2 Consistency Validator (политика retry), §8 тесты #5/#12, §10 в.11 | `chat_engine`, `round_engine`, `prompt_builder`, `crud` |
| 2 | Двухканальные сенсоры | §2 `PerceptionResult`, §4 Perception Engine, §1 И11, Фаза 5, §8 тесты #8/#11 | `perception.py`, `witness_model.py`, `models.py` |
| 3 | Recency Tail | §1 И12 (был "сигнал приоритета"), §4 Renderer, Фаза 3 | `prompt_builder.py`, `context_builder.py`, `ollama_client.py` |
| 4 | Native Tools | §5.1 контракт `Action[]`, Фаза 4, §8 тест #13, §9 риск латентности | `ollama_client.py`, `schemas.py`, `chat_engine.py` |
| 5 | Event Bus | §5 пошаговый протокол (порядок генерации), Фаза 4, §8 тест #2 | `chat_engine.py`, новый `round_engine.py` |

Ключевое следствие: **порядок генерации в раунде перестаёт быть фиксированным
списком `for current_character in characters`** — он становится очередью
приоритетов, управляемой Event Bus (Ул.5). Это самая структурная правка
`chat_engine.py` за всю историю проекта, поэтому цикл раунда выносится в новый
модуль `round_engine.py` (§9, переработка с нуля), а не обрастает костылями
внутри существующего цикла. Логически шина остаётся **на уровне `chat_engine.py`**
(как требует Ул.5): `process_user_message_streaming` владеет состоянием раунда
и делегирует цикл `run_round`, физическое расположение цикла — `round_engine.py`.

### Покрытие требований заказчика (traceability)

| Требование | Где закрыто |
|---|---|
| Ул.1: валидный `move_to` + молчаливый текст → ремарка, **без retry** | И16, §5 шаги 2/10, тест #16 |
| Ул.1: снизить требования к LLM (не переписывать текст) | §5 шаг 10, Фаза 5 |
| Ул.2: `visual_level`/`audio_level` вместо единой шкалы | И13, §2, §4 |
| Ул.2: "слышит, но не видит" → текст известен, автор — только знакомый голос | §4 voice familiarity, тесты #8/#18 |
| Ул.3: `addressed=true`+`delivered` → в хвост промпта перед генерацией | И15, §6, тест #20 |
| Ул.3: формат `[СИСТЕМНОЕ ВМЕШАТЕЛЬСТВО: ...]` | §6 |
| Ул.4: native tools / structured outputs, без regex-парсинга | И14, §8, тест #22 |
| Ул.4: инструкция про `take_actions` в системном промпте | §8 |
| Ул.5: Event Bus будит NPC при `addressed=true` вне расписания | И17, §7, тест #21 |

---

## 1. Архитектурные инварианты

Инварианты И1–И12 из v2 сохраняются без изменений. Добавляются:

**И13. Восприятие физически двумерно.** `PerceptionResult` несёт
`visual_level` и `audio_level` отдельно (Ул.2). Ни одно решение не использует
"единую громкость события" как единственный вход. Прозрачное стекло,
крик из-за стены и невидимость — разные комбинации каналов, не уровни одной
шкалы. Двумерность действует с Фазы 0 (модель данных), в read-path — с Фазы 4.

**И14. Действия из LLM — только через нативные механизмы.** Текст и
структурированные действия возвращаются в одном вызове через native tools
(OpenAI/Anthropic/Ollama `tools`) или `response_format`/`format` JSON Schema
(Ул.4). Парсинг JSON из сырого текста регулярками **запрещён** с Фазы 2.
Regex-детекторы (`detect_character_movement`, `_detect_communication_channel`)
существуют только как safety-net для Consistency Validator и legacy-чатов,
никогда не как источник истины.

**И15. Recency Tail обязателен для приоритетных событий.** События с
`addressed=true` и `remote_status=delivered` (и любые P0-события) рендерятся
только в самый конец промпта, непосредственно перед сигналом генерации
(Ул.3). Они не могут быть "закопаны" в середине большого системного промпта.
Формат — `[СИСТЕМНОЕ ВМЕШАТЕЛЬСТВО: ...]`, тенденция (И12), не императив.

**И16. Молчаливое действие ≠ противоречие.** Валидный `Action(move_to)`,
текст которого его не обыгрывает, **не вызывает ретрай** — движок применяет
действие и вставляет ремарку System Narrator (Ул.1). Ретрай остаётся только
для настоящего `contradiction` (текст активно отрицает действие), с бюджетом
≤1 попытки и детерминированным исходом.

**И17. `addressed=true` управляет порядком генерации.** При появлении события
с `target_character_ids`, содержащим NPC B (прямое обращение, звонок,
сообщение), Event Bus будит B и вставляет в очередь генерации (Ул.5), даже
если по расписанию сейчас ход другого персонажа. Один NPC — максимум одна
генерация за раунд (защита от зацикливания).

### Запрещённые пути (дополнение к чек-листу v2)

```
LLM-текст → JSON через regex → Action        (запрещено с Фазы 2, И14)
PerceptionResult.physical_level               (единая шкала удалена, И13)
addressed=true → текст в середину промпта      (нарушение И15)
валидный action + молчаливый текст → retry     (нарушение И16)
раунд → фиксированный порядок без очереди      (нарушение И17 после Фазы 7)
```

---

## 2. Целевая модель данных

Как в v2 (§2): `Location` (+ каноническая "Общая сцена"), `Character.location_id`,
`WorldEvent` (immutable, append-only), `Message`, `Thread`,
`ThreadParticipantState`. Изменения и дополнения:

- **Location**: рёбра соседства несут **проницаемость по каналам**:
  `visual_permeability` (full/partial/none) и `audio_permeability`
  (full/muffled/none). Пример: стекло `visual=full, audio=none`; стена
  `visual=none, audio=muffled`. Обратная совместимость: ребро без явных
  значений по умолчанию `visual=none, audio=muffled` (сейчас это поведение
  "audible из соседней комнаты").
- **PerceptionResult** (эфемерный, И8) — вместо `physical_level`:
  ```
  visual_level: Literal["full", "partial", "none"]
  audio_level:  Literal["full", "muffled", "none"]
  addressed:    bool                     # читается из target_character_ids (И7)
  remote_status: Literal["none", "delivered"]
  ```
  Никакого текста. Атрибуция говорящего — **не часть PerceptionResult**
  (см. §4, voice familiarity).
- **WorldEvent**: поля как в v2. Для `move` — `location_from`/`location_to`.
  Immutable после вставки (И9).
- **Action** (контракт данных, И14):
  ```
  Action:
    type: Literal["move_to", "send_message"]     # расширяемо в данных, не в коде
    location?: str                                # для move_to
    message?: str                                 # для send_message
    channel?: Literal["direct","magic","phone","radio","messenger"]
    target_character_ids?: list[int]
  ```
  Отдельное структурированное поле адресации реплики `reply_target_character_ids`
  (Address Resolution для NPC, §3).
- **Knowledge** — выход Renderer'а поверх `PerceptionResult` (как в v2, §2).

---

## 3. Address Resolution

Как в v2 (§3): NPC — структурированное поле генерации (`reply_target_character_ids`
в том же LLM-вызове, Ул.4); игрок — UI-выбор с эвристикой по имени/вокативу
как первичным механизмом для свободного текста без UI (И7). Safety-net сверка
текста с `target_character_ids` — только вход для Consistency Validator (§5),
не источник истины.

---

## 4. Единый Perception Engine (двухканальный)

```
perceive(world_state_at_event_time, event, observer) -> PerceptionResult
```

Чистая функция, без БД и LLM. Источники уровней:

- **visual_level** — из графа локаций (одинаковая локация → `full`;
  соседство с `visual_permeability`; `none` для дальних локаций или визуальных
  преград: невидимость, темнота). Никогда не додумывается (И11).
- **audio_level** — из графа (`audio_permeability`), громкости события
  (стимулы `loud_sound`/`call`/`shout` повышают `muffled→full`), дальности.
- **addressed** — только из `WorldEvent.target_character_ids` (И7).
- **remote_status** — из `Thread`/`ThreadParticipantState` (delivered для
  адресата канала независимо от локации).

**Voice familiarity (атрибуция при аудио-только).** "Слышит, но не видит:
знает текст, но не знает, кто конкретно, если голос не знаком" (Ул.2).
Perception возвращает только `audio_level`; РЕНДЕР решает, можно ли назвать
говорящего по имени. Это константа Renderer'а, а не LLM-выдумка (И11).

Конкретные правила атрибуции (детерминированные):
- `audio=full` + голос знаком (у наблюдателя есть память/отношение с автором) →
  автор называется по имени;
- `audio=full` + голос незнаком → «чей-то голос», автор не называется;
- `audio=muffled` → атрибуция запрещена всегда (голос искажён), только
  «голоса из-за …»;
- `addressed=true` по удалённому каналу (звонок/мессенджер) → автор известен
  адресату напрямую (обратная связь от `ThreadParticipantState`), даже при
  `visual_level=none`.

Граница: атрибуция никогда не обогащается за счёт визуального канала при
`visual_level=none` (И11).

**Потребители (только через `perceive`, как в v2 §4):** `context_builder`,
`_evidence_mode` (адаптер `PerceptionResult → {direct|observed|hearsay|none}`),
`memory_service` (наблюдаемость фактов). `_evidence_mode` обязан отображать
каналы: полный визуальный контакт и полный аудио-контакт дают `direct`; только
`muffled` аудио — `hearsay`, но не выше.

---

## 5. Action Resolution (Ул.1 + Ул.4)

### Контракт данных
`Action[]` с первого дня, как в v2 (§5.1). Передача — только через native
tools / structured outputs (И14, §8).

### Пошаговый протокол одного хода персонажа

1. LLM генерирует текст реплики + `reply_target_character_ids` + массив
   `Action[]` в **одном нативном вызове** (Ул.4). Действия извлекаются из
   `tool_calls`/JSON-схемы движком, а не парсятся из текста.
2. **Action↔Text Consistency Validator** (v2 §5.2, переопределён Ул.1/И16):
   - `consistent` — действие применяется;
   - `minor_ambiguity` (действие валидно, текст его не обыгрывает — "молчаливое
     действие") — **без ретрая**: действие применяется, System Narrator
     вставляет ремарку (см. ниже);
   - `contradiction` (текст активно отрицает: "я никуда не пойду" при
     `move_to`) — один ретрай в рамках существующего бюджета; если не снято —
     действие **отклоняется**, текст остаётся, инцидент логируется и
     опционально System Narrator фиксирует исход решением движка.
3. Предусловия: локация существует/достижима (`move_to`), thread/участники
   валидны (`send_message`).
4. Невалидное действие — отклоняется молча для `WorldState`, `WorldEvent` не
   создаётся (v2 §5.4).
5. Валидные действия применяются атомарно: изменение `WorldState` + вставка
   `WorldEvent` одной транзакцией (v2 §5.5). Порядок в батче: move → зависящие
   от локации (`send_message`).
6. `location_id` обновляется для успешных `move_to`.
7. `WorldEvent(move)` immutable, с `location_from`/`location_to`, `round_id`.
8. Speech-`WorldEvent` фиксируется с **уже обновлённой** (пост-move) локацией
   (v2 §5.8 — баг "вышел из спальни, зашёл в гостиную").
9. Perception следующих персонажей — только от обновлённого `WorldState`.
10. **System Narrator (Ул.1, И16) — техника, а не LLM.** Требование к модели
    снижено: LLM **не обязана** обыгрывать действия в тексте, и за это не
    бывает retry. Для каждого применённого действия, чей результат не отражён
    в тексте реплики, движок сам создаёт служебное сообщение `role=system` по
    детерминированному шаблону из `WorldEvent`:
    `*[Система: <Имя> <действие>]*`
    (например `*[Система: Пётр покидает локацию 'Гостиная' и переходит в
    'Кухню']*`). Текст ремарки генерирует только движок, никогда — LLM (И16).
    Ремарка сохраняется в историю и участвует в восприятии остальных
    персонажей как обычный system-событие (по локации); рендерится сразу после
    реплики персонажа — визуально это совпадает с примером заказчика
    («Я подумаю об этом.» + ремарка). Текст реплики при этом **не
    редактируется**.
    - `consistent` (текст сам обыгрывает действие) → ремарка не нужна.
    - Крайний случай: пустой текст + валидные действия → ремарка Narrator
      остаётся единственным видимым сообщением; speech-сообщение не создаётся.

Оркестрация хода — одна явная функция в `round_engine.py` (И17), не
эмерджентный порядок вызовов.

---

## 6. Renderer и Recency Tail (Ул.3)

**Renderer** (`witness_model.py`/преемник) — единственный слой
`PerceptionResult → текст промпта` (И8), теперь по каналам (И13):

- `visual=full, audio=full` → полная строка `Имя: <текст>`.
- `visual=full, audio=none` (стекло) → описание действий видно, текст реплик —
  нет ("за стеклом происходит что-то, слов не слышно").
- `visual=none, audio=full` (крик/звонок) → текст известен, атрибуция — по
  voice familiarity (§4), иначе "чей-то голос".
- `audio=muffled` → только факт/фрагмент без семантики содержания (И11).

**Recency Tail (Ул.3, И15):** новый блок в `prompt_builder.py`:

```
build_system_intervention_block(events) -> str
```

Рендерит P0-события (`addressed=true`, `remote_status=delivered`, срочные
стимулы-вызовы) в формате:

```
[СИСТЕМНОЕ ВМЕШАТЕЛЬСТВО: Прямо сейчас игрок обращается к тебе / твой
телефон разрывается от звонка. Отреагируй!]
```

Размещение — в **самый конец** пользовательского сообщения, непосредственно
перед `build_generation_cue`/cue_for_chat, в обоих путях сборки
(`_build_generation_messages` для chat API и список блоков для generate API
в `ollama_client.py`). Блок пересобирается **для каждого персонажа отдельно**:
в хвост конкретного NPC попадают только его собственные P0-события
(обращение к нему, его телефон, доставленные ему сообщения). Гарантия позиции:
в chat-пути — последний блок внутри `build_user_context_message`, перед
generation cue; в generate-пути — последний блок перед `build_generation_cue`.
В `context_builder.py` блок добавляется после финальной сборки и исключается из
усечения бюджетом (резерв из существующего `context_reserve_tokens`). Никогда
не в системный/developer-роль (И15, как и `<open_issue data>` в v2).

---

## 7. Event Bus / Interrupts (Ул.5)

Механизм живёт **на уровне `chat_engine.py`** (как требует Ул.5):
`process_user_message_streaming` владеет состоянием раунда и вызывает
`run_round`; физически цикл вынесен в новый `round_engine.py` по правилу §9
(переработка с нуля вместо костылей в существующем цикле).

**Триггер:** при создании `WorldEvent` с `addressed=true` (NPC B ∈
`target_character_ids` события от NPC A или игрока), если B — NPC и ещё не
генерировал в этом раунде — B помечается как "разбужен".

**Очередь генерации** вместо фиксированного списка:
- приоритет: разбуженные NPC идут впереди плановых; внутри одного приоритета —
  исходный порядок (order_index) для детерминизма;
- игрок → NPC (UI/эвристика адресации) будит адресата первым ходом раунда;
- NPC A → NPC B (реплика с `target_character_ids`) будит B немедленно, даже
  если по расписанию дальше идёт C;
- один NPC генерирует максимум один раз за раунд (И17, защита от циклов);
- повторный `addressed=true` к уже ответившему NPC — игнорируется (событие
  всё равно сохраняется в истории и видно в следующем раунде).

`round_engine.run_round(...)` — единственная оркестрирующая функция; она же
подписывается на шину и выполняет буждение при `addressed=true`.

---

## 8. LLM-интеграция: native tools (Ул.4)

**Канал по умолчанию:** `ollama_client` добавляет ветку tool-calling.

Tool-схема (OpenAI-совместимая, используется и в Ollama `/api/chat`):

```
{
  "type": "function",
  "function": {
    "name": "take_actions",
    "description": "Действия персонажа в этом ходу (перемещение, отправка сообщения).",
    "parameters": {
      "type": "object",
      "properties": {
        "reply_target_character_ids": {"type": "array", "items": {"type": "integer"},
          "description": "Кому адресована реплика (id персонажей)."},
        "actions": {"type": "array", "items": {
          "type": "object",
          "properties": {
            "type": {"type": "string", "enum": ["move_to", "send_message"]},
            "location": {"type": "string"},
            "message": {"type": "string"},
            "channel": {"type": "string", "enum": ["direct","magic","phone","radio","messenger"]},
            "target_character_ids": {"type": "array", "items": {"type": "integer"}}
          },
          "required": ["type"]
        }}
      },
      "required": ["reply_target_character_ids", "actions"]
    }
  }
}
```

Системный промпт (добавляется в `prompt_builder`):
> "Ты должен использовать вызов функции `take_actions` с массивом действий,
> если твой персонаж перемещается или отправляет сообщение. Текст реплики и
> действия — в одном ответе."

**Streaming-контракт:** токены продолжают стримиться для аватара; `tool_calls`
приходят в терминальном сообщении и **не рендерятся как текст**.
`_stream_ollama_chat`/`generate` читают `message.tool_calls` и возвращают
структурированные действия отдельным полем ответа.

**Фоллбэк (строго нативный, без regex):**
1. `tools` поддерживаются → tool calling.
2. tools не поддерживаются моделью → `format: <JSON Schema>` (Ollama) /
   `response_format: {type:"json_schema"}` (OpenAI-совместимо) с той же схемой.
3. ни tools, ни format недоступны → **RuntimeError** (Фаза 8, И14: structured
   actions обязательны); текст-only путь с legacy safety-net детекторами
   удалён в Фазе 8.

Латентность: один вызов на ход (текст+действия), измеряется отдельно
(риск §12; в.1 из v2 сохраняется).

---

## 9. Переработка с нуля vs надстройка (явное правило)

Правило: **модуль, в котором новый механизм обязан жить по-другому, пишется
заново; legacy-файл удаляется в той же фазе, а не живёт рядом как "второй
скрытый движок"** (принцип v2 §6, "не оставить второй движок").

| Модуль | Решение | Причина |
|---|---|---|
| `app/perception.py` | **С нуля** | 1D-шкала (visible/audible/mentioned/absent) несовместима с И13; расширять legacy-функцию `can_character_perceive_event` сложнее, чем написать `perceive()` (тесты `test_perception*.py` переписываются) |
| Цикл раунда в `app/chat_engine.py` | **Выносится в новый `app/round_engine.py`** | Event Bus (И17) требует очереди приоритетов; надстройка внутри существующего `for current_character` плодит флаги и спецкейсы |
| `app/ollama_client.py` (путь генерации) | **Доработка** ветки chat API: tools/format + `tool_calls` в streaming; generate API остаётся как fallback | LLM-вызов не меняет сути, но payload/response-контракт пересобирается |
| `app/movement.py` `detect_character_movement` | **Понижается до safety-net** (вход Consistency Validator), не источник истины | И14: источник — `Action[]` из tools |
| `app/chat_engine._detect_communication_channel` | **Удаляется как источник истины**; `channel` приходит из `send_message` action; regex остаётся только legacy-safety-net с deprecation-логом (Фаза 8: срабатывает лишь когда actions не извлечены) | И14 |
| `app/witness_model.py` | **Надстройка** (Renderer по каналам + voice familiarity) | Существующая структура presence→формат близка к И8; переписывается внутренняя логика строк, публичный слой `filter_history_*` сохраняется |
| `app/prompt_builder.py` | **Надстройка**: `build_system_intervention_block`, narrator-форматирование, инструкция `take_actions` | Отдельные независимые блоки |
| `app/crud.py` (dual-write) | **Надстройка** поверх `create_message` | Одна транзакция Message+WorldEvent |

---

## 10. Фазы внедрения

Каждая фаза: отдельный флаг, критерий выхода, план отката. Фаза N+1 не
начинается, пока не закрыт критерий фазы N на canary-подмножестве чатов.

**Фаза 0 — Фундамент без изменения поведения** — ✅ выполнена 08-04
- ✅ Таблицы `Location`, `Character.location_id` (nullable), резолвер
  строка→`location_id` (написан, не подключён: `crud.resolve_location_name` /
  `crud.resolve_location_string`). Каноническая "Общая сцена" (`""` /
  `"Общая сцена"` → без id, `perception.is_shared_scene`).
- ✅ Таблицы `WorldEvent`, `Thread`, `ThreadParticipantState` (заведены, не
  пишутся; миграция идемпотентна, накатывается на существующую БД).
- ✅ Модель данных И13: `PerceptionResult` с `visual_level`/`audio_level`
  (`schemas.PerceptionResult`); проницаемость рёбер по каналам в
  `Location.adjacent_to` (расширение схемы: строки и/или объекты
  `{"name", "visual_permeability", "audio_permeability"}`; по умолчанию
  `visual=none, audio=muffled`).
- ✅ Контракт `Action[]` + tool/JSON-Schema схема в `schemas.py` (`Action`,
  `TurnOutput`, `build_take_actions_tool`, `build_take_actions_json_schema`;
  написаны, не подключены). Ул.4.
- ✅ Флаги: все `WORLD_ENGINE_* = false` (`app/config.py`, 9 флагов).
- ✅ Новый `perception.perceive()` (двухканальный, чистый) + индекс
  проницаемости `build_permeability_index`; legacy 1D-шкала сохранена до
  Фазы 4.
- ✅ Критерий выхода: миграция накатывается без ошибок (проверено на
  копии прод-БД); резолвер и `perceive()` покрыты юнит-тестами
  (`tests/test_world_engine_phase0.py`, 29 тестов) на реальных значениях
  локаций из текущих чатов (Новоселье/Студ/МММ).
- Откат: тривиален, новые таблицы не читаются.

**Фаза 1 — Канонические локации (read-path)** — ✅ выполнена 08-04
- ✅ Backfill `location_id`; неоднозначные случаи — в отчёт на ручной разбор:
  `crud.backfill_character_location_ids` → `LocationBackfillReport`
  (resolved / shared_scene / unresolved), идемпотентно; "Общая сцена" (`""` /
  `"Общая сцена"`) → `None`; нерезолвленное имя остаётся `None` и
  фиксируется в отчёте. Запуск: `scripts/backfill_location_ids.py` (exit 1 при
  неоднозначных случаях).
- ✅ `perception.py` (новый) сравнивает `location_id`:
  `same_canonical_location(...)` в `perceive()` — при флаге и наличии id с
  обеих сторон решение по каноническому id (синонимичные строки → одна
  локация, разные id → разные даже при совпадении строк); без id — строковый
  legacy-bridge. Старый строковый код (`normalize_location`/`locations_match`/
  `get_perception_level`/`can_character_perceive_event`/`build_adjacency_index`)
  помечен legacy-bridge.
- ✅ Флаг: `WORLD_ENGINE_LOCATIONS_ENABLED` (по умолчанию `false`).
- ✅ Критерий выхода: golden #1 «синонимичная локация → одинаковый present»
  проходит (`tests/test_world_engine_phase1.py`, 11 тестов: golden, откат на
  строковое сравнение, backfill incl. игрока/переименование/идемпотентность),
  регрессий нет (663 passed; 28 pre-existing падений вне scope, набор
  идентичен Фазе 0).
- ✅ Откат: возврат к сравнению строк — выключение флага (проверено тестом
  `test_flag_off_uses_string_comparison`).

**Фаза 2 — Tool-calling генерация (shadow → canary)** — ✅ реализована 08-04
- ✅ `ollama_client` получил ветку tools/format (`take_actions`): payload
  chat/generate принимают `tools` / `format` (JSON-Schema) из
  `schemas.build_take_actions_tool` / `build_take_actions_json_schema`; в
  shadow-режиме действия извлекаются в `schemas.TurnOutput`, **логируются
  (`[WPE-P2] shadow …`), не применяются**; текст прежний. Рядом —
  `reply_target_character_ids`.
- ✅ Системный промпт: `build_take_actions_instruction` (гейт флагом), в
  `build_system_prompt` по опциональному параметру. Streaming-контракт:
  токены стримятся как раньше, `tool_calls` приходят в терминальном
  сообщении и **не рендерятся как текст** (тест #22).
- ✅ Фоллбэк строго нативный (И14): tools → `format` (JSON-Schema). До Фазы 8
  был также text-only (deprecated) по 400 «не поддерживает tools/format»;
  **Фаза 8: text-only фоллбэк удалён** — при недоступных tools/format
  генерация падает с `RuntimeError`. Кэш возможностей модели один раз на имя
  модели (§12).
- ✅ Флаг: `WORLD_ENGINE_TOOLS_ENABLED` (по умолчанию `false`).
- ✅ Shadow-метрики критерия выхода (§10): `ollama_client.WPE_TOOLS_STATS` +
  `wpe_tools_stats_snapshot()` — доля ходов с корректным `move_to`/
  `send_message`/адресацией (поля `with_move_to`/`with_send_message`/
  `with_addressing`, `schema_valid`) и латентность хода (`latency_ms`,
  avg/max) — собираются на canary и документируются (§12).
- ✅ Критерий выхода: покрыто `tests/test_world_engine_phase2.py` (19 тестов:
  инструкция, payload tools/format, streaming-контракт #22, фоллбэк tools→
  format (без text-only, Фаза 8), кэш, shadow без применения действий).
  Регрессий нет
  (682 passed; 28 pre-existing падений вне scope, набор идентичен Фазе 1).
  Канареечный запуск (реальные модели, 100% схема-валидных вызовов без
  падения генерации + прирост латентности) — отдельным отчётом по §12.
- ✅ Откат: tools-ветка выключается флагом, генерация текст-only (проверено
  тестом `test_tools_flag_off_text_only_no_shadow`).

**Фаза 3 — WorldEvent dual-write + shadow Perception (2 канала)** — ✅ реализована 08-04
- ✅ Dual-write: `WorldEvent` создаётся рядом с `Message` атомарно (одна
  транзакция: `db.flush` → добавление `WorldEvent` → один `commit`) в
  `crud.create_message` (`_build_world_event`, event_type `speech`/`system`,
  `round_id` для user выводится `r{chat_id}-m{message_id}`; в round-пути
  chat_engine пробрасывает `round_id` явно).
- ✅ Shadow `perceive()` (2 канала, И13): новый `app/wpe_shadow.py` —
  `classify_shadow_discrepancy` (4 категории v2: `regression`/`fix`/
  `expected_expansion`/`expected_model_change` + И13-подкатегории `GLASS`/
  `SHOUT_THROUGH_WALL`/`INVISIBLE`/`WALL`/`ADDRESSED_PARTIAL`) и
  `run_shadow_perception` — прогон по наблюдателям после коммита; результат
  логируется (`[WPE-P3] shadow …`), **в сборку контекста не идёт**. Ошибки
  shadow не ломают сохранение сообщения.
- ✅ Флаг: `WORLD_ENGINE_EVENTS_ENABLED` (по умолчанию `false`; off → dual-write
  и shadow не выполняются). Откат: shadow выключается флагом, dual-write можно
  оставить.
- ✅ Shadow-метрики критерия выхода (§10): `WPE_SHADOW_STATS` +
  `wpe_shadow_stats_snapshot()` — события/наблюдатели, matched/diverged, по
  категориям и подкатегориям, `unexplained` (должен быть 0).
- ✅ Критерий выхода: покрыто `tests/test_world_engine_phase3.py` (22 теста:
  атомарный dual-write + поля/round_id, off-регрессия, golden-сетка всех
  категорий + И13-комбо, shadow не меняет legacy/контекст и не пишет presence,
  `unexplained == 0`). Регрессий нет (704 passed; 28 pre-existing падений вне
  scope, набор идентичен Фазе 2).

**✅ Фаза 4 — Cutover: Perception Engine + Recency Tail** [Ул.2, Ул.3] *(реализована 08-04)*
- `witness_model`/`crud.compute_and_save_presence_*` переходят на
  `PerceptionResult` через Renderer (`perceive_to_presence` / `perceive_presence_for_character`);
  legacy `perception.can_character_perceive_event` **сохранён** как fallback
  (откат по флагу), удаляется отдельным PR после стабильности.
- `_evidence_mode` — чистый адаптер (`evidence_mode_from_perception`, тот же
  гейт адмиссибилити). Golden #14 идентичности.
- Явная адресация (из tools, Фаза 2) — P0-приоритет в `context_builder`
  (addressed-строки не вытесняются бюджетом). Golden #2 (часть Фазы 4).
- **Recency Tail (И15):** `build_system_intervention_block` +
  `witness_model.build_character_recency_tail` в хвост промпта (chat и generate
  пути), защита от вытеснения бюджетом в `context_builder` (блок — часть
  фиксированных инструкций, не усекается). Флаг отдельный:
  `WORLD_ENGINE_RECENCY_TAIL_ENABLED` (раздельный canary).
- Флаги: `WORLD_ENGINE_PERCEPTION_ENABLED` (canary → глобально).
- Критерий выхода: полный golden-набор §11 (актуальной версии) проходит;
  eval без регресса.
- Откат: флаг выключается по чату; старый код удаляется отдельным PR после
  стабильности.

**✅ Фаза 5 — Action Resolution + System Narrator** [Ул.1] *(реализована 08-04)*
- ✅ Протокол §5 исполнен целиком: действия из tools применяются (move_to,
  send_message), атомарно, с immutable `WorldEvent`. `turn.actions`
  извлекаются только из tool_calls/JSON-схемы (И4) и пробрасываются из
  `ollama_client.generate()` в response-событие (`turn` + `verdict`).
- ✅ Consistency Validator (`app/action_resolution.classify_consistency`) — три
  класса: `consistent` / `minor_ambiguity` (**молчаливое действие → System
  Narrator без ретрая**, И16) / `contradiction` (→ ≤1 retry внутри
  `generate()` с фидбеком `<action_consistency>`, затем детерминированное
  отклонение + ремарка Narrator). Стоик ретраев — `WPE_ACTION_CONSISTENCY_MAX_RETRIES` (1).
- ✅ System Narrator: ремарки `role=system` по шаблону из `WorldEvent`
  (`*[Система: <Имя> <действие>]*`), генерируются только движком (И6), текст
  реплики не редактируется; при `contradiction` — ремарка об отклонении.
- ✅ Применение: `crud.apply_character_actions` — валидные действия атомарно
  (одна транзакция), порядок move → зависящие от локации; невалидное действие
  отклоняется без `WorldEvent` и не портит валидные (#13); `location_id`
  обновляется для успешных `move_to`; `WorldEvent(move)` immutable с
  `location_from`/`location_to`/`round_id`; `send_message` → `WorldEvent(speech)`
  с `target_character_ids` (Threads — Фаза 6).
- ✅ `detect_character_movement`/regex-канал понижены до safety-net (И14):
  активны только при выключенном флаге действий.
- ✅ Флаг: `WORLD_ENGINE_ACTIONS_ENABLED` (+ tuning-настройка
  `WPE_ACTION_CONSISTENCY_MAX_RETRIES`).
- ✅ Критерий выхода: golden "явное перемещение" (#4), "молчаливый телепорт →
  Narrator" (#5/#16), "конфликт action↔текст" (#12), "несколько действий за
  ход" (#13), native tools (#22) покрыты `tests/test_world_engine_phase5.py`
  (27 тестов). Регрессий нет (744 passed; 28 pre-existing падений вне scope,
  набор идентичен Фазе 4).
- ✅ Откат: флаг выключается — движок возвращается к пост-раундовому
  regex-пути движения (проверено off-тестами).

**✅ Фаза 6 — Thread/мессенджер + двухканальное частичное восприятие** [Ул.2] *(реализована 08-04)*
- ✅ Threads в проде: `crud.ensure_message_thread_delivery` вызывается из
  `create_message` для удалённых каналов (magic/phone/radio/messenger) — тред
  создаётся/обновляется, участники = автор + адресаты, доставка
  (`last_delivered_message_id`) — только адресатам; Golden #6 (адресат получает
  `remote_status=delivered` независимо от локации через
  `thread_delivery_ids_for_message` → `world_state.thread_deliveries` →
  `perceive()`) и Golden #15 (групповой тред). `send_message` из
  `apply_character_actions` создаёт тред и участников (И14).
- ✅ Частичное восприятие по каналам (И13): `perceive()` учитывает проницаемость
  рёбер `visual_permeability`/`audio_permeability`, громкость (loud_sound
  повышает muffled→full) и стимул `invisible` (одна локация → visual=none,
  audio=full). Рендер `render_perception_line` соблюдает И11: стекло (full/none)
  → «действия видны, слов не слышно», muffled — без содержания.
- ✅ **Voice familiarity** для атрибуции при audio-only (§4): детерминированный
  `witness_model.voice_familiarity` из `CharacterRelationship` (знакомый голос →
  mentioned/«голос <имя>», незнакомый → audible/«чей-то голос», muffled →
  атрибуция запрещена); подключён в presence (message/round) и в Renderer
  `ContextBuilder` (канало-зависимая строка при включённых обоих флагах).
- ✅ Флаги раздельные: `WORLD_ENGINE_THREADS_ENABLED`,
  `WORLD_ENGINE_PARTIAL_PERCEPTION_ENABLED` (оба default off, инвариант Фазы 0).
- ✅ Критерий выхода: golden "сообщение в мессенджер" (#6), "групповой тред"
  (#15), "стекло" (#17), "крик из-за стены" (#18), "невидимость" (#19),
  "подслушивание без утечки семантики" (#11/#8 — канало-рендер) покрыты
  `tests/test_world_engine_phase6.py` (14 тестов). Регрессий нет
  (758 passed; 28 pre-existing падений вне scope, набор идентичен Фазе 5).
- ✅ Откат: флаги раздельные; partial → бинарный full/none по каналам
  (проверено off-тестами).

**Фаза 7 — Event Bus / Interrupts** [Ул.5] ✅
- ✅ `app/round_engine.py` создан (правило §9): `EventBus` (очередь приоритетов,
  разбуженные впереди плановых, внутри — FIFO, плановый порядок —
  `order_index`), `run_round` — единственная оркестрирующая функция.
- ✅ `chat_engine.process_user_message_streaming` делегирует цикл в
  `run_round` (флаг on) / `run_round_fixed` (флаг off); пер-NPC шаг вынесен
  в `_round_step(current_character, bus)` и сам будит NPC-адресатов реплики.
- ✅ Буждение по `addressed=true`: игрок→NPC — `seed` из `target_character_ids`
  user-сообщения (первый ход); NPC→NPC — `target_character_ids` реплики
  (источник — `send_message` action или regex-канал для отката).
- ✅ Один ответ на NPC за раунд; повторные буждения и буждения уже ответивших
  игнорируются (И17) — защита от зацикливания/инверсии (риски §12).
- ✅ Флаг: `WORLD_ENGINE_EVENT_BUS_ENABLED` (default False, инвариант Фазы 0).
- ✅ Критерий выхода: golden "звонок будит NPC вне очереди", "игрок обратился к
  конкретному NPC → он отвечает первым", "нет повторной генерации за раунд",
  "нет зацикливания" (§11) проходят — `tests/test_world_engine_phase7.py`
  (13 тестов: unit EventBus + run_round + end-to-end streaming). Регрессий нет
  (771 passed; 28 pre-existing падений вне scope, набор идентичен Фазе 6).
- ✅ Откат: флаг выключается, очередь → исходный фиксированный порядок
  (проверено off-тестами `run_round_fixed` + streaming off-регрессией).

**Фаза 8 — Уборка и документация** ✅
- ✅ Закрытие аудита legacy-полей (§6 v2): `Message.visibility`,
  `character.location`-строка — только read-only legacy-bridge. Проверено:
  `Message.visibility` задаётся только при создании (no update-path,
  `app/models.py`); все write-path строки `character.location`
  (`update_character_location`, `update_character_locations_batch` в
  `app/crud.py`) пишут также канонический `location_id`.
- ✅ Удаление deprecated text-only пути генерации (без tools/format) и regex
  детекторов как источника истины. Проверено: `_tool_mode_chain`/`_next_tool_mode`
  (`app/ollama_client.py`) не дают фоллбэк tools→format→text — при
  недоступных tools/format генерация падает с `RuntimeError` (И14); текст-only
  путь остался только для нетools-генерации (`preferred="text"`);
  `detect_character_movement`/`_detect_communication_channel` — legacy-safety-net
  с deprecation-логами (источник истины — `turn.actions` из tools/format).
- ✅ Обновление `docs/architecture.md`, `README.md`. Финальный прогон полного
  golden-набора §11 как регрессионный барьер перед снятием флагов:
  **771 passed, 28 pre-existing (идентично фазе 7)**.

---

## 11. Тестирование (golden-сценарии)

Тесты v2 (#1–#15) сохраняются с изменениями, помеченными ниже. Новые — #16–#22.

| # | Сценарий | Изменение vs v2 | Фаза |
|---|---|---|---|
| 1 | Синонимичная локация → одинаковый `present` | — | 1 |
| 2 | Прямое обращение по имени → P0-приоритет **и буждение через Event Bus** | расширен Ул.3/Ул.5 | 4/7 |
| 3 | (снят в v2) | — | — |
| 4 | Явное перемещение через `move_to` → `WorldEvent(move)` + обновление `location_id` до presence следующего персонажа | источник — tools (И14) | 5 |
| 5 | **Молчаливый телепорт** (валидный `move_to`, текст молчит) → System Narrator вставляет ремарку, **без ретрая** | переопределён Ул.1 (было: contradiction/retry) | 5 |
| 6 | Сообщение в мессенджер → адресат получает `remote_status=delivered` независимо от локации | — | 6 |
| 7 | Отправитель без ответа не утверждает в новой реплике, что ответ получен | — | 5/6 |
| 8 | Подслушивание из соседней локации → **по каналам**: `audio=muffled` известен, `visual=none`; атрибуция по голосу | переопределён Ул.2 | 6 |
| 9 | Регрессия по существующим golden/eval сценариям | — | каждая фаза |
| 10 | `WorldEvent` immutable | — | 3/5 |
| 11 | Утечка семантики через частичное восприятие — теперь **по каналам**: из `audio=muffled` наблюдатель не получает содержание; из `visual=partial` — только визуальную часть | переопределён Ул.2 | 6 |
| 12 | Конфликт action↔текст → `contradiction`: ≤1 retry, затем детерминированное отклонение + ремарка Narrator | переопределён Ул.1 | 5 |
| 13 | Несколько действий за ход применяются атомарно; невалидное не портит валидное | источник — tools | 5 |
| 14 | Единый `PerceptionResult` для генерации и отношений | — | 4 |
| 15 | Групповой тред через `ThreadParticipantState` | — | 6 |
| **16** | **System Narrator, позитив**: текст "Я подумаю об этом." + `move_to(Кухня)` → реплика не меняется, следом system-ремарка `*[Система: Пётр покидает 'Гостиную' и переходит в 'Кухню']*`; **счётчик LLM-вызовов = 1 (без retry)** | новый (Ул.1) | 5 |
| **17** | **Стекло**: наблюдатель в одной локации, событие за стеклом → `visual=full`, `audio=none`; рендер: действия видны, текст реплик — нет | новый (Ул.2) | 6 |
| **18** | **Крик из-за стены**: соседняя локация, `loud_sound` → `visual=none`, `audio=full`; текст известен, атрибуция только знакомому голосу | новый (Ул.2) | 6 |
| **19** | **Невидимость**: событие в одной локации со стимулом невидимости → `visual=none`, `audio=full` | новый (Ул.2) | 6 |
| **20** | **Recency Tail**: события с `addressed=true`/`remote_status=delivered` присутствуют только в конце пользовательского сообщения, перед generation cue; блок выживает при усечении бюджета | новый (Ул.3) | 4 |
| **21** | **Event Bus**: NPC A звонит NPC B, по расписанию дальше идёт C → B генерирует раньше C; B отвечает один раз за раунд | новый (Ул.5) | 7 |
| **22** | **Native tools**: `move_to`/`send_message`/адресация извлекаются из `tool_calls`/JSON-схемы, **не из текста**; мок возвращает tool_calls, assert на путь извлечения | новый (Ул.4) | 2/5 |

Детерминированные (#1, 4–7, 10, 12–22) — моканные LLM-ответы, включая моки с
`tool_calls`. "Естественность" (#2, 8, 11, 17–19) — eval/LLM-as-judge поверх
golden-диалогов.

---

## 12. Риски

- **Нативная поддержка tools у локальных моделей (Ул.4).** Ollama
  поддерживает tools/format не для всех моделей. Митигируется двухступенчатым
  фоллбэком (tools → JSON-Schema format) и проверкой возможностей модели один
  раз (кэш на имя модели). Критичность гипотезы — поэтому Фаза 2 идёт сразу
  после фундамента.
- **Streaming + tool_calls.** Токены стримятся, tool_calls приходят в
  терминальном сообщении. Риск: фронтенд увидит "хвост" ответа с JSON — если
  инструкция нарушена. Митигируется: tool_calls никогда не рендерятся как
  текст (тест #22), отдельный интеграционный тест streaming-контракта.
- **Латентность/стоимость раунда (Фаза 2/5).** Текст+действия в одном вызове
  (без второго вызова); прирост измеряется в Фазе 2 до cutover (§8, в.1 v2).
- **Рассинхронизация Message/WorldEvent (Фаза 3).** Одна транзакция.
- **Ретраи против Narrator (Ул.1).** Основной риск "тихих телепортов"
  закрывается без ретраев; retry остаётся только для настоящего
  `contradiction` с бюджетом ≤1 — риск застревания в цикле retry снижается.
- **Event Bus: зацикливание/инверсия порядка.** Защита: один ответ на NPC за
  раунд, FIFO внутри приоритета. Риск "прыгающего" порядка генерации для
  старых чатов — митигируется canary (Фаза 7).
- **Двухканальное восприятие: чрезмерная "умность".** Риск, что `audio=full`
  + знакомый голос перерастут в утечку невидимой визуальной информации.
  Митигируется: атрибуция — детерминированная константа voice familiarity
  (И11), тесты #17–#19.
- **Recency Tail: вытеснение из бюджета.** Блок добавляется последним и
  защищён резервом токенов; тест #20 проверяет сохранность при усечении.
- **Остаточный риск адресации игрока без UI-выбора** (v2 §9) — не меняется.

---

## 13. Блокирующие решения перед Фазой 0

Решения v2 (§10) сохраняются. Добавлены/переопределены:

1. **Тип передачи действий (Ул.4)** → native tools первичен, `format`
   JSON-Schema вторичен, текст-only deprecated (И14, §8).
2. **Шкала восприятия (Ул.2)** → `visual_level`/`audio_level` вместо
   `physical_level` (И13, §2/§4). Принято.
3. **Политика молчаливого действия (Ул.1)** → System Narrator без ретрая
   (И16, §5, тест #16). Принято.
4. **Размещение приоритетных событий (Ул.3)** → хвост промпта, перед
   generation cue, в user-роли, с защитой бюджета (И15, §6, тест #20).
   Принято.
5. **Порядок генерации (Ул.5)** → Event Bus + очередь приоритетов. Логически
   шина — на уровне `chat_engine.py` (Ул.5); физически цикл вынесен в новый
   `round_engine.py` по правилу §9 (И17, §7, Фаза 7). Принято.
6. **Voice familiarity** → детерминированный атрибут Renderer'а на основе
   памяти/отношений наблюдателя (И11, §4). Подтвердить источник данных
   (только Memory vs Memory+Relationship).
7. **Проницаемость рёбер локаций** → значения по умолчанию
   `visual=none, audio=muffled` для обратной совместимости; "Общая сцена"
   всегда `visual=full, audio=full`. Подтвердить.

---

## 14. Definition of Done

- Все семнадцать инвариантов И1–И17 выполняются в проде; чек-лист
  "запрещённых путей" (§1 v2 + дополнения §1 этого плана) соблюдён — аудит
  подтверждён (Фаза 8).
- `PerceptionResult` — единственный источник и для генерации, и для evidence
  отношений, и для наблюдаемости памяти; двумерность (И13) без исключений.
- Действия из LLM — только через tools/structured outputs; ни одного
  regex-парсинга JSON в production (И14).
- Ни один системный/пользовательский промпт не содержит "ты не знаешь X" —
  только отсутствие или деградированная форма X по каждому каналу (И11).
- Приоритетные события рендерятся в хвост промпта (И15) — проверено тестом
  #20; молчаливые действия нарративизируются без ретраев (И16) — #16.
- Event Bus (И17) работает: звонок/обращение будит NPC, один ответ за раунд,
  зацикливаний нет — #21.
- Полный golden-набор §11 (22 сценария) стабильно проходит в CI.
- `docs/architecture.md`/`README.md` описывают новую модель.

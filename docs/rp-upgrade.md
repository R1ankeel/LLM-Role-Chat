# План улучшений AI Roleplay Chat Engine

> Дата анализа: 2026-07-28  
> **Последнее обновление статуса:** 2026-07-29 — проверка кода: P0 witness model ✅; P1 Chat API, BM25, structured extraction + validation, negative prompting ✅  
> Цель: улучшить удержание памяти, характеров, фактов и разговорного стиля персонажей при сохранении существующей системы изоляции ролей.

---

## Статус P0 (выполнено)

| Компонент | Статус | Файлы |
|-----------|--------|-------|
| Расширение схемы Character (6 полей) | ✅ | `models.py`, `schemas.py`, `database.ensure_schema` |
| Промпт-билдер с XML-структурой | ✅ | `prompt_builder.py` |
| Шаблоны строк промптов | ✅ | `prompts/ru.json` |
| Per-character temperature в генерации | ✅ | `ollama_client.generate` |
| Расширенная карточка в UI | ✅ | `static/index.html`, `static/app.js`, `static/style.css` |
| Тесты prompt builder | ✅ | `tests/test_prompt_builder.py` (10 тестов) |

### P0 «Многоуровневая память» ✅

| Компонент | Статус | Файлы |
|-----------|--------|-------|
| Уровень 1: рабочая история в `<recent_dialogue>` | ✅ | `prompt_builder.build_recent_dialogue_block`, `ollama_client.generate` |
| Уровень 2: эпизодические факты в `<character_memories>` | ✅ | `models.Memory`, `prompt_builder.build_memories_block` |
| Уровень 3: per-character сводка (`CharacterSummary`) | ✅ | `models.CharacterSummary`, `database.ensure_schema` |
| Фоновая суммаризация (каждые 20 сообщений) | ✅ | `memory_service.py`, `ollama_client.summarize_for_character` |
| Post-round orchestration (extraction + summary) | ✅ | `memory_service.process_post_round`, `chat_engine.py` |
| Конфиг: `RECENT_MEMORIES_FOR_PROMPT`, `SUMMARY_INTERVAL_MESSAGES` | ✅ | `config.py` |
| API просмотра сводки | ✅ | `GET /api/characters/{id}/summary` |
| Сброс сводок при очистке истории | ✅ | `crud.clear_chat_messages` → `reset_character_summaries_for_chat` |
| Тесты memory service + prompt blocks | ✅ | `tests/test_memory_service.py` (4 теста), обновлены `test_chat_engine.py`, `test_prompt_builder.py` |

### P0 «Witness model (фильтрация истории по присутствию)» ✅

| Компонент | Статус | Файлы |
|-----------|--------|-------|
| Модель `MessagePresence` + миграция | ✅ | `models.py`, `database.ensure_schema` |
| MVP-эвристики присутствия | ✅ | `witness_model.compute_mvp_presence` |
| Witness-aware фильтрация истории | ✅ | `witness_model.filter_history_for_character`, `ollama_client.format_history_for_character` |
| Сохранение presence после раунда | ✅ | `crud.compute_and_save_presence_for_round`, `chat_engine.py` |
| Шаблоны witness в локализации | ✅ | `prompts/ru.json` → `witness` |
| Конфиг `ENABLE_WITNESS_FILTER` | ✅ | `config.py` |
| Тесты witness filter | ✅ | `tests/test_witness_filter.py` (7 тестов) |

### P1 (выполнено частично или полностью)

| Компонент | Статус | Файлы |
|-----------|--------|-------|
| Миграция на Ollama `/api/chat` + feature flag | ✅ | `config.USE_CHAT_API`, `ollama_client._call_ollama_chat`, `_build_generation_messages` |
| Релевантный отбор памяти (BM25 MVP) | ✅ | `memory_service.SimpleBM25`, `crud.get_memories_for_prompt`, `config.ENABLE_RELEVANT_MEMORY_SELECTION` |
| Поля `importance` / `category` у Memory | ✅ | `models.Memory`, extraction → `crud.create_memory` |
| Structured JSON extraction + validation | ✅ | `schemas.ExtractedFact`, `memory_service.validate_extracted_facts`, `prompts/ru.json` → `extraction` |
| Near-dup dedup (Jaccard при extraction) | ✅ | `memory_service.jaccard_similarity`, `MEMORY_NEAR_DUP_JACCARD` |
| Eviction по importance | ✅ | `crud._delete_lowest_value_memories` |
| Negative prompting (6.2) | ✅ | `prompt_builder.build_negative_prompting_block`, `prompts/ru.json` → `negative` |
| Сокращение post-history reinforcement (3.3) | ✅ | `prompt_builder.build_reinforcement_block`, `prompts/ru.json` → `reinforcement` |
| Исправление generation cue для Chat API (3.4) | ✅ | `role_isolation.build_generation_cue_for_chat` |
| Локализация extraction / summary / witness / reinforcement | ✅ | `prompt_builder.build_extraction_*`, `build_summary_*`, `prompts/ru.json` |
| Тесты Chat API | ✅ | `tests/test_ollama_chat.py` (7 тестов) |
| Тесты extraction / validation | ✅ | `tests/test_memory_service.py` (13 тестов) |

**Осталось (не реализовано или частично):** context budget manager; полный manual memory CRUD (add/edit в API и UI); опции clear history (сообщения / память / summaries); anti-mimicry для 2+ персонажей; semantic regex hard/soft; per-character `min_length`; полная локализация isolation block (`ru.json` → `isolation` есть, но `build_role_isolation_block` пока inline); `last_accessed_at` / `source_message_ids` у Memory; consolidation job; token streaming в UI; pydantic-settings; eval harness; README про isolation.

---

## 1. Текущее состояние

### Архитектура

```
Браузер (SPA) → FastAPI → chat_engine → memory_service + witness_model + ollama_client + role_isolation → SQLite + Ollama
```

**Сильные стороны (сохранять при рефакторинге):**

- Многослойная изоляция ролей: промпты → stop-токены → санитизация → regex-проверка перспективы → retry → fallback (`role_isolation.py`, `ollama_client.generate`)
- Последовательная генерация персонажей в раунде — поздние видят ответы ранних в том же ходе (`chat_engine.process_user_message_streaming`)
- Память изолирована по персонажам с дедупликацией по hash (`models.Memory`, `crud.create_memory`)
- Post-history reinforcement против «перекрытия» инструкций историей (`build_reinforcement_block`)
- Witness-aware history filtering — персонажи не видят чужие сцены (`witness_model.py`, `MessagePresence`)
- Релевантный отбор памяти BM25 + structured extraction с validation (`memory_service.py`, `config.ENABLE_RELEVANT_MEMORY_SELECTION`)
- Ollama Chat API с role-based messages (`config.USE_CHAT_API`, `ollama_client._call_ollama_chat`)
- Фоновое извлечение памяти и суммаризация через snapshots — без багов detached SQLAlchemy session (`memory_service.process_post_round`)
- SSE с сохранением генерации при обрыве клиента

**Ключевые ограничения:**

| Область | Проблема | Где в коде |
|---------|----------|------------|
| Промпт | ~~Один плоский текст через `/api/generate`~~ ✅ Chat API по умолчанию (`USE_CHAT_API=True`); legacy generate — fallback | `ollama_client._call_ollama_chat`, `_build_generation_messages` |
| Персонаж | ~~Только `name`, `personality`, `traits`~~ ✅ Добавлены `speech_style`, `example_messages`, `boundaries`, `background`, `relationships`, `temperature` | `models.Character`, `prompt_builder.build_system_prompt` |
| Память | ~~Последние N фактов без релевантности; нет суммаризации истории~~ ✅ Трёхуровневая модель + BM25-отбор (`ENABLE_RELEVANT_MEMORY_SELECTION`); поля `importance`/`category` | `memory_service.py`, `crud.get_memories_for_prompt`, `config.py` |
| Контекст | ~~Все персонажи видят всю историю~~ ✅ Witness-filtered history (`ENABLE_WITNESS_FILTER`, `MessagePresence`); fallback — текстовая заметка | `witness_model.py`, `ollama_client.format_history_for_character` |
| Стиль | ~~Нет few-shot примеров речи~~ ✅ Few-shot через `example_messages` в `<examples>`; thinking по-прежнему отбрасывается | `prompt_builder`, `ENABLE_THINKING` |
| История | ~~Обрезка по `max_history_length` (30) — старые события теряются навсегда~~ ⚠️ Старые события сохраняются в per-character summary + episodic Memory; полная история в БД | `crud.get_messages_by_chat`, `CharacterSummary` |

---

## 2. Приоритеты

| Приоритет | Направление | Эффект | Сложность |
|-----------|-------------|--------|-----------|
| P0 | Расширение карточки персонажа + промпт-шаблоны | Характер, стиль речи | Низкая | ✅ **Сделано** |
| P0 | Многоуровневая память (краткая + долгая + суммаризация) | Факты, связность сюжета | Средняя | ✅ **Сделано** |
| P0 | Фильтрация истории по присутствию (witness model) | Изоляция знаний | Средняя | ✅ **Сделано** |
| P1 | Миграция на Ollama `/api/chat` | Качество следования инструкциям | Средняя | ✅ **Сделано** |
| P1 | Релевантный отбор памяти | Точность фактов в промпте | Средняя | ✅ **Сделано** (BM25 MVP) |
| P1 | Улучшение extraction + валидация фактов | Качество памяти | Низкая | ✅ **Сделано** (structured JSON, validation, near-dup) |
| P2 | Стриминг токенов в UI | UX, ощущение «живого» диалога | Средняя |
| P2 | Рефакторинг архитектуры (слои, конфиг) | Поддерживаемость | Средняя |
| P3 | Векторный поиск / embeddings | Длинные кампании | Высокая |

---

## 3. Промпты и карточки персонажей

### 3.1. Расширить схему Character ✅

**Было:** `name`, `personality`, `traits`, `order_index`.

**Добавлено (реализовано):**

```python
speech_style: str       # «короткие фразы, сарказм, обращается на «ты»»
example_messages: str   # 2–3 примера реплик персонажа (few-shot)
boundaries: str         # «не выходит из роли торговца, не знает магии»
background: str         # предыстория, не дублирующая personality
relationships: str      # отношения к другим персонажам и игроку
temperature: float | None # override температуры (None = из чата)
```

**Файлы:** `models.py`, `schemas.py`, миграция в `database.ensure_schema`, UI в `static/index.html`, `static/app.js`.

### 3.2. Перестроить `_build_system_prompt` ✅

**Было** (`ollama_client._build_system_prompt`, удалено):

```
Ты — {name}. → personality → traits → Сюжет → «от первого лица» → isolation block
```

**Реализованная структура** (`prompt_builder.build_system_prompt`):

```
<character>
  <identity>{name}</identity>
  <personality>...</personality>
  <traits>...</traits>
  <background>...</background>
  <speech_style>...</speech_style>
  <relationships>...</relationships>
  <boundaries>...</boundaries>
</character>

<scene>{general_prompt}</scene>

<examples>
  Пример речи персонажа:
  «...»
  «...»
</examples>

<rules>
  - Отвечай от первого лица, в разговорном стиле, без мета-комментариев.
  - Длина ответа: 2–6 предложений (настраиваемо).
  - Используй *курсив* для действий, «кавычки» для прямой речи (если принято в чате).
</rules>

{build_role_isolation_block}
```

**Отдельный модуль:** `prompt_builder.py` ✅ — сборка system-блока; `ollama_client.py` вызывает `build_system_prompt()`.

### 3.3. Разделить «характер пользователя» и «технические ограничения движка» ✅

Сейчас `build_role_isolation_block` смешан с character card. Рекомендация:

- **Character card** — только от пользователя (личность, стиль, примеры). ✅ Реализовано в `<character>`, `<scene>`, `<examples>`, `<rules>`.
- **Engine constraints** — отдельный immutable блок в конце system (изоляция, stop-правила). ✅ `build_role_isolation_block` вызывается после `<rules>`.
- **Post-history reinforcement** — оставить, но сократить до 3–4 строк (сейчас дублирует isolation block). ✅ `build_reinforcement_block` из `prompts/ru.json` → `reinforcement`.

### 3.4. Убрать противоречие generation cue ✅ (Chat API)

`build_generation_cue` (legacy `/api/generate`) по-прежнему заканчивается на `{name}:` + `strip_current_character_prefix`.

**Для Chat API реализовано:** `build_generation_cue_for_chat` — cue без префикса имени, «начни сразу с реплики или действия» (`role_isolation.py`, `prompts/ru.json` → `generation_cue`).

### 3.5. Локализация и шаблоны ⚠️ частично

Вынести все строки промптов в `prompts/ru.yaml` (или `.json`) с плейсхолдерами. ✅ Реализовано `prompts/ru.json` для identity, scene, examples, rules, memory, witness, negative, reinforcement, extraction, summary, generation_cue. ⚠️ Блок isolation (`build_role_isolation_block`) — шаблоны в `ru.json` → `isolation`, но код пока inline в `role_isolation.py`.

---

## 4. Память и удержание фактов

### 4.1. Трёхуровневая модель памяти ✅

```
┌─────────────────────────────────────────────────────────┐
│  Уровень 1: Рабочая история (последние N сообщений)   │  ← max_history_length=30, блок `<recent_dialogue>`
├─────────────────────────────────────────────────────────┤
│  Уровень 2: Эпизодическая память (факты, 20 шт.)      │  ← models.Memory, extraction, блок `<character_memories>`
├─────────────────────────────────────────────────────────┤
│  Уровень 3: Сводка сессии / сцены (1–3 абзаца)        │  ← models.CharacterSummary, блок `<character_summary>`
└─────────────────────────────────────────────────────────┘
```

**Уровень 3 — CharacterSummary (реализовано):**

- Таблица `character_summaries` — одна сводка на персонажа (upsert по `character_id`).
- Watermark `through_message_id` — последний обработанный message id.
- Триггер: каждые `SUMMARY_INTERVAL_MESSAGES` (20) новых сообщений — фоновый LLM-вызов (`memory_service._maybe_update_summaries`).
- Промпт суммаризации: только наблюдаемое, ключевые события / отношения / нерешённые сюжетные линии (`ollama_client.summarize_for_character`).

**В промпт генерации (реализовано):**

```
<character_summary>...</character_summary>
<character_memories>...</character_memories>
<recent_dialogue>...</recent_dialogue>
```

**Файлы:** `models.py`, `schemas.py`, `database.ensure_schema`, `crud.py`, `memory_service.py`, `prompt_builder.py`, `ollama_client.generate`, `chat_engine.py`, `routers/characters.py`.

### 4.2. Релевантный отбор памяти (вместо «последние 10») ✅ (BM25 MVP)

**Было:** `get_memories_for_characters(..., limit=10)` — только recency.

**Реализовано:** `memory_service.SimpleBM25` + `crud.get_memories_for_prompt` — top-K по релевантности к контексту последних сообщений (`ENABLE_RELEVANT_MEMORY_SELECTION`, `MEMORY_RELEVANCE_TOP_K=5`).

**Новые поля Memory (реализовано частично):**

```python
importance: float         # 0.0–1.0, задаётся при extraction ✅
category: str             # "relationship" | "event" | "location" | "item" | "other" ✅
last_accessed_at: datetime  # ❌ не реализовано
source_message_ids: str   # JSON, откуда извлечён факт — ❌ не реализовано
```

### 4.3. Улучшить extraction ⚠️ частично (P1 core — ✅)

**Проблемы текущего `extract_memories_for_character`:**

- Анализирует только текущий раунд (`round_text`), не весь контекст. ⚠️ По-прежнему.
- ~~Нет проверки качества извлечённых фактов.~~ ✅ `memory_service.validate_extracted_fact(s)`.
- ~~Нет слияния дубликатов по смыслу (только exact hash).~~ ⚠️ Near-dup через Jaccard при extraction; периодический consolidation job — ❌.

**Улучшения (P0, частично):** дескриптор персонажа в extraction расширен через `format_character_descriptor()` — включает `background`, `speech_style`, `boundaries`, `relationships`. ✅ Snapshots персонажа в post-round передают полную карточку (`memory_service._character_from_snapshot`).

**Улучшения:**

1. **Structured output** — явная JSON-схема: ✅ `schemas.ExtractedFact`, промпт в `prompts/ru.json` → `extraction`.
2. **Post-extraction validation** — ✅ rule-based фильтр в `memory_service.validate_extracted_fact`.
3. **Consolidation job** — ❌ периодическое объединение похожих фактов (только inline Jaccard-dedup).
4. **Manual CRUD** — ⚠️ API: GET list + DELETE (`routers/characters.py`); UI: просмотр + удаление (`static/app.js` → `renderMemoriesTab`); add/edit — ❌.

### 4.4. Согласованность «очистить историю» vs память ⚠️ частично

**Было:** `clear_chat_messages` удаляет сообщения, память остаётся.

**Реализовано:** при очистке истории сводки (`CharacterSummary`) сбрасываются вместе с сообщениями (`crud.reset_character_summaries_for_chat`). Эпизодическая память (`Memory`) по-прежнему сохраняется.

**Осталось:** опции в API/UI:

- Только сообщения (+ summaries ✅)
- Сообщения + episodic memory
- Полный reset (включая summaries и memories)

### 4.5. World state (опционально, P2)

Отдельная сущность `SceneState` — JSON с текущей локацией, временем суток, присутствующими NPC. Обновляется системным вызовом после раунда. Помогает удерживать «где мы» без раздувания истории.

---

## 5. Изоляция ролей и модель присутствия

### 5.1. Witness-aware history filtering ✅

**Было:** `format_history_for_character` отдаёт всю историю + текстовую заметку «ты видишь только...».

**Реализовано:**

- Модель `MessagePresence` (`models.py`) + CRUD upsert (`crud.upsert_message_presence`, `compute_and_save_presence_for_round`).
- MVP-эвристики: `witness_model.compute_mvp_presence` (user/system → present; автор → present; same round → present; упоминание имени → mentioned; иначе → absent).
- Фильтрация: `witness_model.filter_history_for_character` → `ollama_client.format_history_for_character` (при `ENABLE_WITNESS_FILTER=True`).
- Шаблоны mentioned/told: `prompts/ru.json` → `witness`.
- Тесты: `tests/test_witness_filter.py`.

**Осталось (V2/V3):** LLM-classifier presence; UI выбора присутствующих; presence `told` из эвристик.

### 5.2. Смягчить semantic regex

`contains_perspective_violation` даёт false positives (например, «он улыбнулся» — наблюдаемое действие, не мысль).

**Улучшения:**

- Разделить паттерны на **hard block** (мысли/решения других) и **soft warn** (наблюдаемые действия).
- Soft — логировать, не retry; hard — retry.
- Whitelist: «{Name} улыбнулся», «{Name} кивнул» — разрешённые наблюдаемые действия.
- Тесты на корпус типичных RP-реплик (`tests/test_role_isolation.py` расширить).

### 5.3. Anti-mimicry для последовательной генерации

Поздние персонажи в раунде могут копировать стиль/содержание ранних.

**В промпт для 2+ персонажа в раунде добавить:**

```
Другие персонажи уже ответили в этом ходе. Не повторяй их реплики.
Ответь со своей уникальной перспективы и в своём стиле речи.
```

**Опционально:** передавать только краткое summary ответов других, а не полный текст (если персонаж «не слышал»).

### 5.4. Гибкая длина ответа

`MIN_CHARACTER_RESPONSE_LENGTH = 10` отвергает короткие, но валидные реакции («— Нет.»).

- Сделать min_length настраиваемым per chat или per character.
- Fallback уже использует `min_length=3` — унифицировать логику.

---

## 6. Разговорный стиль и качество отыгрыша

### 6.1. Few-shot examples в промпте ✅

Поле `example_messages` — 2–3 эталонных реплики. Реализовано в `prompt_builder.build_examples_block()`.

**Формат в промпте:**

```
Примеры твоей речи (подражай этому стилю, не копируй дословно):
---
{example_1}
---
{example_2}
```

### 6.2. Negative prompting для стиля ✅

Реализовано в `prompt_builder.build_negative_prompting_block()` и внутри `<rules>` → `<negative>`; строки в `prompts/ru.json` → `negative`. Подключается в Chat API user message (`ollama_client._build_generation_messages`).

### 6.3. Использовать thinking для self-check (не для UI)

**Сейчас:** thinking генерируется и отбрасывается.

**Вариант:** в thinking-блоке модель проверяет:
- «Я отвечаю только за {name}?»
- «Стиль соответствует примерам?»
- «Нет ли знаний, которых у меня не могло быть?»

Финальный `response` — после internal check. Не показывать thinking пользователю.

### 6.4. Per-character temperature ✅

- Эмоциональные/импульсивные персонажи: 0.85–0.95
- Сдержанные/формальные: 0.6–0.75
- Хранить в `Character.temperature`, fallback на chat-level default. ✅ Поле в БД/UI; `ollama_client._character_temperature()` в `generate()`.

### 6.5. System messages для сценических указаний

Роль `system` в `models.Message` поддерживается, но не используется в flow.

**Применение:**

- «[Система: Наступила ночь, в таверне шумно]»
- «[Система: {Name} вышел из комнаты]»

UI: кнопка «Сценическое указание» в настройках или `/sys` префикс в поле ввода.

---

## 7. Архитектура и инфраструктура

### 7.1. Миграция на Ollama Chat API ✅

**Было:** один `prompt` string в `/api/generate`.

**Реализовано:**

- `config.USE_CHAT_API = True` (fallback на `/api/generate` при `False`).
- `ollama_client._call_ollama_chat`, `_build_generation_messages`, `_invoke_llm`.
- Структура messages: system (card + isolation) + user (summary + memories + dialogue + reinforcement + negative + cue).
- Тесты: `tests/test_ollama_chat.py`.

### 7.2. Модульная структура

```
ai-roleplay-chat/
├── chat_engine.py          # оркестрация раундов
├── memory_service.py       # ✅ extraction + summarization + BM25 + validation (post-round)
├── witness_model.py        # ✅ witness-aware history filtering (P0)
├── ollama_client.py        # ✅ /api/chat + /api/generate
├── prompt_builder.py
├── role_isolation.py
├── prompts/
│   └── ru.json             # ✅ (ru.yaml — не использовался)
├── config.py               # ✅ RECENT_MEMORIES_FOR_PROMPT, SUMMARY_*, USE_CHAT_API, WITNESS_*, BM25_*
└── ...
```

### 7.3. Конфигурация через environment

Вынести в `.env` / `config.py`:

| Параметр | Сейчас |
|----------|--------|
| `OLLAMA_BASE_URL` | hardcoded `localhost:11434` |
| `RECENT_MEMORIES_FOR_PROMPT` | ✅ `config.py` (= 10) |
| `DEFAULT_TEMPERATURE` | hardcoded 0.8 в `ollama_client` |
| `RATE_LIMIT_SECONDS` | в `ratelimit.py` |
| `SUMMARY_INTERVAL_MESSAGES` | ✅ `config.py` (= 20) |
| `SUMMARY_MAX_PARAGRAPHS` | ✅ `config.py` (= 3) |
| `USE_CHAT_API` | ✅ `config.py` (= True) |
| `ENABLE_WITNESS_FILTER` | ✅ `config.py` (= True) |
| `ENABLE_RELEVANT_MEMORY_SELECTION` | ✅ `config.py` (= True) |

Использовать `pydantic-settings` для type-safe config.

### 7.4. Token budget manager

Новый компонент `ContextBudget`:

1. Зарезервировать tokens для system + isolation (~fixed).
2. Выделить budget на memories (top-K by relevance).
3. Остальное — recent history (с конца).
4. Если не влезает — подключить summary вместо старых сообщений.

Оценка tokens: `len(text) // 3` для русского (MVP) или tiktoken/модельный tokenizer.

### 7.5. Транзакции и надёжность

- Batch commit сообщений раунда в одной транзакции (сейчас commit на каждое сообщение).
- Task queue для memory extraction с retry и статусом (вместо bare `asyncio.create_task`).
- Логирование prompt/response hashes для отладки (opt-in, без PII в prod).

---

## 8. Frontend и UX

### 8.1. Расширенная карточка персонажа ✅

Форма с полями: speech style, examples, boundaries, background, relationships, temperature. Реализовано в модалке `#modal-character`.

### 8.2. Управление памятью ⚠️ частично

- ~~Добавить / редактировать факт вручную.~~ ❌ add/edit не реализовано (`MemoryUpdate` в `schemas.py` есть, API — нет).
- ~~Показать category и importance.~~ ❌ в UI не отображаются (только content).
- Просмотр и удаление фактов. ✅ `GET /characters/{id}/memories`, `DELETE /memories/{id}`, вкладка «Память» в UI.
- Кнопка «Пересобрать память из истории» (batch re-extraction). ❌

### 8.3. Сценические инструменты

- System message input.
- Выбор «кто в сцене» (для witness model MVP).
- «Перегенерировать ответ» для последнего сообщения персонажа.

### 8.4. Token streaming (P2)

Прокинуть stream chunks через SSE (`type: "token"`) для ощущения живого диалога. Thinking по-прежнему скрыт.

### 8.5. Согласовать лимиты UI и backend

UI загружает 50 сообщений (`ChatDetail`), промпт — `max_history_length`. Показывать пользователю, сколько сообщений реально попадает в контекст модели.

---

## 9. Тестирование

### 9.1. Добавить

| Тест | Что проверяет | Статус |
|------|---------------|--------|
| `test_prompt_builder.py` | Структура промпта, few-shot, XML-секции (в т.ч. memory blocks) | ✅ (10 тестов) |
| `test_memory_service.py` | Суммаризация, watermark, clear reset, extraction/validation, eviction | ✅ (13 тестов; BM25 rank — ❌ отдельных тестов нет) |
| `test_witness_filter.py` | Фильтрация истории по presence, CRUD persistence | ✅ (7 тестов) |
| `test_ollama_chat.py` | Chat API messages, negative prompting, fallback | ✅ (7 тестов) |
| `test_role_isolation.py` | Изоляция, sanitization, semantic regex | ✅ (20 тестов; 2 failing на момент проверки) |
| `test_chat_engine.py` | Sequential generation, memory isolation | ✅ (4 теста) |
| `test_api_routers.py` | FastAPI TestClient, SSE contract | ❌ |
| `test_context_budget.py` | Укладка в token limit | ❌ |
| Golden-file tests | Snapshot промптов для регрессии | ❌ |

### 9.2. Evaluation harness (offline)

Скрипт `scripts/eval_rp.py`:

- Фиксированные сценарии (multi-char, memory recall, style consistency).
- Метрики: isolation violation rate, fact recall@5, style similarity to examples.
- Прогон против реальной Ollama в CI (optional nightly job).

---

## 10. Дорожная карта

### Фаза 1 — Quick wins (1–2 недели)

- [x] Расширить Character: `speech_style`, `example_messages`, `boundaries` (+ `background`, `relationships`, `temperature`)
- [x] Перестроить `_build_system_prompt` + вынести в `prompt_builder.py`
- [x] Улучшить extraction: structured JSON, validation rules
- [ ] Согласовать clear history + память (опции в API/UI)
- [ ] Смягчить `MIN_CHARACTER_RESPONSE_LENGTH` / per-character override
- [ ] Anti-mimicry блок для 2+ персонажей в раунде
- [ ] Документировать isolation architecture в README

### Фаза 2 — Память и контекст (2–4 недели)

- [x] CharacterSummary + фоновая суммаризация (per-character, incremental)
- [x] XML-блоки памяти в промпте (`<character_summary>`, `<character_memories>`, `<recent_dialogue>`)
- [x] `memory_service.py` — post-round extraction + summarization
- [x] Сброс summaries при clear history
- [x] Релевантный отбор памяти (BM25 MVP)
- [x] Memory fields: importance, category
- [ ] Context budget manager
- [ ] Manual memory CRUD в API и UI (add/edit; view/delete ✅)
- [ ] Consolidation похожих фактов (periodic job; inline Jaccard-dedup ✅)

### Фаза 3 — Witness model и Chat API (3–5 недель)

- [x] MessagePresence (MVP: эвристики)
- [x] Witness-aware `format_history_for_character`
- [ ] Refine semantic regex (hard/soft)
- [x] Миграция на `/api/chat` с feature flag
- [x] Per-character temperature *(перенесено из Фазы 3, реализовано в P0)*

### Фаза 4 — Polish и scale (по необходимости)

- [ ] Token streaming в UI
- [ ] Embeddings для memory retrieval
- [ ] SceneState / world tracking
- [ ] Regenerate message
- [ ] pydantic-settings, task queue для memory jobs
- [ ] Eval harness

---

## 11. Метрики успеха

| Метрика | Как измерять | Целевое улучшение |
|---------|--------------|-------------------|
| Isolation violation rate | % ответов с foreign marker или regex fail | −30% retry rate |
| Fact recall | Eval: «что персонаж знает о X» через 50+ сообщений | >80% на scripted scenarios |
| Style consistency | Embedding similarity к example_messages | >0.7 cosine |
| User-visible silence | % раундов с `*[молчит]*` placeholder | <2% |
| Prompt token efficiency | Средний prompt_len при том же качестве | −15% после budget manager |

---

## 12. Риски и mitigations

| Риск | Mitigation |
|------|------------|
| Раздувание промпта (summary + memories + history + examples) | Context budget manager, жёсткие лимиты на секции |
| Witness model ошибается — персонаж «не знает» очевидного | Fallback: user override presence; conservative default = present |
| Regex soft/hard путает наблюдение и telepathy | Тесты на корпус; постепенный rollout с логированием |
| Chat API ломает совместимость со старыми моделями | Feature flag; fallback на `/api/generate` |
| Summarization теряет детали | Episodic Memory хранит факты параллельно со summary (✅); structured summary schema — в планах |

---

## 13. Связанные артефакты

- `role_isolation.py` — ядро изоляции, не ломать при рефакторинге
- `memory_service.py` — post-round extraction, summarization, BM25, validation (P0 + P1)
- `witness_model.py` — witness-aware history filtering (P0)
- `prompt_builder.py`, `prompts/ru.json` — сборка system prompt и memory blocks (P0 + P1)
- `tests/test_memory_service.py`, `tests/test_chat_engine.py`, `tests/test_prompt_builder.py`, `tests/test_witness_filter.py`, `tests/test_ollama_chat.py` — regression

---

## 14. Резюме

Проект уже имеет **зрелую инженерную защиту от semantic contamination** — это редкость для прототипов такого размера. Главный пробел — не изоляция, а **качество персонализации и долгосрочного контекста**:

1. **Богатые карточки персонажей** с примерами речи дадут наибольший прирост «характера» при минимальных затратах. ✅ **Реализовано (P0).**
2. **Многоуровневая память** (summary + episodic facts + recent dialogue) закрывает провалы в фактах и знаниях. ✅ **Реализовано (P0 + P1):** per-character summary, XML-блоки, фоновая суммаризация, BM25-отбор, structured extraction. ⚠️ Осталось: context budget, full memory CRUD.
3. **Witness model** — персонажи больше не видят всю историю целиком. ✅ **Реализовано (P0):** `MessagePresence`, MVP-эвристики, фильтрация в промпте.
4. **Chat API + prompt builder** улучшают следование инструкциям без потери isolation pipeline. ✅ **Реализовано (P1):** `USE_CHAT_API`, role-based messages, negative prompting.

Рекомендуется далее: context budget manager, anti-mimicry, опции clear history, golden-тесты промптов.

# План реализации: Token-Aware Context Builder

> Источник требований: `Token-Aware Context Builder.docx`
> Директория кода: `ai-roleplay-chat/`
> Статус: **план** (код не изменялся)

---

## 1. Карта текущего pipeline (до изменений)

Составлена по коду. Здесь фиксируется «точка отсчёта», от которой отталкивается реализация.

```
POST /api/chats/{id}/message  (routers/chat_engine.py)
  └─ chat_engine.process_user_message_streaming()   [chat_engine.py:231]
       1. chat = crud.get_chat(); history_limit = chat.max_history_length (default 30)   [:249]
       2. pre_round_messages = crud.get_messages_by_chat(db, chat_id, history_limit)     [:254]
            → история ОБРЕЗАЕТСЯ до последних N сообщений ДО retrieval
       3. create user_message; compute_and_save_presence_for_message                     [:259, :289]
       4. context_text = user_text + последние 3 сообщения                                [:296]
       5. Retrieval memories ОДИН раз на весь раунд, top_k = memory_relevance_top_k (5):
            - embedding_enabled → crud.get_hybrid_memories_for_characters (BM25+Vector+RRF)  [:315]
            - else → crud.get_relevant_memories_for_characters (BM25)                        [:324]
       6. context_messages = pre_round + round; обрезка до history_limit                    [:332]
       7. scene_state = crud.get_scene_state_with_presence                                  [:337]
       8. relationships_block (один раз на персонажа) → relationship_service.build_relationships_block
       9. Для КАЖДОГО NPC последовательно (multi-character round):
            - presence_map = crud.get_presence_map(история, character.id)                  [:369]
            - ollama_client.generate(..., messages_history=context_messages,
              memories=..., summary=..., viewer_character_id=..., ...)                     [:385]
       10. post-round: extract_scene_state (LLM), relationships analysis (background),
           stagnation tracking, process_post_round (memory+summary, background)
```

**Генерация** (`ollama_client.py`):
- `generate()` [:1020] → цикл ретраев (isolation/repetition) → `_generate_once()` [:675]
- `_generate_once()` собирает блоки:
  - system prompt: `build_system_prompt()` = character card + scene + examples + relationships + rules + isolation (prompt_builder.py:262)
  - `format_history_for_character()` → `filter_history_for_character()` (witness filtering, witness_model.py:175) → `<recent_dialogue>`
  - `<character_summary>` (`build_character_summary_block`), `<character_memories>` (`build_memories_block`)
  - personality / consistency / reinforcement / vocabulary / scene_advancement / isolated / repetition_feedback
  - chat API: system + user message; generate API: единый prompt
  - `prompt_len = sum(len(msg["content"]) ...)` — это **символы, не токены** [:799]
- `_build_generate_payload` [:287] / `_build_chat_payload` [:311] — `options` содержат только `temperature`/`stop`, **`num_ctx` не передаётся**.

**Retrieval** (`crud.py` + `memory_service.py`):
- `get_hybrid_memories_for_characters` [:657]: candidate_limit = top_k*8, BM25 (`memory_service.select_relevant_memories`, SimpleBM25 [:177]) + vector (embedding_service) + RRF; witness-filter по `source_message_ids` через `filter_memories_by_witness` [:431].
- Retrieval **по историческим сообщениям (не memories) отсутствует** — искать события за пределами recent history сейчас нечем.

**Данные** (`models.py`):
- `Chat.max_history_length` (30) — единственное ограничение истории.
- `CharacterSummary` — `content` + `through_message_id`; обновляется в фоне `_maybe_update_summaries` (memory_service.py:737), интервал `summary_interval_messages`=20.
- `MessagePresence` — per-character witness (present/told/mentioned/absent) на каждое сообщение.
- `SceneState` — `time_of_day`, `character_locations`, `custom_state`.
- `CharacterRelationship` + `RelationshipEvent`; блок — `relationship_service.build_relationships_block` [:252].

**Тесты**: `tests/` — unit/integration/golden/eval. Тесты `test_chat_engine.py` мокают `chat_engine.ollama_client.generate(**kwargs)` через `kwargs["character"]`, `kwargs["messages_history"]`, `kwargs["memories"]`, `kwargs["summary"]`, `kwargs["viewer_character_id"]` — сигнатуру `generate()` ломать нельзя.

---

## 2. Целевая архитектура

```
                   FULL CHAT HISTORY (загрузка окна, напр. до N=2000 сообщений)
                                   │
                          CHARACTER KNOWLEDGE FILTER (witness / presence)
                                   │
            ┌──────────────────────┴───────────────────────┐
            ▼                                              ▼
   RETRIEVAL PIPELINE                            RECENT GENERATION HISTORY
   (BM25 + Vector + RRF)                         (после summary frontier)
            │                                              │
            ▼                                              ▼
   Relevant historical events                      recent dialogue
   (token-aware selection)                                │
            │                                              │
            └──────────────────┬───────────────────────────┘
                               ▼
            CONTEXT BUILDER (ContextBuilder.build, per-character)
            - ContextBudget (приоритеты P0..P4, reserve)
            - token counting (exact / estimated)
            - summary frontier (CharacterSummary.through_message_id)
            - дедупликация summary/memories/events
                               ▼
                    CHARACTER-SPECIFIC PROMPT (BuiltContext)
                               ▼
                        ollama_client.generate → Ollama (num_ctx)
```

Ключевое отличие от текущего кода: история **не обрезается до 30 сообщений до retrieval**. Retrieval работает по широкому окну полной истории (в пределах knowledge/perception boundary), а в генерацию уходит только собранный в пределах token budget контекст.

---

## 3. Новые настройки (config.py + .env.example)

Стиль именования — как в существующем `Settings` (SNAKE_CASE alias).

| Настройка | Default | Назначение |
|---|---|---|
| `MAX_CONTEXT_TOKENS` (`max_context_tokens`) | `60000` | Верхняя граница контекста для ContextBuilder |
| `CONTEXT_RECENT_MIN_TOKENS` | `8000` | Soft target под recent dialogue (не жёсткая гарантия — см. §5) |
| `CONTEXT_RECENT_MAX_TOKENS` | `40000` | Максимум под recent dialogue |
| `CONTEXT_MEMORY_BUDGET` | `5000` | Бюджет `<character_memories>` |
| `CONTEXT_RETRIEVAL_BUDGET` | `5000` | Бюджет retrieved historical events |
| `CONTEXT_SUMMARY_BUDGET` | `4000` | Бюджет `<character_summary>` |
| `CONTEXT_STATE_BUDGET` | `3000` | Бюджет SceneState + location + relationships |
| `CONTEXT_RESERVE_TOKENS` | `3000` | Резерв под форматирование/вывод (не заполняется текстом) |
| `TOKEN_COUNT_MODE` | `estimated` | `estimated` / `exact` (см. раздел 5) |
| `CONTEXT_ENABLED` | `true` | Глобальный включатель; `false` → прежняя логика по `max_history_length` (backward compat) |
| `CONTEXT_HISTORY_LOAD_CAP` | `2000` | Safety cap загрузки сообщений для retrieval (защита от чтения всей истории в память). **Не семантическая граница** — не означает, что события старше cap нерелевантны (см. §7.1, §16) |
| `CONTEXT_RETRIEVAL_CANDIDATES` | `30` | Кандидатов событий до token-aware отбора |
| `CONTEXT_MESSAGE_EMBEDDING_ENABLED` | `false` | Фоновая эмбеддинг-индексация сообщений для vector retrieval событий |
| `OLLAMA_NUM_CTX` | `0` (не передавать) | Явный `num_ctx` в payload; если 0 → вывести из `max_context_tokens` |
| `OLLAMA_NUM_CTX_EXTRA` | `10000` | Запас `num_ctx` сверх `max_context_tokens` (для вывода) |

Значения не хардкодятся в модулях — только в `Settings`.

Дополнительно (опционально, с миграцией БД):
- колонка `chats.max_context_tokens INTEGER NULL` — персональный бюджет чата (переопределяет глобальный). Миграция через существующий паттерн `database.ensure_schema` (ALTER TABLE + inspector, как для `memories.content_hash`). Если решено не трогать БД — используем только глобальную настройку.

---

## 4. Token counting abstraction

Новый модуль `token_counter.py` (плоский layout проекта, рядом с `memory_service.py`).

```python
class TokenCounter(Protocol):
    def count(self, text: str) -> int: ...
    def count_messages(self, messages: list[dict]) -> int: ...
```

Реализации:
1. `EstimatedTokenCounter` (default) — честная **аппроксимация**, логирует `mode=estimated`:
   - для CJK-подобных… нет, проект русский/английский: `est = ceil(len(text) / 4)` + надбавка за пробелы/переносы. Явно помечается как approximation; **нигде не называется «токены» — только «estimated tokens»**.
2. `ExactTokenCounter` — опциональный (плагин), **не добавляется в `requirements.txt` по умолчанию** (не тянем тяжёлые зависимости):
   - если установлен `tiktoken` и задано имя модели/энкодинга (`TOKENIZER_ENCODING`) — использовать его;
   - либо, если есть локальный tokenizer модели — но не скачивать и не загружать при каждом запросе.

Правила (из ТЗ §4, §29):
- Токенизатор **кэшируется** в модульном singleton (загружается один раз на процесс).
- Быстрая оценка — при отборе кандидатов (retrieval); **точный подсчёт — только для финального промпта**.
- В лог выводится `token_count_mode: exact|estimated`.

`count_messages(messages)` считает сумму по контенту + надбавку за роль/границы сообщений (для protocol formatting overhead).

---

## 5. ContextBudget и приоритеты

Новый модуль `context_budget.py` (или внутри `context_builder.py`):

```python
@dataclass
class ContextBudget:
    total_tokens: int            # max_context_tokens
    system_budget: int           # фикс-бюджет system (карточка+правила) — фактически не лимитируется
    state_budget: int            # SceneState + location + relationships
    summary_budget: int
    memory_budget: int
    retrieved_history_budget: int
    recent_history_budget: int   # интервал [recent_min, recent_max]
    reserve_tokens: int
```

Распределение **гибкое**: лимиты — это верхние границы, а не требование заполнить. Если сцена укладывается в 18K при max 60K — это нормальный результат (ТЗ §6).

**Приоритеты** (ТЗ §12), константы-перечисление:

| Уровень | Состав | Политика |
|---|---|---|
| P0 Critical | system safety/rules, identity/карточка, текущий SceneState, локация персонажа, критические relationship facts, последние 1–2 сообщения | никогда не удалять без крайней нужды |
| P1 Very High | recent dialogue, текущие цели, актуальные relationship facts, события текущей сцены | резать только после P2/P3/P4 |
| P2 High | relevant memories, relevant historical events, character summary | нормальный кандидат на сжатие |
| P3 Medium | старые исторические детали, второстепенные memories | сжимать первым |
| P4 Low | дубли, нерелевантное старое, второстепенный metadata | отбрасывать в первую очередь |

Порядок обрезки при переполнении: P4 → P3 → P2 (сначала memories/events, затем summary), recent dialogue режется в последнюю очередь. `CONTEXT_RECENT_MIN_TOKENS` — **soft target**, а не жёсткий минимум: держим его по возможности, но при маленьком бюджете (например 16K) или переполнении после обрезки P4–P2 recent dialogue может опуститься ниже target.

---

## 6. ContextBuilder и BuiltContext

Новый модуль `context_builder.py`:

```python
class ContextBuilder:
    def __init__(self, db, *, token_counter=None, settings=settings): ...

    async def build(
        self,
        *,
        chat_id: int,
        character_id: int,
        user_message: str,
        max_tokens: int | None = None,   # None → settings.max_context_tokens
    ) -> BuiltContext: ...
```

```python
@dataclass
class BuiltContext:
    messages: list[dict]                    # финальный список сообщений для Ollama (chat API)
    # или rendered blocks для generate API — поле prompt: str
    total_tokens: int
    token_count_mode: str                   # "exact" | "estimated"
    components: dict[str, str]              # {"system","summary","memories","scene_state",
                                            #  "relationships","retrieved_history","recent_history","instructions"}
    component_tokens: dict[str, int]
    budget: ContextBudget
    dropped_items: list[dict]               # {"component","reason","message_id"|"memory_id","preview"}
    diagnostics: ContextDiagnostics
```

`ContextDiagnostics` (ТЗ §15): `oldest_included_message_id`, `newest_included_message_id`, `summary_through_message_id`, `retrieved_message_ids`, `recent_message_ids`, `excluded_message_ids` (агрегированно), `memories_candidates`, `memories_selected`, `retrieved_events_selected`, `total_tokens`. Тексты сообщений сюда не пишутся.

**Алгоритм сборки (детерминированный):**
1. Вычислить `ContextBudget` от `max_tokens`.
2. Загрузить данные персонажа (summary + frontier, scene state, relationships, memory-кандидаты, recent messages).
3. Собрать «жёсткие» блоки по приоритету, измеряя токены:
   - system/identity (P0, не режется),
   - scene_state + location + relationships (P0, бюджет `state_budget`),
   - summary (P2, бюджет `summary_budget`),
   - memories (P2, token-aware отбор, бюджет `memory_budget`),
   - retrieved history (P2/P3, token-aware, бюджет `retrieved_history_budget`),
   - recent dialogue (P1, диапазон `[recent_min, recent_max]`),
   - instructions (personality/consistency/vocabulary/scene_advancement) — учитываются в `system_budget`/`instructions`.
4. Дедупликация (ТЗ §20): неагрессивная. Совпадение факта «summary ↔ memory ↔ retrieved event» (нормализованная похожесть текста, например Jaccard по токенам, порог ~0.9) → оставить самый структурированный/свежий источник; для критичных фактов (importance > 0.8) допускается контролируемый дубль.
5. Если сумма > `total_tokens - reserve_tokens` → срезать по порядку приоритетов (раздел 5); результат записывается в `dropped_items`.
6. `total_tokens` = фактический (точный или оценочный) счётчик финального промпта; финальный рендер считается **точно**, отбор кандидатов — быстро/оценочно.

**Context Frontier** (ТЗ §10): `summary_through_message_id` из `CharacterSummary`. Сообщения `id <= frontier` **не попадают** в recent dialogue автоматически; их может вернуть только retrieval. Сообщения `id > frontier` → recent dialogue.

---

## 7. Retrieval: широкое окно + token-aware отбор

### 7.1 Загрузка истории
- Новая функция `crud.get_messages_by_chat_window(db, chat_id, limit)` — загрузка последних сообщений **без обрезки по `max_history_length`** (использовать существующий `get_messages_by_chat` с `limit=None` либо новый параметр окна).
- `CONTEXT_HISTORY_LOAD_CAP` (2000) — **только safety cap** (защита памяти/времени), а **не семантическая граница**: семантической границей для retrieval остаётся witness/knowledge boundary персонажа. Применение cap означает лишь «для retrieval доступно последних N сообщений», а не «события старше cap нерелевантны».
- `get_presence_map` уже умеет работать с произвольным списком `message_ids` — witness-граница применима к любому сообщению.

### 7.2 Retrieval событий (новое, поверх существующих примитивов)
- Новая функция `crud.get_retrieval_message_candidates(db, chat_id, character_id, query_text, limit, *, witness_filter=True)`:
  1. загружает окно истории;
  2. фильтрует по witness/presence (present/told — видимое; absent/mentioned — исключается; ТЗ §8 «retrieval обязан работать поверх knowledge/perception boundary»);
  3. ранжирует SimpleBM25 (переиспользовать `memory_service.SimpleBM25`/`select_relevant_memories` логику по контенту) по query;
  4. если `CONTEXT_MESSAGE_EMBEDDING_ENABLED` и у сообщений есть кэшированные эмбеддинги — опциональный vector-реранк; **без пересчёта эмбеддингов на каждый запрос** (ТЗ §29). Фоновая индексация сообщений — джоба в `task_queue` типа `embed_message` (по аналогии с `embed_memory`).
- Результат — список кандидатов: `{message_id, content, presence, relevance_score, token_estimate, priority, timestamp}`.

### 7.3 Token-aware selection (ТЗ §19)
- После ранжирования — жадный отбор по budget: кандидаты с наибольшим score укладываются в `retrieved_history_budget`; крупные кандидаты не могут переполнить бюджет (10×1500 токенов → войдут только 3–4).
- Retrieval exception: кандидат с `id <= summary_frontier` может попасть в контекст (в отличие от recent dialogue), если score высок — это и есть «исключения из старой истории».

### 7.4 Улучшение query (ТЗ §18)
- Компактный state context для semantic query:
  `user_message + last N recent dialogue + current goal (SceneState.active_goal/active_goals) + SceneState (time/atmosphere/tension) + relevant relationship facts` — короткий текст (~300–500 знаков), не огромный prompt.
- Использовать тот же query и для memory retrieval (`context_text` в `get_hybrid_memories_for_characters`), заменив текущий `context_text = user_text + 3 последних` в `chat_engine`.

---

## 8. Интеграция в генерацию (ollama_client.py)

### 8.1 `_build_generate_payload` / `_build_chat_payload`
- Добавить необязательный `num_ctx: int | None` в `options`.
- Источник: `OLLAMA_NUM_CTX` если задан, иначе `max_context_tokens + OLLAMA_NUM_CTX_EXTRA`. `max_context_tokens` (бюджет сборки) и `num_ctx` (окно backend) — **разные вещи** (ТЗ §26): валидная конфигурация `max_context_tokens=50000`, `num_ctx=60000`.

### 8.2 `generate()` и `_generate_once()`
- Добавить опциональный параметр `built_context: BuiltContext | None = None` (сигнатура расширяется **аддитивно**, существующие тесты, мокающие `generate(**kwargs)`, не ломаются).
- При `built_context`:
  - история/диалог берётся из `built_context` (recent_history + retrieved_history уже собраны и отфильтрованы witness'ом);
  - `format_history_for_character`/`filter_history_for_character` **не вызывается повторно** (или вызывается только для round messages текущего раунда, которых ещё нет в БД — `same_round_ids`);
  - summary/memories/scene/relationships берутся из компонентов `BuiltContext`;
  - в конце `_generate_once` логируется диагностика (раздел 9).
- При `built_context=None` — прежний код полностью (fallback/legacy).

---

## 9. Интеграция в chat_engine.py

Переписать `process_user_message_streaming` так, чтобы:

1. `pre_round_messages` для **retrieval**: `crud.get_messages_by_chat(db, chat_id, settings.context_history_load_cap)` (широкое окно), **не** обрезать до `history_limit`.
2. Внутри цикла по персонажам (вместо ручной сборки):
   ```python
   built_context = await context_builder.build(
       chat_id=chat_id, character_id=current_character.id,
       user_message=user_text, max_tokens=chat_max_tokens,
   )
   ```
   `chat_max_tokens` = `chat.max_context_tokens` (если колонка добавлена) иначе `settings.max_context_tokens`.
3. `ollama_client.generate(..., built_context=built_context, ...)`.
   - `context_messages` (история для генерации) заменяется компонентами `built_context`.
   - Для сообщений текущего раунда (`round_messages`, ещё не в широком окне) сохранить существующую логику `same_round_ids`/`prior_replies` и witness-фильтрацию.
4. Memory retrieval:
   - оставить вызовы `get_hybrid_memories_for_characters` / `get_relevant_memories_for_characters` как источник `memories` **candidate set** (они уже witness-filter и RRF), НО поднять `top_k` (кандидатов больше) и отдать финальный отбор ContextBuilder'у по `memory_budget` через token-aware selection. Либо перенести вызов внутрь `ContextBuilder.build` — решается на этапе реализации; требование: не делать несколько одинаковых retrieval-запросов на компонент (ТЗ §29).
5. `history_limit`/`max_history_length` больше не ограничивает итоговый prompt: при `CONTEXT_ENABLED=true` используется как legacy-верхний порог кандидатов (fallback/legacy constraint, ТЗ §25). При `false` — текущее поведение.
6. Multi-character: `build()` вызывается **для каждого NPC отдельно** → контексты A ≠ B (ТЗ §22). Общие подзадачи (загрузка окна, эмбеддинги) кэшируются/шарятся между персонажами раунда.

---

## 10. Логирование и diagnostics (ТЗ §14, §15)

- В `_generate_once`/`chat_engine` после сборки — структурированный лог `logger.info` вида:

```
Context budget: 60000
system:             2431
character_state:     812
scene_state:         641
relationships:       532
summary:            1844
memories:           2167
retrieved_history:  3210
recent_history:    28441
instructions:       1422
reserve:            8490
-------------------------
TOTAL:             50000
token_count_mode: exact
history_messages_loaded: 1200
history_messages_included: 180
history_messages_dropped: 1020
memories_candidates: 40
memories_selected: 6
retrieved_events_selected: 4
```

- `BuiltContext.diagnostics` сохраняется для debug-режима (агрегированные id, без текстов). По умолчанию в лог — только сводка; полная диагностика — при `CONTEXT_DEBUG=true` (новый флаг) или `logging.DEBUG`.

---

## 11. SceneState и relationships — как state, а не history (ТЗ §16, §17)

- Продолжать использовать компактные `build_scene_block` и `relationship_service.build_relationships_block` (не менять их вывод).
- Не возвращать десятки старых сообщений «ради факта из SceneState»: если факт уже представлен в state — retrieved history для него не дублируется (дедупликация, раздел 6, п.4).
- Relationships: только текущий `CharacterRelationship` + ограниченный `RelationshipEvent` (уже `relationship_max_events_in_prompt`=5).

---

## 12. Ollama num_ctx (ТЗ §26)

Описан в разделе 8.1. Отдельный прогон: сверить текущее поведение (payload без `num_ctx`) и новое (с `num_ctx`) на реальном Ollama — smoke-тест.

---

## 13. Backward compatibility (ТЗ §25)

- `CONTEXT_ENABLED=false` → ровно текущий код (ничего не удаляется из `chat_engine`, `ollama_client`, `prompt_builder`).
- `max_history_length` не удалять: при `CONTEXT_ENABLED=true` он остаётся legacy-ограничением кандидатов / fallback.
- Новые параметры `generate()` — опциональные с дефолтами.
- Новые настройки — с безопасными default; `MAX_CONTEXT_TOKENS` отсутствует в `.env` → 60000.
- Существующие чаты (БД без новых колонок) работают: колонка `chats.max_context_tokens` либо не вводится, либо вводится через `ALTER TABLE` в `ensure_schema` с default `NULL` (= глобальная настройка).
- Существующие тесты должны проходить без изменений (раздел 15).

---

## 14. Что НЕ делаем (ТЗ §30)

- Не переписываем memory system, BM25, vector retrieval, RRF, witness system, SceneState, relationships.
- Не делаем глобальный summary вместо per-character.
- Не отправляем всю историю «потому что 60K».
- Не обрезаем историю до фиксированного числа сообщений как основной механизм.
- Не создаём summary на каждый запрос и не добавляем лишних LLM-вызовов на обычный turn (ContextBuilder не вызывает LLM; только BM25/state).
- Не меняем personality-промпты и не жертвуем character behavior ради бюджета.
- Не скачиваем тяжёлые токенизаторы на каждый запрос.

---

## 15. Тесты

### 15.1 Новые unit-тесты
- `tests/test_token_counter.py`: оценка vs точный режим; `count_messages`; логирование `token_count_mode`; кэширование.
- `tests/test_context_builder.py` (обязательные сценарии из ТЗ §27):
  - **Token budget**: никогда не превышает `max_context_tokens` (+ допустимый overhead на форматирование).
  - **Short history**: нет искусственного сжатия/дополнения.
  - **Long history**: старые нерелевантные исключаются, summary используется, recent dialogue сохраняется.
  - **Summary frontier**: при `through_message_id=500` сообщения 1–500 не попадают автоматически.
  - **Retrieval exception**: старое релевантное сообщение возвращается, несмотря на frontier.
  - **Witness isolation**: персонаж не получает события с presence `absent`.
  - **Multi-character**: A и B получают разные `BuiltContext`.
  - **Token-aware retrieval**: 10 больших memories не переполняют бюджет.
  - **Empty retrieval**: корректно работает без memories/embeddings.
  - **Embeddings disabled**: BM25/fallback работает.
  - **SceneState отсутствует / Summary отсутствует**: prompt формируется.
  - **Budgets 16K / 32K / 60K**: все режимы работают.
- `tests/test_retrieval_candidates.py`: ранжирование BM25 кандидатов, witness-фильтр, token-aware отбор, query из state context.

### 15.2 Regression (ТЗ §28) — не менять семантику
- existing: `test_witness_filter`, `test_perception`, `test_memory_service`, `test_consolidation`, `test_role_isolation`, `test_repetition_detector`, `test_relationship_service`, `test_chat_engine`, `test_prompt_builder`, golden-тесты (`tests/golden/`).
- New regression на состав контекста при `CONTEXT_ENABLED=true` и `false` (параллельный прогон одинаковых фикстур → одинаковые семантики: present/told/mentioned/absent, location-based visibility, character-specific memories/summary, relationships, SceneState, RRF, personality, anti-mimicry, vocabulary, repetition).

### 15.3 Интеграционные
- `process_user_message_streaming` с `CONTEXT_ENABLED=true` и замоканным `ollama_client.generate`: проверить, что `built_context` не превышает бюджет и что каждому NPC передаётся собственный контекст (паттерн `test_chat_engine.py`).

### 15.4 Запуск
- `pytest` из `ai-roleplay-chat/` (pytest.ini: `asyncio_mode=auto`, `pythonpath=.`).

---

## 16. Производительность (ТЗ §29)

- Токенизатор — singleton, загружается один раз; оценка при отборе, точный подсчёт только финального промпта.
- Эмбеддинги истории не пересчитываются на каждый запрос: retrieval событий по умолчанию BM25; vector — только по кэшированным эмбеддингам (фоновая джоба) и только при `CONTEXT_MESSAGE_EMBEDDING_ENABLED`.
- Один retrieval-проход на персонажа (без дублирующих запросов на компонент); общие данные раунда (окно истории, presence, summaries, scene state) загружаются один раз и шаредятся.
- Окно загрузки `CONTEXT_HISTORY_LOAD_CAP` — **только safety cap** (память/время), не семантическая граница: семантическая граница — witness/knowledge boundary. Значение настраивается; при истории, превышающей cap, в лог выводится warning (без потери «широкого окна» для типовых сценариев).

---

## 17. Порядок реализации

| Этап | Файлы | Содержание |
|---|---|---|
| 1 | `config.py`, `.env.example`, `schemas.py`, `database.py` (опц.) | Настройки; схемы `BuiltContext`/`ContextBudget`/`ContextDiagnostics`; опц. колонка `chats.max_context_tokens` |
| 2 | `token_counter.py` (новый) | `TokenCounter`, estimated/exact, кэш, лог режима |
| 3 | `context_budget.py` + `context_builder.py` (новые) | Budget, приоритеты, сборка `BuiltContext`, frontier, дедупликация, token-aware отбор (чистая логика, тестируется без БД/LLM) |
| 4 | `crud.py` | `get_messages_by_chat_window`; `get_retrieval_message_candidates`; helper-ы по presence/фронтиру |
| 5 | `chat_engine.py`, `ollama_client.py` | Интеграция `ContextBuilder` в цикл персонажей; `built_context` в `generate/_generate_once`; улучшенный retrieval query |
| 6 | `ollama_client.py` | `num_ctx` в `_build_generate_payload`/`_build_chat_payload` |
| 7 | `chat_engine.py`, `ollama_client.py` | Логирование состава контекста + diagnostics (debug) |
| 8 | `tests/` | Новые unit/integration/regression тесты; прогон всей suite |
| 9 | — | Smoke-тест против реального Ollama; проверка backward compat (`CONTEXT_ENABLED=false`) |

Каждый этап коммитится отдельно; код не меняется до утверждения плана.

---

## 18. Критерии готовности (ТЗ §32)

1. Контекст ограничивается реальными токенами (не числом сообщений) — `total_tokens <= max_context_tokens` в логах/тестах.
2. Каждый AI-персонаж получает собственный `BuiltContext`.
3. История > 60K не заставляет отправлять всю историю (регресс-тест с синтетической историей).
4. Retrieval находит события за пределами recent history.
5. Summary frontier реально исключает дубли (тест «Summary frontier»).
6. Witness/knowledge isolation сохраняется (тест «Witness isolation» + regression).
7. Recent dialogue имеет высокий приоритет (режется последним).
8. SceneState и relationship state не вытесняются историей.
9. Retrieval учитывает token budget (тест «Token-aware retrieval»).
10. В логах виден состав контекста по компонентам + `token_count_mode`.
11. Работают 16K / 32K / 50K / 60K бюджеты.
12. Все существующие тесты проходят; добавлены новые.
13. Нет лишних LLM-вызовов на обычный turn и нет существенного роста latency (BM25-дефолт; токенизатор кэширован).

---

## 19. Отчёт после реализации (ТЗ §33)

По завершении предоставить:
1. Архитектурное резюме — изменённые/новые файлы и зачем.
2. Фактический context pipeline после реализации.
3. Пример диагностики одного реального/тестового запроса (Total budget / компоненты / TOTAL).
4. Список новых тестов и их результаты.
5. Проверка backward compatibility (старые чаты, старые настройки).
6. Потенциальные дальнейшие улучшения (не реализуются автоматически).

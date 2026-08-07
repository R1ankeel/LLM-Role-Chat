# Документация проекта AI Roleplay Chat

Локальное веб-приложение для ролевых игр с AI-персонажами через Ollama.
FastAPI + SQLAlchemy 2.0 (async/aiosqlite). Фронтенд: два независимых интерфейса на общем backend —
существующий Vanilla JS SPA (`app/static/`, отдаётся на `:8000`) и новый Vue 3 + TypeScript + Vite (`frontend/`, dev на `:3000`).

## Оглавление

| Документ | Содержание |
|----------|-----------|
| [architecture.md](architecture.md) | Общая архитектура, жизненный цикл запроса, потоки данных, фоновые задачи |
| [api.md](api.md) | Полный справочник HTTP API |
| [database.md](database.md) | Схема базы данных (SQLite), индексы, миграции |
| [configuration.md](configuration.md) | Все настройки через `.env` (значения по умолчанию) |
| [relations.md](relations.md) | Система отношений между персонажами (анализатор, интерпретатор, issues, epistemic mask) |
| [events.md](events.md) | Structured World Events (Sprint 1): канонический журнал, event extraction, causal links, post-round pipeline |
| [sensors.md](sensors.md) | Sensors Model — отдельный аналитический слой LLM (предложения perception/event/emotion/memory/relationship) |
| [character_state.md](character_state.md) | Character State (Sprint 3): единое runtime-состояние персонажа — эмоции/стресс/mood, emotion_engine, блок YOUR STATE |
| [attention.md](attention.md) | Attention (Sprint 4): «воспринято ≠ вошло в сознание» — детерминированный score, фильтр памяти/recency tail, Sensors perception-proposal |
| [beliefs.md](beliefs.md) | Belief System (Sprint 5): знание/убеждение персонажа вместо плоской истины — pipeline event→presence→attention→belief, блок WHAT YOU KNOW |
| [retrieval.md](retrieval.md) | Hybrid Retrieval v2 (Sprint 6): детерминированный rerank memories после RRF — оси (lexical/semantic/emotional/story/relationship/recency/salience) + сигналы контекста |
| [intervention.md](intervention.md) | Одноразовое «Вмешательство» игрока: жизнь инструкции, API, промпт |
| [locations.md](locations.md) | «Локации 2.0»: модель Location, миграция, CRUD API, синхронизация ссылок |
| [story.md](story.md) | Dynamic Story State (Sprint 8/9): Original Plot + Current Story State, write-path из раундных событий, блок STORY, API + Story Consolidation (LLM-обновление с валидацией) + Plot Engine (Sprint 10): NPC Intent (детерминированный, перед генерацией), долгоживущие планы NPC (не GOAP), story pressure, архивация завершённых линий |
| [crisis.md](crisis.md) | Crisis Engine (Sprint 11): мягкое обнаружение кризисов — story pressure → кандидат (правила) → resolution (мягко: story_thread + story_event + boost), LLM-оценка под benchmark gate §27, блок CRISIS в контексте |
| [adaptive_consolidation.md](adaptive_consolidation.md) | Adaptive Consolidation (Sprint 12): замена 24h-таймера на score-based soft/hard/critical — `consolidation_state` counters, `compute_consolidation_score`, `is_critical_event` (детерм.), idle-чат не консолидируется, critical → немедленно (дедуп ≤ N/раунд), полный набор memory+summary+relationship+anchors+story+index |
| [context_v2.md](context_v2.md) | Context Builder v2 (Sprint 13): приоритизированная сборка WORLD/WHAT YOU KNOW/WHAT YOU PERCEIVE/YOUR STATE/RELATIONSHIP/ACTIVE GOAL/RELEVANT MEMORY/STORY под флагом `context_v2_enabled`, токен-подбюджеты, удаление дублирующих legacy-блоков (scene → WORLD, relationships → RELATIONSHIP), debug-контур §29.1 (`/api/debug` + `static/debug.html`) |
| [lora.md](lora.md) | LoRA-адаптеры (MVP: одна LoRA на чат, без весов): модель данных, миграции, валидация пути (§2.7), CRUD, runtime-слой `LoRAManager` (Sprint 2), интеграция в основную генерацию (Sprint 3), REST API `/api/lora` + `/api/chats/{id}/lora` (Sprint 4), Vue-фронтенд — вкладка «LoRA» + индикатор в шапке чата (Sprint 5); план — `Plans/LoRA.md` |

## Краткий обзор

- **Бэкенд**: FastAPI-приложение `app.main:app`, статика отдаётся из `app/static/`.
- **База данных**: SQLite `ai_chat.db` (sync + async движки), таблицы создаются и мигрируются автоматически при старте (`database.ensure_schema`).
- **LLM**: локальный Ollama (`/api/generate` или `/api/chat`), поддержка thinking, динамический `num_ctx` (KV-окно) на каждый чат.
- **Ядро**: каждый раунд игры — одно сообщение пользователя, на которое по очереди отвечают все NPC-персонажи.
- **Ключевые системы**:
  - *Perception / Witness* — персонаж видит в контексте только те события мира, которые способен воспринять (локация + visibility + каналы связи).
  - *Locations 2.0* — локации как самостоятельная сущность (таблица `locations`): CRUD API + UI, `chats.locations` остаётся кэшем названий для движка (см. `locations.md`).
  - *Memory* — извлечение фактов после каждого раунда, BM25/векторный поиск, саммари, консолидация, фоновые задачи.
  - *Relationships* — направленные отношения персонаж→персонаж, LLM-анализатор дельт, open issues, детерминированная интерпретация.
  - *LoRA (Sprint 1–5)* — регистр LoRA-адаптеров (`lora_adapters`) + конфигурация чата (одна LoRA на чат, `lora_enabled`); валидация пути GGUF, sha256 файла; runtime-слой `LoRAManager` (resolve по семантике `lora_enabled`, кэш runtime-моделей, compatibility check по identity); интеграция в основной `generate()` (Sprint 3); REST API `GET/POST/PUT/DELETE /api/lora` и `GET/PUT /api/chats/{id}/lora` (Sprint 4); Vue-фронтенд — вкладка «LoRA» (registry + конфигурация чата, три состояния §2.4, ограничения runtime) и индикатор в шапке чата (Sprint 5) — см. `lora.md`.
  - *Role isolation* — защита от «разговоров за других персонажей», repetition detector, anti-mimicry.
  - *Context builder* — токено-ориентированная сборка контекста под лимит бюджета.
  - *Appearance* — внешность персонажа (поле `appearance`): попадает в его `<character>`-карту (`<appearance>`) и в scene-блок как «Внешность рядом стоящих» **только** для персонажей той же локации (Этап C плана profile-avatar-appearance).
- **Фронтенд**: одностраничное приложение (Vanilla JS), SSE-стриминг ответов, вкладки настроек, управления персонажами/памятью/сценой/отношениями.
- **UI отношений (Sprint 4)**: модалка «Отношения» (кнопка 🕸️ в шапке чата) — граф отношений (SVG), таймлайн пары, открытые вопросы всего чата с решением, ручное редактирование (тип, метрики, описание, добавление ребра).
- **UI отношений (новый frontend, Этап 5)**: та же функциональность в Vue — кнопка «Отношения» в шапке, модалка с вкладками «Граф / Список / Вопросы» (SVG-граф с перетаскиванием узлов, клик по ребру → детали пары: метрики, issues с «Решить», таймлайн с «Загрузить ещё»), правая панель: список персонажей → детали (описание, редактируемая локация, память, сводка) → компактные отношения выбранного персонажа, панель мира (время/погода/напряжение/цель + смена локации игрока).
- **Полировка (новый frontend, Этап 6)**: скелетоны загрузки и ErrorState во всех списках/панелях, тосты-уведомления (создание/переименование/удаление сцены, сохранение локаций и отношений, закрытие вопросов), глобальный баннер «Backend недоступен» с retry (поллинг `/api/health`), виртуализация длинной ленты через `content-visibility`, анимации появления/hover и переходы drawer'ов, респонсивная полировка (сворачиваемый sidebar на tablet, тач-таргеты и безопасные отступы на mobile, модалки капаются по ширине).

## Структура репозитория

```
ai-roleplay-chat/
├── main.py                  # устаревшая точка входа (см. app/main.py)
├── app/
│   ├── main.py              # FastAPI app, lifespan, фоновые воркеры, CORS
│   ├── config.py            # pydantic-settings, все настройки
│   ├── database.py          # SQLite sync+async, индексы, миграции
│   ├── models.py            # ORM: Chat, Character, Message, Memory, ...
│   ├── schemas.py           # Pydantic-схемы, нормализация категорий/visibility
│   ├── crud.py              # слой доступа к данным (async)
│   ├── perception.py        # правила восприятия событий (локация/visibility/каналы)
│   ├── witness_model.py     # фильтрация истории и памяти по presence
│   ├── prompt_builder.py    # сборка system-промптов из шаблонов ru.json
│   ├── ollama_client.py     # клиент Ollama (генерация, извлечение, retry, streaming)
│   ├── chat_engine.py       # движок раунда: генерация, presence, сцена, отношения
│   ├── memory_service.py    # извлечение фактов, саммари, консолидация, embed-задачи
│   ├── context_builder.py   # токено-ориентированная сборка контекста
│   ├── context_budget.py    # распределение токенов по компонентам
│   ├── context_state.py     # динамический num_ctx на чат
│   ├── relationship_*.py     # сервис/анализатор/интерпретатор отношений
│   ├── role_isolation.py    # изоляция роли, validation ответа
│   ├── repetition_detector.py # детекция повторов и стагнации сцены
│   ├── task_queue.py        # очередь задач памяти (persistence, retry)
│   ├── token_counter.py     # оценка/точный подсчёт токенов
│   ├── embedding_service.py # эмбеддинги через Ollama (vector search)
│   ├── ratelimit.py         # throttle 1 сообщение / 5 сек на чат
│   ├── generation_tracker.py# трекинг активной генерации на чат
│   ├── prompts/ru.json      # все шаблоны промптов
│   ├── routers/             # API-роутеры (chats, characters, locations, chat_engine, jobs, relationships)
│   └── static/              # SPA: index.html, app.js, style.css
├── frontend/                # НОВЫЙ frontend: Vue 3 + TS + Vite (см. ниже)
├── scripts/                 # CLI-скрипты (backfill_embeddings)
├── tests/                   # pytest + tests/eval (харнесс и метрики) + tests/golden
└── .env / .env.example      # конфигурация
```

## Запуск

```bash
pip install -r requirements.txt
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

На Windows также доступен `start_server.bat` (запускает `uvicorn app.main:app` с `--reload`).

## Новый frontend (`frontend/`)

Отдельное Vite-приложение (Vue 3 + TypeScript + Pinia + Vue Router), изолированное от старого SPA.
Работает против того же backend'а, старый frontend не трогается.

| Компонент | URL |
|-----------|-----|
| Старый frontend (Vanilla SPA) | `http://localhost:8000` |
| Backend API | `http://localhost:8000/api` |
| Новый frontend (dev) | `http://localhost:3000` |

- Dev-прокси Vite: `'/api' → 'http://localhost:8000'` (пути API остаются чистыми, CORS не мешает).
- Mock-режим: `VITE_USE_MOCKS=true` в `.env.development` — `api/`-слой делегирует в `src/mocks/`
  (тот же интерфейс `Api`, mock-стрим имитирует токены). По умолчанию `false` — реальный backend.
- Производственная сборка: `vite build` → `frontend/dist/`, отдаётся статик-сервером (нужен SPA-fallback на `/chat/:id`).

Замечания по контракту backend (важно для фронтенда):

- `GET /api/chats/{id}` возвращает **плоский** `ChatDetail` (поля чата на верхнем уровне + `characters` + `messages`), без вложенного `chat`.
- В SSE при отправке сообщения первым событием приходит **эхо самого сообщения игрока** (`{"type":"message",...}` с `role="user"`); фронтенд заменяет им optimistic-копию, а не добавляет дубликат.
- Пагинация `GET /api/messages?limit&offset` — от начала без счётчика; фронтенд использует «fetch-all» страницами по 500.

Запуск:

```bash
cd frontend
npm install
npm run dev          # http://localhost:3000 (dev + HMR + proxy /api)
npm run build        # vue-tsc + vite build → dist/
npm run preview      # локальный просмотр production-сборки
```

Структура `frontend/src/` (по этапам плана `Plans/frontend-app.md`):

- `types/` — TS-интерфейсы, повторяющие Pydantic-схемы;
- `api/` — единственный слой сетевых запросов: `client.ts` (ApiError, `request` с `query`), `sse.ts`
  (`MessageStream`: `onToken/onMessage/onDone/onError/abort`), домены `chats/characters/messages/scene/relationships/health`, фасад `index.ts` с переключателем `useMocks`;
- `mocks/` — mock-данные и mock-сервис (`data.ts`, `service.ts`), интерфейс 1:1 с `api/`;
- `stores/` — Pinia: `chats` (числовой `currentChatId`, «последний чат» в localStorage), `messages`
  (реальный SSE-стрим, отрицательные temp-id, ошибки rate-limit/conflict, восстановление генерации),
  `characters` (выбранный персонаж, память/сводка, смена локации), `relationships` (граф, issues,
  outgoing/incoming, таймлайн пары), `scene`, `ui` (панели, drawer'ы, тема, тосты), `health` (проверка доступности backend);
- `router/` — маршруты: `/` (redirect на последний чат) и `/chat/:chatId` (валидация числового id);
- `components/` — `layout/` (AppLayout, Sidebar, MainPanel, RightPanel), `chat/` (ChatHeader, MessageList/Item, SystemMessage, WorldEvent, GenerationIndicator, Composer, ChatView), `characters/` (CharacterList, CharacterDetails, RelationshipView, RelationshipGraph, RelationshipPairDetail, RelationshipModal), `scene/` (WorldStatePanel), `common/` (Avatar, Badge, Modal, EmptyState, ErrorState, Skeleton, ProgressBar, Toasts);
- `composables/` — `useViewport.ts` (desktop/tablet/mobile);
- `utils/color.ts` — детерминированные accent-цвета персонажей; `utils/format.ts` — форматирование дат/времени;
- `styles/` — дизайн-токены, базовые стили, компонентные классы.

Актуальное состояние по этапам — в [`Plans/frontend-app.md`](../Plans/frontend-app.md).

## Тесты

```bash
pytest                # pytest.ini: asyncio_mode=auto, testpaths=tests
python -m tests.eval.run_eval --mode mock   # eval-харнесс (без Ollama)
```

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

## Краткий обзор

- **Бэкенд**: FastAPI-приложение `app.main:app`, статика отдаётся из `app/static/`.
- **База данных**: SQLite `ai_chat.db` (sync + async движки), таблицы создаются и мигрируются автоматически при старте (`database.ensure_schema`).
- **LLM**: локальный Ollama (`/api/generate` или `/api/chat`), поддержка thinking, динамический `num_ctx` (KV-окно) на каждый чат.
- **Ядро**: каждый раунд игры — одно сообщение пользователя, на которое по очереди отвечают все NPC-персонажи.
- **Ключевые системы**:
  - *Perception / Witness* — персонаж видит в контексте только те события мира, которые способен воспринять (локация + visibility + каналы связи).
  - *Memory* — извлечение фактов после каждого раунда, BM25/векторный поиск, саммари, консолидация, фоновые задачи.
  - *Relationships* — направленные отношения персонаж→персонаж, LLM-анализатор дельт, open issues, детерминированная интерпретация.
  - *Role isolation* — защита от «разговоров за других персонажей», repetition detector, anti-mimicry.
  - *Context builder* — токено-ориентированная сборка контекста под лимит бюджета.
- **Фронтенд**: одностраничное приложение (Vanilla JS), SSE-стриминг ответов, вкладки настроек, управления персонажами/памятью/сценой/отношениями.
- **UI отношений (Sprint 4)**: модалка «Отношения» (кнопка 🕸️ в шапке чата) — граф отношений (SVG), таймлайн пары, открытые вопросы всего чата с решением, ручное редактирование (тип, метрики, описание, добавление ребра).

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
│   ├── routers/             # API-роутеры (chats, characters, chat_engine, jobs, relationships)
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
  (`MessageStream`: `onToken/onMessage/onDone/onError/abort`), домены `chats/characters/messages/scene/relationships`, фасад `index.ts` с переключателем `useMocks`;
- `mocks/` — mock-данные и mock-сервис (`data.ts`, `service.ts`), интерфейс 1:1 с `api/`;
- `stores/` — Pinia: `chats` (числовой `currentChatId`, «последний чат» в localStorage), `messages`
  (реальный SSE-стрим, отрицательные temp-id, ошибки rate-limit/conflict, восстановление генерации),
  `characters`, `scene`, `ui`;
- `router/` — маршруты: `/` (redirect на последний чат) и `/chat/:chatId` (валидация числового id);
- `components/` — `layout/` (AppLayout, Sidebar, MainPanel, RightPanel), `chat/` (ChatHeader, MessageList/Item, SystemMessage, WorldEvent, GenerationIndicator, Composer, ChatView), `common/` (Avatar, Badge, Modal, EmptyState);
- `composables/` — `useViewport.ts` (desktop/tablet/mobile);
- `utils/color.ts` — детерминированные accent-цвета персонажей;
- `styles/` — дизайн-токены, базовые стили, компонентные классы.

Актуальное состояние по этапам — в [`Plans/frontend-app.md`](../Plans/frontend-app.md).

## Тесты

```bash
pytest                # pytest.ini: asyncio_mode=auto, testpaths=tests
python -m tests.eval.run_eval --mode mock   # eval-харнесс (без Ollama)
```

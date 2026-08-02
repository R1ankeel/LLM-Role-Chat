# Новый frontend ролевого движка — план реализации

> **Статус:** план. Код не изменялся.
> **Дата:** 2026-08-02
> **Ограничение:** существующий frontend (Vanilla JS SPA) НЕ удаляется, НЕ переписывается и НЕ ломается.

---

## 1. Исследование текущей архитектуры (Этап 1 — выполнен)

### 1.1 Общая структура репозитория

```
C:\dev\Role-LLM\
├── ai-roleplay-chat\        # основное приложение (это git-репозиторий)
│   ├── app\
│   │   ├── main.py          # FastAPI, lifespan, CORS, монтирование статики
│   │   ├── config.py        # pydantic-settings (все настройки через .env)
│   │   ├── models.py        # ORM: Chat, Character, Message, Memory, SceneState, ...
│   │   ├── schemas.py       # Pydantic-схемы (ChatRead, MessageRead, CharacterRead, ...)
│   │   ├── crud.py          # слой доступа к данным (async)
│   │   ├── routers\         # chats, characters, chat_engine, relationships, jobs
│   │   └── static\          # СУЩЕСТВУЮЩИЙ frontend (SPA без сборки)
│   ├── main.py              # устаревшая точка входа
│   ├── requirements.txt     # только Python-зависимости (Node в проекте не используется)
│   └── tests\               # pytest, 36 файлов
└── docs\
    ├── Frontend.png         # макет (см. §1.8 про ограничение чтения)
    ├── frontend-app.md      # этот документ
    └── ...                  # api.md, architecture.md, relations.md и др.
```

Ключевой факт: **в проекте сейчас нет ни одного `package.json`, Node-фронтенда не существует**. Существующий UI — это Vanilla JS SPA в `app/static/`, отдаваемая самим FastAPI.

### 1.2 Существующий frontend (`app/static/`)

| Файл | Размер | Что делает |
|------|--------|-----------|
| `index.html` | 16.5 КБ | Разметка: sidebar, main (header, messages, input), модалки (новый чат, настройки с 5 вкладками, отношения, персонаж, память) |
| `app.js` | 105 КБ | Вся логика: API-запросы, SSE-стриминг через `fetch` + `response.body.getReader()`, optimistic UI, состояния генерации, localStorage (последний чат, активная генерация) |
| `style.css` | 28 КБ | Тёмная тема через CSS-переменные (`--bg-primary: #1a1a2e`, `--accent: #e94560` и т.д.) |

Отмечается из старого фронта (чтобы перенести проверенные решения):
- SSE-клиент: POST на `/api/chats/{id}/message`, чтение потока `readSSEStream`, обработка типов событий `token` / `message` / `done` / `error`.
- Аватары: без изображений — инициал + `hashColor(имя)`; пользователь — «Я» на синем фоне.
- Обработка 409 (генерация уже идёт) и 429 (rate limit 5 сек).
- Восстановление состояния генерации после перезагрузки: `GET /api/chats/{id}/generation-status` + localStorage.
- Тёмная тема уже в палитре — новый UI может развить её, а не изобретать с нуля.

### 1.3 Backend и API (полный список endpoints для нового API-слоя)

**Backend:** FastAPI `app.main:app`, порт 8000, SQLite (aiosqlite) + SQLAlchemy 2.0 async.
**CORS:** `allow_origins=["*"]` → кросс-доменные запросы нового dev-сервера работают без изменений backend.

Базовый префикс — `/api`:

| Метод | Путь | Назначение | Нужен UI |
|-------|------|-----------|----------|
| GET | `/api/health` | Liveness | Да |
| GET | `/api/models` | Модели Ollama | Да (настройки) |
| POST | `/api/chats` | Создать чат | Да |
| GET | `/api/chats` | Список чатов (skip/limit, новые первыми) | Да |
| GET | `/api/chats/{id}` | Детали: чат + персонажи + последние 50 сообщений | Да |
| PUT | `/api/chats/{id}` | Обновить чат | Да |
| DELETE | `/api/chats/{id}` | Удалить чат | Да |
| DELETE | `/api/chats/{id}/messages?scope=` | Очистить историю | Да |
| GET | `/api/chats/{id}/scene` | Состояние сцены (время, локации, weather/mood/tension) | Да |
| PATCH | `/api/chats/{id}/scene` | Обновить сцену | Да |
| POST | `/api/chats/{id}/characters` | Добавить персонажа | Да |
| GET | `/api/chats/{id}/characters?include_player=` | Список персонажей | Да |
| PUT | `/api/characters/{id}` | Обновить персонажа | Да |
| DELETE | `/api/characters/{id}` | Удалить персонажа | Да |
| PUT | `/api/chats/{id}/player` | Переименовать игрока | Да |
| GET | `/api/characters/{id}/memories` | Память персонажа | Да |
| POST | `/api/characters/{id}/memories` | Добавить память | Да |
| PUT | `/api/memories/{id}` | Обновить память | Да |
| DELETE | `/api/memories/{id}` | Удалить память | Да |
| GET | `/api/characters/{id}/summary` | Саммари сессии персонажа | Да |
| PATCH | `/api/characters/{id}/location` | Ручная смена локации | Да |
| POST | `/api/chats/{id}/message` | Отправить сообщение (**SSE**) | Да |
| GET | `/api/chats/{id}/messages?limit=&offset=` | История с пагинацией | Да |
| DELETE | `/api/chats/{id}/messages/{mid}` | Удалить сообщение (каскад для user) | Да |
| POST | `/api/chats/{id}/messages/{mid}/regenerate` | Перегенерация ответа (**SSE**) | Да |
| POST | `/api/chats/{id}/stop-generation` | Стоп генерации | Да |
| GET | `/api/chats/{id}/generation-status` | Активна ли генерация | Да |
| GET | `/api/chats/{id}/relationships/graph` | Граф отношений (узлы + направленные рёбра с метриками) | Да |
| GET | `/api/chats/{id}/relationships/issues?state=` | Открытые/решённые вопросы чата | Да |
| GET | `/api/chats/{id}/characters/{cid}/relationships` | Исходящие отношения персонажа | Да |
| GET | `/api/chats/{id}/characters/{cid}/relationships/received` | Входящие | Да |
| GET/PUT | `/api/chats/{id}/relationships/{s}/{t}` | Отношение пары / ручное обновление | Да |
| GET | `/api/chats/{id}/relationships/{s}/{t}/timeline` | Таймлайн пары (события+issues+сообщения) | Да |
| GET | `/api/chats/{id}/relationships/{s}/{t}/issues` | Issues пары | Да |
| POST | `/api/chats/{id}/relationships/{s}/{t}/issues/{iid}/resolve` | Закрыть issue | Да |
| POST | `/api/chats/{id}/relationships/analyze` | Повторный анализ раунда | Опционально |
| GET | `/api/relationships/{rid}/events` | Последние события отношения | Да |
| GET | `/api/jobs/chats/{id}/memory-jobs` | Задачи памяти | Опционально |

### 1.4 SSE-протокол (важно для streaming)

Все события — строки `data: {json}\n\n` (POST + `fetch`, поэтому EventSource/GET не подходит):

| type | payload | когда |
|------|---------|-------|
| `message` | `{type, message: MessageRead}` | эхо пользователя, реплика NPC, системное сообщение (напр. перемещение) |
| `token` | `{type, text, character_id}` | порция текста текущего ответа |
| `done` | `{type}` | завершение раунда |
| `error` | `{type, detail}` (+`rate_limit: true`) | ошибка генерации |

Ограничения, которые нужно отображать в UI:
- 409 — «в этом чате уже выполняется генерация»;
- 429 — rate limit «1 сообщение / 5 сек на чат» (считается от завершения прошлой генерации);
- при обрыве соединения генерация продолжается в фоне.

### 1.5 Модели данных (для типов TypeScript)

- **Chat**: `id, name, general_prompt, model_name, max_history_length, thinking_mode, player_location, locations(JSON-строка), created_at`.
- **Character**: `id, chat_id, name, personality, traits, speech_style, example_messages, boundaries, background, relationships, location, temperature?, order_index, is_player, created_at`.
- **Message**: `id, chat_id, character_id?, role("user"|"character"|"system"), content, visibility("private"|"local"|"targeted"|"public"|"global"), location, target_character_ids, channel, timestamp`. System-сообщения сейчас используются для перемещений: `*Имя переместился в локацию*`, `visibility: global`.
- **SceneState**: `time_of_day, character_locations{}, custom_state{weather, mood, tension, plot_flags, active_goal, important_objects, active_events, time_progression, ...}, present_character_ids, player_location, updated_at`.
- **Relationship**: `relationship_type, affection, trust, attraction, resentment, jealousy, description` (все метрики 0–100; тип — whitelist из `relationship_valid_types`).
- **Memory**: `content, importance, category, source_message_ids, last_accessed_at`.
- **RelationshipIssue**: `issue_type, text, importance, state("open"|"resolved"), ...`.

### 1.6 Авторизация

**Авторизации в backend нет** (нет аутентификации, нет сессий, нет токенов). Значит:
- отдельная система авторизации НЕ создаётся;
- обработка expired session/401 не требуется (при 401/403 просто показать ошибку как network/backend error).

### 1.7 Чего backend НЕ предоставляет (это уйдёт в mock/TODO)

| Требование ТЗ | Статус в backend | Решение нового фронта |
|---------------|------------------|------------------------|
| Аватары-изображения | Нет поля avatar | Инициалы + accent-цвет (hash по имени), как в старом UI; компонент `Avatar` с будущим support `imageUrl` (TODO) |
| Idle events / world reactions как отдельный тип | Нет — есть только `system`-сообщения (перемещения) | Компонент `WorldEvent`, рендерящий `system`-сообщения особым стилем; в моке — примеры idle/reaction событий; TODO: endpoint |
| NPC-to-NPC события вне присутствия игрока | Нет | Тот же `WorldEvent`; placeholder в правой панели (world feed) |
| Streaming: событие «персонаж начал думать» | Нет отдельного события — только `token`/`message` | Индикатор «X размышляет…» включается по первому `token` от `character_id` или по факту начала ответа NPC |
| Avatar/emoji персонажей | Нет | Пре-установленный набор градиентов/иконок по id или выбор цвета при создании персонажа (frontend-only, TODO) |

### 1.8 Ограничение чтения макета

`docs/Frontend.png` — изображение; модель не поддерживает чтение изображений, поэтому макет пиксель-в-пиксель не воспроизводился. План опирается на детальное описание структуры из ТЗ. **Перед Этапом 2 разработчик должен открыть `docs/Frontend.png` вручную** и сверить зоны интерфейса.

---

## 2. Решение по интеграции (критичное ограничение)

### 2.1 Выбор размещения

Принятое решение: **новый frontend — отдельное Vite-приложение в `ai-roleplay-chat/frontend/`**.

Почему:
- лежит внутри git-репозитория `ai-roleplay-chat/` (будет версионироваться);
- полностью изолировано от `app/static/` (старый фронт не трогается, FastAPI продолжает отдавать его на `:8000`);
- имеет собственный `package.json`, `node_modules`, dev-server и build-сборку;
- вариант `localhost:3000/app` не используется — отдельный dev-server безопаснее (минимизация риска регрессий).

### 2.2 Порты и связь с backend

| Компонент | URL |
|-----------|-----|
| Старый frontend (Vanilla SPA) | `http://localhost:8000` (как раньше) |
| Backend API | `http://localhost:8000/api` |
| **Новый frontend (dev)** | `http://localhost:3000` |

Связь: Vite dev proxy `'/api' → 'http://localhost:8000'`. Это убирает CORS-хлопоты в dev (хотя backend уже разрешает `*`) и оставляет пути API чистыми (`/api/...`). Альтернатива — прямой вызов `http://localhost:8000/api` с абсолютными URL через конфиг; запасной вариант, не основной.

В production: `vite build` → `frontend/dist/`, отдаётся отдельным статик-сервером (например `npx serve dist`) на любом порту, API остаётся на `:8000` (адрес берётся из `VITE_API_BASE`). Backend для этого менять не нужно.

### 2.3 Изоляция

- Файлы нового фронта живут ТОЛЬКО в `ai-roleplay-chat/frontend/`.
- Никаких правок в `app/`, `app/static/`, роутерах, моделях, схемах.
- `.gitignore` фронта (node_modules, dist) — отдельный внутри `frontend/`.
- Никакого нового Python-кода в этой задаче.

---

## 3. Стек и структура

### 3.1 Стек

- **Vue 3** (Composition API, `<script setup lang="ts">`) + **TypeScript** + **Vite** (как рекомендовано ТЗ; React не нужен — проект не ориентирован на React).
- **Pinia** — state management.
- **Vue Router** — навигация без перезагрузки.
- **CSS Variables / design tokens** (нативные CSS-переменные, без тяжёлых CSS-фреймворков), минимум библиотек.
- Виртуализация списка сообщений: на выбор `@vueuse/integrations` + кастомная windowing ИЛИ лёгкая библиотека (например `vue-virtual-scroller`) — решение на Этапе 6 по фактическому числу сообщений; на старте обязателен инкрементальный рендер (см. §6.5).

Доступность инструментов: `node v25.6.1`, `npm 11.9.0` установлены.

### 3.2 Структура директорий (целевая)

```
ai-roleplay-chat/frontend/
├── index.html
├── package.json
├── vite.config.ts            # порт 3000, proxy /api → :8000
├── tsconfig.json
├── .env.development          # VITE_API_BASE=/api (или абсолютный URL)
├── .gitignore
└── src/
    ├── main.ts
    ├── App.vue
    ├── router/
    │   └── index.ts          # / → chat list; /chat/:id → сессия; /chat/:id/character/:cid
    ├── stores/
    │   ├── chats.ts          # список чатов, текущий чат
    │   ├── messages.ts       # история текущего чата
    │   ├── characters.ts     # персонажи сцены + детали/память
    │   ├── relationships.ts  # граф, issues, таймлайн пары
    │   ├── scene.ts          # scene state (время, локации, world)
    │   └── ui.ts             # панели, модалки, drawer'ы, тема, toasts
    ├── api/
    │   ├── client.ts         # fetch-обёртка, обработка ошибок, типы ответов
    │   ├── chats.ts
    │   ├── characters.ts
    │   ├── messages.ts       # в т.ч. SSE-streaming-клиент
    │   ├── scene.ts
    │   ├── relationships.ts
    │   └── index.ts          # экспорт всех модулей
    ├── types/
    │   ├── chat.ts
    │   ├── character.ts
    │   ├── message.ts
    │   ├── scene.ts
    │   ├── relationship.ts
    │   └── sse.ts            # типы SSE-событий
    ├── mocks/
    │   ├── index.ts          # флаг useMocks, переключение mock/API
    │   └── data.ts           # чаты, сообщения, персонажи, world events
    ├── components/
    │   ├── layout/
    │   │   ├── AppLayout.vue
    │   │   ├── Sidebar.vue           # левая панель
    │   │   ├── MainPanel.vue         # центральная
    │   │   └── RightPanel.vue        # правая панель
    │   ├── chat/
    │   │   ├── ChatHeader.vue
    │   │   ├── MessageList.vue
    │   │   ├── MessageItem.vue
    │   │   ├── SystemMessage.vue
    │   │   ├── WorldEvent.vue
    │   │   ├── GenerationIndicator.vue
    │   │   └── Composer.vue
    │   ├── characters/
    │   │   ├── CharacterList.vue
    │   │   ├── CharacterCard.vue
    │   │   ├── CharacterDetails.vue
    │   │   └── RelationshipView.vue
    │   ├── scene/
    │   │   ├── WorldStatePanel.vue   # локация/время/мировые события
    │   │   └── LocationChips.vue
    │   └── common/
    │       ├── Avatar.vue
    │       ├── Badge.vue
    │       ├── ProgressBar.vue
    │       ├── Modal.vue
    │       ├── Skeleton.vue
    │       ├── EmptyState.vue
    │       └── ErrorState.vue
    └── styles/
        ├── tokens.css        # дизайн-токены (см. §5.7)
        ├── base.css          # reset, типографика, скроллбары
        └── main.css
```

Компоненты — по одному назначению, без giant-компонентов.

### 3.3 Зависимости (минимальный набор)

- `vue`, `vue-router`, `pinia`, `typescript`, `vite`, `@vitejs/plugin-vue`.
- `@vueuse/core` (опционально, утилиты: `useLocalStorage`, `useVirtualList` и т.п.).
- Никаких UI-библиотек (Ant Design/Element/Vuetify) на старте — свои компоненты дают контроль над внешним видом и весом.

---

## 4. Архитектура приложения

### 4.1 Слои

1. **`types/`** — TypeScript-интерфейсы, повторяющие Pydantic-схемы (1:1 с §1.5).
2. **`api/`** — единственное место сетевых запросов. Компоненты и store'ы вызывают только функции API-слоя. Внутри `client.ts` — единая обработка HTTP-ошибок (преобразование `{detail}` FastAPI в исключения) и SSE-клиент.
3. **`stores/`** — Pinia-хранилища, разделённые по ответственности (см. §4.2).
4. **`mocks/`** — изолированные mock-данные, включаются флагом `useMocks` (из `import.meta.env.VITE_USE_MOCKS`). Не смешиваются с реальным API.
5. **`components/`** — презентационные компоненты.
6. **`styles/`** — дизайн-система.

### 4.2 State (Pinia) — разделение

| Store | Содержимое | Примечание |
|-------|-----------|------------|
| `chats` | список чатов, `currentChatId`, загрузка списка/деталей, CRUD | детали кэшируются, список обновляется после изменений |
| `messages` | массив сообщений текущего чата, optimistic-сообщение, пагинация (loadMore) | рендер инкрементальный/виртуализированный |
| `characters` | персонажи сцены (`include_player`), детали выбранного, memories/summary по запросу | не блокирует чат при загрузке деталей |
| `relationships` | граф, issues, таймлайн пары | ленивая загрузка, только при открытии панелей |
| `scene` | `SceneState` (время, локации, weather/mood/tension, present ids) | опционально — при недоступности mock |
| `ui` | состояние sidebar/drawer'ов, активная модалка, тосты, выбранный персонаж, тема | без бизнес-данных |

Никаких «giant store». `generation state` — внутри `messages` (активный streaming character + буфер токенов), т.к. привязан к конкретному чату и сообщениям.

### 4.3 Навигация (Vue Router)

- `/` — пустой экран (список чатов в sidebar + placeholder центра) → перенаправление на последний открытый чат из localStorage (как в старом UI).
- `/chat/:chatId` — основная сессия.
- `/chat/:chatId/character/:characterId` — детальная панель персонажа (в правом drawer на desktop / полноэкранно на mobile).
- `/chat/:chatId/relationships` — граф и issues (опционально модалка вместо маршрута).

### 4.4 API-слой

- `client.ts`: `request(method, path, body?)` с обработкой `{detail}`; типизированные методы per-module.
- `messages.ts`: `sendMessage(chatId, content, opts)` возвращает управляемый SSE-стрим — объект с методами `onToken`, `onMessage`, `onDone`, `onError`, `abort()`. Реализуется через `fetch` + `response.body.getReader()` (как в старом фронте, но изолированно и типобезопасно).
- Все «отсутствующие» данные (avatar, world feed) — через `mocks/` и поля `TODO`, НЕ через фейковый API-слой.

### 4.5 Mock-режим

- `VITE_USE_MOCKS=true` → `api/` модули делегируют в `mocks/` (тот же интерфейс функций). Это позволяет разрабатывать UI (Этап 3) без backend.
- `VITE_USE_MOCKS=false` (по умолчанию) → реальный API.
- Переключение только через env-флаг, код не меняется.

---

## 5. UI / компоненты

### 5.1 Общий layout (3 зоны, как в макете)

```
┌────────────┬──────────────────────────────┬─────────────┐
│  Sidebar   │          MainPanel           │ RightPanel  │
│  (чаты)    │  ChatHeader                  │ (персонажи) │
│            │  MessageList                 │             │
│  +поиск    │  GenerationIndicator         │ локации/мир │
│  +создать  │  Composer                    │             │
└────────────┴──────────────────────────────┴─────────────┘
```

- Desktop: все три колонки; Sidebar сворачиваемый (кнопка-иконка), RightPanel скрывается/открывается по кнопке.
- Центральная область — основной фокус, занимает максимальную ширину.
- RightPanel узкая (~280–320px), независима от истории сообщений.

### 5.2 Sidebar (левая панель)

- Заголовок «Сцены / Чаты» + кнопка «+ Новый чат» (модалка: название, сюжет/prompt, модель из `GET /api/models`, режим thinking).
- Поле поиска по имени чата (клиентская фильтрация).
- Список чатов: имя + превью последнего сообщения + время; активный чат выделен; удаление/переименование по hover; можно перетаскивать (не обязательно).
- Кнопка сворачивания панели (icon-only на узких экранах).

### 5.3 Центральная область (chat)

- **ChatHeader**: название сцены, модель, бейдж режима thinking (Instant ⚡ / Thinking 🧠), кнопки: отношения, настройки, очистить историю, удалить чат, свернуть правую панель.
- **MessageList**:
  - `MessageItem` — сообщение персонажа: `Avatar` + имя (accent-цвет) + текст + время + метаданные (visibility/channel при необходимости); hover-действия: перегенерация (для ответа NPC), удаление.
  - Сообщение пользователя — визуально отделено (своя вёрстка/цвет, выравнивание вправо или собственный стиль), без спутанности с NPC.
  - `SystemMessage` — центрированный тонкий блок (смена сцены, перемещения) с иконкой; НЕ конкурирует с репликами.
  - `WorldEvent` — карточка события мира (idle/reaction): другой фон, иконка «мир», текст от третьего лица. **Никогда не выглядит как реплика персонажа.**
  - Разделение по персонажам: имя + accent-цвет + возможно тонкая линия/разделитель между блоками разных авторов.
  - Streaming: текущее сообщение рендерится/обновляется точечно (см. §6.1), новые сообщения появляются с лёгкой анимацией появления.
- **GenerationIndicator**: «Alice размышляет…» с точками/пульсом в месте потока, без «прыжков» раскладки.
- **Composer**: авто-растущий textarea (min-height → max-height, скролл после), Enter = отправить, Shift+Enter = перенос строки, кнопка Send; в состоянии генерации — кнопка **Stop** (POST `/stop-generation`); disabled при отсутствии чата/генерации; счётчик не нужен.

### 5.4 Composer — детали

- Отправка по Enter, Shift+Enter — новая строка; если `IME composition` активен — не отправлять.
- Пока идёт генерация: поле не disabled (можно набирать), но отправка заблокирована (бэкенд вернёт 409); показываем Stop.
- Ошибка/промпт при 429: инлайн-подсказка «Подождите 5 секунд».
- Не фиксированная высота: `field-sizing`-подход не используется, делаем JS/`ResizeObserver` рост до `max-height` (~40% высоты панели).

### 5.5 Правая панель

- **Список персонажей сцены**: Avatar + имя + accent + «краткое состояние» (location или активное действие — поле, которое потом можно наполнить).
- Клик по персонажу → **CharacterDetails** (см. §5.6).
- **WorldStatePanel**: текущая локация/время (`SceneState.time_of_day`, weather, mood, tension), список присутствующих, смена локации игрока (PATCH scene / PATCH location).
- Секция **мировые события** (world feed): последние `system`/world события; placeholder «нет новых событий».
- Сворачивается в drawer на мобильных.

### 5.6 Панель персонажа / отношения / мир

**CharacterDetails** (drawer/модалка):
- Большой `Avatar`, имя, badge «Игрок» при `is_player`;
- описание (personality, traits, background, speech_style);
- текущая локация (редактируемая, PATCH `/characters/{id}/location`);
- память персонажа (GET `/characters/{id}/memories`) — список с категорией/важностью;
- кнопка «Отношения» → открывает `RelationshipView` (см. ниже).

**RelationshipView** (компактно, не перегружая экран):
- По данным `GET /api/chats/{id}/relationships/graph` или per-character endpoints;
- для выбранного персонажа: направленные отношения к другим — type badge + 5 progress-баров (affection, trust, attraction, resentment, jealousy);
- открытые issues (badge счётчиком), переход в граф/таймлайн пары;
- полный граф и таймлайн — в модалке «Отношения» (перенос логики из старого `rel-graph-view`, но в Vue-компонентах).

**WorldStatePanel**: время, погода, настроение, напряжение (progress), активная цель, присутствующие — из `SceneStateRead`. Если endpoint недоступен — mock + TODO.

### 5.7 Дизайн-система (tokens)

Тёмная тема, развивающая палитру существующего CSS:

```css
:root {
  --bg-primary: #12141c;        /* фон приложения */
  --bg-secondary: #181b25;      /* панели */
  --bg-panel: #1e222e;          /* карточки/подложка сообщений */
  --bg-hover: #232836;
  --border: #2a2f3d;
  --border-strong: #353b4d;
  --text-primary: #e8eaf0;
  --text-secondary: #9aa2b5;
  --text-muted: #6b7286;
  --accent: #6c8cff;            /* акцент (умеренный) */
  --accent-soft: rgba(108, 140, 255, 0.14);
  --danger: #e5484d;
  --success: #30a46c;
  --warning: #f5a524;
  --radius-sm: 6px;
  --radius: 10px;
  --radius-lg: 14px;
  --shadow-1: 0 1px 2px rgba(0,0,0,.4);
  --shadow-2: 0 8px 24px rgba(0,0,0,.35);
  --font-ui: "Segoe UI", system-ui, sans-serif;
  --font-mono: "Cascadia Code", Consolas, monospace;
  --space-1: 4px; ... --space-6: 24px;
  --transition-fast: 120ms ease;
  --sidebar-width: 280px;
  --right-panel-width: 300px;
}
```

Принципы:
- аккуратные панели, тонкие borders, скругления 6–14px, мягкие hover/focus (outline через `--accent-soft` + border);
- акцент умеренный; избыточная декоративность исключена (читаемость RP важнее);
- accent-цвета персонажей генерируются из хеша имени, но с ограниченной палитрой «приятных» тонов (детерминированно, без пёстроты).

---

## 6. Поведение и состояния

### 6.1 Генерация и streaming

Состояния (в `messages` store): `idle → sending → waiting → streaming(character) → done | error`, плюс `stopped`.

Алгоритм (на основе проверенного старого фронта, изолированный и типизированный):
1. Отправка POST `/api/chats/{id}/message` с optimistic-сообщением пользователя.
2. Открыть SSE-стрим, слушать события:
   - `message` role=user → заменить optimistic на серверное;
   - `token` → если `character_id` сменился — зафинализировать предыдущее и создать новое; иначе дописать в текущий буфер (обновлять только DOM сообщения, не перерисовывать весь список);
   - `message` role=character → зафинализировать streaming-сообщение, добавить в store;
   - `message` role=system → добавить `SystemMessage`;
   - `done` → сбросить генерацию, обновить rate-limit-таймер;
   - `error` → показать инлайн-ошибку (без потери введённого текста).
3. Stop → AbortController + `POST /stop-generation`.
4. Перезагрузка страницы → `GET /generation-status` + localStorage (как старый фронт), при `active: true` — показать «генерация продолжается» и при запросе `GET messages` увидеть уже сохранённые ответы.
5. Streaming НЕ имитируется поверх готового текста — используется реальный поток backend.

Требования «без прыжков»: фиксированная высота области сообщений, автоскролл только если пользователь у нижней границы (иначе — индикатор «новые сообщения»).

### 6.2 Ошибки и пустые состояния

Видимые пользователю состояния (компоненты `ErrorState`/`EmptyState`/`Skeleton`):
- backend недоступен (`/api/health` fail) — баннер + retry;
- Ollama недоступна — при `GET /api/models` `{error: "Ollama недоступна"}` → предупреждение при создании/настройке чата;
- generation failed / network error — инлайн-карточка у последнего сообщения с кнопкой «Повторить» (resend/regenerate);
- 429 — таймер «можно отправлять через N сек»;
- 409 — блокировка composer с пояснением;
- пустой чат / нет персонажей — подсказка «создайте персонажей через настройки»;
- loading — skeleton в списках, но чат не блокируется (панели грузятся независимо).

### 6.3 Multi-character

- Без предположения «один AI». Лента строится по `character_id` из `messages`.
- Ответы NPC идут по очереди (backend сам генерирует по `order_index`); фронт просто отображает последовательность авторов.
- Акцент-цвет и имя каждого персонажа консистентны между лентой и правой панелью (общий `characters` store).
- Восстановление удалённых персонажей: `character_id` может быть `null` (после DELETE) — сообщение рендерим с нейтральным именем «Персонаж»/«Неизвестный».

### 6.4 World events / system messages

- `role === "system"` → `SystemMessage` (перемещения, смена сцены).
- Отдельный визуальный паттерн для «событий мира» (idle/NPC-to-NPC/world reaction) — компонент `WorldEvent`: отличный фон + иконка 🌍/строки-«взгляд со стороны», текст нейтральным шрифтом. Данных от backend пока нет → в `mocks/`, интерфейс готов к будущему endpoint (TODO: `GET /api/chats/{id}/events`).

### 6.5 Производительность

- `MessageList` НЕ рендерит всю историю: инкрементальный рендер на старте (первые ~100) + подгрузка при скролле вверх (`GET /messages?limit&offset`); на Этапе 6 — виртуализация (windowed rendering) для длинных сессий.
- Streaming обновляет только одно сообщение (ref/component-level), без пере-рендера списка.
- Компоненты `<Suspense>`/ленивая загрузка для тяжёлых панелей (relationships, timeline).
- Избегать глубоких реактивных структур: плоские массивы с `Map`-индексами по id, `shallowRef` для больших списков.
- Debounce поиска по чатам; `requestIdleCallback`/`nextTick` для не-критичных операций.

### 6.6 Responsive

- **Desktop (приоритет):** 3 колонки.
- **Tablet (<1024px):** правая панель — скрываемая (overlay/drawer), sidebar сворачивается в icon-режим.
- **Mobile (<768px):** sidebar → drawer (гамбургер), правая панель → drawer, центральный чат на весь экран; composer всегда доступен (фиксирован внизу); брейкпоинты в tokens (`--bp-tablet`, `--bp-mobile`).

---

## 7. Этапы реализации (дорожная карта)

Работа идёт итеративно. После каждого этапа: запуск нового фронта, проверка компиляции/рантайма, проверка, что старый фронт `:8000` работает, проверка интеграции с backend. Проблемы старого фронта, не вызванные нашей задачей, не исправляем.

### Этап 1 — Исследование ✅ (выполнен, резюме в §1)

### Этап 2 — Shell приложения
- Создать `ai-roleplay-chat/frontend/` (Vite + Vue 3 + TS), настроить `vite.config.ts` (порт 3000, proxy `/api`).
- Дизайн-токены, `base.css`, `AppLayout` (sidebar/main/right panel), базовый responsive, пустые состояния.
- Проверка: `npm run dev` на `:3000`, страница без ошибок, старый фронт на `:8000` жив.

### Этап 3 — Chat UI на mock-данных
- Компоненты: Sidebar (список/поиск/создание), ChatHeader, MessageList/MessageItem/SystemMessage/WorldEvent, Composer, GenerationIndicator.
- Включён `VITE_USE_MOCKS=true`: мок чаты/сообщения/персонажи/события из `src/mocks/`.
- Состояния генерации имитируются (таймер), но интерфейс уже полный.
- Проверка: визуал соответствует концепции макета (сверить с `docs/Frontend.png` вручную).

### Этап 4 — Подключение backend
- Реализовать полный `api/` слой + SSE-клиент; выключить mocks.
- Реальные: список/создание/детали чатов, сообщения с пагинацией, отправка + streaming + stop, восстановление после перезагрузки, сцена, персонажи.
- Проверка: полноценный RP-раунд через новый UI против живого backend; rate-limit и 409 отображаются корректно.

### Этап 5 — Character / Relationship UI
- Правая панель: список персонажей, детали, память, смена локации.
- RelationshipView: граф/список метрик, issues, таймлайн пары (реальные endpoints).
- WorldStatePanel: время/погода/присутствующие из `GET /scene`.
- Проверка: открыть чат с несколькими персонажами, посмотреть отношения.

### Этап 6 — Polish
- Анимации появления, hover/focus, переходы drawer'ов.
- Skeleton/error/empty states, тосты.
- Виртуализация длинного списка, инкрементальный рендер.
- Респонсивная полировка (tablet/mobile drawer'ы).
- Финальная сверка с `docs/Frontend.png` и критериями готовности (§8).

---

## 8. Критерии готовности (проверочный лист первого релиза)

1. Старый frontend работает без изменений на `:8000`.
2. Новый frontend запускается отдельно на `:3000` (dev), собирается в `dist/` для статик-сервера.
3. Новый frontend подключается к существующему backend (через proxy/VITE_API_BASE).
4. Список чатов открывается.
5. Чат открывается (маршрут `/chat/:id`).
6. Сообщения видны, включая system-сообщения.
7. Сообщение отправляется, отображается optimistic + серверное эхо.
8. Streaming работает (пофрагментный вывод `token`).
9. Состояние генерации видно («X размышляет…» / Stop), стоп работает.
10. Персонажи отображаются отдельно (акценты, аватары-инициалы).
11. Есть правая информационная панель (персонажи/мир/отношения).
12. Есть composer с авто-ростом, Enter/Shift+Enter, disabled/loading состояниями.
13. UI соответствует общей концепции макета (после ручной сверки с PNG).
14. Нет giant-компонентов и giant-store.
15. Backend не менялся ради базовой работы UI.
16. Старый frontend без регрессий.
17. Проект запускается без ручного редактирования исходников (env/конфиг покрывают пути).

---

## 9. Риски и решения

| Риск | Миграция | Решение |
|------|----------|---------|
| Проект без Node-опыта | низкий | Минимальный набор зависимостей, свои компоненты |
| CORS в production | низкий | CORS уже `*`; в production — статик-сервер рядом с API или абсолютный `VITE_API_BASE` |
| SSE через fetch (нет EventSource) | низкий | Перенести проверенную реализацию чтения потока из старого `app.js` |
| Большой объём сообщений → тормоза | средний | Инкрементальный рендер + виртуализация (Этап 6); точечное обновление streaming-сообщения |
| Отсутствие данных (avatar, world events, idle) | средний | Изолированный mock-слой + TODO; UI готов к будущим endpoints |
| Двойное управление чатом в двух UI | низкий | Общий backend и одна БД; новый UI не меняет данные, которые не трогает пользователь |
| Расхождение с макетом (нет чтения PNG) | средний | Сверить зоны вручную на Этапе 3 и 6 |

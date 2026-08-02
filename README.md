# AI Roleplay Chat

Локальное веб-приложение для ролевых игр с AI-персонажами через Ollama.

## Документация

Техническая документация — в [`docs/`](docs/):

- [`docs/README.md`](docs/README.md) — индекс, обзор и структура проекта
- [`docs/architecture.md`](docs/architecture.md) — архитектура, жизненный цикл раунда, контекст, память
- [`docs/api.md`](docs/api.md) — полный справочник HTTP API и SSE-событий
- [`docs/database.md`](docs/database.md) — схема БД и миграции
- [`docs/configuration.md`](docs/configuration.md) — все настройки через `.env`
- [`docs/relations.md`](docs/relations.md) — система отношений (§1–§15)

## Требования

- Python 3.10+
- [Ollama](https://ollama.com/) **≥ 0.9.0** установлена и запущена (`ollama serve`)
- Модель для генерации с поддержкой thinking (по умолчанию `qwen3-coder:30b-a3b-q4_K_M`, можно сменить в интерфейсе)

## Установка

```bash
# Клонировать / перейти в папку проекта
cd ai-roleplay-chat

# Установить зависимости
pip install -r requirements.txt

# Запустить сервер
python -m uvicorn main:app --host 0.0.0.0 --port 8000
```

## Использование

1. Откройте браузер на `http://localhost:8000`
2. Нажмите **"+ Новый чат"**, укажите название, сюжет, модель и **режим генерации**
3. Добавьте персонажей через **Настройки (⚙️) → Персонажи**
4. Начните диалог — все персонажи ответят по очереди

### Режим генерации (Instant / Thinking)

У каждого чата свой режим:

| Режим | UI | Поведение |
|-------|-----|----------|
| **С размышлением** (Thinking) | 🧠 | Ollama `think: true` — медленнее, обычно качественнее |
| **Быстрый** (Instant) | ⚡ | Без think — быстрее ответы |

Переключение:
- при создании чата
- в **Настройки → Основное**
- быстрый клик по бейджу в шапке чата

Настройка сохраняется в БД (`thinking_mode`) и переживает перезагрузку.

### Локации и восприятие (perception)

Каждое сообщение — **событие мира**. Персонаж получает в LLM-контекст только то, что может воспринять.

| Поле | Где | Смысл |
|------|-----|--------|
| **Локация игрока** | Настройки → Основное | Где происходит реплика игрока |
| **Локация персонажа** | Карточка персонажа | Где стоит персонаж |
| **visibility** | API / сообщение | `local` (по умолчанию), `private`, `targeted`, `public`, `global` |

Правила (MVP):

- **local** — слышат только персонажи **в той же локации**, что и событие
- **private / targeted** — только указанные `target_character_ids` (и автор реплики)
- **public / global** — все
- Пустые локации у всех = общая сцена (обратная совместимость)
- Если Алиса услышала комплимент в гостиной, а Боб на улице — Боб **не** видит это в контексте. Если потом Алиса **расскажет** Бобу на улице — Боб узнает из **её** реплики, а не из исходного события.

Фильтрация идёт **до** вызова модели (context isolation), а не только текстом в промпте.

### Примеры реплик персонажа

В поле **«Примеры реплик»** можно писать многострочные образцы речи.  
Примеры разделяются **только** строкой `---` (переносы строк внутри примера не режут его на части).

```text
*смотрит в сторону*
— Не знаю… может, и прав.
---
*усмехается*
— Ладно. Попробуем ещё раз.
```

### Доступ с других устройств в Wi-Fi

1. Узнайте IP-адрес компьютера:
   - Windows: `ipconfig` (IPv4-адрес)
   - Linux/Mac: `ip addr` или `ifconfig`
2. На другом устройстве откройте `http://<IP>:8000`

## API Endpoints

| Метод | Путь | Описание |
|-------|------|----------|
| GET | `/api/health` | Проверка сервера |
| POST | `/api/chats` | Создать чат (`thinking_mode`, `player_location` optional) |
| GET | `/api/chats` | Список чатов |
| GET | `/api/chats/{id}` | Детали чата + персонажи + 50 сообщений |
| PUT | `/api/chats/{id}` | Обновить чат (в т.ч. `thinking_mode`, `player_location`) |
| DELETE | `/api/chats/{id}` | Удалить чат |
| DELETE | `/api/chats/{id}/messages` | Очистить историю |
| POST | `/api/chats/{id}/characters` | Добавить персонажа (`location` optional) |
| GET | `/api/chats/{id}/characters` | Список персонажей |
| PUT | `/api/characters/{id}` | Обновить персонажа |
| DELETE | `/api/characters/{id}` | Удалить персонажа |
| POST | `/api/chats/{id}/message` | Отправить сообщение (`visibility`, `target_character_ids` optional) |
| GET | `/api/chats/{id}/messages` | История с пагинацией |
| GET | `/api/characters/{id}/memories` | Память персонажа |
| DELETE | `/api/memories/{id}` | Удалить воспоминание |
| GET | `/api/chats/{id}/relationships/graph` | Граф отношений чата (узлы + рёбра с `open_issue_count`) |
| GET | `/api/chats/{id}/relationships/issues?state=open\|resolved\|all` | Все вопросы чата с именами пары |
| GET | `/api/chats/{id}/relationships/{s}/{t}/timeline` | Таймлайн пары: события + issues + source-сообщения (пагинация) |

## Структура проекта

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
├── scripts/                 # CLI-скрипты (backfill_embeddings)
├── tests/                   # pytest + tests/eval (харнесс и метрики) + tests/golden
└── .env / .env.example      # конфигурация
```

## Особенности

- **Location perception**: персонажи не «слышат» события из других локаций (LOCAL по умолчанию)
- **Направленные отношения**: ребро `A→B` не зеркалируется в `B→A` — односторонняя привязанность, неразделённая симпатия и т.п. валидны (см. `docs/relations.md`)
- **Отношения в UI**: кнопка **🕸️** в шапке чата открывает модалку с графом отношений (Vanilla SVG, направленные рёбра, цвет по метрикам, перетаскивание нод), таймлайном пары (события LLM/Затухание/Вручную/Архив, спарклайны метрик, source-сообщения), открытыми вопросами по всему чату (с решением) и ручным редактированием (тип, 5 метрик, описание, добавление ребра)
- **Per-chat thinking mode**: у каждого чата свой Instant/Thinking; рассуждения модели не показываются и не сохраняются
- **Автоматическая память**: после каждого раунда AI извлекает важные факты и сохраняет их
- **Ограничение памяти**: максимум 20 фактов на персонажа, в контекст попадают 10 последних
- **Retry-логика**: 3 попытки при ошибках соединения с Ollama
- **Rate limiting**: 1 сообщение в 5 секунд на чат
- **localStorage**: последний открытый чат восстанавливается при перезагрузке
- **Проверка Ollama при старте**: в консоль выводится статус подключения

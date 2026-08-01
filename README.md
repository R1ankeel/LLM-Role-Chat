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

## Структура проекта

```
ai-roleplay-chat/
├── main.py              # FastAPI app + lifespan (Ollama check) + статика
├── database.py          # SQLite + SQLAlchemy engine + migrations
├── models.py            # ORM: Chat, Character, Message, Memory, MessagePresence
├── schemas.py           # Pydantic-схемы
├── crud.py              # CRUD-функции
├── perception.py        # Правила восприятия (локация / visibility)
├── witness_model.py     # Фильтрация истории по presence
├── ollama_client.py     # Клиент Ollama (generate + memory extraction + retry)
├── chat_engine.py       # Движок чата (раунды + presence + память)
├── ratelimit.py         # Rate limiter (5 сек между сообщениями)
├── routers/
│   ├── chats.py         # CRUD чатов
│   ├── characters.py    # CRUD персонажей + память
│   └── chat_engine.py   # Отправка сообщений + история
├── static/
│   ├── index.html       # SPA-фронтенд
│   ├── style.css        # Тёмная тема
│   └── app.js           # Логика фронтенда
└── requirements.txt
```

## Особенности

- **Location perception**: персонажи не «слышат» события из других локаций (LOCAL по умолчанию)
- **Per-chat thinking mode**: у каждого чата свой Instant/Thinking; рассуждения модели не показываются и не сохраняются
- **Автоматическая память**: после каждого раунда AI извлекает важные факты и сохраняет их
- **Ограничение памяти**: максимум 20 фактов на персонажа, в контекст попадают 10 последних
- **Retry-логика**: 3 попытки при ошибках соединения с Ollama
- **Rate limiting**: 1 сообщение в 5 секунд на чат
- **localStorage**: последний открытый чат восстанавливается при перезагрузке
- **Проверка Ollama при старте**: в консоль выводится статус подключения

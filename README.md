# AI Roleplay Chat

Локальное веб-приложение для ролевых игр с AI-персонажами через Ollama.

## Требования

- Python 3.10+
- [Ollama](https://ollama.com/) установлена и запущена (`ollama serve`)
- Модель для генерации (по умолчанию `qwen3-coder:30b-a3b-q4_K_M`, можно сменить в интерфейсе)

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
2. Нажмите **"+ Новый чат"**, укажите название, сюжет и модель
3. Добавьте персонажей через **Настройки (⚙️) → Персонажи**
4. Начните диалог — все персонажи ответят по очереди

### Доступ с других устройств в Wi-Fi

1. Узнайте IP-адрес компьютера:
   - Windows: `ipconfig` (IPv4-адрес)
   - Linux/Mac: `ip addr` или `ifconfig`
2. На другом устройстве откройте `http://<IP>:8000`

## API Endpoints

| Метод | Путь | Описание |
|-------|------|----------|
| GET | `/api/health` | Проверка сервера |
| POST | `/api/chats` | Создать чат |
| GET | `/api/chats` | Список чатов |
| GET | `/api/chats/{id}` | Детали чата + персонажи + 50 сообщений |
| PUT | `/api/chats/{id}` | Обновить чат |
| DELETE | `/api/chats/{id}` | Удалить чат |
| DELETE | `/api/chats/{id}/messages` | Очистить историю |
| POST | `/api/chats/{id}/characters` | Добавить персонажа |
| GET | `/api/chats/{id}/characters` | Список персонажей |
| PUT | `/api/characters/{id}` | Обновить персонажа |
| DELETE | `/api/characters/{id}` | Удалить персонажа |
| POST | `/api/chats/{id}/message` | Отправить сообщение |
| GET | `/api/chats/{id}/messages` | История с пагинацией |
| GET | `/api/characters/{id}/memories` | Память персонажа |
| DELETE | `/api/memories/{id}` | Удалить воспоминание |

## Структура проекта

```
ai-roleplay-chat/
├── main.py              # FastAPI app + lifespan (Ollama check) + статика
├── database.py          # SQLite + SQLAlchemy engine
├── models.py            # ORM: Chat, Character, Message, Memory
├── schemas.py           # Pydantic-схемы
├── crud.py              # CRUD-функции
├── ollama_client.py     # Клиент Ollama (generate + memory extraction + retry)
├── chat_engine.py       # Движок чата (раунды + извлечение памяти)
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

- **Автоматическая память**: после каждого раунда AI извлекает важные факты и сохраняет их
- **Ограничение памяти**: максимум 20 фактов на персонажа, в контекст попадают 10 последних
- **Retry-логика**: 3 попытки при ошибках соединения с Ollama
- **Rate limiting**: 1 сообщение в 5 секунд на чат
- **localStorage**: последний открытый чат восстанавливается при перезагрузке
- **Проверка Ollama при старте**: в консоль выводится статус подключения
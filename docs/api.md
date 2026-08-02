# HTTP API

Базовый префикс: `/api`. Документация OpenAPI доступна на `/docs` (Swagger UI).

Формат дат — ISO 8601. JSON-поля `target_character_ids` и `source_message_ids` в БД хранятся как JSON-строки, но в API отдаются как массивы.

## Оглавление

- [Chats](#chats)
- [Characters](#characters)
- [Messages / Chat engine](#messages--chat-engine)
- [Memory jobs](#memory-jobs)
- [Relationships](#relationships)
- [Служебные](#служебные)
- [Формат SSE](#формат-sse)

---

## Chats

### POST `/api/chats` — создать чат

Тело: `ChatCreate` (`name`; `general_prompt`, `model_name`, `max_history_length`, `thinking_mode`, `player_location`, `locations`, `player_name` — опционально).

Создаёт чат, автоматически создаёт player-персонажа (имя из `player_name`, по умолчанию «Игрок») и сбрасывает динамический `num_ctx` (`ctx_state.reset`). Ответ — 201 с объектом `ChatRead`.

### GET `/api/chats` — список чатов

Параметры: `skip` (default 0), `limit` (default 100). Сортировка: новые первыми.

### GET `/api/chats/{chat_id}` — детали чата

Ответ — `ChatDetail`: карточка чата + все персонажи (включая player) + последние 50 сообщений.

### PUT `/api/chats/{chat_id}` — обновить чат

Тело: `ChatUpdate` (частичное). Поля: `name`, `general_prompt`, `model_name`, `max_history_length`, `thinking_mode`, `player_location`, `locations`.

### DELETE `/api/chats/{chat_id}` — удалить чат

Каскадно удаляет персонажей, сообщения, память, summaries, scene state; чистит `ctx_state`. Ответ — 204.

### DELETE `/api/chats/{chat_id}/messages` — очистить историю

Query-параметр `scope` (Literal):
- `messages` — только сообщения;
- `messages_memories` — сообщения + воспоминания;
- `full` — сообщения + воспоминания + summaries.

400 при невалидном scope; 404 если чат не найден.

### GET `/api/chats/{chat_id}/scene` — состояние сцены

Ответ — `SceneStateRead`: `time_of_day`, `character_locations`, `custom_state` (weather, mood, tension, plot_flags, active_goal, important_objects, active_events, time_progression, stagnation_rounds, round_count, active_goals), `present_character_ids`, `player_location`, `updated_at`.

### PATCH `/api/chats/{chat_id}/scene` — обновить сцену

Тело: `SceneStateUpdate` (частичное): `time_of_day`, `character_locations`, `custom_state`. Пересчитывает присутствующих персонажей.

---

## Characters

### POST `/api/chats/{chat_id}/characters` — добавить персонажа

Тело: `CharacterCreate` (`name` + `personality`, `traits`, `speech_style`, `example_messages`, `boundaries`, `background`, `relationships`, `appearance`, `location`, `temperature` (0–2), `order_index`, `is_player`; `initial_relationships` — массив `InitialRelationship`). `chat_id` берётся из пути. `order_index` должен быть уникален в чате (400 иначе). `avatar_url` при создании игнорируется (аватар задаётся только через upload-endpoint, Этап B). При создании с `initial_relationships` создаются рёбра отношений.

### GET `/api/chats/{chat_id}/characters` — список персонажей

Query-параметр `include_player` (default false). Сортировка по `order_index`.

### PUT `/api/characters/{character_id}` — обновить персонажа

Тело: `CharacterUpdate` (частичное): `name`, `personality`, `traits`, `speech_style`, `example_messages`, `boundaries`, `background`, `relationships`, `appearance`, `avatar_url`, `location`, `temperature` (0–2, иначе 422), `order_index`, `is_player`. Проверка уникальности `order_index`; нельзя менять `is_player` у player-персонажа.

### DELETE `/api/characters/{character_id}` — удалить персонажа

204. Сообщения персонажа остаются в истории (FK `ON DELETE SET NULL`), память удаляется каскадно. Player-персонажа удалить нельзя (400).

### PUT `/api/chats/{chat_id}/player` — переименовать игрока

Тело: `{"name": str}`. 400 при пустом name; 404 если player не создан.

### GET `/api/characters/{character_id}/memories` — память персонажа

Хронологический список `MemoryRead` (содержимое + `importance`, `category`, `last_accessed_at`, `source_message_ids`).

### POST `/api/characters/{character_id}/memories` — добавить воспоминание

Тело: `MemoryCreate` (`content`, `importance`, `category`, `chat_id`, `character_id`). Проверка, что `chat_id` совпадает с чатом персонажа (400); дубликат по хэшу — 409; при превышении лимита на персонажа выселяется наименее ценное воспоминание.

### PUT `/api/memories/{memory_id}` — обновить воспоминание

Тело: `MemoryUpdate` (частичное): `content`, `importance`, `category`.

### DELETE `/api/memories/{memory_id}` — удалить воспоминание

204 / 404.

### GET `/api/characters/{character_id}/summary` — саммари сессии

Ответ — `CharacterSummaryRead`. 404 если саммари нет.

### PATCH `/api/characters/{character_id}/location` — сменить локацию вручную

Тело: `{"location": str}`. Ручной override локации персонажа (например, когда LLM ошибся).

---

## Messages / Chat engine

### POST `/api/chats/{chat_id}/message` — отправить сообщение (SSE)

Тело: `UserMessage`:
- `content: str` (обязательно, непустое);
- `visibility?: private|local|targeted|public|global`;
- `target_character_ids?: int[]`.

Ограничения: 400 — пустое сообщение; 404 — чат не найден; 429 — слишком часто (rate limit 5 сек, считается от завершения прошлой генерации); 409 — генерация уже идёт.

Ответ: `text/event-stream` (см. [Формат SSE](#формат-sse)). Пользовательское сообщение эхом возвращается событием `message`, затем на каждую реплику NPC — `token`-события и финальное `message`, в конце — `done`.

### GET `/api/chats/{chat_id}/messages` — история с пагинацией

Параметры: `limit` (1–500, default 50), `offset` (>= 0). Хронологический порядок.

### DELETE `/api/chats/{chat_id}/messages/{message_id}` — удалить сообщение

204. Удаление **пользовательского** сообщения каскадно удаляет все последующие (весь раунд); удаление ответа персонажа — только его. 409 при активной генерации.

### POST `/api/chats/{chat_id}/messages/{message_id}/regenerate` — перегенерировать ответ (SSE)

Только для сообщений `role=character` (400 иначе). Пересобирает контекст, генерирует новый текст, сохраняет его и удаляет старый. Формат SSE тот же, что у отправки сообщения.

### POST `/api/chats/{chat_id}/stop-generation` — остановить генерацию

Отменяет активную задачу генерации. Ответ `{"status": "cancelled"}` или 404, если генерации нет. Снимает rate limit.

### GET `/api/chats/{chat_id}/generation-status`

Ответ: `{"active": bool}`. Используется фронтендом для восстановления состояния после перезагрузки.

---

## Memory jobs

Роутер `jobs` (prefix `/api/jobs`, tags `memory-jobs`).

### GET `/api/jobs/chats/{chat_id}/memory-jobs` — задачи чата

Query-параметры: `status` (фильтр: pending/running/succeeded/failed/dead_letter), `limit` (1–200, default 50). Новые первыми.

### GET `/api/jobs/memory-jobs/{job_id}` — конкретная задача

Ответ — `MemoryJobRead` (включая `payload`, `result`, `error_message`, `attempt`, `max_attempts`, `correlation_id`).

### POST `/api/jobs/memory-jobs/{job_id}/retry` — ручной ретрай

Переводит `failed`/`dead_letter` задачу в `pending`, сбрасывает попытки. 404 если не найдена или не ретраябельна.

### POST `/api/jobs/memory-jobs/cleanup` — очистка старых задач

Query-параметр `days` (1–365, default 30). Удаляет завершённые/dead-letter задачи старше периода. Ответ: `{"deleted": N}`.

---

## Relationships

Роутер `relationships`.

### GET `/api/chats/{chat_id}/characters/{character_id}/relationships`

Исходящие отношения (где персонаж — источник). Список `CharacterRelationshipRead`.

### GET `/api/chats/{chat_id}/characters/{character_id}/relationships/received`

Входящие отношения (где персонаж — цель).

### GET `/api/chats/{chat_id}/relationships/{source_id}/{target_id}`

Отношение пары. 404 если не существует.

### PUT `/api/chats/{chat_id}/relationships/{source_id}/{target_id}`

Ручное обновление: `CharacterRelationshipUpdate` — `relationship_type`, `affection`, `trust`, `attraction`, `resentment`, `jealousy`, `description`. Создаёт отношение, если его нет. Метрики клампаются в 0–100.

Валидация: `relationship_type` проверяется по whitelist `relationship_valid_types` и графу переходов — 400 при недопустимом значении или недопустимом переходе (Sprint 4 п.20). После применения старые события сворачиваются в архив (`kind="archive"`, Sprint 4 п.21).

### GET `/api/chats/{chat_id}/relationships/graph`

Весь граф отношений чата (Sprint 4 п.24): узлы-персонажи (NPC + игрок) и все направленные рёбра с метриками и количеством открытых вопросов. Ответ:

```json
{
  "characters": [{"id": 1, "name": "Аня", "is_player": false, "location": "гостиная"}],
  "edges": [{
    "id": 3, "source_character_id": 1, "target_character_id": 2,
    "relationship_type": "друг", "affection": 70, "trust": 60,
    "attraction": 0, "resentment": 5, "jealousy": 10,
    "description": "", "open_issue_count": 1
  }]
}
```

### GET `/api/chats/{chat_id}/relationships/issues`

Все вопросы чата (Sprint 4 п.26). Query-параметр `state`: `open` (default) | `resolved` | `all`. Каждый элемент — `RelationshipIssueRead` + `source_character_id`, `target_character_id`, `source_name`, `target_name`.

### POST `/api/chats/{chat_id}/relationships/analyze`

On-demand повторный анализ отношений за один раунд (Sprint 4 п.23). Query-параметр `round_id` — опционально; по умолчанию берётся последний раунд, для которого уже создавались события отношений. 404 — раунд/чат не найден, 400 — нет существующих раундов. Ответ — summary батча:

```json
{
  "round_id": "r1-m7",
  "analyzed_pairs": 6,
  "applied_deltas": 3,
  "created_issues": 1,
  "resolved_issues": 0,
  "created_events": 3,
  "decay_events": 0,
  "pruned_events": 0
}
```

### GET `/api/chats/{chat_id}/relationships/{source_id}/{target_id}/timeline`

Пагинированная лента отношения (Sprint 4 п.23): события + issues + присоединённые source-сообщения. Query-параметры: `limit` (1–500, default 100), `offset` (>= 0). Ответ:

```json
{
  "events": [{"id": 1, "kind": "llm", "description": "...", "reason": "",
              "delta_affection": 5, "delta_trust": 0, "delta_attraction": 0,
              "delta_resentment": 0, "delta_jealousy": 0,
              "affection_after": 55, "trust_after": 50, "attraction_after": 0,
              "resentment_after": 0, "jealousy_after": 0,
              "importance": 6, "round_id": "r1-m7", "timestamp": "...",
              "source_messages": [{"id": 7, "role": "user", "content": "...", "timestamp": "..."}]}],
  "issues": ["RelationshipIssueRead"],
  "messages": ["MessageRead"],
  "pagination": {"limit": 100, "offset": 0, "total_events": 15, "total_issues": 2, "total": 17}
}
```

### GET `/api/relationships/{relationship_id}/events`

Последние события отношения (до 20), `RelationshipEventRead`.

### GET `/api/chats/{chat_id}/relationships/{source_id}/{target_id}/issues`

Query-параметр `state`: `open` (default) | `resolved` | `all`. Сортировка: importance DESC, created_at DESC, id DESC.

### POST `/api/chats/{chat_id}/relationships/{source_id}/{target_id}/issues/{issue_id}/resolve`

Ручное закрытие issue: тело `{"reason": str}`. Только если issue принадлежит именно этой паре.

---

## Служебные

### GET `/api/health`

`{"status": "ok"}` — liveness.

### GET `/api/models`

Список моделей Ollama (`GET /api/tags` → отсортированные уникальные имена). При недоступности Ollama: `{"models": [], "error": "..."}`.

### GET `/` и `/chat/{chat_id}`

Статический SPA (`index.html`).

---

## Формат SSE

Все события — строки `data: {json}\n\n`.

| type | payload | когда |
|------|---------|-------|
| `message` | `{type, message: MessageRead}` | эхо пользователя, реплика NPC, системное сообщение о перемещении |
| `token` | `{type, text, character_id}` | порция текста генерируемого ответа |
| `done` | `{type}` | завершение раунда |
| `error` | `{type, detail}` (+`rate_limit: true` при лимите) | ошибка генерации |

Заголовки ответа: `Cache-Control: no-cache`, `X-Accel-Buffering: no`. При обрыве соединения генерация продолжается в фоне.

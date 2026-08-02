# Вмешательство (одноразовая инструкция для следующего хода)

Механизм, позволяющий игроку дать **одноразовое указание**, которое применяется
только к ближайшей генерации (следующему ходу) и автоматически исчезает после
успешного раунда.

## Принцип работы

- Инструкция хранится **только в памяти** (`app/pending_intervention.py`) — она
  никогда не попадает в таблицу `messages`, не сохраняется в память персонажа
  (`memories`) и не влияет на последующие ходы.
- Ключ хранилища — `(chat_id, character_id)`. На первом этапе используется
  chat-wide (`character_id=None`), но задел под per-character уже заложен
  (`character_id` в схеме `InterventionRead` и в API модуля).
- В промпт блок внедряется **сразу перед generation cue** (и в chat-режиме, и в
  generate-режиме), как высокоприоритетная директива текущего хода.

## Состояния

```
нет инструкции -> создана -> ожидает генерации -> использована -> удалена
```

- **Создана**: `PUT /chats/{id}/intervention`.
- **Ожидает генерации**: инструкция переживает перезагрузку страницы и ошибки
  генерации.
- **Использована**: после **полностью успешного** раунда (ни один персонаж не
  упал в fallback «*[X молчит, не в силах ответить]*»). Потребление
  защищено identity-проверкой (`consume_intervention(expected=...)`), чтобы не
  удалить новую инструкцию, если игрок заменил её во время генерации.
- **Удалена**: автоматически после использования или вручную игроком
  (`DELETE /chats/{id}/intervention`).
- При ошибке генерации, остановке или разрыве SSE-соединения инструкция
  **сохраняется** для повторной попытки.
- Перегенерация ответа («Повторить ответ») **применяет** инструкцию, но **не
  потребляет** её.

## HTTP API

| Метод | Путь | Тело | Ответ |
|-------|------|------|-------|
| `PUT` | `/api/chats/{chat_id}/intervention` | `{"instruction": "..."}` | `InterventionRead` (200) |
| `GET` | `/api/chats/{chat_id}/intervention` | — | `InterventionRead \| null` (200) |
| `DELETE` | `/api/chats/{chat_id}/intervention` | — | `204` |

Схема `InterventionRead`: `chat_id`, `character_id` (пока `null`), `instruction`,
`created_at`. Пустая/пробельная инструкция отклоняется (422/400), чат должен
существовать (иначе 404).

## Промпт

Шаблон `intervention.block` в `app/prompts/ru.json` оборачивает текст в
`<intervention>…</intervention>` и явно указывает модели:

- действует **только на этот ответ/ход**;
- сохранять характер, стиль, личность, правила мира и логику сцены;
- не упоминать механизм вмешательства, не обращаться к нему как к реплике и не
  добавлять в память/историю.

Точки внедрения: `app/ollama_client.py` — `_build_generation_messages()`
(chat-режим, перед `generation_cue`) и ветка generate-режима
(`context_parts.append(directive_block)` перед `generation_cue`).

## Код

- `app/pending_intervention.py` — in-memory хранилище + жизненный цикл.
- `app/schemas.py` — `InterventionCreate`, `InterventionRead`.
- `app/routers/chat_engine.py` — эндпоинты PUT/GET/DELETE.
- `app/prompt_builder.py` — `build_intervention_block(text)`.
- `app/chat_engine.py` — снимок в начале раунда (`process_user_message_streaming`),
  передача `directive=` во все вызовы `ollama_client.generate`, потребление в
  конце успешного раунда; в `regenerate_message_streaming` — чтение без
  потребления.
- `frontend/src/stores/intervention.ts` + `frontend/src/api/intervention.ts` —
  состояние и API Vue-клиента; UI кнопка/бейдж «⚡ Вмешательство» в
  `Composer.vue`, обновление при открытии чата и после раунда.

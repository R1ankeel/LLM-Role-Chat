# Character State (Sprint 3)

Единое runtime-состояние персонажа (Plans/update20.md §8): эмоции, стресс,
настроение, физическое состояние, внимание, цель. Хранится в `character_states`
(одна строка на персонажа в чате, `UNIQUE(character_id)`).

## Принцип: только то, чего нет в других таблицах

- **Локация** — из `characters.location_id` (не хранится);
- **Отношения** — из `character_relationships` (не хранятся);
- **Окружение** — из `scene_states` / `world_events` (не дублируется).

В `character_states` живут: `emotional_state` (JSON map emotion→intensity 0..1),
`mood`, `stress` (0..1), `physical_state` (JSON), `attention`, `current_focus_id`,
`active_goal`, `personal_goals`, `updated_round_id`.

## Обновление (детерминированное)

Пост-раунд стадия `character_state` в `app/post_round_pipeline.py` (после
relationships, перед story) вызывает `character_state.update_states_from_round`:

```
relationship_events раунда (kind='llm')  ─┐
                                         ├─► emotion_engine.compute_state_update
world_events раунда (emotional_salience) ─┘         │
                                          (опц.) Sensors-предложение ──► только в caps
                                                   ▼
                                   character_states (emotional_state/mood/stress)
```

Правила (`app/emotion_engine.py`, чистый модуль без БД/LLM):

| движение метрики | эмоция | множитель |
|---|---|---|
| affection/attraction ↑ | warmth | 0.25 / 0.10 |
| affection/attraction ↑ | hope | 0.10 / 0.05 |
| trust ↑ | relief / hope | 0.25 / 0.10 |
| resentment ↑ | resentment | 0.30 |
| jealousy ↑ | tension / suspicion | 0.30 / 0.10 |
| trust ↓ | suspicion / hurt | 0.30 / 0.10 |
| affection ↓ | hurt | 0.25 |
| attraction ↓ | hurt | 0.10 |

- дельты нормируются ÷20; прирост эмоции за раунд ограничен
  `EMOTION_ROUND_CAP` (default 0.4);
- старые эмоции затухают (`EMOTION_DECAY` 0.10/раунд), ниже 0.10 — выпадают;
- стресс: события с `emotional_salience > 0.5` (+0.1×salience + importance/10,
  cap 0.05) и негативные дельты (trust/affection↓ 0.06, resentment↑ 0.04,
  jealousy↑ 0.06); прирост за раунд ограничен `STRESS_ROUND_CAP` (0.2);
  стресс мягко возвращается к baseline 0.1;
- **mood всегда выводится движком** (`derive_mood`): stress ≥ 0.70 → panicked,
  ≥ 0.45 → tense; иначе доминирующая эмоция (≥ 0.50) → warm/hopeful/wary/tense/
  resentful/hurt/panicked; нет доминанты → neutral/tense.

### Sensors-нормализация (§5.1.3)

При активной задаче `emotion` (`sensors_emotion_enabled` + `SENSORS_MODEL`)
SensorsService возвращает `{emotion, intensity, confidence, mood_delta}`.
`emotion_engine.apply_sensors_proposal` сдвигает текущую интенсивность к
предложенной в пределах `SENSORS_EMOTION_INTENSITY_CAP` (0.3) × confidence.
Sensors **не задаёт mood** напрямую. Недоступен/ошибка → детерминированный путь.

## Блок YOUR STATE (context)

При `CHARACTER_STATE_ENABLED=true` `context_builder` рендерит `<your_state>`-блок
(эмоции ≥ 0.10, настроение, стресс, физика, фокус, цель) из состояния персонажа;
`BuiltContext.state_text` → `ollama_client` → `_build_generation_messages`
(Chat API) или legacy-путь. Флаг off → блок пуст, поведение контекста не меняется.

## Флаги

| переменная | default | смысл |
|---|---|---|
| `CHARACTER_STATE_ENABLED` | `false` | писать/читать state + рендер блока |
| `EMOTION_ROUND_CAP` | `0.4` | макс. прирост одной эмоции за раунд |
| `STRESS_ROUND_CAP` | `0.2` | макс. прирост стресса за раунд |
| `SENSORS_EMOTION_INTENSITY_CAP` | `0.3` | макс. сдвиг от Sensors-предложения |

## Тесты

`tests/test_character_state.py`: детерминированные правила emotion_engine
(дельты→эмоции, стресс, mood, decay, Sensors-caps), запись из
relationship_events + world_events раунда, отсутствие location/relationships
в state, идемпотентность (одна строка на персонажа), откат по флагу,
Sensors-failure → детерминированный путь, рендер YOUR STATE,
`CharacterStateRead` (JSON-валидаторы).

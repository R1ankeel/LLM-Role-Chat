# Crisis Engine (Sprint 11)

Мягкое обнаружение кризисов (Plans/update20.md §19/§21/§22/§27). Кризис — не
команда, а **вероятность**: engine собирает детерминированный pressure из
нескольких сигналов, формирует кандидата и применяет его мягко (story_event +
story_thread «Кризис» + небольшой proactive boost вовлечённым персонажам).
Запрещён паттерн `if trust<30: force_argument`.

Код: `app/plot/crisis_engine.py`, пост-раунд стадия `crisis`
(`app/post_round_pipeline.py`). Новых таблиц нет — используются
`story_events`/`story_threads`.

## Гейт (canary)

Глобальный флаг `CRISIS_ENGINE_ENABLED=false` (по умолчанию выключен).
При выключенном флаге: стадия `crisis` в пост-раунд пайплайне — no-op
(`skipped: "flag off"`), кризис-блок не рендерится, boost не считается.

## Пайплайн

```
base pressure (story pressure: issues/goals/stagnation/recent)
      +
trajectory score (relationship events: resentment/jealousy рост, trust/affection падение)
      +
beliefs conflict score (suspicion/conflict + confidence)
      ──► compute_crisis_pressure (нормированные веса) ──► build_crisis_candidate
      ──► evaluation (LLM, только под benchmark gate §27) ──► apply (мягко)
```

`run_crisis_engine(db, chat_id, round_id, characters, character_names, client, model_name)`
возвращает `{ok, stage: "crisis", pressure, candidate, type, confidence, thread}`.

### Candidate (детерминированные правила)

- `pressure >= CRISIS_PRESSURE_THRESHOLD` (0.5);
- есть open issue со `rounds_since_last_mention >= CRISIS_MIN_ISSUE_AGE_ROUNDS` (4)
  — «проблема долго не разрешена»;
- пара взаимодействовала хотя бы 1 раунд (`count_pair_interaction_rounds`).

Тип кризиса (`CRISIS_TYPES`, 9 штук) выводится из правил: `direct_conflict`
при противоположных намерениях, иначе — `admission`, `question`, `discovery`,
`third_party`, `world_event`, `secret_hiding`, `departure`, `goal_change`.

### Evaluation (LLM, мягко)

`CRISIS_EVALUATION_ENABLED=false` по умолчанию. Включение — **только после
прохождения `benchmark_structured` на crisis-evaluation** (benchmark gate §27).
LLM-оценка отдаёт JSON по JSON-schema (`validate_crisis_evaluation`); при невалидном
ответе используется детерминированный результат (`confidence` фиксируется). Пока
флаг выключен — полностью детерминированный путь без LLM.

### Resolution (мягко)

`_apply_crisis_softly`: находит/создаёт `story_thread` с префиксом
`CRISIS_THREAD_PREFIX` («Кризис: <issue>»), важность `CRISIS_EVENT_IMPORTANCE`
(7.0). Кризисное событие пишется в `story_events` (без дубликатов).
`world_events` при этом **не** пишутся — кризис не форсирует сюжет.

## Read-path

- `compute_crisis_boost(db, chat_id, character)` — активный кризис-поток для
  персонажа (via `actors_json`) даёт `min(CRISIS_BOOST_CAP, ...)` (0.3) к
  proactive-бусту генерации. 0.0 при выключенном флаге.
- `build_crisis_block(db, chat_id)` — рендерит `[<crisis data> ... </crisis data>]`;
  пусто при выключенном флаге. Блок идёт в контекст персонажа через
  `context_builder.build(... crisis_block=...)` → `BuiltContext.crisis_text` →
  `ollama_client.generate(... crisis_block=...)` (перед behavior drivers /
  open issues).

## Настройки (.env)

| Переменная | По умолчанию | Смысл |
|---|---|---|
| `CRISIS_ENGINE_ENABLED` | `false` | canary |
| `CRISIS_EVALUATION_ENABLED` | `false` | LLM-оценка (benchmark gate §27) |
| `CRISIS_PRESSURE_THRESHOLD` | `0.5` | порог кандидата |
| `CRISIS_MIN_ISSUE_AGE_ROUNDS` | `4` | возраст нерешённого issue |
| `CRISIS_WEIGHT_BASE` | `0.5` | вес story pressure |
| `CRISIS_WEIGHT_TRAJECTORY` | `0.3` | вес траектории отношений |
| `CRISIS_WEIGHT_BELIEFS` | `0.2` | вес конфликта убеждений |
| `CRISIS_BOOST_CAP` | `0.3` | cap proactive boost |
| `CRISIS_EVENT_IMPORTANCE` | `7.0` | важность записей кризиса |
| `CRISIS_THREAD_PREFIX` | `Кризис` | префикс имени линии |

## Риски

- **Запрет принуждения**: кризис никогда не заставляет персонажей ссориться
  детерминированно; boost ограничен `CRISIS_BOOST_CAP`.
- **LLM-оценка под гейтом**: пока не пройден `benchmark_structured` на
  crisis-evaluation, `CRISIS_EVALUATION_ENABLED` не включать.
- **Контекст**: активные кризис-потоки попадают в контекст только вовлечённым
  персонажам и рендерятся компактным блоком.

## Тесты

`tests/test_crisis_engine.py` (31):

- **pressure-компоненты**: `trajectory_score_from_events` (негативные дельты → 1,
  позитивные → 0, пустые → 0), `beliefs_conflict_score` (suspicion+confidence,
  низкая confidence → 0), `compute_crisis_pressure` (нормировка весов,
  trajectory-only, все нули → 0);
- **кандидат**: `build_crisis_candidate` — только при
  `pressure >= threshold` И неразрешённый старый issue И взаимодействие пары;
  низкий pressure / свежий issue / нет взаимодействия → `None`; `direct_conflict`
  при противоположных интентах, иначе `discovery`; characters из issue-рёбер;
- **evaluation**: `validate_crisis_evaluation` (валидный, fallback типа,
  невалидный верхний уровень → `None`, клампинг confidence);
- **resolution**: `run_crisis_engine` — флаг off → `skipped`; нет кандидата без
  stale issue; на затянутом конфликте пишет `story_thread` «Кризис» + `story_event`
  и **НЕ пишет** `world_events` (нет форсированных аргументов);
- **мягкое применение**: `compute_crisis_boost` (только вовлечённые, `0.0` при
  флаге off), `build_crisis_block` (рендер активных линий, пусто при флаге off);
- **стадия pipeline**: `_stage_crisis` (skipped при флаге off), стадия `crisis`
  в наборе `run_post_round_pipeline`.

Полный прогон: **1087 passed / 29 failed** (29 пред-существующих падений в
не тронутых модулях; 31 новый тест проходит, регрессий нет).

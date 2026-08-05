# Dynamic Story State (Sprint 8)

Original Plot + Current Story State + Story History + Phase (Plans/update20.md §16).
Сюжет — отдельная ось поверх World Events: что **важно для истории**, а не всё
подряд. Хранится в `story_states`, `story_events`, `story_threads`
(таблицы созданы в Sprint 0, здесь — write-path и рендер).
## Принципы

- **Original Plot неприкосновенен**: его может менять только пользователь
  (`PATCH /api/chats/{chat_id}/story`), LLM write-path его не трогает
  (пишутся только `story_events` + `current_story`);
- **Исходный `general_prompt` не меняется**: при включённом story сцена берётся
  из `chats.story_prompt` (helper `chat_engine._chat_plot_text`), legacy-путь
  идентичен;
- **Защита контекста**: в STORY-блок идут только активные потоки top-K
  (`STORY_THREADS_MAX`) — контекст не разрастается.

## Пайплайн (пост-раунд стадия `story`)

```
round world_events ──► write_story_events_from_round (порог важности)
        │              • важные пары и мира → story_events
        │              • идемпотентность по event_id
        ▼
update_story_state_from_round
        │              • summary из top-K важных событий
        │              • потоки: создание / рост importance / дедуп по имени
        │              • story_phase сохраняется (меняется только пользователем)
        ▼
   story_states + story_threads + story_events
```

`app/post_round_pipeline.py` стадия `story` (после beliefs), обе функции в
try/except — падение не роняет раунд. Гейт: `STORY_ENABLED` (глобальный флаг)
И `chats.story_enabled` (перчатовый тумблер).

## Пороги сюжетности

| событие | условие записи |
|---|---|
| `story_events` | важность ≥ `STORY_EVENT_MIN_IMPORTANCE` (4.0) |
| создание/обновление `story_threads` | важность ≥ `STORY_THREAD_MIN_IMPORTANCE` (6.0) |

- дедупликация потоков по имени (casefold); importance растёт (max), actors
  мержатся без дублей; прогress = 0..1 по выпадающим событиям.

## Блок STORY (context)

При включённых флагах `context_builder._build_story_block` читает активные
потоки (`crud.get_active_story_threads`) и рендерит `<story>`-блок
(`prompt_builder.build_story_block`): фаза + top-K линий с прогрессом.
`BuiltContext.story_text` → `ollama_client` → оба пути (`_build_generation_messages`
и legacy `context_parts`).

## API (только пользователь)

- `GET /api/chats/{chat_id}/story` — текущее состояние сюжета (state + события + потоки);
- `PATCH /api/chats/{chat_id}/story` — пользовательские правки:
  - `original_plot` (и в `chats`, и в `story_state`), `story_phase`,
    частичный merge `current_story`, `story_enabled` / `story_prompt`;
  - при включении с пустым `story_prompt` — посев из `general_prompt`/`original_plot`.

## Флаги

| переменная | default | смысл |
|---|---|---|
| `STORY_ENABLED` | `false` | писать story_events + рендер STORY |
| `STORY_THREADS_MAX` | `5` | cap активных потоков в контекст |
| `STORY_EVENT_MIN_IMPORTANCE` | `4.0` | порог записи story_events |
| `STORY_THREAD_MIN_IMPORTANCE` | `6.0` | порог создания/обновления потоков |
| `STORY_SUMMARY_MAX_EVENTS` | `20` | cap событий в summary |

## Тесты

`tests/test_story_state.py`: запись story_events (порог, идемпотентность,
выключенный флаг); state (summary/потоки/прогресс, дедуп, рост importance,
фаза сохраняется, chat-disabled через `_stage_story`); рендер STORY (пусто/top-K);
`story_text` в BuiltContext; `_chat_plot_text` (story_prompt vs general_prompt);
API GET/PATCH/merge/404.

---

# Story Consolidation (Sprint 9)

LLM-обновление Current Story State с валидацией (Plans/update20.md §17).
Story state должен **эволюционировать**: цели завершаются, линии архивируются,
появляются новые, фаза сдвигается. Модуль `app/plot/story_consolidation.py`.

## Trigger (§17.1)

Пост-раунд (после детерминированного write-path в `_stage_story`), если:

- с последней консолидации прошло ≥ `STORY_CONSOLIDATION_INTERVAL_ROUNDS`
  раундов (число раундов = distinct `round_id` в `world_events`, хранится в
  `story_states.last_consolidation_rounds`), ИЛИ
- критическое событие в окне (importance ≥ `STORY_CONSOLIDATION_CRITICAL_IMPORTANCE`)
  затронуло story — консолидация раньше срока.

## Входы → LLM → выход (§17.2)

```text
Original Plot + Current Story State + Recent Story Events (окно grounding)
        ↓ (LLM, JSON-schema format, T=0.2)
Updated Current Story State
```

Контракт (`CONSOLIDATION_SCHEMA`, `format` при вызове Ollama):
`completed_goals`, `progress.overall`, `new_threads`,
`updated_threads` (progress/importance), `archived_threads`,
`character_state_changes`, `phase_change`, `summary` — каждое поле с
`confidence` 0..1.

## Валидация и защита (§17.3)

- **Original Plot diff**: консолидация НЕ пишет `original_plot` (нет write-path);
  смена `phase_change` применяется ТОЛЬКО если новая фаза зарегистрирована в
  original_plot (иначе остаётся предложением для пользователя, §16.4);
- **Hallucination guard**: новые/архивированные/updated-линии и цели — только
  при подтверждении в окне `story_events` (по актёрам или значимым токенам
  имени); неподтверждённое отбрасывается;
- **Confidence**: изменение с `confidence < STORY_CONSOLIDATION_MIN_CONFIDENCE`
  не применяется;
- **Rollback**: невалидный JSON / нарушение правил / ошибка LLM → предыдущая
  версия `story_states` остаётся, `version` не растёт, ошибка логируется;
  раунд не ломается (стадия в try/except).

## Применение

- `new_threads` → `story_threads` (CREATE, dedupe по имени, status=active);
- `archived_threads` / `completed_goals` → status=archived + вывод из
  `active_threads`, цели в `completed_goals`;
- `updated_threads` → importance (max) + `thread_progress` (кламп 0..1);
- `character_state_changes` → `current_story.characters` (роль/заметки);
- `phase_change` → `story_phase`; `summary` → `narrative_summary`;
  `progress.overall` → кламп 0..1.
- `version` растёт только если что-то реально применилось;
  `last_consolidation_rounds` фиксируется при каждой состоявшейся консолидации.

## Флаги

| переменная | default | смысл |
|---|---|---|
| `STORY_CONSOLIDATION_ENABLED` | `false` | включать LLM-консолидацию (benchmark gate §27) |
| `STORY_CONSOLIDATION_INTERVAL_ROUNDS` | `15` | минимальный интервал в раундах |
| `STORY_CONSOLIDATION_CRITICAL_IMPORTANCE` | `8.0` | порог критического события |
| `STORY_CONSOLIDATION_MODEL` | `` | модель (пустая = модель генерации чата) |
| `STORY_CONSOLIDATION_TIMEOUT` | `60` | таймаут вызова |
| `STORY_CONSOLIDATION_MIN_CONFIDENCE` | `0.5` | порог confidence для применения |
| `STORY_CONSOLIDATION_MAX_RECENT_EVENTS` | `30` | окно grounding |

## Benchmark gate (§27)

`STORY_CONSOLIDATION_ENABLED` по умолчанию выключен. Перед включением
обязателен прогон `benchmark_structured` на story-update (schema-validity
≥ 90%, grounding ≥ порога); при неудовлетворительном результате — только
кандидаты-флаги без применения.

## Тесты

`tests/test_story_consolidation.py`: trigger (interval/critical/not-reached),
canary (флаг и перчатовый тумблер), completed_goals уходят из active,
new_threads только grounded, progress сохраняется и клампится, фаза только из
original_plot, original_plot не искажается, low confidence не применяется,
rollback (невалидный JSON / ошибка LLM / без изменений), version bump.

---

# Plot Engine: Intent, Plans, Pressure (Sprint 10)

Plot-слой поверх story (Plans/update20.md §19, §21, §22): у NPC появляется
**цель** (intent) и **мешающие обстоятельства** (план). Всё детерминированное
(без LLM-фантазий о мире); intent — **тенденция, а не команда** (LLM решает,
как её реализовать, по образцу behavior drivers). Не каждый ход имеет intent.

## NPC Intent (§21) — `app/plot/intent.py`

Формируется **перед генерацией** в `chat_engine._round_step` правилами из
`character_states.active_goal` + активного плана + топ-open-issues + beliefs +
story threads + story pressure.

Источник цели по приоритету: активный план > `active_goal` > топ-open-issue >
активный story_thread (с участием персонажа). У каждого intent:
`goal`, `target`/`target_name`, `approach` (direct/indirect/avoid/delay),
`urgency`, `emotion`, `risk`.

- **approach**: blocked план → `delay`; suspicion (belief про цель) → `indirect`;
  риск ≥ `INTENT_RISK_AVOID` → `avoid`; риск ≥ `INTENT_RISK_DELAY` при
  слабой настойчивости → `delay`.
- **min_urgency**: слабая issue/thread-цель ниже `INTENT_MIN_URGENCY` intent
  не формирует (не каждый ход).
- Write-path: `intents` (только при `NPC_INTENT_ENABLED`, canary); read-path —
  блок `ACTIVE GOAL` рендерится из текущего intent.

## NPC Plans (§22) — `app/npc_plans.py`

«Я хочу сделать X, но сейчас мне мешает Y». **НЕ GOAP/planner** — один активный
план на персонажа; второй не создаётся, пока предыдущий жив. status:
`active | blocked | done | abandoned`.

- Создание детерминированное (из intent/active_goal) при `NPC_PLANS_ENABLED`;
- **Пост-раунд продвижение** (стадия `plans`): события раунда сопоставляются с
  целью/блокировкой по token overlap (≥ 0.4). Событие, пересекающееся с целью:
  важность ≥ `NPC_PLAN_RESOLVE_IMPORTANCE` → план `done`; иначе → `next_step` =
  текст события + снятие блокировки. Событие, пересекающееся с `blocked_by` →
  unblock.

## Story pressure (§19) — `app/plot/plot_pressure.py`

`pressure = Σ w_i × component_i` (веса нормируются), компоненты:
нерешённость issues (importance × salience с затуханием), блокировка личных
целей, застой (rounds без событий), интенсивность недавних событий. Сигнал
для urgency/risk intent; не форсирует сюжет (нет `if trust<30: force_argument`).

## Story threads archiving — `app/plot/story_threads.py`

Активные линии, чьё имя пересекается с `completed_goals` из
`story_state.current_story` (token overlap ≥ `STORY_THREAD_ARCHIVE_OVERLAP`),
переводятся в `status=archived` (стадия `story_threads`). Общие helpers
`significant_tokens`/`token_overlap` используются intent и планами.

## Блоки ACTIVE GOAL / ACTIVE PLAN (context)

`context_builder.build()` принимает `active_goal_block`/`active_plan_block`
(фиксированные блоки, не усекаются); `BuiltContext.active_goal_text`/
`active_plan_text` → `ollama_client` (оба пути). Блоки рендерятся только при
включённых флагах.

## Флаги

| переменная | default | смысл |
|---|---|---|
| `NPC_INTENT_ENABLED` | `false` | формировать intent + писать в `intents` (canary) |
| `NPC_PLANS_ENABLED` | `false` | создавать/продвигать планы NPC (canary) |
| `INTENT_RISK_AVOID` | `0.8` | порог риска для approach=avoid |
| `INTENT_RISK_DELAY` | `0.6` | порог риска для approach=delay |
| `INTENT_MIN_URGENCY` | `0.15` | ниже — слабая цель не даёт intent |
| `PLOT_PRESSURE_WEIGHT_*` | `0.25` | веса компонентов pressure |
| `PLOT_PRESSURE_GOAL_BLOCKED_ROUNDS` | `8` | нормировка застоя/блокировки |
| `NPC_PLAN_RESOLVE_IMPORTANCE` | `7.0` | порог важности события → план done |
| `STORY_THREAD_ARCHIVE_OVERLAP` | `0.5` | overlap имени линии с completed_goal |

## Benchmark gate (§27)

`NPC_INTENT_ENABLED`/`NPC_PLANS_ENABLED` по умолчанию выключены. Перед
включением обязателен прогон `benchmark_structured` на intent-блоке
(intent — тенденция, не «режиссёр») и plans (нет GOAP-инструкций).

## Тесты

`tests/test_intent.py` (14), `tests/test_npc_plans.py` (9),
`tests/test_story_threads.py` (11): приоритет источника цели, approach,
min_urgency, write-path + canary, один активный план, продвижение/разрешение/
unblock по событиям, token overlap, архивация завершённых линий, plot pressure.


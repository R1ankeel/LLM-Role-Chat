# Adaptive Consolidation (Sprint 12)

Замена фиксированного 24h-таймера консолидации на **score-based soft/hard/
critical** триггер (Plans/update20.md §20). Простаивающий чат НЕ консолидируется
по таймеру: консолидация запускается, когда накопилось достаточно новых входов
(сообщения/события/факты/relationship-события/story-события/якоря) ИЛИ случилось
критическое событие — тогда немедленно, независимо от score.

Код: `app/memory_service.py` (score/критика/триггер/полный набор),
`app/crud.py` (`consolidation_state` + счётчики), `app/post_round_pipeline.py`
(стадия `adaptive_consolidation`), `app/main.py` (score-схедьюлер).

## Гейт (canary)

Глобальный флаг `ADAPTIVE_CONSOLIDATION_ENABLED=false` (по умолчанию выключен).
При выключенном флаге: стадия `adaptive_consolidation` в пост-раунд пайплайне —
no-op (`skipped: "flag off"`), а `_consolidation_scheduler` в `app/main.py`
возвращается к прежнему legacy-поведению — глобальный 24h-интервальный job
(`CONSOLIDATION_INTERVAL_HOURS`). При включённом — опрос каждые
`CONSOLIDATION_POLL_SECONDS` с решением по каждому чату.

## Score

```
consolidation_score =
    new_messages × 1
  + new_events × 2
  + new_facts × 3
  + relationship_events × 4
  + story_events × 5
  + emotional_anchors × 7
```

`new_*` — строки в шести таблицах (`messages`, `world_events`, `memories`,
`relationship_events`, `story_events`, `memory_anchors`), созданные **после
последней консолидации** (`consolidation_state.last_soft_at` /
`last_hard_at`, изначально — `chats.created_at`). Подсчёт — индексированные
`COUNT` по timestamp (`count_consolidation_inputs`); relationship-события и
якоря не имеют `chat_id` и скоупятся через `character_relationships`. Веса —
конфиг.

## Пороги и уровни

| Уровень | Условие | Набор |
|---|---|---|
| `soft` | `score_soft >= CONSOLIDATION_SOFT_THRESHOLD` (25) | только memories (clustering + merge) + summary |
| `hard` | `score_hard >= CONSOLIDATION_HARD_THRESHOLD` (50) | полный набор: memories + summary + relationship evidence + anchors + story update + embedding/index refresh |
| `critical` | `is_critical_event` в окне с последнего hard | немедленная hard-консолидация независимо от score |

`score_soft` считается от `last_soft_at`, `score_hard` — от `last_hard_at`;
hard-консолидация сдвигает обе базовые точки, soft — только мягкую (hard —
супермножество). После срабатывания базовая точка сдвигается на момент
enqueue — повторные poll'ы тех же событий не ре-триггерят.

## Critical events

Детекция **детерминированная** (`is_critical_event`, без LLM): `importance >=
CONSOLIDATION_CRITICAL_IMPORTANCE` (8.0) ИЛИ совпадение whitelist-ключевых слов
(`CRITICAL_ACTION_KEYWORDS`) в `action`/`event_type`/`description`: смерть,
предательство, признание, свадьба, важное раскрытие, сюжетный milestone,
завершение главной цели и т.п.

LLM-предложение критического события помечается `suspicion` до подтверждения —
пока не реализовано (задел §20).

**Дедупликация**: critical не чаще `CONSOLIDATION_CRITICAL_MAX_PER_ROUND` (2)
раз за раунд — счётчик `critical_count`/`critical_round` в `counters`; при
достижении капа происходит деградация на score-уровень (hard/soft/skip).

## Пайплайн

```
count_consolidation_inputs ──► compute_consolidation_score (soft, hard)
       + _latest_critical_event (is_critical_event по world_events окна)
       ──► evaluate_consolidation ──► {critical|hard|soft|skip}
       ──► schedule_adaptive_consolidation
            (сдвиг базовых точек + enqueue job с level и окном
             since_soft/since_hard) ──► run_job
            ──► consolidate_chat_adaptive
```

Триггерные точки:

- **пост-раунд** (`_stage_adaptive_consolidation`, после `crisis`): критическое
  событие → немедленный hard-консолидейшн;
- **схедьюлер** (`_adaptive_consolidation_pass` в `app/main.py`): poll каждые
  `CONSOLIDATION_POLL_SECONDS` по всем чатам, enqueue + fire-and-forget
  `run_job`.

`consolidate_chat_adaptive(db, client, *, chat_id, model_name, level)` — полный
набор:

- **memory** — `_consolidate_character_memories` на каждого персонажа (кластер +
  merge);
- **summary** — refresh per-character summary из нового диалога
  (`summarize_for_character`, canary `ADAPTIVE_CONSOLIDATION_ENABLED`);
  consolidation ≠ summary: summary — только один из результатов;
- **relationship** (hard) — `prune_relationship_events` (fold старых событий в
  archive-строки) по каждому отношению чата;
- **anchors** (hard) — дедуп эмоциональных якорей по `(relationship_id,
  event_id)`, держим max `importance`;
- **story** (hard) — `maybe_consolidate_story` (Sprint 9, свой canary
  `STORY_CONSOLIDATION_ENABLED`);
- **index** (hard) — embedding refresh для memories без эмбеддинга (canary
  `EMBEDDING_ENABLED`).

Каждая компонента изолирована (try/except) — падение одной не роняет набор.

## Настройки (.env)

| Переменная | По умолчанию | Смысл |
|---|---|---|
| `ADAPTIVE_CONSOLIDATION_ENABLED` | `false` | canary |
| `CONSOLIDATION_WEIGHT_MESSAGES` | `1` | вес новых сообщений |
| `CONSOLIDATION_WEIGHT_EVENTS` | `2` | вес world-событий |
| `CONSOLIDATION_WEIGHT_FACTS` | `3` | вес фактов памяти |
| `CONSOLIDATION_WEIGHT_REL_EVENTS` | `4` | вес relationship-событий |
| `CONSOLIDATION_WEIGHT_STORY_EVENTS` | `5` | вес story-событий |
| `CONSOLIDATION_WEIGHT_ANCHORS` | `7` | вес эмоциональных якорей |
| `CONSOLIDATION_SOFT_THRESHOLD` | `25` | порог soft-консолидации |
| `CONSOLIDATION_HARD_THRESHOLD` | `50` | порог hard-консолидации |
| `CONSOLIDATION_CRITICAL_IMPORTANCE` | `8.0` | порог критического события |
| `CONSOLIDATION_CRITICAL_MAX_PER_ROUND` | `2` | дедуп critical N/раунд |
| `CONSOLIDATION_POLL_SECONDS` | `600` | интервал poll score-схедьюлера |

## Риски

- **Стоимость**: critical может дорого стоить при частых событиях — дедуп ≤ N
  раз/раунд (`CONSOLIDATION_CRITICAL_MAX_PER_ROUND`).
- **Idle**: score≈0 → skip — чат без активности не консолидируется (причина
  замены 24h-таймера).
- **Не смешивать summary и consolidation**: summary — отдельный результат
  (один из компонентов consolidation), не подменяющий сам процесс.
- **Сдвиг базовой точки при enqueue**: если job упадёт, следующий poll по новым
  событиям снова поднимет score (повторный триггер возможен, но не мгновенный).
  Чтобы summary-компонент не потерял триггерный диалог, окно консолидации
  (`since_soft`/`since_hard`) передаётся в payload job'а — не читается из уже
  сдвинутых маркеров.

## Тесты

`tests/test_adaptive_consolidation.py` (20):

- **score/критика**: `compute_consolidation_score` (defaults/кастомные веса),
  `is_critical_event` (importance, whitelist-ключевые слова);
- **решение**: `evaluate_consolidation` — idle → `skip` (score 0), 30 сообщений →
  `soft`, 60 → `hard`, critical → `critical` немедленно, дедуп-кап раунда →
  `skip`, другой раунд → `critical`;
- **триггер**: `schedule_adaptive_consolidation` — флаг off → skip без enqueue,
  idle → skip без job, soft → enqueued + повторный вызов → skip (базовая точка
  сдвинута), critical → enqueued c `level=critical`, `critical_count` в counters;
- **набор**: `consolidate_chat_adaptive` soft — редуцированный набор (без
  relationship/anchors/story/index), hard — полный набор (summary обновлён,
  anchors дедуплицированы, отчёты relationship/story/index);
- **legacy**: `consolidate_memories_job` с фильтром `chat_id` не ломается;
- **pipeline**: `_stage_adaptive_consolidation` — флаг off → `skipped`,
  idle → `level=skip`; стадия `adaptive_consolidation` в наборе
  `run_post_round_pipeline`.

Полный прогон: **1107 passed / 29 failed** (29 пред-существующих падений в не
тронутых модулях; 20 новых тестов проходят, регрессий нет).

# Аудит legacy-полей (Sprint 0, п.4)

Аудит полей, которые движок **не пишет** (или пишет только по legacy-путям) и
которые являются кандидатами на замену в state-driven архитектуре
(`Plans/update20.md`). Заведены новые таблицы состояния (Sprint 0), но read-path
их не читает; перенос значений из legacy-полей — в соответствующих спринтах.

## `scene_states.custom_state` (JSON)

Единый JSON глобального состояния сцены. Пользователь может писать через
`PATCH scene`. Движок пишет только часть полей; несколько полей legacy:

| поле | статус | комментарий | замена |
|---|---|---|---|
| `mood` | legacy | глобальное настроение сцены; движок не пишет | `character_states.mood` (per-character, Sprint 3 ✅) |
| `tension` | legacy | глобальное напряжение; движок не пишет | `character_states` эмоции/стресс (Sprint 3 ✅) |
| `plot_flags` | legacy | флаги сюжета; движок не пишет, устанавливает только пользователь | `story_states.current_state` (Sprint 8) |
| `active_events` | legacy | активные сюжетные события; движок не пишет | `story_threads` / `story_events` (Sprint 8/10) |
| `active_goal` / `active_goals` | legacy | цели сцены; движок не пишет | `character_states.active_goal/personal_goals` (Sprint 3 ✅), `npc_plans` (Sprint 10) |
| `weather` | актуально | пишется/читается существующим путём | — |
| `time_of_day` | актуально | из `extract_scene_state` (но движком не пишется) | — |

Правило: legacy-поля **сохраняются** для обратной совместимости (пользовательский
PATCH и старые сценарии не должны ломаться), но **не дублируются** в новых
таблицах до их спринтов. Переносить значения из legacy в state-таблицы не нужно
до спринтов, которые их наполняют. С Sprint 3 per-character `mood`/`stress`/
`active_goal` пишет `emotion_engine` в `character_states`; глобальные
`custom_state.mood/tension` при этом остаются нетронутыми (не дублируются и не
читаются движком).

## `characters.location` (строковая)

Legacy-bridge до перехода на `characters.location_id` (WPE 3.0 Фаза 1,
`docs/database.md`). Сохраняется для совместимости; `location_id` — канон.

## `world_events.location` (строковая)

Legacy-bridge. Sprint 0 добавил `world_events.location_id` (каноническая локация
события) + backfill `backfill_event_location_ids`. `location` сохраняется.

## `chats.general_prompt`

Статичный сюжет («Сюжет: …» в `<scene>`). Sprint 0 выделил
`chats.original_plot`/`chats.story_prompt` (копия `general_prompt`) +
`story_enabled=false` (backfill `backfill_plot_fields`). Сюжет по-прежнему
читается из `general_prompt` до Sprint 8; `story_prompt` движком пока не пишется.

## Сводка

| legacy-поле | спринт замены |
|---|---|
| `custom_state.mood` | 3 ✅ (`character_states.mood`) |
| `custom_state.tension` | 3 ✅ (`character_states`) |
| `custom_state.plot_flags` | 8 (`story_states`) |
| `custom_state.active_events` | 8/10 (`story_threads`/`story_events`) |
| `custom_state.active_goal(s)` | 3 ✅/10 (`character_states`/`npc_plans`) |
| `chats.general_prompt` (сюжет) | 8 (`story_states.original_plot`) |
| `characters.location` | уже частично (WPE Фаза 1 → `location_id`) |
| `world_events.location` | Sprint 0 (→ `location_id`) |

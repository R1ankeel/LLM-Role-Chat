# Attention (Sprint 4)

Слой «воспринято ≠ вошло в сознание» (Plans/update20.md §11). Детерминированная
оценка внимания для пары (персонаж, событие) между Perception и
Interpretation/Memory:

```
Perception → Attention → Interpretation → Memory / Reaction
```

Attention **не меняет** presence-лестницу и рендер recent history: что персонаж
«видит» в контексте, по-прежнему решает `perceive()`/presence. Attention
управляет только тем, что идёт дальше — в память и в recency tail (реакцию).

## Score

`app/attention.py` (чистый модуль, без БД и LLM):

```text
attention_score = w_volume    × громкость (громкие стимулы / audio_level)
                + w_distance  × presence (same > adjacent > remote)
                + w_relevance × важность события (своя речь=1.0, игрок=0.8,
                                персонаж=0.5, система=0.4)
                + w_personal  × упоминание имени наблюдателя
                + w_emotional × активен эмоциональный якорь по автору события
                + w_novelty   × новое vs повтор
                + w_relationship × в событии участвует target отношения наблюдателя
                + w_address   × addressed=true (в target_character_ids)
```

Своя речь всегда возвращает `1.0`. Веса нормируются на 1.0.

## Пороги

| бакет | диапазон | поведение |
|---|---|---|
| `LOW` | `score < ATTENTION_LOW` (0.35) | «слышал фоном»: в память НЕ идёт, в реакцию НЕ идёт |
| `MEDIUM` | `0.35 ≤ score < ATTENTION_HIGH` (0.7) | «заметил»: в память |
| `HIGH` | `score ≥ 0.7` | «в центре внимания»: в память, в recency tail |

`attention_bucket(None)` → `HIGH`: при выключенном флаге (или отсутствии score)
всё воспринятое ведёт себя как раньше — это точка отката.

**Примеры из постановки** (с весами по умолчанию):

- падение стакана в соседней комнате (audible, без стимулов/адресации) — score
  ≈ 0.25 → `LOW`;
- крик персонажа по имени (shout + addressed + имя в тексте) — score ≈ 0.80 → `HIGH`.

## Запись: `message_presence.attention`

Attention пишется детерминированно вместе с presence (`app/crud.py`):

- `compute_and_save_presence_for_message` — синхронный путь одного события;
- `compute_and_save_presence_for_round` — пост-раунд presence round pass.

Для каждого персонажа `_attention_context_for_chat` за один заход (2 запроса)
поднимает targets его направленных отношений (`rel_targets`, компонента
`w_relationship`) и targets отношений с эмоциональным якорем (`anchor_authors`,
компонента `w_emotional`). Score считается через `_attention_score_for`.

`upsert_message_presence_batch` пишет `attention` при создании строки и
обновляет только при явно переданном значении (`None` не затирает).

## Фильтры

### Memory extraction

`witness_model.filter_history_for_memory_extraction(..., attention_map=...)`
(обёртка `memory_service.get_observable_context_for_character`) исключает
события с `attention_bucket == LOW` из memory-контекста (reason
`low_attention_background`) даже при `present`/`told`. `memory_service`
подтягивает карту через `crud.get_attention_map` в обоих путях
(per-character extraction и summarization).

### Recency tail / реакция

`witness_model.build_character_recency_tail(..., attention_map=...)` не включает
события с `attention_bucket == LOW` в блок реакции. `context_builder` грузит
карту через `_load_attention_map`; `chat_engine` — в обоих путях генерации
(передаёт в fallback, когда `built_context` недоступен).

## Sensors perception-proposal (§5.1.3)

Только в presence round pass (`compute_and_save_presence_for_round`): при
`attention_enabled` и активной задаче `perception` один вызов
`sensors_service.run(task="perception", minimal_context=...)` на раунд.
Предложенная `significance` (0..1) поднимает score в пределах
`SENSORS_PERCEPTION_SIGNIFICANCE_CAP` (`apply_sensors_significance`).

Sensors **не определяет** доступность информации (решает `perceive()`/presence),
**не принимает** решение о внимании и не пишет в БД. Недоступен/ошибка/timeout
→ детерминированный путь (graceful degradation).

## Флаги

| переменная | default | смысл |
|---|---|---|
| `ATTENTION_ENABLED` | `false` | считать/писать attention + фильтровать память/recency tail |
| `ATTENTION_LOW` | `0.35` | нижний порог («фон») |
| `ATTENTION_HIGH` | `0.7` | верхний порог («в центре внимания») |
| `ATTENTION_WEIGHT_VOLUME` | `0.15` | вес громкости |
| `ATTENTION_WEIGHT_DISTANCE` | `0.15` | вес близости (presence) |
| `ATTENTION_WEIGHT_RELEVANCE` | `0.10` | вес важности события |
| `ATTENTION_WEIGHT_PERSONAL` | `0.25` | вес упоминания имени |
| `ATTENTION_WEIGHT_EMOTIONAL` | `0.10` | вес эмоционального якоря |
| `ATTENTION_WEIGHT_NOVELTY` | `0.05` | вес новизны |
| `ATTENTION_WEIGHT_RELATIONSHIP` | `0.05` | вес участия target отношения |
| `ATTENTION_WEIGHT_ADDRESS` | `0.15` | вес addressed=true |
| `SENSORS_PERCEPTION_SIGNIFICANCE_CAP` | `0.15` | макс. подъём от Sensors-significance |

## Откат

`ATTENTION_ENABLED=false` (default): attention не считается (NULL в БД),
`get_attention_map` пуст, memory/recency фильтры ведут себя как раньше
(`None` → `HIGH` bucket). Presence-лестница и рендер recent history не меняются.

## Тесты

`tests/test_attention.py`: сценарии из постановки (падение стакана=low, крик по
имени=high, своя речь=1.0), компоненты меняют ровно свою весовую долю
(якорь/новизна/имя), пороги `attention_bucket` и откат `None`→HIGH, Sensors-caps,
запись attention через presence round pass, откат по флагу (NULL + пустая карта),
upsert не затирает существующее значение, memory filter (low исключается / high
включается), recency tail (low исключается, legacy без карты сохраняется).

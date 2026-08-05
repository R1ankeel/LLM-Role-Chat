# Belief System (Sprint 5)

Знание/убеждение персонажа вместо плоской истины (Plans/update20.md §9).
Персонаж **НЕ автоматически знает World Truth** — в контекст попадают только
его beliefs. Хранится в `beliefs` (одна строка на (character, subject,
predicate, object)).

## Принцип: знание ≠ истина

- Персонаж узнаёт только то, что **реально воспринял** (presence из
  `message_presence`, внимание из той же строки) — изоляция R2;
- Источник и уверенность определяются **детерминированно** (никто не «прокидывает»
  LLM-слова в beliefs без grounding, §9 «НЕ делать»);
- В контекст идёт только `top-K` по confidence
  (`BELIEFS_TOP_K`, порог `BELIEFS_RENDER_CONFIDENCE`) — защита от переизбытка.

## Пайплайн (пост-раунд стадия `beliefs`)

```
world_events раунда ──► presence (message_presence) ──► source ──► confidence
        │                       (absent → skip)              │
        │                       attention gating ────────────┤  (< attention_low → skip)
        ▼                                                     ▼
     триплет (subject, predicate, object)            belief update (upsert/merge)
```

`app/belief_service.update_beliefs_from_round` (детерминированный; только
direct_observation-путь — LLM-suggestion под benchmark gate §27):

| presence | source | базовая confidence |
|---|---|---|
| present | direct_observation | 0.85 |
| mentioned | heard | 0.70 |
| audible | rumor | 0.30 |
| told | told_by | 0.2 + 0.6·(trust/100), trust = believer→teller |
| absent | — | не пишется |

- `attention gating`: при `ATTENTION_ENABLED` событие с `attention <
  ATTENTION_LOW` («слышал фоном») в belief **не** идёт;
- повторное наблюдение повышает уверенность (`merge_confidence` = max, cap 0..1);
- `world_truth_ref` (FK на `world_events.id`) заполняется только при прямом
  наблюдении — «подтверждено миром».

## type: fact | belief | suspicion

| тип | условие |
|---|---|
| fact | confidence ≥ 0.75 И (direct_observation ИЛИ подтверждено миром) |
| belief | confidence ≥ 0.50 |
| suspicion | иначе (низкая уверенность / неподтверждённый слух) |

## Блок WHAT YOU KNOW (context)

При `BELIEFS_ENABLED=true` `context_builder._build_what_you_know_block` читает
top-K beliefs персонажа (`crud.get_beliefs_for_character`, порог
`BELIEFS_RENDER_CONFIDENCE`) и рендерит `<what_you_know>`-блок
(`prompt_builder.build_what_you_know_block`) с маркерами «Ты знаешь / Ты
полагаешь / Ты подозреваешь» + уверенность. `BuiltContext.what_you_know_text` →
`ollama_client` → оба пути (`_build_generation_messages` и legacy `context_parts`).

## Замещение MVP epistemic mask

- `BELIEFS_ENABLED=false` (default): mask из `relationship_service.build_epistemic_mask_block`
  остаётся fallback, `beliefs`-таблица никем не читается (canary);
- `BELIEFS_ENABLED=true`: mask при отсутствии раундных direct/observed-свидетельств
  подставляет belief персонажа вместо «неизвестно»; `_compute_epistemic_evidence`
  расширяется beliefs-персонажами (`_belief_evidenced_ids`).

## Флаги

| переменная | default | смысл |
|---|---|---|
| `BELIEFS_ENABLED` | `false` | писать/читать beliefs + рендер WHAT YOU KNOW |
| `BELIEFS_TOP_K` | `8` | cap beliefs в контекст |
| `BELIEFS_RENDER_CONFIDENCE` | `0.3` | порог уверенности для рендера |
| `BELIEFS_LLM_SUGGESTION_ENABLED` | `false` | LLM-suggestion beliefs (за benchmark gate §27) |

## Тесты

`tests/test_beliefs.py`: невоспринятое не пишется (absent → skip); present →
direct_observation/fact + world_truth_ref; низкий attention → skip; told_by по
trust (высокий/низкий/без ребра); неподтверждённый слух → suspicion без
world_truth_ref; read-path пуст при `BELIEFS_ENABLED=false` (mask fallback).

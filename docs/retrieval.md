# Hybrid Retrieval v2 (Sprint 6)

Детерминированный rerank memories (Plans/update20.md §14): после RRF, до
witness-boost, кандидаты упорядочиваются взвешенной суммой осей
(memory_type/valence/intensity + сигналы контекста: отношения,
story_threads). Rerank **не создаёт и не удаляет** кандидатов — только меняет
их порядок. BM25 остаётся базовым lexical-путём и fallback-ом при отсутствии
embeddings.

При `HYBRID_RERANK_ENABLED=false` (default) весь RRF-путь не меняется.

## Место в конвейере

```
query ──► lexical (BM25) ─┐
         semantic (emb) ──┤── RRF ──► rerank (Sprint 6) ──► witness boost ──► контекст
                          └──────────────────┘
                гибрид только при HYBRID_RERANK_ENABLED=true
```

Порядок применения строго: **RRF → rerank → witness-boost**. Это гарантирует,
что свидетельские/изолирующие фильтры (`_apply_witness_boost`) остаются
последним словом, а rerank только переупорядочивает то, что уже прошло RRF.

## Оси rerank

`app/memory_service.rerank_memories(candidates, context, weights=None)`
считает каждому кандидату score = Σ wᵢ·axisᵢ и стабильно сортирует по убыванию
(равные очки сохраняют порядок RRF). Веса нормируются на 1.0
(`rerank_weights`); ось, данные для которой недоступны, выпадает из нормировки:

| ось | значение | когда отпадает |
|---|---|---|
| `lexical` | BM25-подобный overlap токенов запроса и текста памяти | нет `query_text` |
| `semantic` | cosine-похожесть embeddings | нет `query_embedding`/embeddings памяти |
| `emotional` | `intensity` + 0.5·\|valence\| (0..1) | — |
| `story` | story memory + overlap с активными thread-ами | — |
| `relationship` | target ∈ отношения текущего контекста; fallback по категории | — |
| `recency` | свежесть (`created_at`) | — |
| `salience` | салиентность памяти | — |

## Сигналы контекста

`app/crud.build_rerank_signals(db, chat_id, character_ids, character_names)`
собирает из БД:

- `relationship_target_names` — имена `target` персонажей, с которыми у текущих
  персонажей есть рёбра `character_relationships` (source/target в составе
  `character_ids`);
- `active_threads` — незавершённые `story_threads` (по `chat_id`).

При `HYBRID_RERANK_ENABLED=false` функция возвращает пустые сигналы, и
`_apply_rerank` в `app/crud.py` — no-op. Оба retrieval call-site в
`app/chat_engine.py` обёрнуты в try/except: сбой сбора сигналов не роняет раунд.

## Переупорядочивание блока memories в контексте

`app/context_builder.build(..., rerank_signals)` в секции «7. memories» при
`HYBRID_RERANK_ENABLED=true` и наличии сигналов применяет
`rerank_memories` (детерминированный re-order; `query_text` пуст → lexical-ось
не участвует). Порядок блоков в контексте не меняется — меняется только
последовательность записей внутри memories.

## Флаги

| переменная | default | смысл |
|---|---|---|
| `HYBRID_RERANK_ENABLED` | `false` | включить rerank |
| `HYBRID_RERANK_WEIGHT_LEXICAL` | `0.30` | вес lexical-оси |
| `HYBRID_RERANK_WEIGHT_SEMANTIC` | `0.25` | вес semantic-оси |
| `HYBRID_RERANK_WEIGHT_EMOTIONAL` | `0.10` | вес emotional-оси |
| `HYBRID_RERANK_WEIGHT_STORY` | `0.15` | вес story-оси |
| `HYBRID_RERANK_WEIGHT_RELATIONSHIP` | `0.10` | вес relationship-оси |
| `HYBRID_RERANK_WEIGHT_RECENCY` | `0.05` | вес recency-оси |
| `HYBRID_RERANK_WEIGHT_SALIENCE` | `0.05` | вес salience-оси |

## Тесты

`tests/test_hybrid_rerank.py` (24): нормировка весов; каждая ось по отдельности;
rerank-сценарии (story memory выше при активном thread; эмоциональная
релевантность при anchors); fallback BM25 без embeddings; стабильность
сортировки; `build_rerank_signals` (отношения/threads, пусто при `false`);
интеграция BM25/RRF-путей; RRF-путь без флага не меняется.

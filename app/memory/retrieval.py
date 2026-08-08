"""Память: чистый BM25/rerank и гибридный поиск (без прямых сервисных импортов).

Sprint 1 (§7.1 decomposition.md): извлечение из ``crud.py``/``memory_service.py``
чистых retrieval-функций. ``SimpleBM25``/``rerank`` не зависят от ORM; гибридный
поиск (RRF) работает поверх чистого ``crud`` — направление memory → crud.
"""

from __future__ import annotations

import random
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import crud
from .. import embedding_service
from .. import models
from ..config import settings

logger = structlog.get_logger(__name__)


class SimpleBM25:
    """Lightweight BM25 scorer for relevant memory selection (P1). No external deps."""

    def __init__(self, corpus: list[str], k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.corpus = [self._tokenize(doc) for doc in corpus]
        self.doc_lengths = [len(doc) for doc in self.corpus]
        self.avg_doc_len = (
            sum(self.doc_lengths) / len(self.corpus) if self.corpus else 1.0
        )
        self.doc_freq = self._compute_doc_freq()

    def _tokenize(self, text: str) -> list[str]:
        text = text.lower()
        return re.findall(r"\b\w+\b", text)

    def _compute_doc_freq(self) -> dict[str, int]:
        df = Counter()
        for doc in self.corpus:
            unique_terms = set(doc)
            for term in unique_terms:
                df[term] += 1
        return df

    def score(self, query: str, doc_idx: int) -> float:
        if not self.corpus or doc_idx >= len(self.corpus):
            return 0.0
        query_terms = self._tokenize(query)
        score = 0.0
        doc = self.corpus[doc_idx]
        doc_len = self.doc_lengths[doc_idx]
        for term in query_terms:
            if term not in self.doc_freq or self.doc_freq[term] == 0:
                continue
            tf = doc.count(term)
            idf = len(self.corpus) / self.doc_freq[term]
            numerator = tf * (self.k1 + 1)
            denominator = tf + self.k1 * (
                1 - self.b + self.b * (doc_len / self.avg_doc_len)
            )
            score += idf * (numerator / denominator)
        return score


def select_relevant_memories(
    memories: list[models.Memory], context_text: str, top_k: int = 5
) -> list[models.Memory]:
    """Select top-K memories by BM25 relevance to current context."""
    if not memories or not context_text or not context_text.strip():
        return memories[:top_k]

    if not settings.enable_relevant_memory_selection:
        return memories[:top_k]

    contents = [getattr(m, "content", "") for m in memories]
    bm25 = SimpleBM25(contents, k1=settings.bm25_k1, b=settings.bm25_b)

    scored = []
    for i, mem in enumerate(memories):
        score_val = bm25.score(context_text, i)
        if score_val >= settings.bm25_min_score_threshold:
            scored.append((score_val, mem))

    scored.sort(key=lambda x: x[0], reverse=True)
    selected = [mem for _, mem in scored[:top_k]]

    if not selected and memories:
        logger.debug("No memories above BM25 threshold; falling back to recent")
        return memories[:top_k]

    logger.debug(
        "Selected %d relevant memories (top scores: %s)",
        len(selected),
        [round(s, 2) for s, _ in scored[:3]],
    )
    return selected


# ---------------------------------------------------------------------------
# Hybrid Retrieval v2 (Plans/update20.md §14, Sprint 6)
# ---------------------------------------------------------------------------
# Детерминированный rerank memories ПОСЛЕ существующего RRF-слияния (crud) и ДО
# witness-boost. НЕ удаляет BM25: lexical-ось остаётся, semantic-ось отпадает
# только при отсутствии embeddings (веса перенормируются). LLM-вызовов нет.


@dataclass
class RerankSignals:
    """Входные сигналы текущего контекста для rerank (§14), собираемые в crud.

    ``relationship_target_names`` — имена персонажей, к которым у наблюдателя
        есть направленные отношения (relationship-ось);
    ``active_threads`` — названия активных ``story_threads`` (story-ось).
    """

    relationship_target_names: tuple[str, ...] = ()
    active_threads: tuple[str, ...] = ()


@dataclass
class RerankContext(RerankSignals):
    """Полный контекст rerank: сигналы + текст/эмбеддинг запроса.

    ``query_text`` — текст запроса (lexical-ось, BM25);
    ``query_embedding`` — эмбеддинг запроса (semantic-ось; None → ось
        отбрасывается, веса нормируются — fallback без embeddings, §14).
    """

    query_text: str = ""
    query_embedding: list[float] | None = None


def rerank_weights() -> dict[str, float]:
    """Веса осей rerank из config, нормированные на 1.0."""
    w = {
        "lexical": settings.hybrid_rerank_weight_lexical,
        "semantic": settings.hybrid_rerank_weight_semantic,
        "emotional": settings.hybrid_rerank_weight_emotional,
        "story": settings.hybrid_rerank_weight_story,
        "relationship": settings.hybrid_rerank_weight_relationship,
        "recency": settings.hybrid_rerank_weight_recency,
        "salience": settings.hybrid_rerank_weight_salience,
    }
    total = sum(w.values()) or 1.0
    return {k: v / total for k, v in w.items()}


def _memory_kind(mem) -> str:
    """Ось памяти: story | social | semantic.

    Приоритет — ``memory_type`` (Sprint 2); fallback по ``category``, чтобы
    relationship/story-оси работали и без включённых типов памяти.
    """
    mt = str(getattr(mem, "memory_type", "") or "").lower()
    if mt == "story":
        return "story"
    if mt == "social":
        return "social"
    category = str(getattr(mem, "category", "") or "").lower()
    if category == "отношения":
        return "social"
    return "semantic"


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def _story_relevance(mem, active_threads: tuple[str, ...]) -> float:
    """Сюжетная релевантность: story-память выше при активном thread (§14).

    - ``story``-память без активных потоков (write-path ``story_threads`` —
      Sprint 8) → мягкий базовый буст 0.3;
    - совпадение токенов содержания с названием активного потока → 1.0;
    - story-память при активных потоках без совпадения → 0.5;
    - не story → 0.0.
    """
    if _memory_kind(mem) != "story":
        return 0.0
    if not active_threads:
        return 0.3
    text_tokens = _tokenize_for_overlap(mem.content or "")
    if not text_tokens:
        return 0.3
    for thread in active_threads:
        thread_tokens = _tokenize_for_overlap(thread)
        if thread_tokens and text_tokens & thread_tokens:
            return 1.0
    return 0.5


def _relationship_relevance(mem, target_names: tuple[str, ...]) -> float:
    """Relationship-релевантность: social-память об участнике отношений (§14).

    Социальная память, в которой упоминается имя персонажа, к которому у
    наблюдателя есть направленное отношение, → 1.0; иначе 0.0.
    """
    if _memory_kind(mem) != "social":
        return 0.0
    if not target_names:
        return 0.0
    text = mem.content or ""
    for name in target_names:
        if name and _name_in_text(text, name):
            return 1.0
    return 0.0


def _emotional_relevance(mem) -> float:
    """Эмоциональная релевантность из ``valence``/``intensity`` (§14)."""
    intensity = getattr(mem, "intensity", None)
    valence = getattr(mem, "valence", None)
    if intensity is None and valence is None:
        return 0.0
    return _clamp01((intensity or 0.0) + 0.5 * abs(valence or 0.0))


def _recency_score(mem, now=None) -> float:
    if now is None:
        now = datetime.utcnow()
    created = getattr(mem, "created_at", None)
    if not created:
        return 0.0
    age_days = max(0.0, (now - created).total_seconds() / 86400.0)
    return 1.0 / (1.0 + age_days)


def _salience_score(mem) -> float:
    importance = getattr(mem, "importance", None)
    if importance is None:
        return 0.5
    return _clamp01(float(importance))


def rerank_memories(
    candidates: list[models.Memory],
    context: RerankContext | None = None,
    *,
    weights: dict[str, float] | None = None,
) -> list[models.Memory]:
    """Детерминированный rerank memories (§14) после RRF, до witness-boost.

    ``score_final = w_lex×lex + w_sem×sem + w_emotion×emotion + w_story×story
    + w_rel×rel + w_recency×recency + w_salience×salience``

    - lexical: BM25 содержимого по ``query_text``, нормализованный по максимуму;
    - semantic: cosine similarity ``query_embedding`` × ``mem.embedding``
      (без embeddings ось отбрасывается, веса перенормируются — fallback BM25);
    - emotional: ``intensity`` + 0.5·|``valence``| (эмоциональные якоря);
    - story: ``_story_relevance`` (активные ``story_threads``);
    - relationship: ``_relationship_relevance`` (target-имена отношений);
    - recency: ``1/(1+возраст_в_днях)``;
    - salience: ``importance``.

    Сортировка стабильная: при равном score сохраняется исходный порядок
    (top-K от RRF), поэтому вызов на уже-отсортированном списке идемпотентен.
    """
    if not candidates or len(candidates) < 2:
        return list(candidates)

    context = context or RerankContext()
    weights = dict(weights or rerank_weights())

    query_text = (context.query_text or "").strip()
    has_semantic = bool(context.query_embedding)

    available = {k: w for k, w in weights.items()}
    if not query_text:
        available.pop("lexical", None)
    if not has_semantic:
        available.pop("semantic", None)
    total_w = sum(available.values()) or 1.0
    w = {k: v / total_w for k, v in available.items()}

    contents = [getattr(m, "content", "") or "" for m in candidates]
    bm25 = SimpleBM25(contents, k1=settings.bm25_k1, b=settings.bm25_b)
    lex_scores = (
        [bm25.score(query_text, i) for i in range(len(candidates))]
        if query_text
        else [0.0] * len(candidates)
    )
    max_lex = max(lex_scores) if lex_scores else 0.0

    sem_scores = [0.0] * len(candidates)
    if has_semantic:
        query_emb = context.query_embedding
        for i, mem in enumerate(candidates):
            raw = getattr(mem, "embedding", None)
            if raw:
                mem_emb = embedding_service.EmbeddingService.unpack_embedding(raw)
                if mem_emb:
                    sem_scores[i] = _clamp01(
                        embedding_service.EmbeddingService.cosine_similarity(
                            query_emb, mem_emb
                        )
                    )

    scored: list[tuple[float, int, models.Memory]] = []
    for i, mem in enumerate(candidates):
        lex = lex_scores[i] / max_lex if max_lex > 0 else 0.0
        score = (
            w.get("lexical", 0.0) * lex
            + w.get("semantic", 0.0) * sem_scores[i]
            + w.get("emotional", 0.0) * _emotional_relevance(mem)
            + w.get("story", 0.0)
            * _story_relevance(mem, context.active_threads)
            + w.get("relationship", 0.0)
            * _relationship_relevance(mem, context.relationship_target_names)
            + w.get("recency", 0.0) * _recency_score(mem)
            + w.get("salience", 0.0) * _salience_score(mem)
        )
        scored.append((score, i, mem))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [mem for _, _, mem in scored]


def _tokenize_for_overlap(text: str) -> set[str]:
    return set(re.findall(r"\b\w+\b", (text or "").lower()))


def _name_in_text(text: str, name: str) -> bool:
    if not name or not text:
        return False
    pattern = rf"(?<!\w){re.escape(name)}(?!\w)"
    return bool(re.search(pattern, text, flags=re.IGNORECASE))


# ----------------- Hybrid Retrieval v2 — гибридный поиск (RRF) ---------------
# Перенесено из crud.py (Sprint 1, §7.1): оркестрация поверх чистого crud.


async def build_rerank_signals(
    db: AsyncSession,
    chat_id: int,
    character_ids: list[int],
    character_names: dict[int, str],
) -> dict[int, RerankSignals]:
    """Сигналы текущего контекста для rerank (§14): направленные отношения
    персонажа (имена targets) и активные ``story_threads``.

    Собирается только при ``hybrid_rerank_enabled`` (read-path canary);
    пустые сигналы (нет отношений/потоков) — валидный результат: rerank просто
    работает без relationship/story-слагаемых. write-path ``story_threads`` —
    Sprint 8, поэтому на текущий момент поток обычно пуст (story-ось работает
    по ``memory_type='story'`` с мягким базовым бустом).
    """
    if not character_ids or not settings.hybrid_rerank_enabled:
        return {}

    rel_stmt = select(
        models.CharacterRelationship.source_character_id,
        models.CharacterRelationship.target_character_id,
    ).where(
        models.CharacterRelationship.chat_id == chat_id,
        models.CharacterRelationship.source_character_id.in_(character_ids),
    )
    rel_result = await db.execute(rel_stmt)
    rel_targets: dict[int, list[int]] = {cid: [] for cid in character_ids}
    for source_id, target_id in rel_result.all():
        rel_targets.setdefault(int(source_id), []).append(int(target_id))

    thread_stmt = select(models.StoryThread.name).where(
        models.StoryThread.chat_id == chat_id,
        models.StoryThread.status == "active",
    )
    thread_result = await db.execute(thread_stmt)
    active_threads = tuple(
        name for (name,) in thread_result.all() if name and str(name).strip()
    )

    signals: dict[int, RerankSignals] = {}
    for cid in character_ids:
        names = tuple(
            n
            for t in rel_targets.get(cid, [])
            if (n := character_names.get(int(t)))
        )
        signals[cid] = RerankSignals(
            relationship_target_names=names,
            active_threads=active_threads,
        )
    return signals


def _apply_rerank(
    selected: list[models.Memory],
    *,
    query_text: str,
    query_embedding: list[float] | None,
    signals: RerankSignals | None,
) -> list[models.Memory]:
    """Применить rerank после RRF/BM25, до witness-boost (Sprint 6, §14).

    No-op (возвращает список без изменений) при выключенном
    ``hybrid_rerank_enabled`` или отсутствии сигналов — RRF-путь не меняется.
    """
    if not settings.hybrid_rerank_enabled or not signals:
        return selected
    context = RerankContext(
        query_text=query_text,
        query_embedding=query_embedding,
        relationship_target_names=signals.relationship_target_names,
        active_threads=signals.active_threads,
    )
    return rerank_memories(selected, context)


async def _build_scoring_context(
    context_text: str,
    character_summaries: dict[int, str] | None,
    cid: int,
) -> str:
    """Augment the BM25 scoring context with the character's summary when available."""
    if not character_summaries:
        return context_text
    summary = character_summaries.get(cid)
    if not summary:
        return context_text
    return f"{context_text}\n\n{summary}"


async def get_relevant_memories_for_characters(
    db: AsyncSession,
    character_ids: list[int],
    context_text: str,
    top_k: int | None = None,
    *,
    witness_filter: bool = True,
    character_summaries: dict[int, str] | None = None,
    rerank_signals: dict[int, RerankSignals] | None = None,
) -> dict[int, list[models.Memory]]:
    """Load and rank memories by BM25 relevance to current context (P1).

    When *witness_filter* is True (default), memories whose *source_message_ids*
    reference messages the character did not witness (present/told) are filtered
    out before ranking.

    When *character_summaries* is provided, each character's summary is appended
    to the scoring context so BM25 biases toward memories relevant to the
    character's current state.

    Sprint 6 (§14): when *rerank_signals* is provided and ``hybrid_rerank_enabled``
    is on, the BM25-selected top candidates are reranked (semantic-ось отпадает —
    embeddings не используются на этом пути, веса нормируются) before the
    witness boost. Without the flag the behaviour is unchanged.
    """
    if not character_ids:
        return {}
    if not settings.enable_relevant_memory_selection:
        return await crud.get_memories_for_characters(
            db, character_ids, top_k or settings.memory_relevance_top_k
        )

    candidate_limit = (top_k or settings.memory_relevance_top_k) * 4
    # Decay importance periodically (approx every 20th call)
    if random.random() < 0.05:
        await crud.decay_memory_importance(db)
    relevant: dict[int, list[models.Memory]] = {}
    for cid in character_ids:
        candidates = await crud.get_memories_by_character(db, cid, candidate_limit)
        quality_map: dict[int, crud.WitnessQuality] = {}
        if witness_filter and settings.enable_witness_memory_filter:
            candidates, quality_map = await crud.filter_memories_by_witness(db, candidates, cid)
        scoring_context = await _build_scoring_context(context_text, character_summaries, cid)
        selected = select_relevant_memories(
            candidates, scoring_context, top_k or settings.memory_relevance_top_k
        )
        # Sprint 6 (§14): rerank после BM25, до witness-boost (fallback без
        # embeddings — semantic-слагаемое отбрасывается, веса нормируются).
        selected = _apply_rerank(
            selected,
            query_text=scoring_context,
            query_embedding=None,
            signals=rerank_signals.get(cid) if rerank_signals else None,
        )
        if quality_map:
            selected = crud._apply_witness_boost(selected, quality_map)
        # Touch last_accessed_at for selected memories
        if selected:
            now = datetime.utcnow()
            for mem in selected:
                mem.last_accessed_at = now
            await db.commit()
        relevant[cid] = selected
    return relevant


async def get_hybrid_memories_for_characters(
    db: AsyncSession,
    character_ids: list[int],
    context_text: str,
    top_k: int | None = None,
    bm25_weight: float | None = None,
    vector_weight: float | None = None,
    *,
    witness_filter: bool = True,
    character_summaries: dict[int, str] | None = None,
    rerank_signals: dict[int, RerankSignals] | None = None,
) -> dict[int, list[models.Memory]]:
    """
    Hybrid retrieval: BM25 (lexical) + Vector (semantic) with RRF fusion (P3).

    When *witness_filter* is True (default), memories whose *source_message_ids*
    reference messages the character did not witness are filtered out first.

    When *character_summaries* is provided, each character's summary is appended
    to the BM25 scoring context.

    Sprint 6 (§14): when *rerank_signals* is provided and ``hybrid_rerank_enabled``
    is on, the RRF top-K are reranked (lexical/semantic/emotional/story/
    relationship/recency/salience) BEFORE the witness boost. Without the flag the
    RRF path is unchanged.

    Returns top_k memories per character ranked by reciprocal rank fusion.
    """
    if not character_ids:
        return {}

    if not settings.embedding_enabled:
        logger.info("Embeddings disabled, falling back to BM25-only")
        return await get_relevant_memories_for_characters(
            db, character_ids, context_text, top_k,
            witness_filter=witness_filter,
            character_summaries=character_summaries,
            rerank_signals=rerank_signals,
        )

    bm25_w = bm25_weight if bm25_weight is not None else settings.hybrid_bm25_weight
    vector_w = vector_weight if vector_weight is not None else settings.hybrid_vector_weight
    rrf_k = settings.hybrid_rrf_k

    top_k = top_k or settings.memory_relevance_top_k
    candidate_limit = top_k * 8  # Get more candidates for better fusion

    emb_service = embedding_service.get_embedding_service()
    query_embedding = await emb_service.embed_single(context_text)

    if not query_embedding:
        return await get_relevant_memories_for_characters(
            db, character_ids, context_text, top_k,
            witness_filter=witness_filter,
            character_summaries=character_summaries,
            rerank_signals=rerank_signals,
        )

    # Decay importance periodically (approx every 20th call)
    if random.random() < 0.05:
        await crud.decay_memory_importance(db)
    relevant: dict[int, list[models.Memory]] = {}

    for cid in character_ids:
        candidates = await crud.get_memories_by_character(db, cid, candidate_limit)
        quality_map: dict[int, crud.WitnessQuality] = {}
        if witness_filter and settings.enable_witness_memory_filter:
            candidates, quality_map = await crud.filter_memories_by_witness(db, candidates, cid)

        if not candidates:
            relevant[cid] = []
            continue

        scoring_context = await _build_scoring_context(context_text, character_summaries, cid)

        # BM25 ranking
        bm25_results = select_relevant_memories(
            candidates, scoring_context, candidate_limit
        )
        bm25_rank = {mem.id: rank for rank, mem in enumerate(bm25_results)}

        # Vector ranking
        vector_scores = []
        for mem in candidates:
            if mem.embedding:
                mem_emb = emb_service.unpack_embedding(mem.embedding)
                if mem_emb:
                    sim = emb_service.cosine_similarity(query_embedding, mem_emb)
                    vector_scores.append((sim, mem))

        vector_scores.sort(key=lambda x: x[0], reverse=True)
        vector_rank = {mem.id: rank for rank, (_, mem) in enumerate(vector_scores)}

        # RRF fusion
        all_mem_ids = set(bm25_rank.keys()) | set(vector_rank.keys())
        rrf_scores = {}

        for mem_id in all_mem_ids:
            bm25_r = bm25_rank.get(mem_id, len(bm25_results))
            vec_r = vector_rank.get(mem_id, len(vector_scores))
            rrf = bm25_w / (rrf_k + bm25_r + 1) + vector_w / (rrf_k + vec_r + 1)
            rrf_scores[mem_id] = rrf

        # Sort by RRF score
        sorted_mem_ids = sorted(rrf_scores.keys(), key=lambda x: rrf_scores[x], reverse=True)
        top_mem_ids = sorted_mem_ids[:top_k]

        # Get memory objects
        mem_map = {mem.id: mem for mem in candidates}
        selected = [mem_map[mid] for mid in top_mem_ids if mid in mem_map]

        # Sprint 6 (§14): rerank после RRF, до witness-boost.
        selected = _apply_rerank(
            selected,
            query_text=scoring_context,
            query_embedding=query_embedding,
            signals=rerank_signals.get(cid) if rerank_signals else None,
        )

        # Apply witness boost — direct facts rank before hearsay
        if quality_map:
            selected = crud._apply_witness_boost(selected, quality_map)

        # Touch last_accessed_at
        if selected:
            now = datetime.utcnow()
            for mem in selected:
                mem.last_accessed_at = now
            await db.commit()

        relevant[cid] = selected

    return relevant

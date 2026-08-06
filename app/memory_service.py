"""Post-round memory extraction and session summarization."""

import asyncio
import json
import logging
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from . import crud
from . import embedding_service
from . import models
from . import ollama_client
from . import schemas
from . import task_queue
from . import witness_model
from .config import settings
from .database import AsyncSessionLocal, get_async_session_factory

logger = structlog.get_logger(__name__)

# How many existing memories to load for near-dup checks
MAX_EXISTING_FOR_DEDUP = 40

# Minimum token overlap between fact and observable context (grounding)
MEMORY_FACT_GROUNDING_MIN_OVERLAP = 0.22

# Stopwords ignored when grounding facts to observable context (RU + EN)
_GROUNDING_STOPWORDS = frozenset(
    {
        "и",
        "в",
        "во",
        "на",
        "с",
        "со",
        "к",
        "ко",
        "у",
        "о",
        "об",
        "от",
        "до",
        "по",
        "за",
        "из",
        "для",
        "при",
        "не",
        "ни",
        "но",
        "а",
        "что",
        "это",
        "как",
        "так",
        "же",
        "бы",
        "ли",
        "я",
        "ты",
        "он",
        "она",
        "они",
        "мы",
        "вы",
        "мне",
        "меня",
        "мной",
        "тебе",
        "тебя",
        "его",
        "её",
        "ее",
        "их",
        "мой",
        "моя",
        "мое",
        "моё",
        "твой",
        "the",
        "a",
        "an",
        "to",
        "of",
        "in",
        "on",
        "at",
        "for",
        "and",
        "or",
        "but",
        "is",
        "was",
        "were",
        "be",
        "been",
        "i",
        "you",
        "he",
        "she",
        "they",
        "we",
        "me",
        "my",
        "his",
        "her",
        "their",
        "this",
        "that",
        "with",
        "from",
    }
)

# Generic / low-value fact patterns (RU + EN)
_GENERIC_FACT_PATTERNS = (
    re.compile(r"^\s*(они|мы|все)\s+поговорил", re.I),
    re.compile(r"что[- ]?то\s+произошл", re.I),
    re.compile(r"ничего\s+важн", re.I),
    re.compile(r"обычн(ый|ая|ое)\s+разговор", re.I),
    re.compile(r"просто\s+поговорил", re.I),
    re.compile(r"^(they|we|everyone)\s+(talked|spoke|chatted)\b", re.I),
    re.compile(r"\bsomething\s+happened\b", re.I),
    re.compile(r"\bnothing\s+(important|special|notable)\b", re.I),
    re.compile(r"\bhad\s+a\s+(conversation|chat|talk)\b", re.I),
    re.compile(r"^факт\s*\d*\s*$", re.I),
    re.compile(r"^(unknown|n/?a|none|нет)\s*$", re.I),
)

# Other-mind / unobservable internal state
_OTHER_MIND_PATTERNS = (
    re.compile(
        r"\b(думает|подумал[аи]?|решил[аи]?|почувствовал[аи]?|"
        r"хочет|хотел[аи]?|планирует|собирается|намерен[а]?|"
        r"тайно|про себя|втайне)\b",
        re.I,
    ),
    re.compile(
        r"\b(thinks|thought|decided|felt|wants|wanted|plans|intends|"
        r"secretly|to\s+himself|to\s+herself)\b",
        re.I,
    ),
)

# Physical-action stems used when detecting false "me"-as-patient claims
_ACTION_STEM = (
    r"поцелова\w*|обнял\w*|ударил\w*|коснул\w*|тронул\w*|"
    r"схват\w*|сжал\w*|укусил\w*|толкнул\w*|ударил\w*|"
    r"kissed|hugged|hit|struck|touched|grabbed|bit|pushed"
)

# Fact claims the character was the patient/target of someone else's action
_FALSE_ME_PATIENT_PATTERNS = (
    # «поцеловал меня» / «обнял меня»
    re.compile(rf"(?:{_ACTION_STEM})\s+(?:меня|мне)\b", re.I),
    # «меня поцеловал» / «мне ударил»
    re.compile(rf"\b(?:меня|мне)\s+(?:{_ACTION_STEM})", re.I),
    # English: kissed me / hugged me
    re.compile(
        r"\b(?:kissed|hugged|hit|struck|touched|grabbed|bit|pushed)\s+me\b",
        re.I,
    ),
    re.compile(
        r"\bme\s+(?:was|got)\s+(?:kissed|hugged|hit|struck|touched)\b",
        re.I,
    ),
)


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


def _content_tokens(text: str) -> set[str]:
    """Tokens useful for grounding (stopwords removed)."""
    return {
        t
        for t in _tokenize_for_overlap(text)
        if t not in _GROUNDING_STOPWORDS and len(t) > 1
    }


def jaccard_similarity(a: str, b: str) -> float:
    """Token Jaccard similarity for near-duplicate detection."""
    ta = _tokenize_for_overlap(a)
    tb = _tokenize_for_overlap(b)
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    union = len(ta | tb)
    if union == 0:
        return 0.0
    return inter / union


def fact_grounding_overlap(fact_text: str, context_text: str) -> float:
    """Fraction of fact content-tokens that appear in observable context."""
    fact_tokens = _content_tokens(fact_text)
    if not fact_tokens:
        return 0.0
    context_tokens = _content_tokens(context_text)
    if not context_tokens:
        return 0.0
    return len(fact_tokens & context_tokens) / len(fact_tokens)


def _name_in_text(text: str, name: str) -> bool:
    if not name or not text:
        return False
    pattern = rf"(?<!\w){re.escape(name)}(?!\w)"
    return bool(re.search(pattern, text, flags=re.IGNORECASE))


def _looks_like_false_me_patient(
    text: str, character_name: str, context_text: str
) -> bool:
    """True if fact casts character as action patient without support in context."""
    if not any(p.search(text) for p in _FALSE_ME_PATIENT_PATTERNS):
        return False
    # Allowed when the character's name appears in observable dialogue
    # (addressed / named target) or they clearly speak about themselves.
    if _name_in_text(context_text, character_name):
        return False
    return True


def _looks_like_other_mind(text: str, character_name: str) -> bool:
    """True if fact claims unobservable internal state of someone else."""
    lowered = text.lower()
    name = (character_name or "").strip().lower()
    has_mind = any(p.search(text) for p in _OTHER_MIND_PATTERNS)
    if not has_mind:
        return False
    # Allow internal state clearly about this character
    if name and name in lowered:
        return False
    return True


def _is_generic_fact(text: str) -> bool:
    for pattern in _GENERIC_FACT_PATTERNS:
        if pattern.search(text):
            return True
    tokens = _tokenize_for_overlap(text)
    if len(tokens) <= 2:
        return True
    return False


# Story-факты: сюжетная информация «мы ищем Николая», цели, задания (§7).
_STORY_FACT_PATTERNS = (
    re.compile(r"\b(мы|группа|отряд|команда)\s+ищ\w+", re.I),
    re.compile(r"\bзадан\w+\s+[^.]*\b(найти|разыскать|отыскать|достать)\b", re.I),
    re.compile(r"\b(поиск\w*|квест|миссия|задание|цель\s+похода)\b", re.I),
)


def classify_memory_type(fact) -> str:
    """Детерминированный fallback-классификатор типа памяти (§7).

    Правила из плана: ``category=="отношения" → social``; локация/предмет →
    semantic; событийный текст → episodic; привязка к сюжету (story-маркеры)
    → story. LLM-тип (``fact.memory_type``) приоритетен — этот классификатор
    применяется только когда тип не задан/не валиден.
    """
    if isinstance(fact, dict):
        category = str(fact.get("category") or "").strip().lower()
        text = str(fact.get("fact") or "")
    else:
        category = str(getattr(fact, "category", "") or "").strip().lower()
        text = str(getattr(fact, "fact", "") or "")
    if category == "отношения":
        return "social"
    if category in ("локация", "предмет"):
        return "semantic"
    if category == "событие":
        return "episodic"
    for pattern in _STORY_FACT_PATTERNS:
        if pattern.search(text):
            return "story"
    return "semantic"


def validate_extracted_fact(
    fact: schemas.ExtractedFact,
    character_name: str,
    *,
    existing_contents: list[str] | None = None,
    observable_context: str | None = None,
) -> schemas.ExtractedFact | None:
    """Rule-based post-extraction validation. Returns cleaned fact or None."""
    if not settings.enable_memory_fact_validation:
        if not fact.witnessed:
            return None
        text = (fact.fact or "").strip()
        if not text:
            return None
        # Sprint 2 (§7): тип памяти заполняется даже при выключенной валидации.
        return fact.model_copy(
            update={
                "fact": text,
                "memory_type": fact.memory_type or classify_memory_type(fact),
            }
        )

    if not fact.witnessed:
        logger.debug(
            "Drop fact (not witnessed): %s",
            fact.fact[:80] if fact.fact else "",
        )
        return None

    text = (fact.fact or "").strip()
    if not text:
        return None

    text = re.sub(r"\s+", " ", text).strip(" -•*\t")
    if not text:
        return None

    if len(text) < settings.memory_fact_min_len:
        logger.debug("Drop fact (too short): %s", text)
        return None
    if len(text) > settings.memory_fact_max_len:
        text = text[:settings.memory_fact_max_len].rstrip()

    if _is_generic_fact(text):
        logger.debug("Drop fact (generic): %s", text)
        return None

    if _looks_like_other_mind(text, character_name):
        logger.debug("Drop fact (other-mind): %s", text)
        return None

    context = (observable_context or "").strip()
    if context:
        overlap = fact_grounding_overlap(text, context)
        if overlap < MEMORY_FACT_GROUNDING_MIN_OVERLAP:
            logger.debug(
                "Drop fact (not grounded overlap=%.2f): %s",
                overlap,
                text,
            )
            return None
        if _looks_like_false_me_patient(text, character_name, context):
            logger.debug(
                "Drop fact (false me-patient for %s): %s",
                character_name,
                text,
            )
            return None

    if existing_contents:
        for existing in existing_contents:
            if jaccard_similarity(text, existing) >= settings.memory_near_dup_jaccard:
                logger.debug("Drop fact (near-dup of existing): %s", text)
                return None

    return fact.model_copy(
        update={
            "fact": text,
            "importance": float(fact.importance),
            "category": fact.category or "событие",
            "witnessed": True,
            # Sprint 2 (§7): детерминированный fallback-классификатор.
            "memory_type": fact.memory_type or classify_memory_type(fact),
        }
    )


def validate_extracted_facts(
    facts: list[schemas.ExtractedFact],
    character_name: str,
    *,
    existing_contents: list[str] | None = None,
    max_facts: int | None = None,
    observable_context: str | None = None,
) -> list[schemas.ExtractedFact]:
    """Validate, dedupe within batch, and keep top facts by importance."""
    limit = max_facts if max_facts is not None else settings.memory_max_facts_per_round
    accepted: list[schemas.ExtractedFact] = []
    batch_texts: list[str] = list(existing_contents or [])

    for fact in facts:
        cleaned = validate_extracted_fact(
            fact,
            character_name,
            existing_contents=batch_texts,
            observable_context=observable_context,
        )
        if cleaned is None:
            continue
        if any(
            jaccard_similarity(cleaned.fact, prev) >= settings.memory_near_dup_jaccard
            for prev in batch_texts
        ):
            continue
        accepted.append(cleaned)
        batch_texts.append(cleaned.fact)

    accepted.sort(key=lambda f: f.importance, reverse=True)
    return accepted[:limit]


_CHARACTER_CARD_FIELDS = (
    "name",
    "personality",
    "traits",
    "speech_style",
    "example_messages",
    "boundaries",
    "background",
    "relationships",
    "location",
)


def _get_attr(obj, key: str):
    return obj[key] if isinstance(obj, dict) else getattr(obj, key)


def _character_from_snapshot(snapshot: dict) -> SimpleNamespace:
    return SimpleNamespace(
        **{field: snapshot.get(field, "") for field in _CHARACTER_CARD_FIELDS}
    )


def _format_messages_as_text(
    messages: list,
    character_names: dict[int, str] | None = None,
) -> str:
    lines = []
    for message in messages:
        role = _get_attr(message, "role")
        content = _get_attr(message, "content")
        if role == "user":
            lines.append(f"Игрок: {content}")
        elif role == "character":
            character_id = _get_attr(message, "character_id")
            if character_names and character_id:
                name = character_names.get(character_id, "Персонаж")
            elif not isinstance(message, dict) and getattr(message, "character", None):
                name = message.character.name
            else:
                name = "Персонаж"
            lines.append(f"{name}: {content}")
        elif role == "system":
            lines.append(f"Система: {content}")
    return "\n".join(lines)


def _format_round_as_text(
    messages: list,
    character_names: dict[int, str] | None = None,
) -> str:
    return _format_messages_as_text(messages, character_names)


def _witness_filtered_text(
    messages: list,
    viewer_character_id: int,
    character_names: dict[int, str],
    presence_map: dict[int, str] | None = None,
    *,
    same_round_ids: set[int] | None = None,
    viewer_location: str | None = None,
    character_locations: dict[int, str] | None = None,
) -> str:
    """RP-style witness filter (includes mentioned snippets). Prefer memory filter for extraction."""
    return witness_model.filter_history_for_character(
        messages,
        viewer_character_id,
        character_names,
        presence_map,
        same_round_ids=same_round_ids,
        max_len=len(messages) or 1,
        viewer_location=viewer_location,
        character_locations=character_locations,
    )


def get_observable_context_for_character(
    messages: list,
    viewer_character_id: int,
    character_names: dict[int, str],
    presence_map: dict[int, str] | None = None,
    *,
    same_round_ids: set[int] | None = None,
    viewer_location: str | None = None,
    character_locations: dict[int, str] | None = None,
    attention_map: dict[int, float] | None = None,
) -> witness_model.ObservableContext:
    """Perception-filtered context safe for memory extraction (present/told only).

    Sprint 4 (§11): ``attention_map`` — attention score пары (персонаж,
    сообщения) из ``crud.get_attention_map``; события с attention < LOW в память
    не идут даже при present/told (воспринято ≠ вошло в сознание).
    """
    return witness_model.filter_history_for_memory_extraction(
        messages,
        viewer_character_id,
        character_names,
        presence_map,
        same_round_ids=same_round_ids,
        max_len=len(messages) or 1,
        viewer_location=viewer_location,
        character_locations=character_locations,
        attention_map=attention_map,
    )


def _log_memory_perception(
    *,
    chat_id: int,
    character_name: str,
    character_id: int,
    context: witness_model.ObservableContext,
) -> None:
    for line in context.lines:
        logger.debug(
            "[Memory] character=%s id=%s event=%s location=%r perceived=true "
            "presence=%s memory_candidate=eligible preview=%r",
            character_name,
            character_id,
            line.message_id,
            line.location,
            line.presence,
            line.content_preview,
        )
    for item in context.skipped:
        logger.debug(
            "[Memory] character=%s id=%s event=%s location=%r perceived=false "
            "presence=%s memory_candidate=skipped reason=%s preview=%r",
            character_name,
            character_id,
            item.get("message_id"),
            item.get("location"),
            item.get("presence"),
            item.get("reason"),
            item.get("preview"),
        )
    if not context.has_observable_events:
        logger.debug(
            "[Memory] character=%s id=%s chat_id=%s memory_candidate=skipped "
            "reason=no_observable_events",
            character_name,
            character_id,
            chat_id,
        )


def _sensors_proposal_to_facts(sensors_result: dict) -> list[schemas.ExtractedFact]:
    """Sensors memory-candidates (§5.1.3) → ExtractedFact (движок валидирует).

    Sensors предлагает ``{facts: [{text, importance}]}``; категория по умолчанию
    «событие» (fallback-классификатор уточнит тип). Sensors память НЕ пишет.
    """
    facts: list[schemas.ExtractedFact] = []
    for item in sensors_result.get("facts") or []:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        importance = item.get("importance")
        try:
            imp = float(importance) if importance is not None else 0.5
        except (TypeError, ValueError):
            imp = 0.5
        facts.append(
            schemas.ExtractedFact(
                fact=text,
                category="событие",
                importance=max(0.0, min(1.0, imp)),
                witnessed=True,
            )
        )
    return facts


async def _extract_and_save_memories(
    client: httpx.AsyncClient,
    chat_id: int,
    round_snapshots: list[dict],
    character_snapshots: list[dict],
    model_name: str,
) -> None:
    if len(round_snapshots) < 2:
        return

    character_names = {c["id"]: c["name"] for c in character_snapshots}
    character_locations = {
        c["id"]: (c.get("location") or "") for c in character_snapshots
    }
    same_round_ids = {
        snapshot["id"]
        for snapshot in round_snapshots
        if snapshot.get("id") is not None
    }
    message_ids = [
        snapshot["id"] for snapshot in round_snapshots if snapshot.get("id") is not None
    ]

    async with AsyncSessionLocal() as db:
        try:
            for character_snap in character_snapshots:
                character = _character_from_snapshot(character_snap)
                char_id = character_snap["id"]
                char_name = character_snap["name"]
                viewer_location = character_snap.get("location") or ""

                presence_map = await crud.get_presence_map(db, message_ids, char_id)
                attention_map = await crud.get_attention_map(db, message_ids, char_id)
                observable = get_observable_context_for_character(
                    round_snapshots,
                    char_id,
                    character_names,
                    presence_map,
                    same_round_ids=same_round_ids,
                    viewer_location=viewer_location,
                    character_locations=character_locations,
                    attention_map=attention_map,
                )
                _log_memory_perception(
                    chat_id=chat_id,
                    character_name=char_name,
                    character_id=char_id,
                    context=observable,
                )

                if not observable.has_observable_events:
                    continue

                round_text = observable.text
                # Sprint 2 (§5.1.3/§7): Sensors memory-candidates имеют
                # приоритет над прямым LLM-извлечением; Sensors память НЕ пишет —
                # только предлагает факты, движок валидирует и сохраняет.
                raw_facts = None
                sensors_used = False
                try:
                    from .sensors_service import sensors_service

                    sensors_result = await sensors_service.run(
                        client, task="memory", minimal_context=round_text
                    )
                    sensors_facts = _sensors_proposal_to_facts(sensors_result or {})
                    if sensors_facts:
                        raw_facts = sensors_facts
                        sensors_used = True
                        logger.debug(
                            "[Memory] character=%s source=sensors candidates=%d",
                            char_name,
                            len(sensors_facts),
                        )
                except Exception:
                    logger.warning(
                        "[chat_id=%d] Sensors memory proposal failed for %s",
                        chat_id,
                        char_name,
                    )
                    sensors_facts = []

                if raw_facts is None:
                    try:
                        raw_facts = await ollama_client.extract_memories_for_character(
                            client, model_name, character, round_text
                        )
                    except Exception:
                        logger.warning(
                            "[chat_id=%d] Memory extraction failed for %s",
                            chat_id,
                            char_name,
                        )
                        continue

                if not raw_facts:
                    logger.debug(
                        "[Memory] character=%s memory_candidate=skipped reason=llm_empty",
                        char_name,
                    )
                    continue

                structured: list[schemas.ExtractedFact] = []
                for item in raw_facts:
                    if isinstance(item, schemas.ExtractedFact):
                        structured.append(item)
                    elif isinstance(item, str):
                        structured.append(schemas.ExtractedFact(fact=item))
                    elif isinstance(item, dict):
                        try:
                            structured.append(schemas.ExtractedFact.model_validate(item))
                        except Exception:
                            continue

                existing = await crud.get_memories_by_character(
                    db, char_id, limit=MAX_EXISTING_FOR_DEDUP
                )
                existing_contents = [m.content for m in existing]

                validated = validate_extracted_facts(
                    structured,
                    char_name,
                    existing_contents=existing_contents,
                    observable_context=round_text,
                )
                if not validated:
                    logger.debug(
                        "[chat_id=%d] No valid facts for %s after validation",
                        chat_id,
                        char_name,
                    )
                    continue

                # Only link memory to messages the character actually witnessed (present/told)
                observed_message_ids = [
                    line.message_id for line in observable.lines
                    if line.message_id is not None
                ]
                saved = 0
                for fact in validated:
                    # Sprint 2 (§7): тип памяти пишется только при включённом
                    # флаге memory_types_enabled (canary); иначе legacy-поведение.
                    memory_type = (
                        fact.memory_type if settings.memory_types_enabled else None
                    )
                    created = await crud.create_memory(
                        db,
                        schemas.MemoryCreate(
                            chat_id=chat_id,
                            character_id=char_id,
                            content=fact.fact,
                            importance=fact.importance,
                            category=fact.category,
                            memory_type=memory_type,
                        ),
                        source_message_ids=observed_message_ids,
                    )
                    if created is not None:
                        saved += 1
                        # Enqueue embedding generation job (non-blocking)
                        if settings.embedding_enabled:
                            await task_queue.memory_job_queue.enqueue(
                                job_type="embed_memory",
                                chat_id=chat_id,
                                payload={"memory_id": created.id, "content": fact.fact},
                            )
                        logger.debug(
                            "[Memory] character=%s memory_candidate=created fact=%r",
                            char_name,
                            fact.fact[:120],
                        )
                await crud.ensure_memory_limit(db, char_id)
                logger.info(
                    "[chat_id=%d] Saved %d/%d validated facts for %s",
                    chat_id,
                    saved,
                    len(validated),
                    char_name,
                )

            logger.info("[chat_id=%d] Per-character memory extraction complete", chat_id)
        except Exception:
            logger.exception("[chat_id=%d] Background memory save failed", chat_id)


async def _maybe_update_summaries(
    client: httpx.AsyncClient,
    chat_id: int,
    character_snapshots: list[dict],
    model_name: str,
) -> None:
    async with AsyncSessionLocal() as db:
        try:
            character_ids = [c["id"] for c in character_snapshots]
            character_names = {c["id"]: c["name"] for c in character_snapshots}
            character_locations = {
                c["id"]: (c.get("location") or "") for c in character_snapshots
            }
            summaries = await crud.get_summaries_for_characters(db, character_ids)

            for character_snap in character_snapshots:
                character_id = character_snap["id"]
                existing = summaries.get(character_id)
                through_message_id = existing.through_message_id if existing else 0

                pending_count = await crud.count_messages_after(
                    db, chat_id, through_message_id
                )
                if pending_count < settings.summary_interval_messages:
                    continue

                new_messages = await crud.get_messages_since(
                    db, chat_id, through_message_id
                )
                if not new_messages:
                    continue

                presence_map = await crud.get_presence_map(
                    db,
                    [message.id for message in new_messages],
                    character_id,
                )
                attention_map = await crud.get_attention_map(
                    db,
                    [message.id for message in new_messages],
                    character_id,
                )
                # Same stricter filter as memory: present/told only (no mentioned snippets)
                observable = get_observable_context_for_character(
                    new_messages,
                    character_id,
                    character_names,
                    presence_map,
                    viewer_location=character_snap.get("location") or "",
                    character_locations=character_locations,
                    attention_map=attention_map,
                )
                dialogue_text = observable.text
                if not dialogue_text.strip():
                    continue
                character = _character_from_snapshot(character_snap)
                existing_content = existing.content if existing else ""

                try:
                    updated_summary = await ollama_client.summarize_for_character(
                        client,
                        model_name,
                        character,
                        dialogue_text,
                        existing_summary=existing_content,
                    )
                except Exception:
                    logger.warning(
                        "[chat_id=%d] Summary update failed for %s",
                        chat_id,
                        character_snap["name"],
                    )
                    continue

                if not updated_summary.strip():
                    continue

                through_message_id = max(message.id for message in new_messages)
                await crud.upsert_character_summary(
                    db,
                    chat_id,
                    character_id,
                    updated_summary.strip(),
                    through_message_id,
                )
                logger.info(
                    "[chat_id=%d] Summary updated for %s (through_message_id=%d)",
                    chat_id,
                    character_snap["name"],
                    through_message_id,
                )
        except Exception:
            logger.exception("[chat_id=%d] Background summary update failed", chat_id)


async def _process_post_round_job(payload: dict) -> dict:
    """Job handler for post-round memory processing."""
    # Recreate httpx client from settings
    client = httpx.AsyncClient(
        base_url=settings.ollama_base_url,
        timeout=settings.ollama_timeout,
    )
    try:
        await _extract_and_save_memories(
            client,
            payload["chat_id"],
            payload["round_snapshots"],
            payload["character_snapshots"],
            payload["model_name"],
        )
        await _maybe_update_summaries(
            client,
            payload["chat_id"],
            payload["character_snapshots"],
            payload["model_name"],
        )
        return {"status": "completed", "chat_id": payload["chat_id"]}
    finally:
        await client.aclose()


async def process_post_round(
    client: httpx.AsyncClient,
    chat_id: int,
    round_snapshots: list[dict],
    character_snapshots: list[dict],
    model_name: str,
) -> None:
    """Enqueue memory processing job instead of fire-and-forget."""
    if not settings.task_queue_enabled:
        # Fallback: direct execution (for testing/simple deployments)
        try:
            await _extract_and_save_memories(
                client,
                chat_id,
                round_snapshots,
                character_snapshots,
                model_name,
            )
            await _maybe_update_summaries(
                client,
                chat_id,
                character_snapshots,
                model_name,
            )
        except Exception:
            logger.exception("post_round_failed", chat_id=chat_id)
        return

    def _serialize_datetime(obj):
        """JSON serializer for datetime objects."""
        if isinstance(obj, datetime):
            return obj.isoformat()
        raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")

    payload = {
        "chat_id": chat_id,
        "round_snapshots": round_snapshots,
        "character_snapshots": character_snapshots,
        "model_name": model_name,
    }

    job = await task_queue.memory_job_queue.enqueue(
        job_type="post_round",
        chat_id=chat_id,
        payload=payload,
    )

    # Fire-and-forget the actual processing
    # run_job will dispatch to _process_post_round_job via _dispatch_job based on job_type
    asyncio.create_task(
        task_queue.memory_job_queue.run_job(job)
    )


# ============================================================
# Memory Consolidation (P3)
# ============================================================

async def _cluster_memories_by_similarity(
    memories: list[models.Memory], threshold: float
) -> list[list[models.Memory]]:
    """Group memories into clusters by Jaccard similarity (greedy agglomerative)."""
    if not memories:
        return []

    # Sort by importance desc (highest first) - these become cluster centers
    sorted_memories = sorted(
        memories, key=lambda m: m.importance if m.importance is not None else 0.5, reverse=True
    )

    clusters: list[list[models.Memory]] = []
    used: set[int] = set()

    for mem in sorted_memories:
        if mem.id in used:
            continue

        cluster = [mem]
        used.add(mem.id)

        for other in sorted_memories:
            if other.id in used:
                continue

            sim = jaccard_similarity(mem.content, other.content)
            if sim >= threshold:
                cluster.append(other)
                used.add(other.id)

        clusters.append(cluster)

    return clusters


async def _merge_memory_cluster_llm(
    client: httpx.AsyncClient,
    model_name: str,
    cluster: list[models.Memory],
    character_name: str,
) -> str | None:
    """Use LLM to merge similar facts into one concise fact."""
    if len(cluster) == 1:
        return cluster[0].content

    facts_text = "\n".join(f"- {m.content}" for m in cluster)

    # Use extraction model or default
    consolidation_model = settings.consolidation_llm_model or model_name

    prompt = (
        f"Персонаж: {character_name}\n"
        f"Схожие факты для объединения:\n{facts_text}\n\n"
        "Объедини эти факты в ОДИН точный и краткий факт. "
        "Сохрани важные детали: имена, места, даты, отношения. Убери повторы. "
        "Результат — только объединённый факт, без лишних слов."
    )

    try:
        async with ollama_client.llm_request(consolidation_model, "/api/generate"):
            resp = await client.post(
                "/api/generate",
                json={
                    "model": consolidation_model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {"temperature": 0.3, "num_predict": 100},
                },
                timeout=settings.ollama_timeout,
            )
        resp.raise_for_status()
        data = resp.json()
        merged = data.get("response", "").strip()
        if merged:
            logger.debug(
                "[Consolidation] Merged %d facts into: %s",
                len(cluster),
                merged[:120],
            )
            return merged
    except Exception:
        logger.warning("[Consolidation] LLM merge failed for cluster of %d", len(cluster))

    # Fallback: keep longest fact
    return max(cluster, key=lambda m: len(m.content)).content


async def _consolidate_character_memories(
    db: AsyncSession,
    client: httpx.AsyncClient,
    model_name: str,
    character_id: int,
    character_name: str,
    threshold: float,
    min_cluster_size: int,
    max_memories: int,
) -> tuple[int, int]:
    """Consolidate memories for a single character. Returns (merged_count, deleted_count)."""
    # Load memories - most recent/important first
    stmt = (
        select(models.Memory)
        .where(models.Memory.character_id == character_id)
        .order_by(models.Memory.importance.desc(), models.Memory.created_at.desc())
        .limit(max_memories)
    )
    result = await db.execute(stmt)
    memories = list(result.scalars().all())

    if len(memories) < min_cluster_size:
        return 0, 0

    clusters = await _cluster_memories_by_similarity(memories, threshold)

    merged_count = 0
    deleted_count = 0

    for cluster in clusters:
        if len(cluster) < min_cluster_size:
            continue

        # Sort cluster by importance desc - primary is first
        cluster.sort(key=lambda m: m.importance if m.importance is not None else 0.5, reverse=True)
        primary = cluster[0]
        to_merge = cluster[1:]

        merged_content = await _merge_memory_cluster_llm(
            client, model_name, cluster, character_name
        )

        if merged_content and merged_content != primary.content:
            primary.content = merged_content
            primary.last_accessed_at = datetime.utcnow()
            primary.importance = min(1.0, primary.importance + 0.1)  # slight boost
            merged_count += 1

        # Delete merged memories
        for mem in to_merge:
            await db.delete(mem)
            deleted_count += 1

    if merged_count > 0 or deleted_count > 0:
        await db.commit()

    return merged_count, deleted_count


async def consolidate_memories_job(
    db: AsyncSession,
    client: httpx.AsyncClient,
    model_name: str,
    chat_id: int = 0,
) -> dict:
    """Main consolidation job - processes all characters (optionally of one chat).

    Legacy P3 job: memory clustering + merge only. Sprint 12 replaces the
    trigger (score-based) and extends the set via ``consolidate_chat_adaptive``;
    ``chat_id`` filters to a single chat (0 = all chats, legacy behavior).
    """
    if not settings.consolidation_enabled:
        return {"status": "disabled", "chars_processed": 0, "merged": 0, "deleted": 0}

    # Get characters with memories (optionally restricted to a chat)
    stmt = (
        select(models.Character.id, models.Character.name, models.Character.chat_id)
        .join(models.Memory, models.Memory.character_id == models.Character.id)
        .distinct()
    )
    if chat_id:
        stmt = stmt.where(models.Character.chat_id == chat_id)
    result = await db.execute(stmt)
    characters = result.all()

    total_merged = 0
    total_deleted = 0
    chars_processed = 0

    for char_id, char_name, char_chat_id in characters:
        merged, deleted = await _consolidate_character_memories(
            db,
            client,
            model_name,
            char_id,
            char_name,
            settings.consolidation_similarity_threshold,
            settings.consolidation_min_cluster_size,
            settings.consolidation_max_memories_per_char,
        )
        total_merged += merged
        total_deleted += deleted
        chars_processed += 1

    logger.info(
        "[Consolidation] Complete: chars=%d merged=%d deleted=%d",
        chars_processed,
        total_merged,
        total_deleted,
    )

    return {
        "status": "completed",
        "chars_processed": chars_processed,
        "merged": total_merged,
        "deleted": total_deleted,
    }


async def _process_consolidation_job(payload: dict) -> dict:
    """Job handler for consolidation - compatible with task queue.

    Sprint 12: payload with ``level`` (soft/hard/critical) runs the full
    adaptive set for a single chat; legacy payload (no ``level``) keeps the
    old all-chats memory clustering behaviour.
    """
    client = httpx.AsyncClient(
        base_url=settings.ollama_base_url,
        timeout=settings.ollama_timeout,
    )
    try:
        async with AsyncSessionLocal() as db:
            model_name = payload.get("model_name", settings.default_model)
            chat_id = int(payload.get("chat_id") or 0)
            level = payload.get("level")
            if level:
                return await consolidate_chat_adaptive(
                    db,
                    client,
                    chat_id=chat_id,
                    model_name=model_name,
                    level=level,
                    since_soft=_parse_payload_dt(payload.get("since_soft")),
                    since_hard=_parse_payload_dt(payload.get("since_hard")),
                )
            return await consolidate_memories_job(
                db, client, model_name, chat_id=chat_id
            )
    finally:
        await client.aclose()


async def enqueue_consolidation_job(
    chat_id: int = 0,
    model_name: str | None = None,
    level: str | None = None,
    since_soft: datetime | None = None,
    since_hard: datetime | None = None,
) -> models.MemoryJob:
    """Enqueue a consolidation job.

    ``level`` = soft/hard/critical (Sprint 12). ``since_soft``/``since_hard``
    pin the consolidation window (the pre-trigger baselines) so the job
    refreshes summaries from the dialogue that actually triggered it.
    """
    payload: dict = {
        "chat_id": chat_id,
        "model_name": model_name or settings.default_model,
    }
    if level:
        payload["level"] = level
    if since_soft is not None:
        payload["since_soft"] = since_soft.isoformat()
    if since_hard is not None:
        payload["since_hard"] = since_hard.isoformat()
    return await task_queue.memory_job_queue.enqueue(
        job_type="consolidation",
        chat_id=chat_id,
        payload=payload,
    )


# ============================================================
# Adaptive Consolidation (Sprint 12, Plans/update20.md §20)
# ============================================================

# Input keys in the same order as the score weights.
CONSOLIDATION_COUNT_KEYS = (
    "messages",
    "events",
    "facts",
    "rel_events",
    "story_events",
    "anchors",
)

# Deterministic critical-event whitelist (§20): смерть, предательство, признание,
# свадьба, важное раскрытие, сюжетный milestone, завершение главной цели и т.п.
CRITICAL_ACTION_KEYWORDS = (
    "смерть",
    "умирает",
    "погиб",
    "погибает",
    "предател",
    "признание",
    "признаётся",
    "признается",
    "свадьб",
    "бракосочетан",
    "раскрыти",
    "разоблач",
    "milestone",
    "сюжетн",
    "главная цель",
    "завершени",
    "завершен",
    "завершён",
    "начиная отношений",
    "конец отношений",
)


def _consolidation_weights() -> tuple[float, ...]:
    """Score weights from config (§20)."""
    return (
        settings.consolidation_weight_messages,
        settings.consolidation_weight_events,
        settings.consolidation_weight_facts,
        settings.consolidation_weight_rel_events,
        settings.consolidation_weight_story_events,
        settings.consolidation_weight_anchors,
    )


def compute_consolidation_score(
    counts: dict[str, int], weights: tuple[float, ...] | None = None
) -> float:
    """Weighted score of new inputs since the last consolidation (§20).

    ``counts`` has the ``CONSOLIDATION_COUNT_KEYS`` shape. Deterministic.
    """
    if weights is None:
        weights = _consolidation_weights()
    return sum(
        float(counts.get(key, 0)) * weight
        for key, weight in zip(CONSOLIDATION_COUNT_KEYS, weights)
    )


def is_critical_event(event: Any, *, critical_importance: float | None = None) -> bool:
    """Deterministic critical-event detection (§20).

    True when ``importance >= critical_importance`` OR the event's action/type/
    description matches the whitelist keywords. No LLM involved.
    """
    if critical_importance is None:
        critical_importance = float(settings.consolidation_critical_importance or 8.0)

    importance = 0.0
    try:
        importance = float(getattr(event, "importance", 0) or 0)
    except (TypeError, ValueError):
        importance = 0.0
    if importance >= critical_importance:
        return True

    parts: list[str] = []
    for attr in ("action", "event_type", "description", "event", "kind", "content"):
        value = getattr(event, attr, None)
        if value is None:
            continue
        if isinstance(value, dict):
            value = json.dumps(value, ensure_ascii=False)
        parts.append(str(value))
    haystack = " ".join(parts).lower()
    return any(keyword in haystack for keyword in CRITICAL_ACTION_KEYWORDS)


async def _latest_critical_event(
    db: AsyncSession, chat_id: int, since: datetime
) -> dict | None:
    """Most recent critical world event in the chat since ``since`` (§20)."""
    stmt = (
        select(models.WorldEvent)
        .where(
            models.WorldEvent.chat_id == chat_id,
            models.WorldEvent.created_at > since,
        )
        .order_by(models.WorldEvent.created_at.desc(), models.WorldEvent.id.desc())
        .limit(200)
    )
    result = await db.execute(stmt)
    for event in result.scalars().all():
        if is_critical_event(event):
            return {
                "id": event.id,
                "round_id": event.round_id,
                "importance": event.importance,
                "event_type": event.event_type,
                "action": event.action,
            }
    return None


def _parse_consolidation_counters(state) -> dict:
    if state is None or not state.counters:
        return {}
    try:
        parsed = json.loads(state.counters)
        return parsed if isinstance(parsed, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _parse_payload_dt(value: Any) -> datetime | None:
    """Parse an ISO datetime from a job payload (Sprint 12)."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


async def evaluate_consolidation(
    db: AsyncSession,
    chat_id: int,
    *,
    round_id: str | None = None,
) -> dict:
    """Deterministic adaptive-consolidation decision (§20).

    Computes soft/hard scores since the last consolidation and detects critical
    events. Returns ``level`` in {critical, hard, soft, skip} plus the counts
    and scores. No DB writes; the scheduler/hook uses this to decide whether to
    enqueue a consolidation job.
    """
    chat = await crud.get_chat(db, chat_id)
    if chat is None:
        return {"level": "skip", "reason": "no chat"}
    if not settings.consolidation_enabled:
        return {"level": "skip", "reason": "consolidation disabled"}

    state = await crud.get_consolidation_state(db, chat_id)
    base = getattr(chat, "created_at", None) or datetime.min
    since_soft = state.last_soft_at if state is not None and state.last_soft_at else base
    since_hard = state.last_hard_at if state is not None and state.last_hard_at else base

    counts_soft = await crud.count_consolidation_inputs(db, chat_id, since_soft)
    counts_hard = await crud.count_consolidation_inputs(db, chat_id, since_hard)
    score_soft = compute_consolidation_score(counts_soft)
    score_hard = compute_consolidation_score(counts_hard)

    soft_threshold = float(settings.consolidation_soft_threshold or 25.0)
    hard_threshold = float(settings.consolidation_hard_threshold or 50.0)
    max_per_round = int(settings.consolidation_critical_max_per_round or 2)

    result = {
        "chat_id": chat_id,
        "score_soft": score_soft,
        "score_hard": score_hard,
        "counts": counts_hard,
        "critical": None,
        "round_id": round_id,
        "since_soft": since_soft,
        "since_hard": since_hard,
    }

    critical = await _latest_critical_event(db, chat_id, since_hard)
    if critical is not None:
        result["critical"] = critical
        counters = _parse_consolidation_counters(state)
        current_round = critical.get("round_id") or round_id
        already = counters.get("critical_round") == current_round
        if already and int(counters.get("critical_count", 0)) >= max_per_round:
            # Dedup: no more critical consolidations for this round; fall back
            # to score-based tiers.
            if score_hard >= hard_threshold:
                result["level"] = "hard"
                result["reason"] = "critical dedup -> hard score"
            elif score_soft >= soft_threshold:
                result["level"] = "soft"
                result["reason"] = "critical dedup -> soft score"
            else:
                result["level"] = "skip"
                result["reason"] = "critical dedup cap reached"
            return result
        result["level"] = "critical"
        result["reason"] = "critical event"
        return result

    if score_hard >= hard_threshold:
        result["level"] = "hard"
        result["reason"] = "hard threshold"
    elif score_soft >= soft_threshold:
        result["level"] = "soft"
        result["reason"] = "soft threshold"
    else:
        result["level"] = "skip"
        result["reason"] = "idle (score below thresholds)"
    return result


async def schedule_adaptive_consolidation(
    db: AsyncSession,
    *,
    chat_id: int,
    model_name: str | None = None,
    round_id: str | None = None,
) -> dict:
    """Score-based trigger (§20): evaluate and, when triggered, mark the
    ``consolidation_state`` baseline and enqueue a background job.

    Idle chats (score≈0, no critical) are skipped. Critical events trigger an
    immediate hard consolidation (deduplicated to N per round). Enqueuing
    advances the baseline, so repeated polls for the same events do not
    re-trigger.
    """
    if not settings.adaptive_consolidation_enabled:
        return {"level": "skip", "reason": "adaptive disabled"}

    decision = await evaluate_consolidation(db, chat_id, round_id=round_id)
    level = decision["level"]
    if level == "skip":
        return decision

    state = await crud.get_consolidation_state(db, chat_id)
    counters = _parse_consolidation_counters(state)
    for key in CONSOLIDATION_COUNT_KEYS:
        counters[key] = int(decision["counts"].get(key, 0))

    now = datetime.utcnow()
    last_soft: datetime | None = now
    last_hard: datetime | None = None

    if level == "critical":
        last_hard = now
        current_round = (decision.get("critical") or {}).get("round_id") or round_id
        if counters.get("critical_round") != current_round:
            counters["critical_round"] = current_round
            counters["critical_count"] = 1
        else:
            counters["critical_count"] = int(counters.get("critical_count", 0)) + 1
    elif level == "hard":
        last_hard = now

    await crud.upsert_consolidation_state(
        db,
        chat_id,
        last_soft_at=last_soft,
        last_hard_at=last_hard,
        counters=counters,
    )

    job = await enqueue_consolidation_job(
        chat_id=chat_id,
        model_name=model_name,
        level=level,
        since_soft=decision.get("since_soft"),
        since_hard=decision.get("since_hard"),
    )
    decision["enqueued"] = True
    decision["job_id"] = job.id
    decision["job"] = job
    return decision


async def _consolidate_chat_memories(
    db: AsyncSession,
    client: httpx.AsyncClient,
    *,
    chat_id: int,
    model_name: str,
) -> dict:
    """Component: memory clustering + merge for all characters of a chat."""
    stmt = (
        select(models.Character.id, models.Character.name, models.Character.chat_id)
        .join(models.Memory, models.Memory.character_id == models.Character.id)
        .where(models.Character.chat_id == chat_id)
        .distinct()
    )
    result = await db.execute(stmt)
    characters = result.all()

    total_merged = 0
    total_deleted = 0
    chars_processed = 0
    for char_id, char_name, _cid in characters:
        merged, deleted = await _consolidate_character_memories(
            db,
            client,
            model_name,
            char_id,
            char_name,
            settings.consolidation_similarity_threshold,
            settings.consolidation_min_cluster_size,
            settings.consolidation_max_memories_per_char,
        )
        total_merged += merged
        total_deleted += deleted
        chars_processed += 1
    return {"chars": chars_processed, "merged": total_merged, "deleted": total_deleted}


async def _consolidate_chat_summary(
    db: AsyncSession,
    client: httpx.AsyncClient,
    *,
    chat_id: int,
    model_name: str,
    since: datetime,
) -> dict:
    """Component: refresh per-character summaries from dialogue since ``since``.

    Summary is ONE of the consolidation outputs (§20) — the pipeline stays
    separate from the ~20-message summarizer in ``chat_engine``.
    """
    if since is None:
        since = datetime.min
    characters = await crud.get_characters_by_chat(db, chat_id)
    updated = 0
    for character in characters:
        dialogue = await crud.get_messages_since_ts(
            db,
            chat_id,
            since,
            role="character",
            character_id=character.id,
            limit=120,
        )
        if not dialogue:
            continue
        dialogue_text = "\n".join(msg.content for msg in dialogue)
        existing = await crud.get_character_summary(db, character.id)
        existing_content = existing.content if existing else ""
        try:
            updated_summary = await ollama_client.summarize_for_character(
                client,
                model_name,
                character,
                dialogue_text,
                existing_summary=existing_content,
            )
        except Exception:
            logger.warning(
                "[Consolidation][chat_id=%d] summary refresh failed for %s",
                chat_id,
                character.name,
            )
            continue
        if not (updated_summary or "").strip():
            continue
        through_message_id = max(msg.id for msg in dialogue)
        await crud.upsert_character_summary(
            db, chat_id, character.id, updated_summary.strip(), through_message_id
        )
        updated += 1
    return {"updated": updated}


async def _consolidate_chat_relationships(db: AsyncSession, *, chat_id: int) -> dict:
    """Component: fold old relationship events into archive rows (hard).

    Reuses the deterministic ``prune_relationship_events`` fold for every
    relationship of the chat — relationship evidence stays bounded.
    """
    stmt = select(models.CharacterRelationship).where(
        models.CharacterRelationship.chat_id == chat_id
    )
    result = await db.execute(stmt)
    relationships = list(result.scalars().all())

    from . import relationship_service  # локальный импорт против цикла

    folded = 0
    for rel in relationships:
        archived = await relationship_service.prune_relationship_events(db, rel.id)
        if archived is not None:
            folded += 1
    if folded:
        await db.commit()
    return {"relationships": len(relationships), "folded": folded}


async def _consolidate_chat_anchors(db: AsyncSession, *, chat_id: int) -> dict:
    """Component: dedupe emotional anchors by (relationship, event) — keep the
    most important, drop weaker duplicates (hard).
    """
    stmt = (
        select(models.MemoryAnchor)
        .join(
            models.CharacterRelationship,
            models.CharacterRelationship.id == models.MemoryAnchor.relationship_id,
        )
        .where(models.CharacterRelationship.chat_id == chat_id)
        .order_by(
            models.MemoryAnchor.importance.desc(),
            models.MemoryAnchor.timestamp.desc(),
            models.MemoryAnchor.id.desc(),
        )
    )
    result = await db.execute(stmt)
    anchors = list(result.scalars().all())

    seen: dict[tuple, models.MemoryAnchor] = {}
    removed = 0
    for anchor in anchors:
        key = (anchor.relationship_id, anchor.event_id)
        existing = seen.get(key)
        if existing is None:
            seen[key] = anchor
            continue
        # Ordering already puts the highest-importance first; drop the later one.
        await db.delete(anchor)
        removed += 1
    if removed:
        await db.commit()
    return {"anchors": len(anchors), "removed": removed}


async def _consolidate_chat_story(
    db: AsyncSession,
    client: httpx.AsyncClient,
    *,
    chat_id: int,
    model_name: str,
    level: str,
) -> dict:
    """Component: story state update (hard). Delegates to the existing Sprint 9
    hook, which is a canary gated by ``story_consolidation_enabled``.
    """
    if not settings.story_consolidation_enabled:
        return {"skipped": "story consolidation flag off"}
    from .plot import story_consolidation

    try:
        return await story_consolidation.maybe_consolidate_story(
            db,
            client,
            chat_id=chat_id,
            round_id=None,
            model_name=model_name,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[Consolidation][chat_id=%d] story component failed: %s", chat_id, exc
        )
        return {"error": str(exc)}


async def _consolidate_chat_index(db: AsyncSession, *, chat_id: int) -> dict:
    """Component: embedding/index refresh for the chat's memories (hard).

    Deterministic fallback to legacy behaviour when ``embedding_enabled`` is
    off — no forced embedding pipeline.
    """
    if not settings.embedding_enabled:
        return {"skipped": "embedding disabled"}
    try:
        emb_service = embedding_service.get_embedding_service()
        stmt = (
            select(models.Memory)
            .where(
                models.Memory.chat_id == chat_id,
                models.Memory.embedding.is_(None),
            )
            .order_by(models.Memory.created_at.desc())
            .limit(200)
        )
        result = await db.execute(stmt)
        memories = list(result.scalars().all())
        processed = 0
        for mem in memories:
            embedding = await emb_service.embed_single(mem.content)
            if embedding:
                mem.embedding = emb_service.pack_embedding(embedding)
                processed += 1
        if processed:
            await db.commit()
        return {"processed": processed, "missing": len(memories)}
    except Exception as exc:  # noqa: BLE001
        logger.warning("[Consolidation][chat_id=%d] index refresh failed: %s", chat_id, exc)
        return {"error": str(exc)}


async def consolidate_chat_adaptive(
    db: AsyncSession,
    client: httpx.AsyncClient,
    *,
    chat_id: int,
    model_name: str,
    level: str,
    since_soft: datetime | None = None,
    since_hard: datetime | None = None,
) -> dict:
    """Full adaptive consolidation set for one chat (§20, Sprint 12).

    - soft: memory merge + summary refresh;
    - hard/critical: + relationship evidence fold + anchors dedupe + story
      update + embedding/index refresh.

    ``since_soft``/``since_hard`` pin the consolidation window (the baselines
    at trigger time, passed through the job payload); when absent they fall back
    to the ``consolidation_state`` markers (or chat creation). Runs only the
    enabled components; every component is isolated so a failure does not break
    the rest. Returns a per-component report.
    """
    report: dict = {"chat_id": chat_id, "level": level}

    report["memory"] = await _consolidate_chat_memories(
        db, client, chat_id=chat_id, model_name=model_name
    )

    if since_soft is None:
        state = await crud.get_consolidation_state(db, chat_id)
        since_soft = (
            state.last_soft_at
            if state is not None and state.last_soft_at
            else datetime.min
        )
    report["summary"] = await _consolidate_chat_summary(
        db, client, chat_id=chat_id, model_name=model_name, since=since_soft
    )

    if level in ("hard", "critical"):
        report["relationship"] = await _consolidate_chat_relationships(
            db, chat_id=chat_id
        )
        report["anchors"] = await _consolidate_chat_anchors(db, chat_id=chat_id)
        report["story"] = await _consolidate_chat_story(
            db, client, chat_id=chat_id, model_name=model_name, level=level
        )
        report["index"] = await _consolidate_chat_index(db, chat_id=chat_id)

    logger.info(
        "[Consolidation][chat_id=%d] adaptive %s complete: %s",
        chat_id,
        level,
        {k: v for k, v in report.items() if k != "chat_id"},
    )
    return report


# ============================================================
# Embedding Generation (P3)
# ============================================================

async def _process_embed_memory_job(payload: dict) -> dict:
    """Job handler for embedding a single memory."""
    memory_id = payload["memory_id"]
    content = payload["content"]
    
    if not settings.embedding_enabled:
        return {"status": "disabled", "memory_id": memory_id}
    
    emb_service = embedding_service.get_embedding_service()
    try:
        embedding = await emb_service.embed_single(content)
        if embedding:
            async with AsyncSessionLocal() as db:
                db_memory = await db.get(models.Memory, memory_id)
                if db_memory:
                    db_memory.embedding = emb_service.pack_embedding(embedding)
                    await db.commit()
                    logger.debug("[Embedding] Generated for memory %d", memory_id)
                    return {"status": "completed", "memory_id": memory_id}
        return {"status": "failed", "memory_id": memory_id, "reason": "no_embedding"}
    except Exception as exc:
        logger.exception("[Embedding] Failed for memory %d: %s", memory_id, exc)
        return {"status": "failed", "memory_id": memory_id, "reason": str(exc)}


async def _process_backfill_embeddings_job(payload: dict) -> dict:
    """Job handler for backfilling embeddings for existing memories."""
    if not settings.embedding_enabled:
        return {"status": "disabled"}
    
    chat_id = payload.get("chat_id", 0)
    batch_size = payload.get("batch_size", 100)
    limit = payload.get("limit", 0)  # 0 = no limit
    
    emb_service = embedding_service.get_embedding_service()
    processed = 0
    failed = 0
    
    try:
        async with AsyncSessionLocal() as db:
            stmt = select(models.Memory).where(models.Memory.embedding.is_(None))
            if chat_id:
                stmt = stmt.where(models.Memory.chat_id == chat_id)
            stmt = stmt.order_by(models.Memory.created_at.desc())
            if limit:
                stmt = stmt.limit(limit)
            
            result = await db.execute(stmt)
            memories = list(result.scalars().all())
            
            logger.info("[Backfill] Found %d memories without embeddings", len(memories))
            
            for i in range(0, len(memories), batch_size):
                batch = memories[i : i + batch_size]
                contents = [m.content for m in batch]
                embeddings = await emb_service.embed_batch(contents)
                
                for mem, emb in zip(batch, embeddings):
                    if emb:
                        mem.embedding = emb_service.pack_embedding(emb)
                        processed += 1
                    else:
                        failed += 1
                
                await db.commit()
                logger.debug("[Backfill] Processed batch %d-%d", i, min(i + batch_size, len(memories)))
        
        logger.info("[Backfill] Complete: processed=%d failed=%d", processed, failed)
        return {"status": "completed", "processed": processed, "failed": failed}
    except Exception as exc:
        logger.exception("[Backfill] Failed: %s", exc)
        return {"status": "failed", "reason": str(exc)}


async def enqueue_embed_memory_job(memory_id: int, content: str) -> models.MemoryJob:
    """Enqueue an embedding generation job for a memory."""
    payload = {"memory_id": memory_id, "content": content}
    return await task_queue.memory_job_queue.enqueue(
        job_type="embed_memory",
        chat_id=0,
        payload=payload,
    )


async def enqueue_backfill_embeddings_job(
    chat_id: int = 0, batch_size: int = 100, limit: int = 0
) -> models.MemoryJob:
    """Enqueue a backfill job for memories missing embeddings."""
    payload = {"chat_id": chat_id, "batch_size": batch_size, "limit": limit}
    return await task_queue.memory_job_queue.enqueue(
        job_type="backfill_embeddings",
        chat_id=chat_id,
        payload=payload,
    )
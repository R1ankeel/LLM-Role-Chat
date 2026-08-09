"""Память: валидация извлечённых фактов и классификатор типов (Sprint 6C).

Sprint 6C (§4.5 decomposition.md): перенос из ``memory_service.py``
`classify_memory_type`, `validate_extracted_facts`, near-dup/grounding фильтры.
Направление: memory/ → crud (без обратных импортов).
"""

import re

import structlog

from .. import schemas
from ..config import settings
from .retrieval import _name_in_text, _tokenize_for_overlap

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

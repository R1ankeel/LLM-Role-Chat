"""Post-round memory extraction and session summarization."""

import asyncio
import json
import logging
import re
from collections import Counter
from datetime import datetime, timedelta
from types import SimpleNamespace

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
        return fact.model_copy(update={"fact": text})

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
) -> witness_model.ObservableContext:
    """Perception-filtered context safe for memory extraction (present/told only)."""
    return witness_model.filter_history_for_memory_extraction(
        messages,
        viewer_character_id,
        character_names,
        presence_map,
        same_round_ids=same_round_ids,
        max_len=len(messages) or 1,
        viewer_location=viewer_location,
        character_locations=character_locations,
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
                observable = get_observable_context_for_character(
                    round_snapshots,
                    char_id,
                    character_names,
                    presence_map,
                    same_round_ids=same_round_ids,
                    viewer_location=viewer_location,
                    character_locations=character_locations,
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
                    created = await crud.create_memory(
                        db,
                        schemas.MemoryCreate(
                            chat_id=chat_id,
                            character_id=char_id,
                            content=fact.fact,
                            importance=fact.importance,
                            category=fact.category,
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
                # Same stricter filter as memory: present/told only (no mentioned snippets)
                observable = get_observable_context_for_character(
                    new_messages,
                    character_id,
                    character_names,
                    presence_map,
                    viewer_location=character_snap.get("location") or "",
                    character_locations=character_locations,
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
) -> dict:
    """Main consolidation job - processes all characters across all chats."""
    if not settings.consolidation_enabled:
        return {"status": "disabled", "chars_processed": 0, "merged": 0, "deleted": 0}

    # Get all characters with memories
    stmt = (
        select(models.Character.id, models.Character.name, models.Character.chat_id)
        .join(models.Memory, models.Memory.character_id == models.Character.id)
        .distinct()
    )
    result = await db.execute(stmt)
    characters = result.all()

    total_merged = 0
    total_deleted = 0
    chars_processed = 0

    for char_id, char_name, chat_id in characters:
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
    """Job handler for consolidation - compatible with task queue."""
    client = httpx.AsyncClient(
        base_url=settings.ollama_base_url,
        timeout=settings.ollama_timeout,
    )
    try:
        async with AsyncSessionLocal() as db:
            return await consolidate_memories_job(
                db, client, payload.get("model_name", settings.default_model)
            )
    finally:
        await client.aclose()


async def enqueue_consolidation_job(chat_id: int = 0, model_name: str | None = None) -> models.MemoryJob:
    """Enqueue a consolidation job."""
    payload = {
        "chat_id": chat_id,
        "model_name": model_name or settings.default_model,
    }
    return await task_queue.memory_job_queue.enqueue(
        job_type="consolidation",
        chat_id=chat_id,
        payload=payload,
    )


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
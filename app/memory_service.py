"""Post-round memory extraction and session summarization.

Sprint 6C (§4.5 decomposition.md): реализация разнесена по пакету
``app/memory/`` (validation, witness, extraction, summaries, consolidation,
adaptive, jobs). Этот модуль — временный фасад, реэкспортирующий публичный API
``memory_service`` (удаляется в спринте 10, decomposition.md §9 этап 21).
"""

import httpx
import structlog

from . import ollama_client
from . import task_queue
from .config import settings
from .database import AsyncSessionLocal
from .memory.retrieval import (
    SimpleBM25,
    RerankContext,
    RerankSignals,
    rerank_memories,
    rerank_weights,
    select_relevant_memories,
    build_rerank_signals,
    get_relevant_memories_for_characters,
    get_hybrid_memories_for_characters,
    _name_in_text,
    _tokenize_for_overlap,
    _story_relevance,
    _relationship_relevance,
    _emotional_relevance,
)
from .memory.validation import (
    MAX_EXISTING_FOR_DEDUP,
    MEMORY_FACT_GROUNDING_MIN_OVERLAP,
    _ACTION_STEM,
    _FALSE_ME_PATIENT_PATTERNS,
    _GENERIC_FACT_PATTERNS,
    _GROUNDING_STOPWORDS,
    _OTHER_MIND_PATTERNS,
    _STORY_FACT_PATTERNS,
    _content_tokens,
    _is_generic_fact,
    _looks_like_false_me_patient,
    _looks_like_other_mind,
    classify_memory_type,
    fact_grounding_overlap,
    jaccard_similarity,
    validate_extracted_fact,
    validate_extracted_facts,
)
from .memory.witness import (
    _CHARACTER_CARD_FIELDS,
    _character_from_snapshot,
    _format_messages_as_text,
    _format_round_as_text,
    _get_attr,
    _log_memory_perception,
    _sensors_proposal_to_facts,
    _witness_filtered_text,
    get_observable_context_for_character,
)
from .memory.extraction import _extract_and_save_memories
from .memory.summaries import _maybe_update_summaries
from .memory.consolidation import (
    _cluster_memories_by_similarity,
    _consolidate_character_memories,
    _merge_memory_cluster_llm,
    consolidate_memories_job,
)
from .memory.adaptive import (
    CONSOLIDATION_COUNT_KEYS,
    CRITICAL_ACTION_KEYWORDS,
    _consolidate_chat_anchors,
    _consolidate_chat_index,
    _consolidate_chat_memories,
    _consolidate_chat_relationships,
    _consolidate_chat_story,
    _consolidate_chat_summary,
    _consolidation_weights,
    _latest_critical_event,
    _parse_consolidation_counters,
    _parse_payload_dt,
    compute_consolidation_score,
    consolidate_chat_adaptive,
    evaluate_consolidation,
    is_critical_event,
    schedule_adaptive_consolidation,
)
from .memory.jobs import (
    _process_backfill_embeddings_job,
    _process_consolidation_job,
    _process_embed_memory_job,
    _process_post_round_job,
    enqueue_backfill_embeddings_job,
    enqueue_consolidation_job,
    enqueue_embed_memory_job,
    process_post_round,
)

logger = structlog.get_logger(__name__)

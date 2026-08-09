"""Chat engine facade: public API (Milestone 6A).

После спринта 5B (decomposition.md §4.2) streaming-ядро живёт в ``app/pipeline/``:
``process_user_message_streaming`` (streaming.py), ``process_user_message`` и
общие хелперы раунда (session.py), ``regenerate_message_streaming``
(regeneration.py), LoRA-резолв (lora.py), story-блок и belief evidence (story.py).

После Milestone 6A анализ отношений переехал в ``app/pipeline/relations.py``:
``_analyze_and_update_relationships``, ``_run_sensors_relationship_proposal``,
``_run_per_pair_analysis``, evidence/constrain (``_evidence_mode``,
``_constrain_pair_delta``, ``_build_pair_relationship_context``), hearsay caps.
Модуль остаётся фасадом: реэкспортирует публичный API и сохраняет контракт
патчей тестов (``app.chat_engine.{asyncio, ollama_client, settings,
AsyncSessionLocal, relationship_service, relationship_analyzer, ...}``).
"""

import asyncio  # noqa: F401 — патчится тестами (app.chat_engine.asyncio.create_task)
import logging

from . import memory_service
from . import ollama_client
from . import relationship_analyzer
from . import relationship_service
from .config import settings
from .database import AsyncSessionLocal

logger = logging.getLogger(__name__)

from .pipeline.lora import (
    _LORA_MANAGER_DEFAULT,
    _default_lora_manager,
    _lora_unknown_warned_chats,
    lora_first_apply_warning,
    resolve_generation_model,
)
from .pipeline.regeneration import regenerate_message_streaming
from .pipeline.relations import (
    _analyze_and_update_relationships,
    _belief_multiplier,
    _build_batch_scene_summary,
    _build_pair_relationship_context,
    _compute_hearsay_effective_cap,
    _constrain_pair_delta,
    _evidence_mode,
    _hearsay_effective_cap,
    _run_per_pair_analysis,
    _run_sensors_relationship_proposal,
    _text_mentions_name,
    evidence_mode_from_perception,
)
from .pipeline.session import (
    _build_character_round_text,
    _character_is_isolated,
    _character_to_snapshot,
    _create_message_with_shadow,
    _detect_communication_channel,
    _directly_addressed_ids,
    _effective_prior_replies,
    _is_location_allowed,
    _load_location_descriptions,
    _log_generation_diagnostics,
    _message_snapshot,
    _message_to_dict,
    _parse_allowed_locations,
    _parse_known_locations,
    _scene_gate_confirms,
    process_user_message,
)
from .pipeline.story import (
    _belief_evidenced_ids,
    _chat_plot_text,
    _chat_story_block,
    _compute_epistemic_evidence,
)
from .pipeline.streaming import process_user_message_streaming

"""CRUD-функции для работы с базой данных (Async) — пакет (Sprint 4).

Реэкспорт всего публичного API (и приватных символов, которые
пересекают границы модулей) — временный фасад, чтобы ``from . import
crud`` у потребителей продолжал работать. Снимается в спринте 10
(этап 19).
"""

from __future__ import annotations

from ..config import settings  # публичный атрибут app.crud.settings (тесты)

from ..memory.retrieval import (  # временный фасад (Sprint 1, §7.1)
    RerankContext,
    RerankSignals,
    build_rerank_signals,
    get_hybrid_memories_for_characters,
    get_relevant_memories_for_characters,
)

from .chats import (create_chat, get_chat, get_chats, update_chat, delete_chat, clear_chat_messages, clear_chat_memories, clear_chat_relationships, clear_chat_world_events, clear_chat_threads, clear_chat_memory_jobs)
from .characters import (_sync_player_character_location, _sync_chat_player_location, resolve_player_location, _order_index_taken, create_character, get_character, get_characters_by_chat, get_player_character, create_player_character, update_character, delete_character, update_character_location, get_character_locations_by_chat, update_character_locations_batch, ApplyActionsResult, apply_character_actions, _locations_same)
from .messages import (_build_world_event, create_message, get_messages_by_chat, get_messages_paginated, count_messages_after, get_messages_since, get_messages_since_ts, delete_message)
from .memories import (_memory_exists, create_memory, _count_memories_for_character, _delete_lowest_value_memories, _delete_oldest_memories, ensure_memory_limit, get_memories_by_character, filter_memories_by_witness, _apply_witness_boost, decay_memory_importance, _touch_memory_access, get_memories_for_characters, delete_memory, update_memory, anchor_activation_score, select_top_anchors, create_memory_anchor, get_anchors_for_relationship, get_anchors_for_relationships, get_consolidation_state, upsert_consolidation_state, reset_consolidation_state, count_consolidation_inputs, WitnessQuality, _CONSOLIDATION_INPUTS)
from .summaries import (get_character_summary, get_summaries_for_characters, upsert_character_summary, reset_character_summaries_for_chat)
from .presence import (upsert_message_presence_batch, get_presence_map, get_attention_map, get_presence_for_message, get_attention_for_message, _attention_context_for_chat)
from .locations import (get_chat_locations, get_adjacency_index, get_location, resolve_location_name, resolve_location_string, LocationBackfillReport, backfill_character_location_ids, PlotBackfillReport, backfill_plot_fields, EventLocationBackfillReport, backfill_event_location_ids, _sync_chat_locations_cache, _location_name_conflict, create_location, update_location, _rename_location_references, get_characters_referencing_location, delete_location)
from .threads import (_get_thread, get_or_create_thread, ensure_thread_participant, mark_thread_delivered, _ensure_thread_for_action, ensure_message_thread_delivery, thread_delivery_ids_for_message, _known_voices_for_chat)
from .scene import (get_scene_state, upsert_scene_state, get_present_character_ids, get_scene_state_with_presence)
from .rounds import (parse_round_id, get_latest_round_id, get_round_messages_by_round_id, _clamp01, save_round_events, _clamp_json_number)
from .events import (get_relationship_events_for_round, get_world_events_for_round, get_round_world_events, get_story_round_world_events, get_world_events_for_chat, get_world_events_by_ids, get_event_links_for_events, get_caused_by_ids_for_events)
from .story import (_parse_json_list, get_story_state, get_or_create_story_state, update_story_state, get_story_events_for_chat, count_story_events, count_distinct_rounds, get_story_event_ids_for_chat, create_story_event, get_active_story_threads, get_story_threads_for_chat, get_story_threads_by_status, find_story_thread_by_name, create_story_thread, update_story_thread, set_story_event_thread)
from .state import (get_character_state, get_character_states_for_chat, get_or_create_character_state, update_character_state, get_beliefs_for_character, get_beliefs_for_chat, _find_belief, merge_confidence, upsert_belief, delete_belief)
from .intents import (save_intent, get_intents_for_character, get_relationship_target_id, count_pair_interaction_rounds)
from .interventions import (_recipient_ids, create_intervention, list_interventions, get_chat_wide_intervention, delete_intervention, delete_chat_wide_intervention, clear_interventions)
from .plans import (get_active_npc_plan, get_npc_plans_for_character, create_npc_plan, update_npc_plan)
from .lora import (_normalize_lora_metadata, list_lora_adapters, get_lora_adapter, create_lora_adapter, update_lora_adapter, get_chat_lora_adapter, get_chat_lora_config, put_chat_lora_config, list_adapter_usage_chats, delete_lora_adapter)

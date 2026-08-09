"""Пакет отношений (Milestone 6B, decomposition.md §4.4).

Разбиение ``app/relationship_service.py`` на модули без изменения поведения
(тела функций перенесены 1:1):

- ``crud.py`` — чтение/запись строк ``CharacterRelationship``;
- ``validation.py`` — константы типов/переходов и валидация;
- ``deltas.py`` — применение дельт движка и anti-inflation;
- ``blocks.py`` — форматирование блоков промпта;
- ``issues.py`` — open issues и проактивный буст;
- ``decay.py`` — затухание и архивирование событий;
- ``memory_feed.py`` — интеграция памяти/якорей;
- ``trajectory.py`` — траектория метрик для saturation guard.

Зависимости внутри пакета — ацикличны: ``crud``/``validation``/``memory_feed``
— листья; ``issues`` → crud, memory_feed; ``deltas`` → crud, validation,
issues, memory_feed; ``blocks`` → crud, issues; ``decay`` → app.crud;
``trajectory`` → deltas. Наружу пакет импортирует только ``app.crud``
(направление зависимостей relationships → crud одностороннее).
"""

from ..config import settings
from ..models import RelationshipEvent

from .crud import (
    get_or_create_relationship,
    get_relationship,
    list_received_relationships,
    list_relationships_for_chat,
    list_relationships_for_character,
    update_relationship_fields,
)
from .validation import (
    FAMILY_TYPES,
    TRANSITIONS,
    VALID_TYPES,
    clamp_metric,
    is_family_type,
    validate_relationship_type_update,
    validate_transition,
)
from .deltas import (
    MAX_DELTA,
    _clamp_delta,
    _log_relationship_event,
    apply_delta,
    apply_saturation_guard,
    scale_delta_by_resistance,
    trajectory_metric_gain,
)
from .blocks import (
    _beliefs_by_subject,
    _epistemic_belief_line,
    build_behavior_drivers_block,
    build_epistemic_mask_block,
    build_relationships_block,
    compute_reciprocity_belief_multiplier,
    format_relationship_for_prompt,
    get_recent_events,
)
from .issues import (
    _ISSUE_INSTRUCTION_MARKERS,
    _CTRL_CHARS_RE,
    _clamp,
    _clamp01,
    _is_near_dup,
    _jaccard,
    _tokenize,
    _validate_source_message_ids,
    apply_issue_deltas,
    build_open_issues_block,
    compute_proactive_boost,
    create_issue,
    list_issues_for_chat,
    list_open_issues,
    list_top_open_issues_for_character,
    proactive_boost_from_issues,
    resolve_issue,
    sanitize_issue_text,
    tick_open_issues,
    touch_issue,
)
from .decay import _dynamic_decay_factor, apply_decay, prune_relationship_events
from .memory_feed import (
    _anchor_emotion_from_deltas,
    _anchor_valence_from_deltas,
    _maybe_create_memory_from_event,
    _maybe_create_memory_from_resolved_issue,
)
from .trajectory import build_trajectory_block, get_trajectory_events, recent_gain

__all__ = [
    "settings",
    "RelationshipEvent",
    "FAMILY_TYPES",
    "TRANSITIONS",
    "VALID_TYPES",
    "MAX_DELTA",
    "get_or_create_relationship",
    "get_relationship",
    "list_received_relationships",
    "list_relationships_for_chat",
    "list_relationships_for_character",
    "update_relationship_fields",
    "clamp_metric",
    "is_family_type",
    "validate_relationship_type_update",
    "validate_transition",
    "apply_delta",
    "apply_saturation_guard",
    "scale_delta_by_resistance",
    "trajectory_metric_gain",
    "build_behavior_drivers_block",
    "build_epistemic_mask_block",
    "build_relationships_block",
    "compute_reciprocity_belief_multiplier",
    "format_relationship_for_prompt",
    "get_recent_events",
    "apply_issue_deltas",
    "build_open_issues_block",
    "compute_proactive_boost",
    "create_issue",
    "list_issues_for_chat",
    "list_open_issues",
    "list_top_open_issues_for_character",
    "proactive_boost_from_issues",
    "resolve_issue",
    "sanitize_issue_text",
    "tick_open_issues",
    "touch_issue",
    "apply_decay",
    "prune_relationship_events",
    "build_trajectory_block",
    "get_trajectory_events",
    "recent_gain",
]

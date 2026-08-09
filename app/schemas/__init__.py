"""Pydantic-схемы для CRUD-операций (Sprint 3, decomposition-sprints.md §4).

Пакет разбит на доменные модули: chat, character, message, memory,
relationship, scene, context, job, story, belief, state, lora, perception.
Все классы/функции реэкспортируются отсюда — публичный API пакета
``app.schemas`` не меняется (сверка ``Plans/artifacts/schemas-api-before.txt``).
"""

import json
from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..config import settings
from ..perception import parse_target_ids, serialize_target_ids
from ..stimuli import parse_stimuli, serialize_stimuli

from .belief import BeliefRead, BeliefSource, BeliefType
from .character import (
    CharacterBase,
    CharacterCreate,
    CharacterLocationUpdate,
    CharacterRead,
    CharacterSummaryRead,
    CharacterUpdate,
    InitialRelationship,
    LocationBase,
    LocationCreate,
    LocationRead,
    LocationUpdate,
)
from .chat import (
    ChatBase,
    ChatCreate,
    ChatDetail,
    ChatRead,
    ChatUpdate,
    ClearHistoryRequest,
    InterventionCreate,
    InterventionRead,
    UserMessage,
)
from .context import BuiltContext, ContextBudget, ContextDiagnostics, DroppedItem
from .job import MemoryJobRead
from .lora import (
    ChatLoRAConfig,
    LoRAAdapterCreate,
    LoRAAdapterFormat,
    LoRAAdapterRead,
    LoRAAdapterUpdate,
)
from .memory import (
    ExtractedFact,
    MemoryBase,
    MemoryCategory,
    MemoryCreate,
    MemoryRead,
    MemoryType,
    MemoryUpdate,
    normalize_category,
    normalize_memory_type,
)
from .message import (
    CommunicationChannel,
    EventVisibility,
    MessageBase,
    MessageCreate,
    MessagePresenceCreate,
    MessageRead,
    PresenceType,
    Role,
)
from .perception import (
    Action,
    ActionType,
    AudioLevel,
    PerceptionResult,
    RemoteStatus,
    TurnOutput,
    VisualLevel,
    build_take_actions_json_schema,
    build_take_actions_tool,
)
from .relationship import (
    ISSUE_TYPES,
    CharacterRelationshipRead,
    CharacterRelationshipUpdate,
    IssueDelta,
    IssueType,
    RelationshipDelta,
    RelationshipEventRead,
    RelationshipIssueRead,
    RelationshipIssueResolve,
)
from .scene import (
    EventAction,
    EventExtractionReport,
    EventExtractionResult,
    ExtractedEvent,
    SceneCustomState,
    SceneStateBase,
    SceneStateRead,
    SceneStateUpdate,
)
from .state import CharacterStateRead
from .story import (
    StoryEventRead,
    StoryStateRead,
    StoryStateResponse,
    StoryStateUpdate,
    StoryThreadRead,
)

__all__ = [
    "Action",
    "ActionType",
    "Any",
    "AudioLevel",
    "BaseModel",
    "BeliefRead",
    "BeliefSource",
    "BeliefType",
    "BuiltContext",
    "CharacterBase",
    "CharacterCreate",
    "CharacterLocationUpdate",
    "CharacterRead",
    "CharacterRelationshipRead",
    "CharacterRelationshipUpdate",
    "CharacterStateRead",
    "CharacterSummaryRead",
    "CharacterUpdate",
    "ChatBase",
    "ChatCreate",
    "ChatDetail",
    "ChatLoRAConfig",
    "ChatRead",
    "ChatUpdate",
    "ClearHistoryRequest",
    "CommunicationChannel",
    "ConfigDict",
    "ContextBudget",
    "ContextDiagnostics",
    "DroppedItem",
    "EventAction",
    "EventExtractionReport",
    "EventExtractionResult",
    "EventVisibility",
    "ExtractedEvent",
    "ExtractedFact",
    "Field",
    "ISSUE_TYPES",
    "InitialRelationship",
    "InterventionCreate",
    "InterventionRead",
    "IssueDelta",
    "IssueType",
    "Literal",
    "LoRAAdapterCreate",
    "LoRAAdapterFormat",
    "LoRAAdapterRead",
    "LoRAAdapterUpdate",
    "LocationBase",
    "LocationCreate",
    "LocationRead",
    "LocationUpdate",
    "MemoryBase",
    "MemoryCategory",
    "MemoryCreate",
    "MemoryJobRead",
    "MemoryRead",
    "MemoryType",
    "MemoryUpdate",
    "MessageBase",
    "MessageCreate",
    "MessagePresenceCreate",
    "MessageRead",
    "Optional",
    "PerceptionResult",
    "PresenceType",
    "RelationshipDelta",
    "RelationshipEventRead",
    "RelationshipIssueRead",
    "RelationshipIssueResolve",
    "RemoteStatus",
    "Role",
    "SceneCustomState",
    "SceneStateBase",
    "SceneStateRead",
    "SceneStateUpdate",
    "StoryEventRead",
    "StoryStateRead",
    "StoryStateResponse",
    "StoryStateUpdate",
    "StoryThreadRead",
    "TurnOutput",
    "UserMessage",
    "VisualLevel",
    "build_take_actions_json_schema",
    "build_take_actions_tool",
    "datetime",
    "field_validator",
    "json",
    "model_validator",
    "normalize_category",
    "normalize_memory_type",
    "parse_stimuli",
    "parse_target_ids",
    "serialize_stimuli",
    "serialize_target_ids",
    "settings",
]
